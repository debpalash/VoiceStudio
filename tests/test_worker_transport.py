"""Transport: TLS, codec, and a real end-to-end gRPC round trip.

The end-to-end test starts an actual TLS server on a loopback port and drives a
real worker client through it. Mocking the transport would prove only that the
mocks agree with each other; the wiring between protobuf, the servicer, and the
scheduler is exactly what this layer exists to get right.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import threading
from types import SimpleNamespace

import grpc
import pytest
import pytest_asyncio
from worker import identity, registry, tls
from worker.errors import ErrorClass, WorkerError
from worker.identity import WorkerKeypair
from worker.lifecycle import TaskState
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.scheduler import Scheduler
from worker.transport import codec
from worker.transport.client import (
    TerminalRegistrationError,
    WorkerClient,
    WorkerConfig,
    backoff_delay,
    config_from_token,
    describe_host,
)
from worker.transport.server import (
    PROTOCOL_VERSION,
    REQUIRED_FEATURES,
    ControlPlaneBindError,
    WorkerServicer,
    serve,
)
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

ENGINE, MODEL, OP = "indextts", "IndexTTS-2", "tts"


def test_describe_host_reports_system_memory(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=64 * 1024**3))

    assert describe_host()["system_memory_bytes"] == 64 * 1024**3


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Throwaway DB, patched where the stores actually read it."""
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _capabilities(resident: bool = False) -> list[dict]:
    return [
        {
            "engine": ENGINE,
            "model_id": MODEL,
            "operations": [OP],
            "supported": True,
            "installed": True,
            "downloaded": True,
            "resident": resident,
            "backend": "cuda",
            "free_memory_bytes": 24 * 1024**3,
        }
    ]


# ── TLS ────────────────────────────────────────────────────────────────────


def test_self_signed_certificate_is_generated_and_pinnable():
    creds = tls.generate_self_signed(hostnames=["localhost", "127.0.0.1"])
    assert creds.certificate_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert tls.pin_matches(creds.certificate_der, creds.fingerprint) is True


def test_a_different_certificate_fails_the_pin():
    """The café-network substitution this whole design exists to stop."""
    mine = tls.generate_self_signed(hostnames=["localhost"])
    attacker = tls.generate_self_signed(hostnames=["localhost"])
    assert tls.pin_matches(attacker.certificate_der, mine.fingerprint) is False


def test_certificate_is_persisted_and_reused(tmp_path):
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    first = tls.load_or_create(cert, key, hostnames=["localhost"])
    second = tls.load_or_create(cert, key, hostnames=["localhost"])
    assert first.fingerprint == second.fingerprint


def test_new_tls_credential_directory_retries_its_parent_barrier(
    tmp_path, monkeypatch
):
    parent = tmp_path / "data"
    parent.mkdir()
    directory = parent / "workers"
    cert, key = str(directory / "c.pem"), str(directory / "k.pem")
    real_fsync_parent = tls._fsync_parent_directory
    failed = False

    def fail_once(path):
        nonlocal failed
        if os.path.abspath(path) == str(parent) and not failed:
            failed = True
            raise OSError("credential directory barrier failed")
        return real_fsync_parent(path)

    monkeypatch.setattr(tls, "_fsync_parent_directory", fail_once)
    with pytest.raises(OSError, match="credential directory barrier failed"):
        tls.load_or_create(cert, key, hostnames=["localhost"])

    assert directory.is_dir()
    assert not os.path.exists(cert)
    assert not os.path.exists(key)

    retried = []

    def record_retry(path):
        retried.append(os.path.abspath(path))
        return real_fsync_parent(path)

    monkeypatch.setattr(tls, "_fsync_parent_directory", record_retry)
    tls.load_or_create(cert, key, hostnames=["localhost"])

    assert str(parent) in retried
    assert os.path.isfile(cert)
    assert os.path.isfile(key)


def test_mismatched_certificate_and_private_key_are_replaced(tmp_path):
    """A torn two-file update must never be accepted as server credentials."""
    first = tls.generate_self_signed(hostnames=["localhost"])
    other = tls.generate_self_signed(hostnames=["localhost"])
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "k.pem")
    (tmp_path / "c.pem").write_bytes(first.certificate_pem)
    (tmp_path / "k.pem").write_bytes(other.private_key_pem)

    assert tls._load(cert, key) is None
    repaired = tls.load_or_create(cert, key, hostnames=["localhost"])

    assert repaired.fingerprint != first.fingerprint
    assert tls._load(cert, key).fingerprint == repaired.fingerprint


def test_certificate_with_a_corrupt_signature_is_not_reused(tmp_path):
    credentials = tls.generate_self_signed(hostnames=["localhost"])
    corrupted_der = bytearray(credentials.certificate_der)
    corrupted_der[-1] ^= 1
    corrupted_certificate = tls.x509.load_der_x509_certificate(bytes(corrupted_der))
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "k.pem")
    (tmp_path / "c.pem").write_bytes(
        corrupted_certificate.public_bytes(tls.serialization.Encoding.PEM)
    )
    (tmp_path / "k.pem").write_bytes(credentials.private_key_pem)

    assert tls._load(cert, key) is None


def test_interrupted_pair_commit_recovers_the_durable_generation(
    tmp_path, monkeypatch
):
    """The journal closes the crash window between the two atomic renames."""
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "k.pem")
    tls.load_or_create(cert, key, hostnames=["localhost"])
    replacement = tls.generate_self_signed(hostnames=["localhost"])
    real_replace = tls.os.replace
    failed = False

    def interrupt_before_certificate(source, destination):
        nonlocal failed
        if destination == cert and not failed:
            failed = True
            raise OSError("simulated power loss before certificate rename")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(tls.os, "replace", interrupt_before_certificate)
        with pytest.raises(OSError, match="simulated power loss"):
            tls._save(cert, key, replacement)

    assert tls._load(cert, key) is None
    assert (tmp_path / ".c.pem.k.pem.pending").is_file()
    recovered = tls.load_or_create(cert, key, hostnames=["localhost"])

    assert recovered.fingerprint == replacement.fingerprint
    assert tls._load(cert, key).fingerprint == replacement.fingerprint
    assert not (tmp_path / ".c.pem.k.pem.pending").exists()


def test_expiry_check_supports_cryptography_41_certificate_api(monkeypatch):
    import datetime as dt

    credentials = tls.generate_self_signed(
        hostnames=["localhost"], now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    )
    certificate = type(
        "LegacyCertificate",
        (),
        {"not_valid_after": dt.datetime(2026, 6, 1)},
    )()
    monkeypatch.setattr(tls.x509, "load_der_x509_certificate", lambda _der: certificate)

    assert tls._expiring_soon(
        credentials, now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    ) is False


def test_hostname_suffix_is_not_doubled(monkeypatch):
    """macOS already reports the hostname with .local, and 'host.local.local'
    is in nobody's certificate."""
    monkeypatch.setattr(socket, "gethostname", lambda: "mac.local")
    assert "mac.local.local" not in tls.default_hostnames()


def test_private_key_is_not_world_readable(tmp_path):
    import os
    import stat
    import sys

    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    tls.load_or_create(cert, key, hostnames=["localhost"])
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_token_with_a_mismatched_certificate_is_refused():
    """config_from_token must refuse rather than offer an override."""
    server = tls.generate_self_signed(hostnames=["localhost"])
    attacker = tls.generate_self_signed(hostnames=["localhost"])
    token = identity.mint_enrollment_token(
        endpoint="localhost:1", cert_fingerprint=server.fingerprint
    )
    with pytest.raises(ValueError, match="does not match"):
        config_from_token(
            token.encode(),
            keypair=WorkerKeypair.generate(),
            certificate_pem=attacker.certificate_pem,
        )


def test_expired_token_is_refused():
    server = tls.generate_self_signed(hostnames=["localhost"])
    token = identity.mint_enrollment_token(
        endpoint="localhost:1", cert_fingerprint=server.fingerprint, ttl_seconds=-1
    )
    with pytest.raises(ValueError, match="expired"):
        config_from_token(
            token.encode(),
            keypair=WorkerKeypair.generate(),
            certificate_pem=server.certificate_pem,
        )


# ── Codec ──────────────────────────────────────────────────────────────────


def test_error_round_trips_through_protobuf():
    original = WorkerError(
        error_class=ErrorClass.CAPABILITY,
        code="INSUFFICIENT_MEMORY",
        message="too big",
        hint="h",
    )
    restored = codec.error_from_pb(codec.error_to_pb(original))
    assert restored == original


def test_unknown_error_class_degrades_to_retryable():
    """A newer peer describing a failure we do not know must not permanently
    fail work that would have succeeded on retry."""
    restored = codec.error_from_pb(pb.Error(code="FROM_THE_FUTURE", message="?"))
    assert restored.error_class is ErrorClass.TRANSIENT
    assert restored.retryable is True


def test_capability_concurrency_is_derived_when_not_supplied():
    """Never defaulted to a constant: a wrong value corrupts output (#315)."""
    message = codec.capability_to_pb(
        {
            "engine": ENGINE,
            "model_id": MODEL,
            "backend": "cuda",
            "free_memory_bytes": 24 * 1024**3,
        }
    )
    assert message.derived_concurrency >= 1


def test_apple_capability_stays_serial():
    message = codec.capability_to_pb(
        {
            "engine": ENGINE,
            "model_id": MODEL,
            "backend": "mps",
            "free_memory_bytes": 64 * 1024**3,
        }
    )
    assert message.derived_concurrency == 1


def test_capability_round_trips():
    original = {
        **_capabilities(resident=True)[0],
        "display_name": "IndexTTS 2",
        "backend": "vulkan",
        "free_memory_bytes": 4 * 1024**3,
    }
    restored = codec.capability_from_pb(codec.capability_to_pb(original))
    assert restored["engine"] == original["engine"]
    assert restored["resident"] is True
    assert restored["installed"] is True
    assert restored["display_name"] == "IndexTTS 2"
    assert restored["backend"] == "vulkan"
    assert restored["free_memory_bytes"] == 4 * 1024**3


def test_legacy_capability_without_display_name_still_decodes():
    restored = codec.capability_from_pb(
        pb.ModelCapability(engine=ENGINE, model_id=MODEL)
    )
    assert restored["engine"] == ENGINE
    assert restored["display_name"] == ""


def test_protocol_v2_capability_without_backend_inherits_worker_backend():
    """Pre-backend protocol-v2 peers must retain their GPU routing."""
    restored = codec.capability_from_pb(
        pb.ModelCapability(
            engine=ENGINE,
            model_id=MODEL,
            operations=[OP],
            supported=True,
            installed=True,
        ),
        fallback_backend="vulkan",
    )
    pool = WorkerPool()
    record = registry.RemoteWorker(
        id="legacy-v2",
        name="legacy-v2",
        key_id="legacy-key",
        public_key=b"0" * 32,
        capabilities=[restored],
    )
    session = identity.Session(
        token="legacy-token",
        worker_id=record.id,
        key_id=record.key_id,
        epoch=1,
        issued_at=1.0,
        expires_at=10_000.0,
    )
    worker = pool.connect(
        record, session=session, epoch=1, backend="vulkan", now=1.0
    )

    assert restored["backend"] == "vulkan"
    assert worker.execution_device(ENGINE, MODEL, OP) == "vulkan"


def test_protocol_v2_cpu_fallback_does_not_inherit_worker_gpu():
    restored = codec.capability_from_pb(
        pb.ModelCapability(engine=ENGINE, model_id=MODEL, cpu_fallback=True),
        fallback_backend="cuda",
    )

    assert restored["backend"] == "cpu"


@pytest.mark.asyncio
async def test_cancel_is_sent_and_ack_releases_the_parked_slot(tmp_path):
    pool = WorkerPool()
    record = registry.RemoteWorker(
        id="w1",
        name="w1",
        key_id="key-w1",
        public_key=b"0" * 32,
        capabilities=_capabilities(),
        consent_granted_at=1.0,
        created_at=1.0,
    )
    token = identity.issue_session(worker_id="w1", key_id="key-w1", epoch=1, now=1000.0)
    pool.connect(
        record,
        session=token,
        epoch=1,
        max_concurrent_tasks=1,
        backend="cuda",
        now=1000.0,
    )
    scheduler = Scheduler(pool, persist=False)
    task = scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL, now=1000.0)
    assignment = scheduler.next_assignment(now=1000.0)
    servicer = WorkerServicer(scheduler, pool, artifact_dir=str(tmp_path))

    class Session:
        worker_id = "w1"
        revoked = False

        def __init__(self):
            self.sent = []
            self.pending_claim_cancels = set()

        async def send(self, message):
            self.sent.append(message)

    session = Session()
    servicer._sessions["w1"] = session
    scheduler.cancel(task.task_id, now=1001.0)
    assert await servicer.cancel("w1", task.task_id, assignment.attempt.attempt_id, 1)
    assert session.sent[-1].WhichOneof("payload") == "cancel"
    assert pool.get("w1").capacity.active_tasks == 1

    await servicer._handle(
        session,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=session.sent[-1].cancel.ref)),
    )
    assert pool.get("w1").capacity.active_tasks == 0


def test_host_round_trips():
    host = {
        "hostname": "box",
        "os": "linux",
        "arch": "x86_64",
        "worker_version": "0.3.1",
        "cpu_count": 16,
        "gpus": [
            {
                "vendor": "nvidia",
                "model": "RTX 4090",
                "backend": "cuda",
                "memory_bytes": 1,
            }
        ],
    }
    restored = codec.host_from_pb(codec.host_to_pb(host))
    assert restored["hostname"] == "box"
    assert restored["gpus"][0]["model"] == "RTX 4090"


# ── Backoff ────────────────────────────────────────────────────────────────


def test_backoff_grows_and_is_bounded():
    assert backoff_delay(1, jitter=lambda: 1.0) == 1.0
    assert backoff_delay(4, jitter=lambda: 1.0) == 8.0
    assert backoff_delay(50, jitter=lambda: 1.0) == 60.0


def test_backoff_is_jittered():
    """Deterministic delays reconnect every worker behind one router in the
    same instant — a spike exactly when the server can least absorb it."""
    assert backoff_delay(5, jitter=lambda: 0.0) == 0.0
    assert backoff_delay(5, jitter=lambda: 0.5) == 8.0


@pytest.mark.asyncio
async def test_registration_refusal_escapes_the_reconnect_loop(monkeypatch):
    """A spent or rejected token needs a new user action, not backoff forever."""

    async def execute(_assignment):
        return {}

    client = WorkerClient(
        WorkerConfig(
            endpoint="panel.invalid:7443",
            cert_fingerprint="",
            certificate_pem=b"certificate",
            keypair=WorkerKeypair.generate(),
            enrollment_token="spent-token",
        ),
        execute=execute,
    )
    attempts = 0

    async def refused():
        nonlocal attempts
        attempts += 1
        await client.accept_registration(
            pb.RegisterResponse(
                error=pb.Error(
                    error_class=pb.ERROR_CLASS_PROTOCOL,
                    code="AUTH_FAILED",
                    message="registration rejected",
                )
            )
        )

    monkeypatch.setattr(client, "_connect_once", refused)

    with pytest.raises(TerminalRegistrationError, match="AUTH_FAILED"):
        await asyncio.wait_for(client.run_forever(), timeout=HANG_GUARD_S)
    assert attempts == 1


@pytest.mark.asyncio
async def test_terminal_reconnect_refusal_cancels_work_retained_across_disconnect(
    monkeypatch,
):
    cancelled = asyncio.Event()
    cancel_callbacks = []

    async def execute(_assignment):
        return {}

    async def cancel(task_id):
        cancel_callbacks.append(task_id)

    client = WorkerClient(
        WorkerConfig(
            endpoint="panel.invalid:7443",
            cert_fingerprint="",
            certificate_pem=b"certificate",
            keypair=WorkerKeypair.generate(),
            worker_id="revoked-worker",
        ),
        execute=execute,
        cancel=cancel,
    )

    async def retained_work():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    work = asyncio.create_task(retained_work())
    await asyncio.sleep(0)
    client._running["task-1/attempt-1"] = work

    async def refused():
        raise TerminalRegistrationError("AUTH_FAILED: worker was revoked")

    monkeypatch.setattr(client, "_connect_once", refused)

    with pytest.raises(TerminalRegistrationError, match="AUTH_FAILED"):
        await client.run_forever()

    assert cancelled.is_set()
    assert work.done()
    assert client._running == {}
    assert cancel_callbacks == ["task-1"]


@pytest.mark.asyncio
async def test_registration_persistence_failure_adopts_no_live_session():
    """The server response is not usable until its reconnect identity is durable."""

    async def execute(_assignment):
        return {}

    config = WorkerConfig(
        endpoint="panel.invalid:7443",
        cert_fingerprint="",
        certificate_pem=b"certificate",
        keypair=WorkerKeypair.generate(),
        worker_id="old-worker",
        enrollment_token="one-use-token",
    )

    def fail_persistence(_worker_id):
        raise OSError("disk full")

    client = WorkerClient(config, execute=execute, on_registered=fail_persistence)

    with pytest.raises(TerminalRegistrationError, match="LOCAL_STATE"):
        await client.accept_registration(
            pb.RegisterResponse(
                worker_id="new-worker",
                session_token="new-session",
                session_epoch=4,
            )
        )

    assert config.worker_id == "old-worker"
    assert config.enrollment_token == "one-use-token"
    assert client._epoch == 0
    assert client._session_token == ""


@pytest.mark.asyncio
async def test_registration_persistence_is_off_loop_and_drained_before_adoption():
    """Cancellation cannot publish a session while its identity write is active."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    main_thread = threading.current_thread()

    async def execute(_assignment):
        return {}

    def persist(_worker_id):
        assert threading.current_thread() is not main_thread
        started.set()
        release.wait(BARRIER_WATCHDOG_S)
        finished.set()

    config = WorkerConfig(
        endpoint="panel.invalid:7443",
        cert_fingerprint="",
        certificate_pem=b"certificate",
        keypair=WorkerKeypair.generate(),
        worker_id="old-worker",
        enrollment_token="one-use-token",
    )
    client = WorkerClient(config, execute=execute, on_registered=persist)
    accepting = asyncio.create_task(client.accept_registration(
        pb.RegisterResponse(
            worker_id="new-worker",
            session_token="new-session",
            session_epoch=4,
        )
    ))
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    await client._on_server_message(pb.ServerMessage(ping=pb.Ping(nonce=7)))
    assert (await asyncio.wait_for(client._outbox.get(), timeout=HANG_GUARD_S)).pong.nonce == 7

    try:
        accepting.cancel()
        await asyncio.sleep(0)
        assert not accepting.done(), "identity write was abandoned on cancellation"
        assert config.worker_id == "old-worker"
        assert config.enrollment_token == "one-use-token"
        assert client._epoch == 0
        assert client._session_token == ""
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(accepting, timeout=HANG_GUARD_S)

    assert finished.is_set()
    assert config.worker_id == "old-worker"
    assert config.enrollment_token == "one-use-token"
    assert client._epoch == 0
    assert client._session_token == ""


@pytest.mark.asyncio
async def test_provisional_registration_is_not_ready_until_control_config_arrives():
    registered = []
    activated = []

    async def execute(_assignment):
        return {}

    client = WorkerClient(
        WorkerConfig(
            endpoint="panel.invalid:7443",
            cert_fingerprint="",
            certificate_pem=b"certificate",
            keypair=WorkerKeypair.generate(),
            enrollment_token="one-use-token",
        ),
        execute=execute,
        on_registered=registered.append,
        on_activated=activated.append,
    )
    response = pb.RegisterResponse(
        worker_id="worker-1", session_token="session-1", session_epoch=1
    )

    await client.accept_registration(response)

    assert registered == ["worker-1"]
    assert activated == []
    await client._on_server_message(
        pb.ServerMessage(config=pb.ConfigUpdate(max_concurrent_tasks=1))
    )
    await client._on_server_message(
        pb.ServerMessage(config=pb.ConfigUpdate(max_concurrent_tasks=1))
    )
    assert activated == ["worker-1"]


# ── End to end ─────────────────────────────────────────────────────────────


class _Harness:
    """A live TLS server plus a connected worker client."""

    def __init__(self, tmp_path):
        self.pool = WorkerPool()
        self.scheduler = Scheduler(self.pool, persist=True)
        self.creds = tls.generate_self_signed(hostnames=["localhost", "127.0.0.1"])
        self.servicer = WorkerServicer(
            self.scheduler,
            self.pool,
            artifact_dir=str(tmp_path / "artifacts"),
            cert_fingerprint=self.creds.fingerprint,
        )
        self.port = _free_port()
        self.server = None
        self.client = None
        self.client_task = None
        self.executed: list[str] = []

    async def start(self):
        self.server = await serve(
            self.servicer,
            host="127.0.0.1",
            port=self.port,
            certificate_pem=self.creds.certificate_pem,
            private_key_pem=self.creds.private_key_pem,
        )

    async def connect_worker(
        self,
        *,
        execute=None,
        capabilities=None,
        max_concurrent_tasks=2,
        on_registered=None,
        wait=True,
    ):
        token = registry.create_enrollment(
            endpoint=f"localhost:{self.port}", cert_fingerprint=self.creds.fingerprint
        )
        config = WorkerConfig(
            endpoint=f"localhost:{self.port}",
            cert_fingerprint=self.creds.fingerprint,
            certificate_pem=self.creds.certificate_pem,
            keypair=WorkerKeypair.generate(),
            enrollment_token=token.encode(),
            max_concurrent_tasks=max_concurrent_tasks,
            capabilities=capabilities or _capabilities(),
            host={"hostname": "test-worker", "os": "linux", "arch": "x86_64"},
        )

        async def _default_execute(assignment):
            self.executed.append(assignment.ref.task_id)
            return {"meta": {"ok": True}, "payload": b"audio-bytes"}

        self.client = WorkerClient(
            config,
            execute=execute or _default_execute,
            on_registered=on_registered,
        )
        self.client_task = asyncio.create_task(self.client.run_forever())
        if wait:
            await self._await_connection()
        return self.client

    async def _await_connection(self, timeout=10.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if len(self.pool) and self.servicer._sessions:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("worker never connected")

    async def await_state(self, task_id, state, timeout=15.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            task = self.scheduler.get(task_id)
            if task is not None and task.state is state:
                return task
            await asyncio.sleep(0.05)
        actual = self.scheduler.get(task_id)
        raise AssertionError(
            f"task never reached {state}; last state {actual.state if actual else None}"
        )

    async def stop(self):
        if self.client is not None:
            await self.client.stop()
        if self.client_task is not None:
            self.client_task.cancel()
            await asyncio.gather(self.client_task, return_exceptions=True)
        if self.server is not None:
            await self.server.stop(grace=0)


@pytest_asyncio.fixture
async def harness(tmp_path, db):
    h = _Harness(tmp_path)
    await h.start()
    try:
        yield h
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_worker_enrolls_over_tls(harness):
    """The full join: token → TLS → keypair identity → registered worker."""
    await harness.connect_worker()

    assert len(harness.pool) == 1
    stored = registry.list_workers()
    assert len(stored) == 1
    assert stored[0].name == "test-worker"
    assert stored[0].schedulable is True


@pytest.mark.asyncio
async def test_unpersisted_registration_never_becomes_a_connected_worker(
    harness, monkeypatch
):
    """Register is provisional until the worker durably accepts its identity."""
    from worker.transport import server as server_module

    monkeypatch.setattr(server_module, "_REGISTRATION_OPEN_TIMEOUT_SECONDS", 0.01)

    def fail_persistence(_worker_id):
        raise OSError("disk full")

    await harness.connect_worker(on_registered=fail_persistence, wait=False)

    with pytest.raises(TerminalRegistrationError, match="LOCAL_STATE"):
        await asyncio.wait_for(harness.client_task, timeout=HANG_GUARD_S)
    async def registration_is_retired():
        while len(harness.pool) or harness.servicer._sessions:
            await asyncio.sleep(0)

    await asyncio.wait_for(registration_is_retired(), timeout=HANG_GUARD_S)

    assert len(harness.pool) == 0
    assert harness.servicer._sessions == {}
    harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert harness.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_enrollment_token_is_single_use(harness, db):
    """A second worker cannot join on a token that has been spent."""
    await harness.connect_worker()
    token = identity.EnrollmentToken.decode(
        registry.create_enrollment(
            endpoint=f"localhost:{harness.port}",
            cert_fingerprint=harness.creds.fingerprint,
        ).encode()
    )
    assert registry.redeem_enrollment(token, worker_id="a") is True
    assert registry.redeem_enrollment(token, worker_id="b") is False


@pytest.mark.asyncio
async def test_task_flows_end_to_end(harness):
    """Submit → dispatch → execute on the worker → result committed."""
    await harness.connect_worker()

    task = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = harness.scheduler.next_assignment()
    assert assignment is not None
    assert await harness.servicer.dispatch(assignment) is True

    completed = await harness.await_state(task.task_id, TaskState.COMPLETED)

    assert harness.executed == [task.task_id]
    assert completed.result_ref is not None


@pytest.mark.asyncio
async def test_keepalive_frame_carries_slow_task_past_one_progress_lease(
    harness, monkeypatch
):
    """No real 120s sleep: advance the scheduler at its injected ``now`` seam."""
    from worker.transport import client as client_module

    release = asyncio.Event()

    async def _slow(_assignment):
        await release.wait()
        return {"meta": {}, "payload": b"audio"}

    monkeypatch.setattr(client_module, "keepalive_interval", lambda _lease: 0.01)
    await harness.connect_worker(execute=_slow)
    task = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = harness.scheduler.next_assignment(now=1_000.0)
    await harness.servicer.dispatch(assignment)
    running = await harness.await_state(task.task_id, TaskState.RUNNING)
    attempt = running.active_attempt
    initial_expiry = attempt.lease_expires_at

    deadline = asyncio.get_running_loop().time() + 2.0
    while attempt.lease_expires_at <= initial_expiry:
        assert asyncio.get_running_loop().time() < deadline, (
            "no keepalive reached the server"
        )
        await asyncio.sleep(0.01)

    harness.scheduler.sweep(now=initial_expiry + 1.0)
    assert task.state is TaskState.RUNNING
    assert attempt.progress == 0.0, "a keepalive must not impersonate real progress"

    release.set()
    await harness.await_state(task.task_id, TaskState.COMPLETED)


@pytest.mark.asyncio
async def test_result_is_persisted_before_it_is_acknowledged(harness):
    """The ordering that makes at-least-once safe: if the ack arrived first and
    the server then died, a finished render would be gone."""
    from worker import task_store

    await harness.connect_worker()
    task = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    await harness.servicer.dispatch(harness.scheduler.next_assignment())
    await harness.await_state(task.task_id, TaskState.COMPLETED)

    assert task_store.is_committed(task.task_id) is True


@pytest.mark.asyncio
async def test_worker_failure_propagates_with_its_taxonomy(harness):
    async def _boom(assignment):
        raise RuntimeError("the engine exploded")

    await harness.connect_worker(execute=_boom)
    task = harness.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=MODEL, max_attempts=1
    )
    await harness.servicer.dispatch(harness.scheduler.next_assignment())

    failed = await harness.await_state(task.task_id, TaskState.FAILED)
    assert failed.error is not None
    assert "exploded" in failed.attempts[-1].error.message


@pytest.mark.asyncio
async def test_worker_at_capacity_rejects_without_penalty(harness):
    """The worker's own accept/reject is authoritative; the scheduler's view of
    capacity is only ever advisory."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _block(assignment):
        started.set()
        await release.wait()
        return {"meta": {}, "payload": b""}

    # The limit goes through the handshake, not a post-connect mutation:
    # the server's stream-open ConfigUpdate carries the REGISTERED capacity
    # (from the hello), and a mutation racing that frame gets overwritten —
    # the client then honours 2, accepts the second task, and this test
    # reports over-concurrency that never existed (failed twice in CI on
    # 2026-08-21). With 1 registered end-to-end there is no window.
    await harness.connect_worker(execute=_block, max_concurrent_tasks=1)
    registered = next(iter(harness.pool))
    assert registered.capacity.max_concurrent_tasks == 1

    first = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    await harness.servicer.dispatch(harness.scheduler.next_assignment())
    await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_S)

    # Force a second assignment onto a worker the scheduler thinks has room.
    second = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = harness.scheduler.next_assignment()
    if assignment is not None:
        await harness.servicer.dispatch(assignment)
        # Deterministic wait, not sleep-as-sync: the worker's answer settles
        # the second task out of its dispatch states (QUEUED on a capacity
        # rejection, RUNNING on an over-accept) — poll until it does.
        deadline = asyncio.get_running_loop().time() + 10
        while (
            second.state in (TaskState.ASSIGNED, TaskState.ACCEPTED)
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.05)
        # Capacity rejections are penalty-free no matter how the first
        # attempt is faring — the worker is never excluded for being honest.
        assert second.excluded_workers == set()
        # The invariant under test is NO OVER-CONCURRENCY, not the
        # scheduler's bookkeeping timing. On a loaded runner the first
        # attempt can die environmentally (a stream hiccup fails _run, whose
        # finally frees the slot) and the worker then accepts the second
        # LEGITIMATELY — asserting on second's state alone flaked exactly
        # that way in CI (#1536). Only both running together is the bug.
        if first.state is TaskState.RUNNING:
            assert second.state in (
                TaskState.QUEUED,
                TaskState.ASSIGNED,
                TaskState.ACCEPTED,
            ), f"over-accept: second={second.state} while first is still RUNNING"

    release.set()
    await harness.await_state(first.task_id, TaskState.COMPLETED)


@pytest.mark.asyncio
async def test_cancelled_accept_send_releases_the_reserved_slot(harness):
    """The slot is reserved before the accept-send await (#1536); a handler
    cancelled while that send is in flight must release it again — the
    scheduler never saw the accept, so keeping the reserved task running
    means double-execution after reassignment."""
    await harness.connect_worker()
    client = harness.client
    blocker = asyncio.Event()

    async def _stuck_send(message, **kw):
        await blocker.wait()

    original_send = client._send
    client._send = _stuck_send
    try:
        assignment = pb.TaskAssignment(
            ref=pb.TaskRef(task_id="t-cancel", attempt_id="a1", session_epoch=client._epoch)
        )
        handler = asyncio.create_task(client._on_assignment(assignment))
        deadline = asyncio.get_running_loop().time() + 5
        while not client._running and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0)
        assert client._running, "the slot was never reserved"
        handler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler
        assert not client._running, (
            "a cancelled accept-send left the reserved task in _running"
        )
    finally:
        client._send = original_send
        blocker.set()


@pytest.mark.asyncio
async def test_stale_epoch_messages_are_ignored(harness):
    """A half-open previous stream must not be able to drive a live task."""
    await harness.connect_worker()
    task = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = harness.scheduler.next_assignment()

    ignored = harness.scheduler.on_accepted(
        task.task_id,
        assignment.attempt.attempt_id,
        epoch=assignment.attempt.session_epoch + 5,
    )
    assert ignored is None
    assert task.state is TaskState.ASSIGNED


@pytest.mark.asyncio
async def test_disconnect_starts_a_grace_window_rather_than_failing(harness):
    await harness.connect_worker()
    task = harness.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = harness.scheduler.next_assignment()
    harness.scheduler.on_accepted(
        task.task_id,
        assignment.attempt.attempt_id,
        epoch=assignment.attempt.session_epoch,
    )
    harness.scheduler.on_started(
        task.task_id,
        assignment.attempt.attempt_id,
        epoch=assignment.attempt.session_epoch,
    )

    harness.scheduler.on_disconnected(assignment.worker.worker_id)

    assert task.state is TaskState.RUNNING
    assert assignment.attempt.grace_expires_at is not None


@pytest.mark.asyncio
async def test_control_stream_requires_a_session(harness):
    """An unauthenticated stream must be refused, not merely ignored."""
    import grpc
    from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc

    credentials = grpc.ssl_channel_credentials(
        root_certificates=harness.creds.certificate_pem
    )
    async with grpc.aio.secure_channel(
        f"localhost:{harness.port}", credentials
    ) as channel:
        stub = pb_grpc.WorkerServiceStub(channel)

        async def _messages():
            yield pb.WorkerMessage(
                heartbeat=pb.Heartbeat(active_tasks=0, available_slots=1)
            )

        with pytest.raises(grpc.aio.AioRpcError) as exc:
            async for _ in stub.Control(_messages()):
                pass
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_control_stream_missing_worker_does_not_leak_open_flag():
    session = type(
        "Session",
        (),
        {
            "stream_open": False,
            "activated": False,
            "revoked": False,
            "registration": None,
            "worker_id": "gone",
            "send": None,
        },
    )()
    servicer = object.__new__(WorkerServicer)
    servicer.pool = type("Pool", (), {"get": lambda _self, _worker_id: None})()
    servicer._sessions = {"gone": session}
    servicer._session_from_metadata = lambda _context: session

    class Context:
        async def abort(self, code, message):
            assert code == grpc.StatusCode.FAILED_PRECONDITION
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="no longer connected"):
        await servicer.Control(None, Context())
    assert session.stream_open is False


@pytest.mark.asyncio
async def test_control_stream_setup_failure_clears_open_flag():
    class Session:
        stream_open = False
        activated = False
        revoked = False
        registration = None
        worker_id = "w1"
        session = type("Token", (), {"token": "session-token"})()

        async def send(self, _message):
            raise RuntimeError("queue closed")

    session = Session()
    worker = type(
        "Worker",
        (),
        {"capacity": type("Capacity", (), {"max_concurrent_tasks": 1})()},
    )()
    servicer = object.__new__(WorkerServicer)
    servicer.pool = type("Pool", (), {"get": lambda _self, _worker_id: worker})()
    servicer.scheduler = type(
        "Scheduler", (), {"on_disconnected": lambda _self, _worker_id: None}
    )()
    servicer._sessions = {"w1": session}
    servicer._by_token = {"session-token": session}
    servicer._session_from_metadata = lambda _context: session

    with pytest.raises(RuntimeError, match="queue closed"):
        await servicer.Control(None, object())
    assert session.stream_open is False
    assert servicer._sessions == {}
    assert servicer._by_token == {}


@pytest.mark.asyncio
async def test_stream_start_refuses_a_worker_removed_after_registration(tmp_path):
    """Both stream directions must leave a raced session reusable/closed."""
    from worker.transport.server import SESSION_METADATA_KEY, _Session

    pool = WorkerPool()
    scheduler = Scheduler(pool, persist=False)
    servicer = WorkerServicer(scheduler, pool, artifact_dir=str(tmp_path / "artifacts"))
    issued = identity.issue_session(worker_id="gone", key_id="key", epoch=1)
    session = _Session("gone", 1, issued)
    servicer._sessions["gone"] = session
    servicer._by_token[issued.token] = session
    aborted = []

    class Context:
        def invocation_metadata(self):
            return ((SESSION_METADATA_KEY, issued.token),)

        async def abort(self, code, message):
            aborted.append((code, message))

    async def no_messages():
        if False:
            yield None

    await servicer.Control(no_messages(), Context())

    assert aborted and aborted[0][0] == grpc.StatusCode.FAILED_PRECONDITION
    assert session.stream_open is False

    connection = object()
    await servicer.run_inbound_stream(session, no_messages(), connection)
    assert session.stream_open is False
    assert session.connection is None


@pytest.mark.asyncio
async def test_inbound_stream_refuses_a_duplicate_open():
    session = SimpleNamespace(stream_open=True)
    servicer = object.__new__(WorkerServicer)

    with pytest.raises(RuntimeError, match="already has an open stream"):
        await servicer.run_inbound_stream(session, None, object())


@pytest.mark.asyncio
async def test_inbound_result_pull_honours_artifact_and_task_caps(tmp_path):
    from types import SimpleNamespace

    from worker.transport.server import (
        MAX_ARTIFACT_BYTES,
        MAX_TASK_ARTIFACT_BYTES,
    )

    pool = WorkerPool()
    servicer = WorkerServicer(
        Scheduler(pool, persist=False), pool, artifact_dir=str(tmp_path / "artifacts")
    )
    calls = []

    class Connection:
        async def fetch_result(self, *_args, **_kwargs):
            calls.append((_args, _kwargs))

    session = SimpleNamespace(worker_id="worker", connection=Connection())
    attempt = SimpleNamespace(task_id="task", attempt_id="attempt")

    oversized = pb.ArtifactRef(size_bytes=MAX_ARTIFACT_BYTES + 1)
    assert await servicer._fetch_inbound_artifact(session, attempt, oversized) is None

    servicer._artifact_bytes_spent = lambda _task_id: MAX_TASK_ARTIFACT_BYTES - 10
    over_budget = pb.ArtifactRef(size_bytes=11)
    assert await servicer._fetch_inbound_artifact(session, attempt, over_budget) is None
    assert calls == []


@pytest.mark.asyncio
async def test_inbound_input_containment_failure_never_reaches_connector(tmp_path):
    from types import SimpleNamespace

    pool = WorkerPool()
    servicer = WorkerServicer(
        Scheduler(pool, persist=False), pool, artifact_dir=str(tmp_path / "artifacts")
    )
    calls = []

    class Connection:
        async def push_input(self, ref, local):
            calls.append((ref, local))

    message = pb.TaskAssignment()
    message.inputs.add(artifact_id="../../outside.wav", filename="outside.wav")

    with pytest.raises(ValueError, match="artifact directory"):
        await servicer._push_inbound_inputs(
            SimpleNamespace(connection=Connection()), message
        )
    assert calls == []


@pytest.mark.asyncio
async def test_registration_refuses_an_unknown_key(harness, db):
    """Knowing a worker id proves nothing without the private key."""
    import grpc
    from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc

    credentials = grpc.ssl_channel_credentials(
        root_certificates=harness.creds.certificate_pem
    )
    async with grpc.aio.secure_channel(
        f"localhost:{harness.port}", credentials
    ) as channel:
        stub = pb_grpc.WorkerServiceStub(channel)
        stranger = WorkerKeypair.generate()
        response = await stub.Register(
            pb.RegisterRequest(
                features=sorted(REQUIRED_FEATURES),
                protocol_version_min=PROTOCOL_VERSION,
                protocol_version_max=PROTOCOL_VERSION,
                public_key=stranger.public_bytes(),
                challenge=b"c" * 32,
                challenge_signature=b"x" * 64,
                nonce=b"n" * 32,
            )
        )
    assert response.error.code == "AUTH_FAILED"
    assert not response.session_token


@pytest.mark.asyncio
async def test_registration_refuses_an_incompatible_protocol(harness, db):
    import grpc
    from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc

    credentials = grpc.ssl_channel_credentials(
        root_certificates=harness.creds.certificate_pem
    )
    async with grpc.aio.secure_channel(
        f"localhost:{harness.port}", credentials
    ) as channel:
        stub = pb_grpc.WorkerServiceStub(channel)
        response = await stub.Register(
            pb.RegisterRequest(protocol_version_min=99, protocol_version_max=99)
        )
    assert response.error.code == "UPGRADE_REQUIRED"
    assert "update" in response.error.message.lower()


@pytest.mark.asyncio
async def test_artifact_download_cannot_escape_its_directory(harness):
    """An artifact id is attacker-controlled input from a remote peer."""
    assert harness.servicer._resolve_input("../../../../etc/passwd") is None
    assert harness.servicer._resolve_input("") is None


def test_default_hostnames_include_a_routable_address():
    """gRPC resolves through c-ares, which does not speak mDNS. A certificate
    that only names `host.local` produces a token no worker can connect with —
    found by actually running the thing, not by a unit test."""
    names = tls.default_hostnames()
    assert "localhost" in names
    address = tls.primary_ip()
    if address:
        assert address in names


@pytest.mark.asyncio
async def test_server_accepts_the_keepalive_interval_it_configures(monkeypatch):
    """The server must not evict healthy idle workers for its own ping policy."""
    captured = {}

    class FakeServer:
        def add_secure_port(self, *_args):
            return 7443

        async def start(self):
            pass

    def fake_server(*, options):
        captured.update(dict(options))
        return FakeServer()

    monkeypatch.setattr(grpc.aio, "server", fake_server)
    monkeypatch.setattr(
        pb_grpc, "add_WorkerServiceServicer_to_server", lambda *_args: None
    )
    monkeypatch.setattr(grpc, "ssl_server_credentials", lambda *_args: object())

    await serve(
        object(),
        certificate_pem=b"certificate",
        private_key_pem=b"private-key",
    )

    assert captured["grpc.http2.min_ping_interval_without_data_ms"] <= 25_000
    assert captured["grpc.http2.max_pings_without_data"] == 0
    assert captured["grpc.so_reuseport"] == 0


@pytest.mark.asyncio
async def test_server_refuses_an_occupied_control_plane_port(monkeypatch):
    """add_secure_port returns zero when the exclusive bind cannot be made."""
    started = False

    class FakeServer:
        def add_secure_port(self, *_args):
            return 0

        async def start(self):
            nonlocal started
            started = True

    monkeypatch.setattr(grpc.aio, "server", lambda **_kwargs: FakeServer())
    monkeypatch.setattr(
        pb_grpc, "add_WorkerServiceServicer_to_server", lambda *_args: None
    )
    monkeypatch.setattr(grpc, "ssl_server_credentials", lambda *_args: object())

    with pytest.raises(ControlPlaneBindError, match="Another VoiceStudio instance"):
        await serve(
            object(),
            port=7443,
            certificate_pem=b"certificate",
            private_key_pem=b"private-key",
        )

    assert started is False


def test_certificate_regenerates_when_it_stops_covering_this_machine(
    tmp_path, monkeypatch
):
    """A laptop that moved networks otherwise keeps a certificate no worker on
    the new network can validate."""
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    monkeypatch.setattr(tls, "primary_ip", lambda: "10.0.0.5")
    first = tls.load_or_create(cert, key)
    assert tls.covers(first, "10.0.0.5")

    monkeypatch.setattr(tls, "primary_ip", lambda: "192.168.9.9")
    second = tls.load_or_create(cert, key)

    assert second.fingerprint != first.fingerprint
    assert tls.covers(second, "192.168.9.9")


def test_explicit_hostnames_are_not_second_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "primary_ip", lambda: "10.0.0.5")
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    first = tls.load_or_create(cert, key, hostnames=["localhost"])
    second = tls.load_or_create(cert, key, hostnames=["localhost"])
    assert first.fingerprint == second.fingerprint


def test_certificate_regenerates_when_an_explicit_listener_host_is_added(tmp_path):
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    first = tls.load_or_create(cert, key, hostnames=["localhost"])
    second = tls.load_or_create(cert, key, hostnames=["localhost", "192.168.0.110"])

    assert second.fingerprint != first.fingerprint
    assert tls.covers(second, "192.168.0.110")


def test_bracketed_ipv6_is_encoded_as_an_ip_certificate_identity():
    from cryptography import x509

    credentials = tls.generate_self_signed(hostnames=["[::1]"])
    certificate = x509.load_der_x509_certificate(credentials.certificate_der)
    san = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value

    assert [str(value) for value in san.get_values_for_type(x509.IPAddress)] == [
        "::1"
    ]
    assert san.get_values_for_type(x509.DNSName) == []
    assert tls.covers(credentials, "::1")


@pytest.mark.asyncio
async def test_a_restarted_worker_reconnects_without_a_new_token(
    harness, tmp_path, monkeypatch
):
    """Key-based identity is pointless if a restart needs a fresh token.

    The challenge signature binds to the worker id, so a worker that does not
    remember its own id signs something the server cannot verify — and every
    reconnect fails AUTH_FAILED. Found by restarting a real worker, not by a
    unit test: every earlier test enrolled fresh with a token.
    """
    from worker import agent as worker_agent

    root = tmp_path / "worker-state"
    root.mkdir()
    monkey_paths = {
        "root": str(root),
        "worker_key": str(root / "worker.key"),
        "pinned_cert": str(root / "pinned.crt"),
        "worker_id": str(root / "worker-id"),
    }
    original_paths = worker_agent._paths
    worker_agent._paths = lambda: monkey_paths
    persisted = asyncio.Event()
    original_save_worker_id = worker_agent.save_worker_id

    def save_worker_id(path, worker_id):
        original_save_worker_id(path, worker_id)
        persisted.set()

    monkeypatch.setattr(worker_agent, "save_worker_id", save_worker_id)
    try:
        # First run: enroll with a token, exactly as a new machine would.
        token = registry.create_enrollment(
            endpoint=f"127.0.0.1:{harness.port}",
            cert_fingerprint=harness.creds.fingerprint,
        )
        agent = worker_agent.WorkerAgent()
        await agent.start(token_text=token.encode())
        await harness._await_connection()
        first_id = registry.list_workers()[0].id
        # The server publishes its session before the registration response
        # reaches the client. Wait for that response callback to persist the
        # id instead of racing its filesystem write.
        await asyncio.wait_for(persisted.wait(), timeout=HANG_GUARD_S)
        assert worker_agent.load_worker_id(monkey_paths["worker_id"]) == first_id

        # The process goes away.
        await agent.stop()
        harness.pool.disconnect(first_id)
        harness.servicer._sessions.clear()
        harness.servicer._by_token.clear()

        # Second run: no token, only the key and the remembered id.
        revived = worker_agent.WorkerAgent()
        await revived.start(endpoint=f"127.0.0.1:{harness.port}")
        await harness._await_connection()

        assert len(registry.list_workers()) == 1, (
            "a reconnect must not enroll a second worker"
        )
        assert harness.pool.get(first_id) is not None
        await revived.stop()
    finally:
        worker_agent._paths = original_paths
