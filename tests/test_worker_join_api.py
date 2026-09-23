"""Joining a control plane from the app, instead of from the environment.

Before these endpoints, becoming a worker meant launching the app with
OMNIVOICE_WORKER_MODE and OMNIVOICE_WORKER_TOKEN set and relaunching — on the
machine that is usually the least convenient one to configure by hand. The
control plane could mint join codes that had nowhere to go.

What is pinned here is what makes the flow survive contact with reality:

* worker mode persists, so a machine that joined is still a worker after a
  restart — but only after a join that actually worked, or a failed enrolment
  would have the app retrying forever on every launch;
* the endpoint from the redeemed token is remembered, because the agent needs
  it to reconnect and asking the user to also set OMNIVOICE_WORKER_ENDPOINT
  would put the barrier straight back;
* a failed join answers with the reason ("that code expired"), not a bare 409,
  because the user's next action depends on which failure it was;
* the environment still wins over the setting, so a deployment that pins
  worker mode cannot be silently switched off from a UI.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import require_admin
from api.routers import workers as workers_router
from worker import agent as worker_agent
from hang_guard import HANG_GUARD_S


@pytest.fixture
def client(monkeypatch, tmp_path):
    """The workers router with the admin guard stubbed out."""
    settings: dict[str, str] = {}

    class _Store:
        @staticmethod
        def get_text(key, default=""):
            return settings.get(key, default)

        @staticmethod
        def get_text_state(key):
            return key in settings, settings.get(key, "")

        @staticmethod
        def set_text(key, value):
            settings[key] = value

        @staticmethod
        def clear_text(key):
            settings.pop(key, None)

    # Both bindings: `from services import settings_store` resolves the package
    # ATTRIBUTE when another test has already imported the real module, and the
    # sys.modules entry only when it has not — patching one leaves the outcome
    # dependent on test order.
    import services

    monkeypatch.setattr(services, "settings_store", _Store, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "services.settings_store", _Store)
    monkeypatch.delenv("OMNIVOICE_WORKER_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_WORKER_ENDPOINT", raising=False)
    monkeypatch.setattr(worker_agent.agent, "last_error", "")
    monkeypatch.setattr(worker_agent.agent, "endpoint", "")
    monkeypatch.setattr(
        worker_agent, "_paths", lambda: {"pinned_cert": str(tmp_path / "pinned.crt")}
    )

    app = FastAPI()
    app.include_router(workers_router.router)
    app.dependency_overrides[require_admin] = lambda: None
    with TestClient(app) as c:
        yield c, settings


def _stub_agent(monkeypatch, *, fail: str = "", never_registers: str = ""):
    """Replace the real agent's start/stop/registration with recorded no-ops.

    `fail` makes `start()` raise (a token that cannot even be redeemed);
    `never_registers` makes the connection start fine and the control plane
    never accept it — the case a scheduled-means-success join could not tell
    apart from a working one.
    """
    calls: list = []

    async def _start(*, token_text: str = "", endpoint: str = ""):
        calls.append(("start", token_text))
        if fail:
            raise RuntimeError(fail)
        worker_agent.agent.endpoint = "studio-mac:7443"

    async def _stop():
        calls.append(("stop", ""))

    async def _wait_until_registered(timeout: float = 20.0):
        calls.append(("wait", ""))
        if never_registers:
            raise RuntimeError(never_registers)

    monkeypatch.setattr(worker_agent.agent, "start", _start)
    monkeypatch.setattr(worker_agent.agent, "stop", _stop)
    monkeypatch.setattr(worker_agent.agent, "wait_until_registered", _wait_until_registered)
    monkeypatch.setattr(worker_agent.agent, "last_error", "")
    monkeypatch.setattr(worker_agent.agent, "endpoint", "")
    return calls


def test_status_reports_a_machine_that_has_never_joined(client):
    c, _ = client
    body = c.get("/workers/agent").json()
    assert body == {
        "worker_mode": False,
        "running": False,
        "enrolled": False,
        "endpoint": "",
        "last_error": "",
        "env_pinned": False,
    }


def test_worker_readiness_is_503_until_initial_registration(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(
        worker_agent.agent,
        "readiness",
        lambda: {"ready": False, "status": "registering"},
    )

    response = c.get("/workers/agent/readiness")

    assert response.status_code == 503
    assert response.json() == {"ready": False, "status": "registering"}


def test_worker_readiness_is_200_after_initial_registration(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(
        worker_agent.agent,
        "readiness",
        lambda: {"ready": True, "status": "ready"},
    )

    response = c.get("/workers/agent/readiness")

    assert response.status_code == 200
    assert response.json() == {"ready": True, "status": "ready"}


def test_join_redeems_the_code_and_persists_worker_mode(client, monkeypatch):
    c, settings = client
    calls = _stub_agent(monkeypatch)

    body = c.post("/workers/agent/join", json={"token": "ovw_abc123"}).json()

    assert ("start", "ovw_abc123") in calls
    # Persisted, so the machine is still a worker after a restart.
    assert settings["worker_mode_enabled"] == "true"
    assert body["worker_mode"] is True
    assert body["endpoint"] == "studio-mac:7443"


def test_explicit_join_can_repair_a_corrupt_enrollment_manifest(
    client, monkeypatch, tmp_path
):
    """Corrupt committed state fails closed on startup but must not brick Join."""
    c, settings = client
    (tmp_path / "enrollment.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "pinned.crt").write_bytes(b"stale compatibility certificate")
    calls = _stub_agent(monkeypatch)

    response = c.post("/workers/agent/join", json={"token": "ovw_fresh"})

    assert response.status_code == 200
    assert ("start", "ovw_fresh") in calls
    assert settings["worker_mode_enabled"] == "true"


def test_join_stops_any_previous_connection_first(client, monkeypatch):
    c, _ = client
    calls = _stub_agent(monkeypatch)

    c.post("/workers/agent/join", json={"token": "ovw_abc123"})

    # Re-joining a DIFFERENT control plane must not leave the old dial-out
    # loop running against the machine the user just left.
    assert calls[0][0] == "stop"


def test_a_failed_join_answers_with_the_reason_and_stays_off(client, monkeypatch):
    c, settings = client
    _stub_agent(monkeypatch, fail="This enrollment token has expired. Generate a new one.")

    response = c.post("/workers/agent/join", json={"token": "ovw_expired"})

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    # Never persisted: a machine that failed to enrol must not come back up
    # retrying forever.
    assert "worker_mode_enabled" not in settings
    assert c.get("/workers/agent").json()["last_error"].startswith("This enrollment token")


def test_a_join_the_control_plane_never_accepts_is_not_a_success(client, monkeypatch, tmp_path):
    """Scheduling the connection is not joining.

    `start()` returns as soon as the dial-out loop is created, so a control
    plane that rejects this worker — or never answers — used to persist worker
    mode and report success, leaving the machine retrying forever against an
    address that will not have it.
    """
    c, settings = client
    calls = _stub_agent(monkeypatch, never_registers="The control plane did not answer in time.")

    response = c.post("/workers/agent/join", json={"token": "ovw_unreachable"})

    assert response.status_code == 409
    assert "did not answer" in response.json()["detail"]
    assert "worker_mode_enabled" not in settings
    # …and the half-started agent is not left dialling in the background.
    assert calls[-1][0] == "stop"


def test_a_failed_rejoin_restores_the_working_enrollment(client, monkeypatch, tmp_path):
    """A rejoin that fails must not cost the user the control plane they had.

    Trust state is staged until acceptance, but the UI still stops the working
    agent while it tries the new code and must resume it on failure.
    """
    c, settings = client
    calls = _stub_agent(monkeypatch, never_registers="That code has expired.")
    pinned = tmp_path / "pinned.crt"
    pinned.write_bytes(b"previous-control-plane")
    settings["worker_mode_enabled"] = "true"
    settings["worker_endpoint"] = "studio-mac:7443"

    assert c.post("/workers/agent/join", json={"token": "ovw_expired"}).status_code == 409
    # …and the agent it was running is dialling again. Without the rollback the
    # machine sits stopped until someone notices and toggles it back on: the
    # join stops the old agent before it knows the new code is any good.
    assert calls[-1] == ("start", ""), (
        f"expected the previous enrollment to be resumed, got {calls!r}"
    )
    assert settings["worker_endpoint"] == "studio-mac:7443"
    assert settings["worker_mode_enabled"] == "true"


def test_failed_control_after_registration_restores_the_previous_manifest(
    client, monkeypatch, tmp_path
):
    """Register acceptance replaces the manifest before Control activation.

    If Control then fails, rollback must restore the exact previous generation,
    not merely its legacy certificate and endpoint mirrors.
    """
    c, settings = client
    manifest_path = tmp_path / "enrollment.json"
    worker_agent._save_enrollment_manifest(
        str(manifest_path),
        endpoint="old-studio:7443",
        certificate=b"old certificate",
        worker_id="old-worker",
        token_hash=worker_agent._token_hash("ovw_old"),
    )
    old_generation = manifest_path.read_bytes()
    settings["worker_mode_enabled"] = "true"
    settings["worker_endpoint"] = "old-studio:7443"
    calls = []

    async def start(*, token_text: str = "", endpoint: str = ""):
        calls.append(("start", token_text))
        if token_text:
            # This is the accepted Register callback: the new identity is
            # durable locally, but the Config/activation confirmation is not.
            worker_agent._save_enrollment_manifest(
                str(manifest_path),
                endpoint="new-studio:7443",
                certificate=b"new certificate",
                worker_id="new-worker",
                token_hash=worker_agent._token_hash(token_text),
            )

    async def stop():
        calls.append(("stop", ""))

    async def wait_until_registered(timeout: float = 20.0):
        raise RuntimeError("Control closed before activation")

    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(
        worker_agent.agent, "wait_until_registered", wait_until_registered
    )

    response = c.post("/workers/agent/join", json={"token": "ovw_new"})

    assert response.status_code == 409
    assert manifest_path.read_bytes() == old_generation
    assert worker_agent._load_enrollment_manifest(str(manifest_path))["worker_id"] == (
        "old-worker"
    )
    assert calls[-1] == ("start", "")


def test_join_refuses_to_replace_a_manifest_it_cannot_back_up(
    client, monkeypatch, tmp_path
):
    c, _settings = client
    calls = _stub_agent(monkeypatch)
    manifest_path = tmp_path / "enrollment.json"
    manifest_path.write_bytes(b"existing enrollment generation")
    existing = manifest_path.read_bytes()
    real_open = open

    def unreadable_manifest(path, *args, **kwargs):
        if str(path) == str(manifest_path) and args and args[0] == "rb":
            raise PermissionError("manifest is unreadable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(worker_agent, "open", unreadable_manifest, raising=False)

    response = c.post("/workers/agent/join", json={"token": "ovw_repair"})

    assert response.status_code == 409
    assert "backed up safely" in response.json()["detail"]
    assert manifest_path.read_bytes() == existing
    assert calls == []


def test_join_refuses_to_stop_when_settings_cannot_be_snapshotted(
    client, monkeypatch
):
    c, settings = client
    calls = _stub_agent(monkeypatch)
    settings["worker_mode_enabled"] = "true"
    settings["worker_endpoint"] = "old-studio:7443"
    import services

    def unreadable_setting(_key):
        raise OSError("settings database is unreadable")

    monkeypatch.setattr(services.settings_store, "get_text_state", unreadable_setting)

    response = c.post("/workers/agent/join", json={"token": "ovw_repair"})

    assert response.status_code == 409
    assert "settings cannot be backed up safely" in response.json()["detail"]
    assert settings == {
        "worker_mode_enabled": "true",
        "worker_endpoint": "old-studio:7443",
    }
    assert calls == []


def test_join_surfaces_rollback_failure_as_actionable_conflict(
    client, monkeypatch
):
    c, _settings = client
    calls = _stub_agent(monkeypatch, never_registers="Replacement activation failed.")

    async def fail_rollback(_previous):
        raise worker_agent.EnrollmentRollbackError(
            "The previous enrollment could not be restored; worker mode remains stopped."
        )

    monkeypatch.setattr(worker_agent, "restore_enrollment", fail_rollback)

    response = c.post("/workers/agent/join", json={"token": "ovw_replacement"})

    assert response.status_code == 409
    assert "could not be restored" in response.json()["detail"]
    assert calls[-1][0] == "stop"


def test_failed_legacy_upgrade_restores_every_identity_mirror(
    client, monkeypatch, tmp_path
):
    c, settings = client
    pinned = tmp_path / "pinned.crt"
    worker_id = tmp_path / "worker-id"
    token_hash = tmp_path / "enrollment-token.sha256"
    manifest = tmp_path / "enrollment.json"
    pinned.write_bytes(b"old certificate")
    worker_id.write_bytes(b"old-worker\n")
    token_hash.write_bytes(worker_agent._token_hash("ovw_old").encode("ascii"))
    old_files = {
        pinned: pinned.read_bytes(),
        worker_id: worker_id.read_bytes(),
        token_hash: token_hash.read_bytes(),
    }
    settings["worker_mode_enabled"] = "true"
    settings["worker_endpoint"] = "old-studio:7443"
    worker_agent.agent.endpoint = "old-runtime:7443"
    calls = []

    async def start(*, token_text: str = "", endpoint: str = ""):
        calls.append(("start", token_text))
        if not token_text:
            return
        worker_agent._save_enrollment_manifest(
            str(manifest),
            endpoint="new-studio:7443",
            certificate=b"new certificate",
            worker_id="new-worker",
            token_hash=worker_agent._token_hash(token_text),
        )
        pinned.write_bytes(b"new certificate")
        worker_id.write_bytes(b"new-worker")
        token_hash.write_bytes(worker_agent._token_hash(token_text).encode("ascii"))
        worker_agent._remember_endpoint("new-studio:7443")
        worker_agent.agent.endpoint = "new-studio:7443"

    async def stop():
        calls.append(("stop", ""))

    async def wait_until_registered(timeout: float = 20.0):
        raise RuntimeError("Control closed before activation")

    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(
        worker_agent.agent, "wait_until_registered", wait_until_registered
    )

    response = c.post("/workers/agent/join", json={"token": "ovw_new"})

    assert response.status_code == 409
    assert not manifest.exists()
    assert {path: path.read_bytes() for path in old_files} == old_files
    assert settings["worker_endpoint"] == "old-studio:7443"
    assert calls[-1] == ("start", "")


def test_first_failed_join_restores_endpoint_absence(client, monkeypatch, tmp_path):
    c, settings = client
    manifest = tmp_path / "enrollment.json"

    async def start(*, token_text: str = "", endpoint: str = ""):
        worker_agent._save_enrollment_manifest(
            str(manifest),
            endpoint="new-studio:7443",
            certificate=b"new certificate",
            worker_id="new-worker",
            token_hash=worker_agent._token_hash(token_text),
        )
        (tmp_path / "worker-id").write_text("new-worker", encoding="utf-8")
        (tmp_path / "enrollment-token.sha256").write_text(
            worker_agent._token_hash(token_text), encoding="ascii"
        )
        worker_agent._remember_endpoint("new-studio:7443")
        worker_agent.agent.endpoint = "new-studio:7443"

    async def stop():
        pass

    async def wait_until_registered(timeout: float = 20.0):
        raise RuntimeError("Control closed before activation")

    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(
        worker_agent.agent, "wait_until_registered", wait_until_registered
    )

    response = c.post("/workers/agent/join", json={"token": "ovw_new"})

    assert response.status_code == 409
    assert "worker_endpoint" not in settings
    assert worker_agent.agent.endpoint == ""
    assert not manifest.exists()
    assert not (tmp_path / "worker-id").exists()
    assert not (tmp_path / "enrollment-token.sha256").exists()


def test_an_env_pinned_machine_refuses_to_be_toggled(client, monkeypatch):
    """The environment wins everywhere else, so it wins here too.

    Writing the setting under OMNIVOICE_WORKER_MODE would store a value the
    rest of the app ignores, and the next restart would undo whatever the
    toggle appeared to do.
    """
    c, settings = client
    _stub_agent(monkeypatch)
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")

    response = c.post("/workers/agent/enabled", json={"enabled": False})

    assert response.status_code == 409
    assert "OMNIVOICE_WORKER_MODE" in response.json()["detail"]
    assert "worker_mode_enabled" not in settings


def test_an_env_pinned_machine_refuses_a_join_too(client, monkeypatch):
    """Joining ENABLES worker mode, so the same rule applies as to the toggle.

    Under OMNIVOICE_WORKER_MODE the join would persist a setting nothing
    consults — and with the variable pinned off, hand the user a machine that
    reports a successful join and never lends anything.
    """
    c, settings = client
    calls = _stub_agent(monkeypatch)
    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "0")

    response = c.post("/workers/agent/join", json={"token": "ovw_abc123"})

    assert response.status_code == 409
    assert "OMNIVOICE_WORKER_MODE" in response.json()["detail"]
    assert calls == []
    assert "worker_mode_enabled" not in settings


def test_join_rejects_an_empty_code(client, monkeypatch):
    c, _ = client
    calls = _stub_agent(monkeypatch)
    assert c.post("/workers/agent/join", json={"token": "   "}).status_code == 422
    assert calls == []


def test_stopping_clears_the_setting_but_keeps_the_enrollment(client, monkeypatch, tmp_path):
    c, settings = client
    calls = _stub_agent(monkeypatch)
    (tmp_path / "pinned.crt").write_bytes(b"cert")

    c.post("/workers/agent/join", json={"token": "ovw_abc123"})
    body = c.post("/workers/agent/enabled", json={"enabled": False}).json()

    assert ("stop", "") in calls
    assert settings["worker_mode_enabled"] == "false"
    assert body["worker_mode"] is False
    # The pinned certificate survives, which is what lets "on" resume without
    # asking for another code.
    assert body["enrolled"] is True


def test_resuming_needs_no_new_code(client, monkeypatch, tmp_path):
    c, _ = client
    calls = _stub_agent(monkeypatch)
    (tmp_path / "pinned.crt").write_bytes(b"cert")

    assert c.post("/workers/agent/enabled", json={"enabled": True}).status_code == 200
    assert ("start", "") in calls


def test_the_environment_still_wins_over_the_stored_setting(client, monkeypatch):
    c, settings = client
    _stub_agent(monkeypatch)
    c.post("/workers/agent/enabled", json={"enabled": False})
    assert settings["worker_mode_enabled"] == "false"

    monkeypatch.setenv("OMNIVOICE_WORKER_MODE", "1")

    body = c.get("/workers/agent").json()
    assert body["worker_mode"] is True
    # …and the panel is told, so it disables a switch it cannot honour.
    assert body["env_pinned"] is True


def test_the_redeemed_endpoint_is_remembered_for_the_next_launch(client, monkeypatch):
    """The token carries the address; forgetting it puts the barrier back.

    A machine that joined from the UI used to come back up enrolled but with
    nowhere to dial, and the only fix was OMNIVOICE_WORKER_ENDPOINT.
    """
    c, settings = client
    worker_agent._remember_endpoint("studio-mac:7443")

    assert settings["worker_endpoint"] == "studio-mac:7443"
    assert worker_agent._stored_endpoint() == "studio-mac:7443"
    assert c.get("/workers/agent").json()["endpoint"] == "studio-mac:7443"


def test_join_rolls_back_when_enabling_worker_mode_cannot_commit(
    client, monkeypatch, tmp_path
):
    c, settings = client
    state = {"running": False}
    manifest = tmp_path / "enrollment.json"

    async def start(*, token_text: str = "", endpoint: str = ""):
        state["running"] = True
        worker_agent.agent.endpoint = "new-studio:7443"
        worker_agent._save_enrollment_manifest(
            str(manifest),
            endpoint="new-studio:7443",
            certificate=b"new certificate",
            worker_id="new-worker",
            token_hash=worker_agent._token_hash(token_text),
        )
        worker_agent._remember_endpoint("new-studio:7443")

    async def stop():
        state["running"] = False

    async def registered(timeout: float = 20.0):
        return None

    monkeypatch.setattr(type(worker_agent.agent), "running", property(lambda _self: state["running"]))
    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(worker_agent.agent, "wait_until_registered", registered)

    import services

    real_set_text = services.settings_store.set_text

    def fail_mode_commit(key, value):
        if key == "worker_mode_enabled" and value == "true":
            raise OSError("settings commit failed")
        real_set_text(key, value)

    monkeypatch.setattr(
        services.settings_store, "set_text", staticmethod(fail_mode_commit)
    )

    response = c.post("/workers/agent/join", json={"token": "ovw_new"})

    assert response.status_code == 409
    assert "settings commit failed" in response.json()["detail"]
    assert state["running"] is False
    assert not manifest.exists()
    assert "worker_endpoint" not in settings
    assert "worker_mode_enabled" not in settings


@pytest.mark.asyncio
async def test_join_cancellation_restores_the_previous_enrollment_and_agent(
    client, monkeypatch, tmp_path
):
    _c, settings = client
    settings["worker_mode_enabled"] = "true"
    settings["worker_endpoint"] = "old-studio:7443"
    manifest = tmp_path / "enrollment.json"
    worker_agent._save_enrollment_manifest(
        str(manifest),
        endpoint="old-studio:7443",
        certificate=b"old certificate",
        worker_id="old-worker",
        token_hash=worker_agent._token_hash("ovw_old"),
    )
    previous = manifest.read_bytes()
    state = {"running": True}
    waiting = asyncio.Event()

    async def start(*, token_text: str = "", endpoint: str = ""):
        state["running"] = True
        if token_text:
            worker_agent._save_enrollment_manifest(
                str(manifest),
                endpoint="new-studio:7443",
                certificate=b"new certificate",
                worker_id="new-worker",
                token_hash=worker_agent._token_hash(token_text),
            )
            worker_agent._remember_endpoint("new-studio:7443")
            worker_agent.agent.endpoint = "new-studio:7443"

    async def stop():
        state["running"] = False

    async def never_registered(timeout: float = 20.0):
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(type(worker_agent.agent), "running", property(lambda _self: state["running"]))
    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(
        worker_agent.agent, "wait_until_registered", never_registered
    )

    request = asyncio.create_task(
        workers_router.join_control_plane(workers_router.JoinRequest(token="ovw_new"))
    )
    await asyncio.wait_for(waiting.wait(), timeout=HANG_GUARD_S)
    request.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request, timeout=HANG_GUARD_S)

    assert manifest.read_bytes() == previous
    assert settings["worker_endpoint"] == "old-studio:7443"
    assert settings["worker_mode_enabled"] == "true"
    assert worker_agent.agent.endpoint == "old-studio:7443"
    assert state["running"] is True


@pytest.mark.parametrize(
    ("enabled", "previous_mode", "previous_running"),
    [(True, None, False), (False, "true", True)],
)
def test_toggle_setting_failure_restores_durable_and_live_state(
    client,
    monkeypatch,
    enabled,
    previous_mode,
    previous_running,
):
    c, settings = client
    if previous_mode is not None:
        settings["worker_mode_enabled"] = previous_mode
    state = {"running": previous_running}

    async def start(*, token_text: str = "", endpoint: str = ""):
        state["running"] = True

    async def stop():
        state["running"] = False

    async def registered(timeout: float = 20.0):
        return None

    monkeypatch.setattr(type(worker_agent.agent), "running", property(lambda _self: state["running"]))
    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)
    monkeypatch.setattr(worker_agent.agent, "wait_until_registered", registered)

    import services

    real_set_text = services.settings_store.set_text
    failing_value = "true" if enabled else "false"

    def fail_requested_commit(key, value):
        if key == "worker_mode_enabled" and value == failing_value:
            raise OSError("settings commit failed")
        real_set_text(key, value)

    monkeypatch.setattr(
        services.settings_store, "set_text", staticmethod(fail_requested_commit)
    )

    response = c.post("/workers/agent/enabled", json={"enabled": enabled})

    assert response.status_code == 409
    assert state["running"] is previous_running
    assert settings.get("worker_mode_enabled") == previous_mode


@pytest.mark.asyncio
async def test_toggle_cancellation_restores_the_previous_live_state(
    client, monkeypatch
):
    _c, settings = client
    settings["worker_mode_enabled"] = "true"
    state = {"running": True, "stop_calls": 0}
    stopping = asyncio.Event()

    async def start(*, token_text: str = "", endpoint: str = ""):
        state["running"] = True

    async def stop():
        state["stop_calls"] += 1
        state["running"] = False
        if state["stop_calls"] == 1:
            stopping.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(type(worker_agent.agent), "running", property(lambda _self: state["running"]))
    monkeypatch.setattr(worker_agent.agent, "start", start)
    monkeypatch.setattr(worker_agent.agent, "stop", stop)

    request = asyncio.create_task(
        workers_router.set_agent_enabled(workers_router.EnableRequest(enabled=False))
    )
    await asyncio.wait_for(stopping.wait(), timeout=HANG_GUARD_S)
    request.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request, timeout=HANG_GUARD_S)

    assert state["running"] is True
    assert settings["worker_mode_enabled"] == "true"
