"""Worker mode: the bootstrap-trust step and the opt-in gate.

The security-critical moment in the whole feature is here. A worker has nothing
to validate the control plane's self-signed certificate against except the
fingerprint inside its enrollment token, so this is the one place where a
mismatch must stop everything rather than warn.
"""
from __future__ import annotations

import asyncio
import errno
import socket
import ssl
import threading
from pathlib import Path

import pytest

from worker import agent, identity, tls
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S


@pytest.fixture
def control_plane_tls(tmp_path):
    """A throwaway TLS listener presenting a real certificate."""
    creds = tls.generate_self_signed(hostnames=["localhost", "127.0.0.1"])
    cert_file = tmp_path / "c.pem"
    key_file = tmp_path / "k.pem"
    cert_file.write_bytes(creds.certificate_pem)
    key_file.write_bytes(creds.private_key_pem)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def _serve():
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True):
                    pass
            except OSError:
                pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        yield creds, f"localhost:{port}"
    finally:
        stop.set()
        listener.close()


# ── The opt-in gate ────────────────────────────────────────────────────────


def test_worker_mode_is_off_by_default(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_WORKER_MODE", raising=False)
    assert agent.worker_mode_enabled() is False


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("on", True),
                                            ("0", False), ("", False), ("no", False)])
def test_worker_mode_env_gate(monkeypatch, value, expected):
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", value)
    assert agent.worker_mode_enabled() is expected


@pytest.mark.asyncio
async def test_start_if_worker_mode_is_a_no_op_when_off(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_WORKER_MODE", raising=False)
    started = []
    monkeypatch.setattr(agent.agent, "start", lambda **k: started.append(True))
    await agent.start_if_worker_mode()
    assert started == []


@pytest.mark.asyncio
async def test_a_failing_agent_never_takes_the_app_down(monkeypatch):
    """A machine that cannot reach its control plane is still a perfectly good
    OmniVoice install for whoever is sitting at it."""
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")

    async def _boom(**kwargs):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(agent.agent, "start", _boom)
    await agent.start_if_worker_mode()  # must not raise


# ── Trust on first use ─────────────────────────────────────────────────────


def test_certificate_is_fetched_from_a_live_server(control_plane_tls):
    creds, endpoint = control_plane_tls
    fetched = agent.fetch_server_certificate(endpoint)
    assert fetched.startswith(b"-----BEGIN CERTIFICATE-----")

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    presented = x509.load_pem_x509_certificate(fetched)
    assert tls.pin_matches(
        presented.public_bytes(serialization.Encoding.DER), creds.fingerprint
    )


def test_matching_fingerprint_pins_the_certificate(control_plane_tls, tmp_path):
    creds, endpoint = control_plane_tls
    token = identity.mint_enrollment_token(
        endpoint=endpoint, cert_fingerprint=creds.fingerprint
    )
    path = str(tmp_path / "pinned.crt")

    resolved_endpoint, certificate = agent.pin_certificate(token.encode(), cert_path=path)

    assert resolved_endpoint == endpoint
    assert certificate.startswith(b"-----BEGIN CERTIFICATE-----")
    with open(path, "rb") as fh:
        assert fh.read() == certificate


def test_a_mismatched_fingerprint_stops_everything(control_plane_tls, tmp_path):
    """The café-network substitution. A warn-and-continue here would make the
    entire pinning design decorative."""
    _creds, endpoint = control_plane_tls
    impostor = tls.generate_self_signed(hostnames=["localhost"])
    token = identity.mint_enrollment_token(
        endpoint=endpoint, cert_fingerprint=impostor.fingerprint
    )
    path = str(tmp_path / "pinned.crt")

    with pytest.raises(ValueError, match="does not match"):
        agent.pin_certificate(token.encode(), cert_path=path)

    import os

    assert not os.path.exists(path), "a rejected certificate must never be pinned"


def test_an_expired_token_is_refused_before_any_connection(tmp_path):
    token = identity.mint_enrollment_token(
        endpoint="127.0.0.1:1", cert_fingerprint="ab" * 32, ttl_seconds=-1
    )
    with pytest.raises(ValueError, match="expired"):
        agent.pin_certificate(token.encode(), cert_path=str(tmp_path / "p.crt"))


def test_a_malformed_endpoint_is_rejected():
    with pytest.raises(ValueError, match="host:port"):
        agent.fetch_server_certificate("no-port-here")


def test_ipv6_certificate_fetch_strips_endpoint_brackets(monkeypatch):
    captured = {}

    class ContextManager:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    class Secured:
        def getpeercert(self, *, binary_form=False):
            assert binary_form is True
            return b"certificate-der"

    class Context:
        def wrap_socket(self, raw, *, server_hostname):
            captured["raw"] = raw
            captured["server_hostname"] = server_hostname
            return ContextManager(Secured())

    raw = object()

    def connect(address, *, timeout):
        captured["address"] = address
        captured["timeout"] = timeout
        return ContextManager(raw)

    monkeypatch.setattr(agent.ssl, "create_connection", connect)
    monkeypatch.setattr(
        agent.ssl,
        "DER_cert_to_PEM_cert",
        lambda der: "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----",
    )
    monkeypatch.setattr(tls, "unverified_client_context", Context)

    certificate = agent.fetch_server_certificate("[2001:db8::1]:7443", timeout=3)

    assert captured["address"] == ("2001:db8::1", 7443)
    assert captured["server_hostname"] == "2001:db8::1"
    assert captured["timeout"] == 3
    assert certificate.startswith(b"-----BEGIN CERTIFICATE-----")


# ── Enrollment state ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_starting_without_enrollment_explains_what_to_do(monkeypatch, tmp_path):
    """The error has to name the next action; "not enrolled" alone is a wall."""
    monkeypatch.delenv("OMNIVOICE_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(
        agent,
        "_paths",
        lambda: {
            "root": str(tmp_path),
            "worker_key": str(tmp_path / "worker.key"),
            "pinned_cert": str(tmp_path / "absent.crt"),
            "worker_id": str(tmp_path / "worker-id"),
        },
    )

    with pytest.raises(RuntimeError, match="has not been enrolled"):
        await agent.WorkerAgent().start()


# ── What the agent hands the client ────────────────────────────────────────


class _FakeClient:
    """Stands in for WorkerClient; records what the agent constructed it with."""

    last = None

    def __init__(self, config, **hooks):
        type(self).last = self
        self.config = config
        self.hooks = hooks
        self.stopped = False

    async def run_forever(self):
        await asyncio.Event().wait()

    async def stop(self):
        self.stopped = True


class _RegisteringClient(_FakeClient):
    """Completes enrollment, then remains alive like the real dial-out loop."""

    async def run_forever(self):
        self.advertised_capabilities = self.hooks["capability_probe"]()
        self.hooks["on_registered"]("headless-worker")
        self.hooks["on_activated"]("headless-worker")
        await asyncio.Event().wait()


class _RejectingClient(_FakeClient):
    """The candidate control plane refuses registration before the callback."""

    async def run_forever(self):
        raise RuntimeError("AUTH_FAILED: registration rejected")


@pytest.mark.asyncio
async def test_retry_retires_failed_client_and_idle_sweep(
    monkeypatch, enrolled
):
    from worker import capabilities
    from worker.transport import client as transport

    class FailThenRun(_FakeClient):
        instances = []

        def __init__(self, config, **hooks):
            super().__init__(config, **hooks)
            type(self).instances.append(self)

        async def run_forever(self):
            if self is type(self).instances[0]:
                raise transport.TerminalRegistrationError(
                    "AUTH_FAILED: registration rejected"
                )
            await asyncio.Event().wait()

    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(transport, "WorkerClient", FailThenRun)
    instance = agent.WorkerAgent()

    await instance.start()
    first_task = instance._task
    first_sweep = instance._idle_sweep
    assert first_task is not None
    assert first_sweep is not None
    with pytest.raises(transport.TerminalRegistrationError):
        await asyncio.wait_for(asyncio.shield(first_task), timeout=HANG_GUARD_S)

    await instance.start()
    second_task = instance._task
    second_sweep = instance._idle_sweep

    assert FailThenRun.instances[0].stopped is True
    assert first_sweep.done() and first_sweep.cancelled()
    assert second_task is not None and second_task is not first_task
    assert second_sweep is not None and second_sweep is not first_sweep
    assert not second_task.done()

    await instance.stop()

    assert all(client.stopped for client in FailThenRun.instances)
    assert second_task.done()
    assert second_sweep.done()
    assert instance._task is None
    assert instance._idle_sweep is None
    assert instance._client is None


@pytest.mark.asyncio
async def test_environment_only_startup_enrolls_and_advertises_capabilities(
    monkeypatch, tmp_path
):
    """A headless host needs only the two documented environment variables."""
    from worker import capabilities
    from worker.transport import client as transport

    expected_capabilities = [
        {
            "engine": "indextts",
            "model_id": "indextts:default",
            "operations": ["tts", "clone"],
            "supported": True,
            "installed": True,
            "downloaded": True,
        }
    ]
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
    }
    remembered = []
    verified = []

    def _verify(token_text):
        verified.append(token_text)
        assert token_text == "ovw_headless"
        return "studio.internal:7443", b"pinned certificate"

    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_headless")
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_verify_enrollment_token", _verify)
    # Record the compatibility write without persisting it. The manifest must
    # be sufficient on a headless host where settings_store is unavailable.
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: "")
    monkeypatch.setattr(agent, "_remember_endpoint", remembered.append)
    monkeypatch.setattr(capabilities, "discover", lambda **_: expected_capabilities)
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [{"vendor": "nvidia"}])
    monkeypatch.setattr(transport, "WorkerClient", _RegisteringClient)
    _RegisteringClient.last = None
    instance = agent.WorkerAgent()
    monkeypatch.setattr(agent, "agent", instance)

    try:
        await agent.start_if_worker_mode()
        await instance.wait_until_registered(timeout=1)
        registered_readiness = instance.readiness()
    finally:
        await instance.stop()

    assert remembered == ["studio.internal:7443"]
    assert (tmp_path / "control-plane.pinned.crt").read_bytes() == b"pinned certificate"
    assert (tmp_path / "worker-id").read_text(encoding="utf-8") == "headless-worker"
    assert (tmp_path / "enrollment-token.sha256").read_text(encoding="ascii") == (
        agent._token_hash("ovw_headless")
    )
    assert agent._load_enrollment_manifest(str(tmp_path / "enrollment.json")) == {
        "endpoint": "studio.internal:7443",
        "worker_id": "headless-worker",
        "token_hash": agent._token_hash("ovw_headless"),
        "certificate": b"pinned certificate",
    }
    assert _RegisteringClient.last.config.enrollment_token == "ovw_headless"
    assert _RegisteringClient.last.config.capabilities == expected_capabilities
    assert _RegisteringClient.last.advertised_capabilities == expected_capabilities
    assert registered_readiness == {"ready": True, "status": "ready"}

    monkeypatch.setattr(transport, "WorkerClient", _FakeClient)
    _FakeClient.last = None
    restarted = agent.WorkerAgent()
    try:
        await restarted.start()
    finally:
        await restarted.stop()

    assert verified == ["ovw_headless"]
    assert _FakeClient.last.config.endpoint == "studio.internal:7443"
    assert _FakeClient.last.config.certificate_pem == b"pinned certificate"
    assert _FakeClient.last.config.worker_id == "headless-worker"
    assert _FakeClient.last.config.enrollment_token == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("", "has not been enrolled"),
        ("not-a-token", "invalid enrollment token"),
    ],
)
async def test_headless_readiness_rejects_missing_or_invalid_tokens(
    monkeypatch, tmp_path, token, message
):
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
    }
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    if token:
        monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
        monkeypatch.setattr(
            agent,
            "_verify_enrollment_token",
            lambda _token: (_ for _ in ()).throw(ValueError(message)),
        )
    else:
        monkeypatch.delenv("OMNIVOICE_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    instance = agent.WorkerAgent()
    monkeypatch.setattr(agent, "agent", instance)

    await agent.start_if_worker_mode()

    readiness = instance.readiness()
    assert readiness["ready"] is False
    assert readiness["status"] == "failed"
    assert message in instance.last_error


@pytest.mark.asyncio
async def test_a_consumed_environment_token_is_not_redeemed_after_restart(
    monkeypatch, enrolled, tmp_path
):
    """Compose keeps its environment across restarts; the join code is one-use."""
    from worker import capabilities

    (tmp_path / "worker-id").write_text("headless-worker", encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(
        agent._token_hash("ovw_already_spent"), encoding="ascii"
    )
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_already_spent")
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])

    def _redeem_again(_token):
        raise AssertionError("a persisted one-use token was redeemed twice")

    monkeypatch.setattr(agent, "_verify_enrollment_token", _redeem_again)

    instance = agent.WorkerAgent()
    try:
        await instance.start()
    finally:
        await instance.stop()

    assert enrolled.last.config.worker_id == "headless-worker"
    assert enrolled.last.config.enrollment_token == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("remembered_endpoint", [True, False])
async def test_legacy_same_environment_token_stays_unredeemed_after_key_reconnect(
    monkeypatch, enrolled, tmp_path, control_plane_tls, remembered_endpoint
):
    """Upgrade a legacy Compose volume without spending its old token again."""
    from worker import capabilities
    from worker.transport import client as transport

    creds, endpoint = control_plane_tls
    token = identity.mint_enrollment_token(
        endpoint=endpoint, cert_fingerprint=creds.fingerprint
    ).encode()
    (tmp_path / "control-plane.pinned.crt").write_bytes(creds.certificate_pem)
    (tmp_path / "worker-id").write_text("headless-worker", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
    if remembered_endpoint:
        monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT", endpoint)
        monkeypatch.setattr(agent, "_stored_endpoint", lambda: endpoint)
    else:
        monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
        monkeypatch.setattr(agent, "_stored_endpoint", lambda: "")
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])

    def _redeem_again(_token):
        raise AssertionError("the legacy enrollment token was redeemed twice")

    class LegacyKeyReconnectClient(_RegisteringClient):
        attempts = []

        async def run_forever(self):
            type(self).attempts.append(self.config.enrollment_token)
            await super().run_forever()

    monkeypatch.setattr(agent, "_verify_enrollment_token", _redeem_again)
    monkeypatch.setattr(transport, "WorkerClient", LegacyKeyReconnectClient)
    LegacyKeyReconnectClient.last = None
    LegacyKeyReconnectClient.attempts = []

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        await instance.wait_until_registered(timeout=1)
    finally:
        await instance.stop()

    assert LegacyKeyReconnectClient.attempts == [""]
    assert LegacyKeyReconnectClient.last.config.enrollment_token == ""
    assert LegacyKeyReconnectClient.last.config.endpoint == endpoint
    assert (tmp_path / "control-plane.pinned.crt").read_bytes() == creds.certificate_pem
    assert agent._load_consumed_token_hash(
        str(tmp_path / "enrollment-token.sha256")
    ) == ""
    assert agent._load_enrollment_manifest(str(tmp_path / "enrollment.json"))[
        "token_hash"
    ] == ""


@pytest.mark.asyncio
async def test_legacy_same_origin_fresh_token_recovers_after_key_auth_fails(
    monkeypatch, tmp_path, control_plane_tls
):
    """A reset control plane can redeem an indistinguishable fresh token once."""
    from worker import capabilities
    from worker.protocol.gen import worker_v1_pb2 as pb
    from worker.transport import client as transport

    credentials, endpoint = control_plane_tls
    token = identity.mint_enrollment_token(
        endpoint=endpoint, cert_fingerprint=credentials.fingerprint
    ).encode()
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(tmp_path / "enrollment.json"),
    }
    (tmp_path / "control-plane.pinned.crt").write_bytes(credentials.certificate_pem)
    (tmp_path / "worker-id").write_text("legacy-worker", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
    monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT", endpoint)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: endpoint)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    verified = []

    def _verify(token_text):
        verified.append(token_text)
        return endpoint, credentials.certificate_pem

    monkeypatch.setattr(agent, "_verify_enrollment_token", _verify)
    monkeypatch.setattr(agent, "_remember_endpoint", lambda _endpoint: None)

    class ResetControlPlaneClient(transport.WorkerClient):
        attempts = []
        worker_ids = []

        async def _connect_once(self):
            type(self).attempts.append(self.config.enrollment_token)
            type(self).worker_ids.append(self.config.worker_id)
            if not self.config.enrollment_token:
                raise transport.TerminalRegistrationError(
                    "AUTH_FAILED: worker identity is unknown"
                )
            if type(self).attempts.count(token) == 1:
                # Enrollment committed remotely, but its first Register
                # response vanished.  The identical spent-token retry must be
                # signed over an empty, not panel-local stale, worker id.
                return
            await self.accept_registration(
                pb.RegisterResponse(
                    worker_id="recovered-worker",
                    session_token="recovered-session",
                    session_epoch=1,
                )
            )
            self.confirm_activation()
            await self._stop.wait()

    monkeypatch.setattr(transport, "WorkerClient", ResetControlPlaneClient)
    ResetControlPlaneClient.attempts = []
    ResetControlPlaneClient.worker_ids = []

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        await instance.wait_until_registered(timeout=1)
    finally:
        await instance.stop()

    assert ResetControlPlaneClient.attempts == ["", token, token]
    assert ResetControlPlaneClient.worker_ids == ["legacy-worker", "", ""]
    assert verified == [token]
    assert agent._load_enrollment_manifest(locations["enrollment_manifest"]) == {
        "endpoint": endpoint,
        "worker_id": "recovered-worker",
        "token_hash": agent._token_hash(token),
        "certificate": credentials.certificate_pem,
    }
    assert agent._load_consumed_token_hash(
        locations["enrollment_token_hash"]
    ) == agent._token_hash(token)


@pytest.mark.asyncio
async def test_legacy_token_recovers_after_key_session_then_control_plane_reset(
    monkeypatch, tmp_path, control_plane_tls
):
    """Key success cannot spend the fallback needed by a later database reset."""
    from worker import capabilities
    from worker.protocol.gen import worker_v1_pb2 as pb
    from worker.transport import client as transport

    credentials, endpoint = control_plane_tls
    token = identity.mint_enrollment_token(
        endpoint=endpoint, cert_fingerprint=credentials.fingerprint
    ).encode()
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(tmp_path / "enrollment.json"),
    }
    (tmp_path / "control-plane.pinned.crt").write_bytes(credentials.certificate_pem)
    (tmp_path / "worker-id").write_text("legacy-worker", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
    monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT", endpoint)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: endpoint)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(agent, "_remember_endpoint", lambda _endpoint: None)
    verified = []

    def _verify(token_text):
        verified.append(token_text)
        return endpoint, credentials.certificate_pem

    monkeypatch.setattr(agent, "_verify_enrollment_token", _verify)
    key_connected = asyncio.Event()
    reset_control_plane = asyncio.Event()
    token_accepted = asyncio.Event()

    class ResetAfterKeySuccessClient(transport.WorkerClient):
        attempts = []

        async def _connect_once(self):
            type(self).attempts.append(self.config.enrollment_token)
            if len(type(self).attempts) == 1:
                await self.accept_registration(
                    pb.RegisterResponse(
                        worker_id="legacy-worker",
                        session_token="key-session",
                        session_epoch=1,
                    )
                )
                self.confirm_activation()
                key_connected.set()
                await reset_control_plane.wait()
                return
            if not self.config.enrollment_token:
                raise transport.TerminalRegistrationError(
                    "AUTH_FAILED: worker identity is unknown"
                )
            await self.accept_registration(
                pb.RegisterResponse(
                    worker_id="recovered-worker",
                    session_token="recovered-session",
                    session_epoch=2,
                )
            )
            self.confirm_activation()
            token_accepted.set()
            await self._stop.wait()

    monkeypatch.setattr(transport, "WorkerClient", ResetAfterKeySuccessClient)
    ResetAfterKeySuccessClient.attempts = []
    instance = agent.WorkerAgent()
    try:
        await instance.start()
        await asyncio.wait_for(key_connected.wait(), timeout=HANG_GUARD_S)
        assert agent._load_enrollment_manifest(locations["enrollment_manifest"])[
            "token_hash"
        ] == ""
        assert agent._load_consumed_token_hash(
            locations["enrollment_token_hash"]
        ) == ""

        reset_control_plane.set()
        await asyncio.wait_for(token_accepted.wait(), timeout=HANG_GUARD_S)
    finally:
        await instance.stop()

    assert ResetAfterKeySuccessClient.attempts == ["", "", token]
    assert verified == [token]
    assert agent._load_enrollment_manifest(locations["enrollment_manifest"]) == {
        "endpoint": endpoint,
        "worker_id": "recovered-worker",
        "token_hash": agent._token_hash(token),
        "certificate": credentials.certificate_pem,
    }
    assert agent._load_consumed_token_hash(
        locations["enrollment_token_hash"]
    ) == agent._token_hash(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_kind", ["endpoint", "certificate"])
async def test_legacy_replacement_token_moves_a_non_revoked_worker(
    monkeypatch, enrolled, tmp_path, replacement_kind
):
    """An unmarked legacy volume can still adopt a distinct control plane."""
    from worker import capabilities
    from worker.transport import client as transport

    old_endpoint = "old-studio.internal:7443"
    old_creds = tls.generate_self_signed(hostnames=["old-studio.internal"])
    new_endpoint = (
        "new-studio.internal:7443"
        if replacement_kind == "endpoint"
        else old_endpoint
    )
    new_fingerprint = (
        old_creds.fingerprint if replacement_kind == "endpoint" else "ab" * 32
    )
    token = identity.mint_enrollment_token(
        endpoint=new_endpoint, cert_fingerprint=new_fingerprint
    ).encode()
    old_certificate = old_creds.certificate_pem
    (tmp_path / "control-plane.pinned.crt").write_bytes(old_certificate)
    (tmp_path / "worker-id").write_text("headless-worker", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
    monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT", old_endpoint)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: old_endpoint)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    verified = []
    remembered = []

    def _verify(token_text):
        verified.append(token_text)
        return new_endpoint, b"new pinned certificate"

    monkeypatch.setattr(agent, "_verify_enrollment_token", _verify)
    monkeypatch.setattr(agent, "_remember_endpoint", remembered.append)
    monkeypatch.setattr(transport, "WorkerClient", _RegisteringClient)
    _RegisteringClient.last = None

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        assert (tmp_path / "control-plane.pinned.crt").read_bytes() == old_certificate
        await instance.wait_until_registered(timeout=1)
    finally:
        await instance.stop()

    assert verified == [token]
    assert _RegisteringClient.last.config.enrollment_token == token
    assert _RegisteringClient.last.config.worker_id == ""
    assert (tmp_path / "control-plane.pinned.crt").read_bytes() == b"new pinned certificate"
    assert remembered == (
        [new_endpoint] if new_endpoint != old_endpoint else []
    )
    assert agent._load_consumed_token_hash(
        str(tmp_path / "enrollment-token.sha256")
    ) == agent._token_hash(token)


@pytest.mark.asyncio
async def test_a_fresh_environment_token_moves_a_non_revoked_worker(
    monkeypatch, enrolled, tmp_path
):
    """Changing the Compose token can move an identity the server still accepts."""
    from worker import capabilities

    (tmp_path / "worker-id").write_text("headless-worker", encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(
        agent._token_hash("ovw_old"), encoding="ascii"
    )
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_fresh")
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    redeemed = []

    def _redeem(token_text):
        redeemed.append(token_text)
        return "new-studio.internal:7443", b"new pinned certificate"

    monkeypatch.setattr(agent, "_verify_enrollment_token", _redeem)

    instance = agent.WorkerAgent()
    try:
        await instance.start()
    finally:
        await instance.stop()

    assert redeemed == ["ovw_fresh"]
    assert enrolled.last.config.enrollment_token == "ovw_fresh"
    assert enrolled.last.config.worker_id == ""


@pytest.mark.asyncio
async def test_rejected_replacement_preserves_the_working_enrollment(
    monkeypatch, tmp_path, control_plane_tls
):
    """Verification may stage new trust, but rejection must commit none of it."""
    from worker import capabilities
    from worker.transport import client as transport

    replacement_creds, replacement_endpoint = control_plane_tls
    old_creds = tls.generate_self_signed(hostnames=["old-studio.internal"])
    old_endpoint = "old-studio.internal:7443"
    old_worker_id = "working-worker"
    old_token_hash = agent._token_hash("ovw_old")
    token = identity.mint_enrollment_token(
        endpoint=replacement_endpoint,
        cert_fingerprint=replacement_creds.fingerprint,
    ).encode()
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
    }
    (tmp_path / "control-plane.pinned.crt").write_bytes(old_creds.certificate_pem)
    (tmp_path / "worker-id").write_text(old_worker_id, encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(
        old_token_hash, encoding="ascii"
    )
    remembered = []
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", token)
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: old_endpoint)
    monkeypatch.setattr(agent, "_remember_endpoint", remembered.append)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(transport, "WorkerClient", _RejectingClient)
    _RejectingClient.last = None

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        with pytest.raises(RuntimeError, match="registration rejected"):
            await instance.wait_until_registered(timeout=1)
    finally:
        await instance.stop()

    assert _RejectingClient.last.config.certificate_pem == replacement_creds.certificate_pem
    assert _RejectingClient.last.config.worker_id == ""
    assert (tmp_path / "control-plane.pinned.crt").read_bytes() == old_creds.certificate_pem
    assert (tmp_path / "worker-id").read_text(encoding="utf-8") == old_worker_id
    assert (tmp_path / "enrollment-token.sha256").read_text(
        encoding="ascii"
    ) == old_token_hash
    assert remembered == []
    assert instance.status()["endpoint"] == old_endpoint


def test_manifest_replace_fsyncs_its_parent_directory(monkeypatch, tmp_path):
    events = []
    directory_descriptor = 900_001
    directory_flag = getattr(agent.os, "O_DIRECTORY", 0x10000)
    real_open = agent.os.open
    real_fsync = agent.os.fsync
    real_close = agent.os.close
    real_replace = agent.os.replace

    monkeypatch.setattr(agent.os, "O_DIRECTORY", directory_flag, raising=False)

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path) == str(tmp_path) and flags == agent.os.O_RDONLY | directory_flag:
            events.append("open-parent")
            return directory_descriptor
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def tracked_fsync(descriptor):
        if descriptor == directory_descriptor:
            events.append("fsync-parent")
            return None
        return real_fsync(descriptor)

    def tracked_close(descriptor):
        if descriptor == directory_descriptor:
            events.append("close-parent")
            return None
        return real_close(descriptor)

    def tracked_replace(source, destination):
        events.append("replace")
        return real_replace(source, destination)

    monkeypatch.setattr(agent.os, "open", tracked_open)
    monkeypatch.setattr(agent.os, "fsync", tracked_fsync)
    monkeypatch.setattr(agent.os, "close", tracked_close)
    monkeypatch.setattr(agent.os, "replace", tracked_replace)

    agent._save_enrollment_manifest(
        str(tmp_path / "enrollment.json"),
        endpoint="studio.internal:7443",
        certificate=b"pinned certificate",
        worker_id="worker-1",
        token_hash=agent._token_hash("ovw_once"),
    )

    assert events == ["replace", "open-parent", "fsync-parent", "close-parent"]


@pytest.mark.asyncio
async def test_first_start_fsyncs_each_fresh_workers_directory_parent(
    monkeypatch, tmp_path
):
    from worker import capabilities
    from worker.transport import client as transport

    root = tmp_path / "fresh-volume" / "workers"
    locations = {
        "root": str(root),
        "worker_key": str(root / "worker.key"),
        "pinned_cert": str(root / "control-plane.pinned.crt"),
        "worker_id": str(root / "worker-id"),
        "enrollment_token_hash": str(root / "enrollment-token.sha256"),
        "enrollment_manifest": str(root / "enrollment.json"),
    }
    fsynced = []
    monkeypatch.delenv("OMNIVOICE_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda _token: ("studio.internal:7443", b"pinned certificate"),
    )
    monkeypatch.setattr(agent, "_fsync_parent_directory", fsynced.append)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(transport, "WorkerClient", _FakeClient)
    instance = agent.WorkerAgent()

    try:
        await instance.start(token_text="ovw_fresh")
    finally:
        await instance.stop()

    assert root.is_dir()
    assert fsynced == [str(tmp_path), str(tmp_path / "fresh-volume")]


def test_manifest_accepts_a_filesystem_without_directory_fsync(monkeypatch, tmp_path):
    directory_descriptor = 900_002
    directory_flag = getattr(agent.os, "O_DIRECTORY", 0x10000)
    monkeypatch.setattr(agent.os, "O_DIRECTORY", directory_flag, raising=False)
    monkeypatch.setattr(agent.os, "open", lambda _path, _flags: directory_descriptor)
    monkeypatch.setattr(
        agent.os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(
            OSError(errno.EINVAL, "directory fsync is unsupported")
        ),
    )
    closed = []
    monkeypatch.setattr(agent.os, "close", closed.append)

    agent._fsync_parent_directory(str(tmp_path))

    assert closed == [directory_descriptor]


def test_manifest_reports_a_real_directory_writeback_failure(monkeypatch, tmp_path):
    directory_descriptor = 900_003
    directory_flag = getattr(agent.os, "O_DIRECTORY", 0x10000)
    monkeypatch.setattr(agent.os, "O_DIRECTORY", directory_flag, raising=False)
    monkeypatch.setattr(agent.os, "open", lambda _path, _flags: directory_descriptor)
    monkeypatch.setattr(
        agent.os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(
            OSError(errno.EIO, "directory writeback failed")
        ),
    )
    monkeypatch.setattr(agent.os, "close", lambda _descriptor: None)

    with pytest.raises(OSError) as exc_info:
        agent._fsync_parent_directory(str(tmp_path))

    assert exc_info.value.errno == errno.EIO


@pytest.mark.asyncio
async def test_manifest_commit_failure_is_terminal_and_preserves_old_generation(
    monkeypatch, tmp_path
):
    """A spent token must never leave a half-new reconnect identity."""
    from worker import capabilities
    from worker.protocol.gen import worker_v1_pb2 as pb
    from worker.transport import client as transport

    manifest_path = tmp_path / "enrollment.json"
    old_certificate = b"old pinned certificate"
    old_hash = agent._token_hash("ovw_old")
    agent._save_enrollment_manifest(
        str(manifest_path),
        endpoint="old-studio.internal:7443",
        certificate=old_certificate,
        worker_id="old-worker",
        token_hash=old_hash,
    )
    old_generation = manifest_path.read_bytes()
    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(manifest_path),
    }

    class AcceptedOnceClient(transport.WorkerClient):
        async def _connect_once(self):
            await self.accept_registration(
                pb.RegisterResponse(
                    worker_id="new-worker",
                    session_token="new-session",
                    session_epoch=2,
                )
            )

    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda _token: ("new-studio.internal:7443", b"new pinned certificate"),
    )
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(transport, "WorkerClient", AcceptedOnceClient)
    instance = agent.WorkerAgent()

    await instance.start(token_text="ovw_new")
    real_replace = agent.os.replace

    def fail_manifest_replace(source, destination):
        if destination == str(manifest_path):
            raise OSError("disk full")
        return real_replace(source, destination)

    monkeypatch.setattr(agent.os, "replace", fail_manifest_replace)
    try:
        with pytest.raises(RuntimeError, match="LOCAL_STATE"):
            await instance.wait_until_registered(timeout=1)
        await asyncio.sleep(0)
        readiness = instance.readiness()
    finally:
        await instance.stop()

    assert manifest_path.read_bytes() == old_generation
    assert agent._load_enrollment_manifest(str(manifest_path)) == {
        "endpoint": "old-studio.internal:7443",
        "worker_id": "old-worker",
        "token_hash": old_hash,
        "certificate": old_certificate,
    }
    assert list(tmp_path.glob(".enrollment.*.tmp")) == []
    assert readiness["status"] == "failed"
    assert "LOCAL_STATE" in instance.last_error


@pytest.mark.asyncio
async def test_manifest_rollback_failure_stays_stopped_and_is_surfaced(
    monkeypatch, tmp_path
):
    manifest_path = tmp_path / "enrollment.json"
    old_generation = b"old authoritative generation"
    replacement_generation = b"replacement generation that failed activation"
    manifest_path.write_bytes(replacement_generation)
    locations = {
        "root": str(tmp_path),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(manifest_path),
    }
    instance = agent.WorkerAgent()
    instance.endpoint = "replacement.internal:7443"
    monkeypatch.setattr(agent, "agent", instance)
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    previous = {
        "manifest_path": str(manifest_path),
        "manifest_present": True,
        "manifest_bytes": old_generation,
        "manifest_snapshot_captured": True,
        "worker_mode": "true",
        "worker_mode_setting_present": True,
    }
    started = []
    stopped = []

    async def start(*, token_text: str = "", endpoint: str = ""):
        started.append((token_text, endpoint))

    async def stop():
        stopped.append(True)

    monkeypatch.setattr(instance, "start", start)
    monkeypatch.setattr(instance, "stop", stop)
    monkeypatch.setattr(
        agent,
        "_restore_file_generation",
        lambda _snapshot: (_ for _ in ()).throw(OSError("disk is read-only")),
    )

    with pytest.raises(agent.EnrollmentRollbackError, match="remains stopped"):
        await agent.restore_enrollment(previous)

    assert stopped == [True]
    assert started == []
    assert instance.endpoint == ""
    assert "could not be restored safely" in instance.last_error
    assert manifest_path.read_bytes() == replacement_generation


@pytest.mark.asyncio
async def test_legacy_mirror_rollback_failure_stays_stopped(monkeypatch, tmp_path):
    manifest_path = tmp_path / "enrollment.json"
    worker_id_path = tmp_path / "worker-id"
    instance = agent.WorkerAgent()
    instance.endpoint = "replacement.internal:7443"
    monkeypatch.setattr(agent, "agent", instance)
    monkeypatch.setattr(
        agent,
        "_paths",
        lambda: {
            "root": str(tmp_path),
            "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
            "worker_id": str(worker_id_path),
            "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
            "enrollment_manifest": str(manifest_path),
        },
    )
    previous = {
        "manifest_path": str(manifest_path),
        "manifest_present": False,
        "manifest_bytes": None,
        "manifest_snapshot_captured": True,
        "file_snapshots": {
            "worker_id": {
                "path": str(worker_id_path),
                "present": True,
                "bytes": b"old-worker\n",
            }
        },
        "worker_mode": "true",
        "worker_mode_setting_present": True,
    }
    real_restore = agent._restore_file_generation
    started = []
    stopped = []

    def fail_identity(snapshot):
        if snapshot["path"] == str(worker_id_path):
            raise OSError("identity disk is read-only")
        real_restore(snapshot)

    async def start(*, token_text: str = "", endpoint: str = ""):
        started.append((token_text, endpoint))

    async def stop():
        stopped.append(True)

    monkeypatch.setattr(agent, "_restore_file_generation", fail_identity)
    monkeypatch.setattr(instance, "start", start)
    monkeypatch.setattr(instance, "stop", stop)

    with pytest.raises(agent.EnrollmentRollbackError, match="remains stopped"):
        await agent.restore_enrollment(previous)

    assert stopped == [True]
    assert started == []
    assert instance.endpoint == ""


@pytest.mark.asyncio
async def test_idle_unload_cancellation_drains_the_blocking_release(monkeypatch):
    from threading import Event

    from services import model_manager, tts_backend

    started = Event()
    release = Event()
    finished = Event()

    def blocking_release():
        started.set()
        if not release.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release idle unload")
        finished.set()
        return 0

    monkeypatch.setattr(agent, "IDLE_SWEEP_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(model_manager, "gpu_pool_stats", lambda: {})
    monkeypatch.setattr(tts_backend, "release_idle_engines", blocking_release)
    sweep = asyncio.create_task(agent.idle_unload_loop())
    while not started.is_set():
        await asyncio.sleep(0)

    sweep.cancel()
    await asyncio.sleep(0)
    assert not sweep.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await sweep
    assert finished.is_set()


@pytest.mark.asyncio
async def test_manifest_remains_authoritative_when_a_legacy_mirror_fails(
    monkeypatch, tmp_path
):
    """Compatibility-file failure cannot replay a consumed Compose token."""
    from worker import capabilities
    from worker.transport import client as transport

    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(tmp_path / "enrollment.json"),
    }
    old_hash = agent._token_hash("ovw_old")
    (tmp_path / "enrollment-token.sha256").write_text(old_hash, encoding="ascii")
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_remember_endpoint", lambda _endpoint: None)

    def fail_legacy_hash(_path, _token):
        raise OSError("legacy mirror is read-only")

    monkeypatch.setattr(agent, "_save_consumed_token_hash", fail_legacy_hash)
    accepted = agent.WorkerAgent()
    accepted._on_registered(
        locations["worker_id"],
        manifest_path=locations["enrollment_manifest"],
        token_hash_path=locations["enrollment_token_hash"],
        enrollment_token="ovw_new",
        cert_path=locations["pinned_cert"],
        certificate=b"new pinned certificate",
        endpoint="new-studio.internal:7443",
    )("new-worker")

    assert not accepted._registered.is_set()
    accepted._on_activated("new-worker")
    assert accepted._registered.is_set()
    assert (tmp_path / "enrollment-token.sha256").read_text(
        encoding="ascii"
    ) == old_hash
    assert agent._load_enrollment_manifest(locations["enrollment_manifest"])[
        "token_hash"
    ] == agent._token_hash("ovw_new")

    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_new")
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda _token: (_ for _ in ()).throw(
            AssertionError("the consumed token was redeemed twice")
        ),
    )
    monkeypatch.setattr(transport, "WorkerClient", _FakeClient)
    _FakeClient.last = None
    restarted = agent.WorkerAgent()
    try:
        await restarted.start()
    finally:
        await restarted.stop()

    assert _FakeClient.last.config.endpoint == "new-studio.internal:7443"
    assert _FakeClient.last.config.worker_id == "new-worker"
    assert _FakeClient.last.config.certificate_pem == b"new pinned certificate"
    assert _FakeClient.last.config.enrollment_token == ""


@pytest.mark.asyncio
async def test_restart_never_mixes_generations_after_legacy_mirror_crash(
    monkeypatch, tmp_path
):
    """A crash after the certificate mirror cannot pair it with an old id."""
    from worker import capabilities
    from worker.transport import client as transport

    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(tmp_path / "enrollment.json"),
    }
    old_hash = agent._token_hash("ovw_old")
    (tmp_path / "control-plane.pinned.crt").write_bytes(b"old certificate")
    (tmp_path / "worker-id").write_text("old-worker", encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(old_hash, encoding="ascii")
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: "")
    monkeypatch.setattr(agent, "_remember_endpoint", lambda _endpoint: None)

    class SimulatedCrash(BaseException):
        pass

    monkeypatch.setattr(
        agent,
        "save_worker_id",
        lambda _path, _worker_id: (_ for _ in ()).throw(SimulatedCrash()),
    )
    accepted = agent.WorkerAgent()
    with pytest.raises(SimulatedCrash):
        accepted._on_registered(
            locations["worker_id"],
            manifest_path=locations["enrollment_manifest"],
            token_hash_path=locations["enrollment_token_hash"],
            enrollment_token="ovw_new",
            cert_path=locations["pinned_cert"],
            certificate=b"new certificate",
            endpoint="new-studio.internal:7443",
        )("new-worker")

    # The compatibility files are deliberately half old and half new.
    assert (tmp_path / "control-plane.pinned.crt").read_bytes() == b"new certificate"
    assert (tmp_path / "worker-id").read_text(encoding="utf-8") == "old-worker"
    assert (tmp_path / "enrollment-token.sha256").read_text(
        encoding="ascii"
    ) == old_hash

    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_new")
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda _token: (_ for _ in ()).throw(
            AssertionError("the atomic generation was not authoritative")
        ),
    )
    monkeypatch.setattr(transport, "WorkerClient", _FakeClient)
    _FakeClient.last = None
    restarted = agent.WorkerAgent()
    try:
        await restarted.start()
    finally:
        await restarted.stop()

    assert _FakeClient.last.config.endpoint == "new-studio.internal:7443"
    assert _FakeClient.last.config.certificate_pem == b"new certificate"
    assert _FakeClient.last.config.worker_id == "new-worker"
    assert _FakeClient.last.config.enrollment_token == ""


def test_repeated_registration_skips_unchanged_durable_mirrors(
    monkeypatch, tmp_path
):
    paths = {
        "manifest": str(tmp_path / "enrollment.json"),
        "certificate": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "token_hash": str(tmp_path / "enrollment-token.sha256"),
    }
    calls = {
        "manifest": 0,
        "certificate": 0,
        "endpoint": 0,
        "worker_id": 0,
        "token_hash": 0,
    }
    stored_endpoint = ""
    save_manifest = agent._save_enrollment_manifest
    save_certificate = agent._save_pinned_certificate
    save_worker_id = agent.save_worker_id
    save_token_hash = agent._save_consumed_token_hash

    def count_manifest(*args, **kwargs):
        calls["manifest"] += 1
        return save_manifest(*args, **kwargs)

    def count_certificate(*args, **kwargs):
        calls["certificate"] += 1
        return save_certificate(*args, **kwargs)

    def remember_endpoint(value):
        nonlocal stored_endpoint
        calls["endpoint"] += 1
        stored_endpoint = value.strip()

    def count_worker_id(*args, **kwargs):
        calls["worker_id"] += 1
        return save_worker_id(*args, **kwargs)

    def count_token_hash(*args, **kwargs):
        calls["token_hash"] += 1
        return save_token_hash(*args, **kwargs)

    monkeypatch.setattr(agent, "_save_enrollment_manifest", count_manifest)
    monkeypatch.setattr(agent, "_save_pinned_certificate", count_certificate)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: stored_endpoint)
    monkeypatch.setattr(agent, "_remember_endpoint", remember_endpoint)
    monkeypatch.setattr(agent, "save_worker_id", count_worker_id)
    monkeypatch.setattr(agent, "_save_consumed_token_hash", count_token_hash)

    registered = agent.WorkerAgent()._on_registered(
        paths["worker_id"],
        manifest_path=paths["manifest"],
        token_hash_path=paths["token_hash"],
        enrollment_token="ovw_once",
        cert_path=paths["certificate"],
        certificate=b"pinned certificate",
        endpoint="studio.internal:7443",
    )
    registered("worker-1")
    registered("worker-1")

    assert calls == {
        "manifest": 1,
        "certificate": 1,
        "endpoint": 1,
        "worker_id": 1,
        "token_hash": 1,
    }


@pytest.mark.asyncio
async def test_corrupt_manifest_fails_closed_but_explicit_join_repairs_it(
    monkeypatch, tmp_path
):
    from worker import capabilities
    from worker.transport import client as transport

    locations = {
        "root": str(tmp_path),
        "worker_key": str(tmp_path / "worker.key"),
        "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
        "worker_id": str(tmp_path / "worker-id"),
        "enrollment_token_hash": str(tmp_path / "enrollment-token.sha256"),
        "enrollment_manifest": str(tmp_path / "enrollment.json"),
    }
    (tmp_path / "enrollment.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "control-plane.pinned.crt").write_bytes(b"stale certificate")
    (tmp_path / "worker-id").write_text("stale-worker", encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(
        agent._token_hash("ovw_spent"), encoding="ascii"
    )
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_spent")
    monkeypatch.setattr(agent, "_paths", lambda: locations)
    monkeypatch.setattr(agent, "_stored_endpoint", lambda: "stale.internal:7443")
    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda _token: (_ for _ in ()).throw(
            AssertionError("corrupt state replayed a token")
        ),
    )
    instance = agent.WorkerAgent()

    with pytest.raises(agent.EnrollmentStateError, match="unreadable"):
        await instance.start()
    assert instance.status()["endpoint"] == ""
    assert instance.status()["enrolled"] is True

    monkeypatch.setattr(
        agent,
        "_verify_enrollment_token",
        lambda token: (
            "fresh.internal:7443",
            b"fresh certificate",
        )
        if token == "ovw_fresh"
        else (_ for _ in ()).throw(AssertionError("wrong token")),
    )
    monkeypatch.setattr(agent, "_remember_endpoint", lambda _endpoint: None)
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])
    monkeypatch.setattr(transport, "WorkerClient", _RegisteringClient)
    _RegisteringClient.last = None
    try:
        await instance.start(token_text="ovw_fresh")
        await instance.wait_until_registered(timeout=1)
    finally:
        await instance.stop()

    assert agent._load_enrollment_manifest(locations["enrollment_manifest"]) == {
        "endpoint": "fresh.internal:7443",
        "worker_id": "headless-worker",
        "token_hash": agent._token_hash("ovw_fresh"),
        "certificate": b"fresh certificate",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damaged_hash", ["abc123", "A" * 64, "g" * 64, ("0" * 64) + "\n"]
)
async def test_a_partial_or_corrupt_token_hash_is_treated_as_legacy_state(
    monkeypatch, enrolled, tmp_path, damaged_hash
):
    """A killed marker write must never make a spent token run again."""
    from worker import capabilities

    (tmp_path / "worker-id").write_text("headless-worker", encoding="utf-8")
    (tmp_path / "enrollment-token.sha256").write_text(
        damaged_hash, encoding="ascii"
    )
    monkeypatch.setenv("OMNIVOICE_WORKER_TOKEN", "ovw_already_spent")
    monkeypatch.setattr(capabilities, "discover", lambda **_: [])

    def _redeem_again(_token):
        raise AssertionError("corrupt legacy state retried a spent token")

    monkeypatch.setattr(agent, "_verify_enrollment_token", _redeem_again)

    instance = agent.WorkerAgent()
    try:
        await instance.start()
    finally:
        await instance.stop()

    assert agent._load_consumed_token_hash(
        str(tmp_path / "enrollment-token.sha256")
    ) == ""
    assert enrolled.last.config.enrollment_token == ""


def test_consumed_token_hash_is_replaced_atomically(monkeypatch, tmp_path):
    target = tmp_path / "enrollment-token.sha256"
    replaced = []
    real_replace = agent.os.replace

    def _replace(source, destination):
        replaced.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(agent.os, "replace", _replace)

    agent._save_consumed_token_hash(str(target), "ovw_once")

    assert len(replaced) == 1
    assert replaced[0][1] == str(target)
    assert Path(replaced[0][0]).parent == tmp_path
    assert agent._load_consumed_token_hash(str(target)) == agent._token_hash("ovw_once")
    assert list(tmp_path.iterdir()) == [target]


@pytest.fixture
def enrolled(monkeypatch, tmp_path):
    """An already-enrolled worker whose client is a stand-in."""
    from worker.transport import client as transport

    (tmp_path / "control-plane.pinned.crt").write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT", "127.0.0.1:1")
    monkeypatch.delenv("OMNIVOICE_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(
        agent,
        "_paths",
        lambda: {
            "root": str(tmp_path),
            "worker_key": str(tmp_path / "worker.key"),
            "pinned_cert": str(tmp_path / "control-plane.pinned.crt"),
            "worker_id": str(tmp_path / "worker-id"),
        },
    )
    monkeypatch.setattr(transport, "WorkerClient", _FakeClient)
    _FakeClient.last = None
    return _FakeClient


@pytest.mark.asyncio
async def test_engines_present_but_not_downloaded_are_still_advertised(
    monkeypatch, enrolled
):
    """Otherwise "this worker has no such engine" and "it has it but the weights
    are missing" are the same silence, and the control plane can never offer
    the download that would unblock the job."""
    from worker import capabilities

    seen = []
    monkeypatch.setattr(
        capabilities,
        "discover",
        lambda **kwargs: seen.append(kwargs) or [{"engine": "indextts"}],
    )

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        # The reconnect probe must ask the same question as the first one, or a
        # reconnect quietly drops every not-yet-downloaded engine.
        enrolled.last.hooks["capability_probe"]()
    finally:
        await instance.stop()

    assert seen == [{"include_unavailable": True}, {"include_unavailable": True}]


@pytest.mark.asyncio
async def test_the_worker_releases_engines_it_has_stopped_using(monkeypatch, enrolled):
    """Requirement 6. A machine lending its GPU is usually not the one its owner
    is sitting at — holding several GB against a task that may never come is
    pure cost."""
    from services import tts_backend
    from worker import capabilities

    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(agent, "IDLE_SWEEP_INTERVAL_SECONDS", 0.01)
    swept = asyncio.Event()
    monkeypatch.setattr(
        tts_backend, "release_idle_engines", lambda *a, **k: swept.set() or []
    )

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        await asyncio.wait_for(swept.wait(), timeout=HANG_GUARD_S)
        sweep = instance._idle_sweep
    finally:
        await instance.stop()

    assert sweep.cancelled() or sweep.done(), "the sweep outlives the agent"


@pytest.mark.asyncio
async def test_remote_sweep_does_not_unload_during_local_gpu_work(monkeypatch, enrolled):
    """The same process may serve a local user and remote assignments."""
    from services import model_manager, tts_backend
    from worker import capabilities

    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(agent, "IDLE_SWEEP_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(
        model_manager, "gpu_pool_stats", lambda: {"running": 1, "queued": 0}
    )
    unloaded = []
    monkeypatch.setattr(tts_backend, "release_idle_engines", lambda: unloaded.append(1))

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        await asyncio.sleep(0.05)
    finally:
        await instance.stop()

    assert unloaded == []


@pytest.mark.asyncio
async def test_a_failing_sweep_never_takes_the_worker_down(monkeypatch, enrolled):
    """The agent's standing promise: a machine that cannot do the extra thing is
    still a perfectly good worker."""
    from services import tts_backend
    from worker import capabilities

    monkeypatch.setattr(capabilities, "discover", lambda **_: [])
    monkeypatch.setattr(agent, "IDLE_SWEEP_INTERVAL_SECONDS", 0.01)
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("driver wedged")

    monkeypatch.setattr(tts_backend, "release_idle_engines", _boom)

    instance = agent.WorkerAgent()
    try:
        await instance.start()
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        assert not instance._idle_sweep.done()
    finally:
        await instance.stop()
