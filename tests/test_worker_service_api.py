"""Feature lifecycle, capability discovery, and the management API.

The property that matters most here is the one that is easiest to erode: with
the feature switched off, *nothing* runs. No socket, no certificate, no
background loop. The local-first guarantee is not "we're careful with the
network", it is that a user who never opts in has an app that is unchanged.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from worker import capabilities, service
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S


@pytest.fixture
def db(tmp_path, monkeypatch):
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


# ── The opt-in gate ────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_REMOTE_WORKERS", raising=False)
    monkeypatch.setattr(
        service, "remote_workers_enabled", service.remote_workers_enabled
    )
    # No settings row and no env var: the feature is off.
    monkeypatch.setattr("services.settings_store.get_text", lambda *a, **k: None)
    assert service.remote_workers_enabled() is False


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("on", True),
                                            ("0", False), ("false", False), ("", False)])
def test_env_var_controls_the_gate(monkeypatch, value, expected):
    monkeypatch.setenv("OMNIVOICE_REMOTE_WORKERS", value)
    monkeypatch.setattr("services.settings_store.get_text", lambda *a, **k: None)
    assert service.remote_workers_enabled() is expected


def test_env_var_beats_the_stored_setting(monkeypatch):
    """A headless deployment must be able to force the answer."""
    monkeypatch.setenv("OMNIVOICE_REMOTE_WORKERS", "0")
    monkeypatch.setattr("services.settings_store.get_text", lambda *a, **k: "true")
    assert service.remote_workers_enabled() is False


def test_a_broken_settings_store_does_not_enable_the_feature(monkeypatch):
    """Failing closed matters here: failing open would start a listening
    socket for a user who never asked for one."""
    monkeypatch.delenv("OMNIVOICE_REMOTE_WORKERS", raising=False)

    def _boom(*a, **k):
        raise RuntimeError("db is gone")

    monkeypatch.setattr("services.settings_store.get_text", _boom)
    assert service.remote_workers_enabled() is False


@pytest.mark.asyncio
async def test_start_if_enabled_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: False)
    started = []
    monkeypatch.setattr(
        service.control_plane, "start", lambda **k: started.append(True)
    )

    await service.start_if_enabled()

    assert started == []
    assert service.control_plane.running is False


@pytest.mark.asyncio
async def test_a_failing_start_never_takes_the_app_down(monkeypatch):
    """The user's local workflow does not depend on this feature existing."""
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: True)
    monkeypatch.setattr(service.control_plane, "startup_error", None)

    async def _boom(**kwargs):
        raise OSError("port already in use")

    monkeypatch.setattr(service.control_plane, "start", _boom)
    await service.start_if_enabled()  # must not raise
    assert service.control_plane.startup_error == "port already in use"


@pytest.mark.asyncio
async def test_concurrent_control_plane_starts_publish_only_one_generation(
    monkeypatch,
):
    plane = service.ControlPlane()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def staged_start(*, port=None):
        nonlocal calls
        if plane._started:
            return
        calls += 1
        entered.set()
        await release.wait()
        plane._started = True
        plane._port = port

    monkeypatch.setattr(plane, "_start", staged_start)
    first = asyncio.create_task(plane.start(port=7601))
    await asyncio.wait_for(entered.wait(), timeout=HANG_GUARD_S)
    second = asyncio.create_task(plane.start(port=7602))
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    await asyncio.gather(first, second)
    assert calls == 1
    assert plane._port == 7601


def test_paths_live_under_the_user_data_directory():
    locations = service.paths()
    assert locations["certificate"].startswith(locations["root"])
    assert locations["private_key"].startswith(locations["root"])
    assert locations["artifacts"].startswith(locations["root"])


def test_snapshot_is_inert_when_stopped(monkeypatch):
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: False)
    plane = service.ControlPlane()
    snapshot = plane.snapshot()
    assert snapshot == {
        "enabled": False,
        "running": False,
        "startup_error": None,
        "workers": [],
        "queue_depth": 0,
    }


def test_port_falls_back_when_the_env_var_is_garbage(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_WORKER_PORT", "not-a-port")
    assert service.control_port() == service.DEFAULT_PORT


# ── Capability discovery ───────────────────────────────────────────────────


def test_discovery_survives_a_broken_engine_layer(monkeypatch):
    """One engine that cannot introspect must not hide the others — the same
    guarantee list_backends() already makes locally."""
    monkeypatch.setattr(
        "services.tts_backend.list_backends", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert capabilities.discover() == []


def test_discovery_reports_the_four_states(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [
            {
                "id": "indextts",
                "display_name": "IndexTTS-2",
                "available": True,
                "supports_cloning": True,
                "routing_status": "accelerated",
                "gpu_compat": ["cuda"],
                "effective_device": "cuda",
                "min_vram_gb": 6.0,
            }
        ],
    )
    found = capabilities.discover()

    assert len(found) == 1
    entry = found[0]
    for field in ("supported", "installed", "downloaded", "resident"):
        assert field in entry, f"{field} must be reported separately"
    assert entry["min_memory_bytes"] == int(6 * 1024**3)
    assert "clone" in entry["operations"]


def test_gpu_description_reports_live_cuda_telemetry(monkeypatch):
    from types import SimpleNamespace

    caps = SimpleNamespace(
        family="cuda",
        device_name="NVIDIA RTX 4090",
        vram_gb=24,
        driver=None,
    )
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)
    monkeypatch.setattr(
        capabilities,
        "_accelerator_memory_bytes",
        lambda _caps: (18 * 1024**3, 24 * 1024**3),
    )
    monkeypatch.setattr(
        capabilities,
        "_accelerator_details",
        lambda _caps: ("591.86", "8.9"),
    )

    assert capabilities.describe_gpus() == [
        {
            "vendor": "nvidia",
            "model": "NVIDIA RTX 4090",
            "backend": "cuda",
            "memory_bytes": 24 * 1024**3,
            "free_memory_bytes": 18 * 1024**3,
            "driver_version": "591.86",
            "compute_capability": "8.9",
        }
    ]


def test_nvidia_driver_probe_finds_wsl_system_binary(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: None)
    monkeypatch.setattr(capabilities.os.path, "isfile", lambda path: path == "/usr/lib/wsl/lib/nvidia-smi")
    monkeypatch.setattr(
        capabilities.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args)
        or SimpleNamespace(returncode=0, stdout="591.86\n"),
    )

    assert capabilities._nvidia_driver_version() == "591.86"
    assert calls[0][0] == "/usr/lib/wsl/lib/nvidia-smi"


def test_unknown_native_gpu_memory_keeps_one_serial_worker_slot(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [{
            "id": "audiocpp",
            "available": True,
            "routing_status": "accelerated",
            "gpu_compat": ["vulkan", "cpu"],
            "effective_device": "vulkan",
            "min_vram_gb": 6.0,
            "execution_evidence": {"runtime_vram_gb": 0.0},
        }],
    )

    entry = capabilities.discover()[0]

    assert entry["free_memory_bytes"] == 0
    assert entry["derived_concurrency"] == 1


def test_unknown_native_memory_keeps_mixed_worker_serial(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [{
            "id": "unknown-memory",
            "available": True,
            "routing_status": "accelerated",
            "gpu_compat": ["cuda"],
            "effective_device": "cuda",
            "execution_evidence": {"runtime_vram_gb": 0.0},
        }],
    )

    unknown_memory = capabilities.discover()[0]
    high_capacity = {"derived_concurrency": 4}

    assert unknown_memory["derived_concurrency"] == 1
    assert capabilities.max_concurrent_tasks(
        [unknown_memory, high_capacity]
    ) == 1


def test_static_gpu_profile_derives_known_memory_before_aggregation(
    monkeypatch,
):
    free_bytes = 24 * 1024**3
    derive_calls = []

    def derive_for_static_gpu(**kwargs):
        derive_calls.append(kwargs)
        return 3

    monkeypatch.setattr(capabilities, "_free_memory_bytes", lambda _caps: free_bytes)
    monkeypatch.setattr(capabilities, "derive_concurrency", derive_for_static_gpu)
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [{
            "id": "static-cuda",
            "available": True,
            "routing_status": "accelerated",
            "gpu_compat": ["cuda"],
            "effective_device": "cuda",
            "min_vram_gb": 5.0,
            "execution_evidence": {"runtime_vram_gb": None},
        }],
    )

    static_gpu = capabilities.discover()[0]

    assert static_gpu["derived_concurrency"] == 0
    assert static_gpu["free_memory_bytes"] == free_bytes
    assert capabilities.max_concurrent_tasks(
        [static_gpu, {"derived_concurrency": 4}]
    ) == 3
    assert derive_calls == [
        {
            "backend": "cuda",
            "free_memory_bytes": free_bytes,
            "min_model_bytes": 5 * 1024**3,
            "compiled": False,
        }
    ]


def test_cpu_fallback_is_reported_because_capability_is_not_acceleration(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [
            {
                "id": "slow",
                "available": True,
                "routing_status": "cpu_fallback",
                "gpu_compat": ["cpu"],
                "effective_device": "cpu",
            }
        ],
    )
    assert capabilities.discover()[0]["cpu_fallback"] is True


def test_unavailable_engines_are_omitted_by_default(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [{"id": "broken", "available": False, "gpu_compat": []}],
    )
    assert capabilities.discover() == []
    assert len(capabilities.discover(include_unavailable=True)) == 1


def test_engines_that_cannot_clone_do_not_advertise_it(monkeypatch):
    """`supports_cloning` is None when it depends on the loaded model; treating
    that as "yes" produces a task that fails at the last moment."""
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [{"id": "e", "available": True, "supports_cloning": None, "gpu_compat": ["cuda"]}],
    )
    assert capabilities.discover()[0]["operations"] == [
        "audiobook",
        "batch_segments",
        "tts",
    ]


def test_cloning_engines_advertise_clone_and_dubbing(monkeypatch):
    monkeypatch.setattr(
        "services.tts_backend.list_backends",
        lambda: [
            {
                "id": "e",
                "available": True,
                "supports_cloning": True,
                "gpu_compat": ["cuda"],
            }
        ],
    )
    assert capabilities.discover()[0]["operations"] == [
        "audiobook",
        "batch_segments",
        "tts",
        "clone",
        "dub_segments",
    ]


def test_default_concurrency_is_one():
    """Matching the local GPU queue's deliberate single lane."""
    assert capabilities.max_concurrent_tasks([]) == 1
    assert capabilities.max_concurrent_tasks([{"derived_concurrency": 4}, {"derived_concurrency": 1}]) == 1


# ── Management API ─────────────────────────────────────────────────────────


def _app():
    from fastapi import FastAPI

    from api.routers import workers as workers_router

    app = FastAPI()
    app.include_router(workers_router.router)
    return app


@pytest.fixture
def client(db):
    """A client that satisfies the admin gate.

    The gate is real and is exercised separately below; overriding it here
    keeps every other test about the endpoint's own behaviour.
    """
    from fastapi.testclient import TestClient

    from api.dependencies import require_admin

    app = _app()
    app.dependency_overrides[require_admin] = lambda: None
    return TestClient(app)


def test_management_endpoints_are_loopback_only(db):
    """These mint join tokens and revoke machines, so a non-loopback origin
    must be refused outright rather than merely discouraged."""
    from fastapi.testclient import TestClient

    unguarded = TestClient(_app())
    assert unguarded.get("/workers").status_code == 403
    assert unguarded.post("/workers/enrollments", json={}).status_code == 403
    assert unguarded.delete("/workers/anything").status_code == 403


def test_server_mode_worker_mutations_require_api_key(db, monkeypatch):
    """Bare Docker discovery stays usable; its worker controls stay closed."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    remote = TestClient(_app(), client=("172.17.0.1", 50000))

    assert remote.get("/workers").status_code == 200
    assert remote.post("/workers/enabled", json={"enabled": True}).status_code == 403
    assert remote.post("/workers/agent/join", json={"token": "hostile"}).status_code == 403
    assert remote.delete("/workers/anything").status_code == 403


def test_listing_workers_is_safe_when_the_feature_is_off(client):
    response = client.get("/workers")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_minting_a_token_requires_the_feature_to_be_running(client):
    response = client.post("/workers/enrollments", json={})
    assert response.status_code == 409
    assert "Settings" in response.json()["detail"]


def test_updating_an_unknown_worker_is_a_404(client):
    assert client.patch("/workers/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/workers/nope").status_code == 404
    assert client.post("/workers/nope/consent").status_code == 404


def test_worker_updates_round_trip(client, db):
    from worker import registry
    from worker.identity import WorkerKeypair

    worker = registry.enroll_worker(name="box", public_key=WorkerKeypair.generate().public_bytes())

    response = client.patch(
        f"/workers/{worker.id}", json={"name": "Desktop", "priority": 90, "enabled": False}
    )

    assert response.status_code == 200
    reloaded = registry.get(worker.id)
    assert reloaded.name == "Desktop"
    assert reloaded.priority == 90
    assert reloaded.enabled is False


def test_priority_is_clamped_by_the_schema(client, db):
    from worker import registry
    from worker.identity import WorkerKeypair

    worker = registry.enroll_worker(name="box", public_key=WorkerKeypair.generate().public_bytes())
    assert client.patch(f"/workers/{worker.id}", json={"priority": 500}).status_code == 422


def test_removing_a_worker_revokes_its_key(client, db):
    """Remove must mean revoke, not hide: a hidden row would let the same key
    reconnect as though it were a stranger."""
    from worker import registry
    from worker.identity import WorkerKeypair

    keypair = WorkerKeypair.generate()
    worker = registry.enroll_worker(name="box", public_key=keypair.public_bytes())

    assert client.delete(f"/workers/{worker.id}").status_code == 200
    assert registry.is_revoked(keypair.key_id) is True


def test_consent_is_recorded_explicitly(client, db):
    from worker import registry
    from worker.identity import WorkerKeypair

    worker = registry.enroll_worker(
        name="box", public_key=WorkerKeypair.generate().public_bytes(), consent_granted=False
    )
    assert registry.get(worker.id).schedulable is False

    assert client.post(f"/workers/{worker.id}/consent").status_code == 200
    assert registry.get(worker.id).schedulable is True


def test_task_listing_is_empty_when_stopped(client):
    body = client.get("/workers/tasks").json()
    assert body == {"tasks": [], "queue_depth": 0}


@pytest.mark.asyncio
async def test_enrollment_advertises_the_port_actually_bound(db, monkeypatch, tmp_path):
    """A token carries the endpoint a worker will dial. Advertising the
    configured port while listening on another hands workers an address
    nothing answers on — found by running the thing on a non-default port.
    """
    monkeypatch.setattr(
        service,
        "paths",
        lambda: {
            "root": str(tmp_path),
            "certificate": str(tmp_path / "cp.crt"),
            "private_key": str(tmp_path / "cp.key"),
            "worker_key": str(tmp_path / "w.key"),
            "artifacts": str(tmp_path / "artifacts"),
        },
    )
    monkeypatch.delenv("OMNIVOICE_WORKER_PORT", raising=False)
    monkeypatch.setenv("OMNIVOICE_WORKER_ENDPOINT_HOST", "panel.tailnet.example")

    plane = service.ControlPlane()
    await plane.start(port=7601)
    try:
        from worker import tls

        assert plane.default_endpoint() == "panel.tailnet.example:7601"
        assert plane.create_enrollment().endpoint == "panel.tailnet.example:7601"
        assert tls.covers(plane.credentials, "panel.tailnet.example")
    finally:
        await plane.stop()


def test_enrollment_refuses_a_host_absent_from_the_live_certificate(
    client, monkeypatch
):
    """Rotating only the token pin cannot add a SAN to the running server."""
    from worker import tls

    monkeypatch.setattr(service.control_plane, "_started", True)
    monkeypatch.setattr(
        service.control_plane,
        "credentials",
        tls.generate_self_signed(hostnames=["localhost"]),
    )

    response = client.post(
        "/workers/enrollments", json={"endpoint": "panel.tailnet.example:7443"}
    )

    assert response.status_code == 409
    assert "OMNIVOICE_WORKER_ENDPOINT_HOST" in response.json()["detail"]


@pytest.mark.asyncio
async def test_stopping_releases_everyone_awaiting_a_task():
    """Otherwise quitting hangs on a future nothing will ever complete: the
    sweeper that would have timed the wait out is cancelled first."""
    from worker.lifecycle import Task
    from worker.pool import WorkerPool
    from worker.scheduler import Scheduler, SchedulerStopped

    plane = service.ControlPlane()
    plane.scheduler = Scheduler(WorkerPool(), persist=False)
    plane.scheduler.adopt(Task(task_id="t1", operation="tts", engine="e", model_id="m"))
    waiter = asyncio.ensure_future(plane.scheduler.wait("t1"))
    await asyncio.sleep(0)

    await plane.stop()

    with pytest.raises(SchedulerStopped):
        await waiter


@pytest.mark.asyncio
async def test_production_artifact_gc_uses_a_bounded_off_loop_batch(
    tmp_path, monkeypatch
):
    from worker import task_store

    calls = []
    main_thread = threading.current_thread()

    def purge_finished(**kwargs):
        calls.append((kwargs, threading.current_thread()))
        return 7

    class Servicer:
        def __init__(self):
            self.retries = 0

        def sweep_orphaned_upload_parts(self):
            self.retries += 1

    monkeypatch.setattr(task_store, "purge_finished", purge_finished)
    plane = service.ControlPlane()
    plane.servicer = Servicer()
    artifact_root = str(tmp_path / "artifacts")

    assert await plane._artifact_gc_once(artifact_root) == 7
    assert calls[0][0] == {
        "root": artifact_root,
        "limit": service._ARTIFACT_GC_BATCH_SIZE,
    }
    assert calls[0][1] is not main_thread
    assert plane.servicer.retries == 1


def test_endpoint_falls_back_to_the_configured_port_when_stopped(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_WORKER_PORT", raising=False)
    assert service.ControlPlane().default_endpoint().endswith(f":{service.DEFAULT_PORT}")


# ── Endpoints that had no coverage until a 422 in the UI made the point ─────


def test_enable_endpoint_rejects_a_non_object_body(client):
    """The exact failure the panel shipped: a JSON *string* posted without a
    content type. FastAPI is right to refuse it; the test exists so the shape
    is pinned rather than rediscovered in the UI."""
    response = client.post(
        "/workers/enabled", content='{"enabled":true}', headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 422


def test_enable_endpoint_requires_the_field(client):
    assert client.post("/workers/enabled", json={}).status_code == 422


def test_enable_endpoint_persists_the_setting(client, monkeypatch):
    """Toggling must survive a restart, so it goes through settings_store —
    and turning it off must actually stop the control plane."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(
        "services.settings_store.set_text", lambda k, v: stored.__setitem__(k, v)
    )
    stopped: list[bool] = []

    async def _stop():
        stopped.append(True)

    monkeypatch.setattr(service.control_plane, "stop", _stop)

    assert client.post("/workers/enabled", json={"enabled": False}).status_code == 200
    assert stored["remote_workers_enabled"] == "false"
    assert stopped == [True]


def test_resume_requires_the_feature_to_be_running(client):
    assert client.post("/workers/anything/resume").status_code == 409


def test_cancel_requires_the_feature_to_be_running(client):
    assert client.post("/workers/tasks/abc/cancel").status_code == 409


def test_resume_clears_open_breakers(client, db, monkeypatch):
    """The manual escape hatch: the user fixed the machine and knows it."""
    from worker.errors import ErrorClass, WorkerError
    from worker.pool import WorkerPool

    worker = registry_enroll("box")
    pool = WorkerPool()
    pool.breakers.note_worker(worker.id)
    for _ in range(3):
        pool.breakers.record_failure(
            worker.id,
            "e:m",
            WorkerError(error_class=ErrorClass.TRANSIENT, code="X", message="x"),
            now=1000.0,
        )
    assert pool.breakers.allows(worker.id, "e:m", now=1000.0) is False
    breaker = pool.breakers.get(worker.id, "e:m")
    real_force_close = breaker.force_close

    def force_close_on_owner_loop():
        asyncio.get_running_loop()
        real_force_close()

    monkeypatch.setattr(breaker, "force_close", force_close_on_owner_loop)

    monkeypatch.setattr(service.control_plane, "pool", pool)
    monkeypatch.setattr(type(service.control_plane), "running", property(lambda self: True))

    assert client.post(f"/workers/{worker.id}/resume").status_code == 200
    assert pool.breakers.allows(worker.id, "e:m", now=1000.0) is True


def test_cancel_reports_an_unknown_task(client, monkeypatch):
    class _Sched:
        def cancel(self, *a, **k):
            return False

    monkeypatch.setattr(type(service.control_plane), "running", property(lambda self: True))
    monkeypatch.setattr(service.control_plane, "scheduler", _Sched())
    assert client.post("/workers/tasks/nope/cancel").status_code == 404


def registry_enroll(name: str):
    from worker import registry
    from worker.identity import WorkerKeypair

    return registry.enroll_worker(name=name, public_key=WorkerKeypair.generate().public_bytes())


# ── GPU target picker ──────────────────────────────────────────────────────


def test_target_defaults_to_local(client, monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr("services.settings_store.get_text", lambda k, d=None: store.get(k, d))
    body = client.get("/workers/target").json()
    assert body["target"] == "local"
    assert body["active"]["remote"] is False
    assert body["targets"][0]["id"] == "local"


def test_runtime_status_routes_through_the_selected_compute_target(client, monkeypatch):
    seen = {}

    async def fake_status(engine=None, *, op="tts", control_plane=None):
        seen.update(engine=engine, op=op, control_plane=control_plane)
        return {"target": "worker-1", "remote": True, "models": []}

    monkeypatch.setattr("services.gpu_gateway.status", fake_status)
    response = client.get("/workers/runtime?op=tts&engine=omnivoice")

    assert response.status_code == 200
    assert response.json()["target"] == "worker-1"
    assert seen == {
        "engine": "omnivoice",
        "op": "tts",
        "control_plane": service.control_plane,
    }


def test_choosing_an_unknown_worker_is_refused(client):
    """Otherwise a typo silently parks generation on a target that will never
    resolve, and every job quietly runs locally with no explanation."""
    assert client.post("/workers/target", json={"target": "nosuch"}).status_code == 404


def test_choosing_a_worker_persists_and_is_reflected(client, db, monkeypatch):
    from worker.identity import WorkerKeypair

    store: dict[str, str] = {}
    monkeypatch.setattr("services.settings_store.get_text", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr("services.settings_store.set_text", lambda k, v: store.__setitem__(k, v))

    worker = registry_enroll("desktop-4090")
    response = client.post("/workers/target", json={"target": worker.id})

    assert response.status_code == 200
    assert response.json()["target"] == worker.id
    assert store["worker_target"] == worker.id
    # Not connected, so the ACTIVE answer is still local — and says why.
    assert response.json()["active"]["remote"] is False


def test_target_can_be_set_back_to_local(client, monkeypatch):
    store: dict[str, str] = {"worker_target": "something"}
    monkeypatch.setattr("services.settings_store.get_text", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr("services.settings_store.set_text", lambda k, v: store.__setitem__(k, v))

    assert client.post("/workers/target", json={"target": "local"}).status_code == 200
    assert store["worker_target"] == "local"


# ── The submit path (dev-only) ─────────────────────────────────────────────
#
# The defect this covers is not a wrong answer, it is an absence: the scheduler
# had no caller outside the test suite, so choosing a remote GPU repainted the
# badge and every job still ran on this machine.


def _queued_task():
    from worker.lifecycle import Task

    return Task(task_id="t1", operation="tts", engine="indextts", model_id="m")


def _settled_task(state):
    task = _queued_task()
    task.state = state
    return task


class _Scheduler:
    """The endpoint's whole contract with the scheduler: submit, wait, cancel."""

    queue_depth = 0

    def __init__(self, *, settle=None, wait_error=None, hang=False, submit_error=None):
        self.submitted: list[dict] = []
        self.cancelled: list[tuple[str, str]] = []
        self.waited: tuple[str, float] | None = None
        self._settle = settle
        self._wait_error = wait_error
        self._hang = hang
        self._submit_error = submit_error

    def submit(self, **kwargs):
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted.append(kwargs)
        return _queued_task()

    async def wait(self, task_id, *, timeout):
        self.waited = (task_id, timeout)
        if self._hang:
            await asyncio.sleep(3600)
        if self._wait_error is not None:
            raise self._wait_error
        return self._settle

    def cancel(self, task_id, *, reason="cancelled"):
        self.cancelled.append((task_id, reason))
        return True


def _running(monkeypatch, scheduler):
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: True)
    monkeypatch.setattr(type(service.control_plane), "running", property(lambda self: True))
    monkeypatch.setattr(service.control_plane, "scheduler", scheduler)
    return scheduler


_BODY = {"engine": "indextts", "operation": "tts", "params": {"text": "hi"}, "deadline_seconds": 60}


def test_the_scheduler_finally_has_a_producer(client, monkeypatch):
    """B0: before this route existed, nothing in the app ever called submit."""
    from worker.lifecycle import TaskState

    scheduler = _running(monkeypatch, _Scheduler(settle=_settled_task(TaskState.COMPLETED)))

    response = client.post("/workers/tasks", json=_BODY)

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert scheduler.submitted[0]["operation"] == "tts"
    assert scheduler.submitted[0]["engine"] == "indextts"
    assert scheduler.waited == ("t1", 60.0)


def test_submit_requires_the_feature_to_be_running(client, monkeypatch):
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: True)
    assert client.post("/workers/tasks", json=_BODY).status_code == 409


def test_submit_is_unreachable_when_the_feature_is_off(client, monkeypatch):
    """Opt-in means opt-in: a running control plane is not consent by itself."""
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: False)
    monkeypatch.setattr(type(service.control_plane), "running", property(lambda self: True))
    assert client.post("/workers/tasks", json=_BODY).status_code == 409


def test_submit_demands_a_deadline(client):
    """Without one the task is never stamped with `deadline_at`, and the
    sweeper's only deadline rule then has nothing to enforce — a task queued
    with no worker online would wait for the heat death of the universe."""
    body = {k: v for k, v in _BODY.items() if k != "deadline_seconds"}
    assert client.post("/workers/tasks", json=body).status_code == 422
    assert client.post("/workers/tasks", json={**_BODY, "deadline_seconds": 0}).status_code == 422


def test_submit_refuses_an_operation_with_no_remote_path(client, monkeypatch):
    _running(monkeypatch, _Scheduler())
    response = client.post("/workers/tasks", json={**_BODY, "operation": "asr"})
    assert response.status_code == 400
    assert "asr" in response.json()["detail"]


def test_a_full_queue_is_refused_at_the_door(client, monkeypatch):
    from worker.scheduler import QueueFull

    _running(monkeypatch, _Scheduler(submit_error=QueueFull("full")))
    assert client.post("/workers/tasks", json=_BODY).status_code == 429


def test_a_failed_task_is_not_reported_as_success(client, monkeypatch):
    from worker.lifecycle import TaskState

    _running(monkeypatch, _Scheduler(settle=_settled_task(TaskState.FAILED)))

    response = client.post("/workers/tasks", json=_BODY)

    assert response.status_code == 502
    assert response.json()["detail"]["state"] == "failed"


def test_a_cancelled_task_is_not_a_server_error(client, monkeypatch):
    from worker.lifecycle import TaskState

    _running(monkeypatch, _Scheduler(settle=_settled_task(TaskState.CANCELLED)))
    assert client.post("/workers/tasks", json=_BODY).status_code == 409


def test_an_expired_wait_cancels_the_task_it_gave_up_on(client, monkeypatch):
    scheduler = _running(monkeypatch, _Scheduler(wait_error=asyncio.TimeoutError()))

    response = client.post("/workers/tasks", json=_BODY)

    assert response.status_code == 504
    assert scheduler.cancelled == [("t1", "the task passed its deadline")]


def test_a_shutdown_mid_wait_does_not_claim_the_task_was_cancelled(client, monkeypatch):
    """The worker was never told to stop, so it may still be rendering."""
    from worker.scheduler import SchedulerStopped

    scheduler = _running(monkeypatch, _Scheduler(wait_error=SchedulerStopped("stopped")))

    assert client.post("/workers/tasks", json=_BODY).status_code == 503
    assert scheduler.cancelled == []


def test_the_real_scheduler_agrees_with_how_the_endpoint_drives_it(client, monkeypatch):
    """The stubs above pin this endpoint's behaviour; this pins the seam. With
    no worker connected the task simply waits, so the request's own deadline is
    what ends it — and the queued task must not be left behind."""
    from worker.lifecycle import TaskState
    from worker.pool import WorkerPool
    from worker.scheduler import Scheduler

    scheduler = _running(monkeypatch, Scheduler(WorkerPool(), persist=False))

    response = client.post("/workers/tasks", json={**_BODY, "deadline_seconds": 0.5})

    assert response.status_code == 504
    submitted = next(iter(scheduler._tasks.values()))
    assert submitted.state is TaskState.CANCELLED


@pytest.mark.asyncio
async def test_dev_task_input_staging_keeps_the_request_loop_responsive(
    monkeypatch
):
    from api.routers import workers as workers_router
    from worker import routing, task_store
    from worker.lifecycle import TaskState
    from worker.pool import WorkerPool
    from worker.routing import Decision
    from worker.scheduler import Scheduler

    scheduler = Scheduler(WorkerPool(), persist=True)
    stage_started = threading.Event()
    release_stage = threading.Event()
    staging_thread = []

    def blocked_create(task, **_kwargs):
        staging_thread.append(threading.current_thread())
        stage_started.set()
        assert release_stage.wait(timeout=BARRIER_WATCHDOG_S)
        return task

    async def settle(_request, active, task_id, **_kwargs):
        task = active.get(task_id)
        task.state = TaskState.COMPLETED
        return task

    monkeypatch.setattr(task_store, "create", blocked_create)
    monkeypatch.setattr(service, "remote_workers_enabled", lambda: True)
    monkeypatch.setattr(
        type(service.control_plane), "running", property(lambda self: True)
    )
    monkeypatch.setattr(service.control_plane, "scheduler", scheduler)
    monkeypatch.setattr(routing, "supports_operation", lambda _operation: True)
    monkeypatch.setattr(
        routing,
        "decide",
        lambda *args, **kwargs: Decision(True, "worker-1", "GPU", "chosen"),
    )
    monkeypatch.setattr(workers_router, "_await_terminal", settle)

    class Request:
        async def is_disconnected(self):
            return False

    body = workers_router.SubmitTaskRequest(**_BODY)
    submitting = asyncio.create_task(workers_router.submit_task(Request(), body))
    while not stage_started.is_set():
        await asyncio.sleep(0)

    try:
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        assert not submitting.done()
        assert len(staging_thread) == 1
        assert staging_thread[0] is not threading.current_thread()
    finally:
        release_stage.set()

    assert (await submitting)["state"] == "completed"


def test_a_disconnecting_client_cancels_the_task(client, monkeypatch):
    """Otherwise the tab closes, the request is abandoned, and the 4090 keeps
    rendering — holding its only slot — for something nobody will collect."""
    from api.routers import workers as workers_router

    async def _gone(self):
        return True

    monkeypatch.setattr("starlette.requests.Request.is_disconnected", _gone)
    monkeypatch.setattr(workers_router, "_DISCONNECT_POLL_SECONDS", 0.01)
    scheduler = _running(monkeypatch, _Scheduler(hang=True))

    response = client.post("/workers/tasks", json=_BODY)

    assert response.status_code == 499
    assert scheduler.cancelled == [("t1", "the client disconnected")]


def test_the_target_endpoint_answers_per_operation(client, monkeypatch, db):
    """The badge on a tab whose work is entirely local must say so — and say
    why, in the words of that tab rather than the machine's."""
    worker = registry_enroll("desktop-4090")
    store = {"worker_target": worker.id}
    monkeypatch.setattr("services.settings_store.get_text", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(service.control_plane, "_started", True)

    scoped = client.get("/workers/target", params={"op": "dub"}).json()
    assert scoped["op"] == "dub"
    assert scoped["active"]["remote"] is False
    assert "offline" in scoped["active"]["reason"]

    whole = client.get("/workers/target").json()
    assert whole["op"] == ""
    assert whole["remote_operations"] == [
        "audiobook", "batch", "batch_segments", "clone", "dub", "dub_segments",
        "longform", "tts",
    ]


# ── Config is read from the database, not from the pool's stale copy ───────


def _plane_with_connected_worker(monkeypatch, tmp_path, name="desktop-4090"):
    import time as _time

    from worker import registry
    from worker.identity import WorkerKeypair, issue_session
    from worker.pool import WorkerPool

    worker = registry.enroll_worker(name=name, public_key=WorkerKeypair.generate().public_bytes())
    pool = WorkerPool()
    pool.connect(
        worker,
        session=issue_session(worker_id=worker.id, key_id=worker.key_id, epoch=1, now=_time.time()),
        epoch=1,
        now=_time.time(),
    )
    class _Sched:
        queue_depth = 0

    monkeypatch.setattr(service.control_plane, "pool", pool)
    monkeypatch.setattr(service.control_plane, "scheduler", _Sched())
    monkeypatch.setattr(type(service.control_plane), "running", property(lambda self: True))
    return worker, pool


def test_renaming_a_connected_worker_shows_immediately(client, db, monkeypatch, tmp_path):
    """The pool caches the row from connect time. Reading the name from there
    meant a rename only appeared after the worker reconnected."""
    worker, _pool = _plane_with_connected_worker(monkeypatch, tmp_path)

    assert client.patch(f"/workers/{worker.id}", json={"name": "Studio 4090"}).status_code == 200

    listed = client.get("/workers").json()["workers"]
    entry = next(w for w in listed if w["id"] == worker.id)
    assert entry["name"] == "Studio 4090"
    assert entry["connected"] is True, "liveness must survive the fix"


def test_priority_change_on_a_connected_worker_shows_immediately(client, db, monkeypatch, tmp_path):
    worker, _pool = _plane_with_connected_worker(monkeypatch, tmp_path)

    client.patch(f"/workers/{worker.id}", json={"priority": 90})

    entry = next(w for w in client.get("/workers").json()["workers"] if w["id"] == worker.id)
    assert entry["priority"] == 90


def test_disabling_a_connected_worker_shows_immediately(client, db, monkeypatch, tmp_path):
    worker, _pool = _plane_with_connected_worker(monkeypatch, tmp_path)

    client.patch(f"/workers/{worker.id}", json={"enabled": False})

    entry = next(w for w in client.get("/workers").json()["workers"] if w["id"] == worker.id)
    assert entry["enabled"] is False


def test_revoke_cannot_dispatch_after_commit_before_live_disconnect(
    client, db, monkeypatch, tmp_path
):
    """The durable tombstone and live-pool removal are one authority change."""
    from worker import registry
    from worker.scheduler import Scheduler

    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)
    registry.update_capabilities(
        worker.id,
        capabilities=[
            {
                "engine": "e",
                "model_id": "m",
                "operations": ["tts"],
                "supported": True,
                "installed": True,
            }
        ],
    )
    pool.refresh_record(registry.get(worker.id))
    scheduler = Scheduler(pool, persist=False)
    scheduler.submit(
        operation="tts",
        engine="e",
        model_id="m",
        deadline_seconds=60,
    )
    monkeypatch.setattr(service.control_plane, "scheduler", scheduler)

    after_commit = threading.Event()
    allow_disconnect = threading.Event()
    real_disconnect = scheduler.on_disconnected

    def delayed_disconnect(worker_id):
        after_commit.set()
        assert allow_disconnect.wait(BARRIER_WATCHDOG_S), "test did not release live publication"
        return real_disconnect(worker_id)

    monkeypatch.setattr(scheduler, "on_disconnected", delayed_disconnect)
    observed = {}

    def revoke():
        observed["response"] = client.delete(f"/workers/{worker.id}")

    revoke_thread = threading.Thread(target=revoke)
    revoke_thread.start()
    assert after_commit.wait(HANG_GUARD_S), "revoke never reached its live publication"
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT revoked FROM remote_workers WHERE id = ?", (worker.id,)
        ).fetchone()[0] == 1

    assignment_finished = threading.Event()

    def assign():
        observed["assignment"] = scheduler.next_assignment()
        assignment_finished.set()

    assignment_thread = threading.Thread(target=assign)
    assignment_thread.start()
    assert not assignment_finished.wait(0.05), "revoked worker received new work"

    allow_disconnect.set()
    revoke_thread.join(HANG_GUARD_S)
    assignment_thread.join(HANG_GUARD_S)

    assert not revoke_thread.is_alive()
    assert not assignment_thread.is_alive()
    assert observed["response"].status_code == 200
    assert observed["assignment"] is None


def test_the_pool_copy_is_refreshed_too(client, db, monkeypatch, tmp_path):
    """Otherwise the scheduler's logs keep naming the worker by its old name."""
    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)

    client.patch(f"/workers/{worker.id}", json={"name": "Studio 4090"})

    assert pool.get(worker.id).name == "Studio 4090"


def test_worker_update_keeps_sqlite_off_loop_and_live_publication_on_loop(
    client, db, monkeypatch, tmp_path
):
    from api.routers import workers as worker_routes

    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)
    real_persist = worker_routes._persist_worker_update
    real_refresh = pool.refresh_record
    observed = {"durable_off_loop": False, "live_on_loop": False}

    def persist_off_loop(worker_id, request):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        observed["durable_off_loop"] = True
        return real_persist(worker_id, request)

    def refresh_on_loop(record):
        asyncio.get_running_loop()
        observed["live_on_loop"] = True
        return real_refresh(record)

    monkeypatch.setattr(worker_routes, "_persist_worker_update", persist_off_loop)
    monkeypatch.setattr(pool, "refresh_record", refresh_on_loop)

    response = client.patch(f"/workers/{worker.id}", json={"name": "Loop-owned"})

    assert response.status_code == 200
    assert observed == {"durable_off_loop": True, "live_on_loop": True}


def test_multi_field_worker_update_is_durable_and_live_atomic(
    client, db, monkeypatch, tmp_path
):
    from worker import registry

    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TRIGGER fail_late_worker_policy "
            "BEFORE UPDATE OF name, enabled, priority ON remote_workers "
            "WHEN NEW.priority = 91 BEGIN "
            "SELECT RAISE(ABORT, 'late policy write failed'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="late policy write failed"):
        client.patch(
            f"/workers/{worker.id}",
            json={"enabled": False, "priority": 91},
        )

    durable = registry.get(worker.id)
    live = pool.get(worker.id)
    assert durable is not None
    assert live is not None
    assert (durable.enabled, durable.priority) == (True, 50)
    assert (live.record.enabled, live.record.priority) == (True, 50)
    assert live.registration_pending is False


def test_revoke_mutates_sessions_scheduler_and_breakers_on_owner_loop(
    client, db, monkeypatch, tmp_path
):
    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)
    calls: list[str] = []

    class Servicer:
        def revoke_worker_sessions(self, worker_id):
            asyncio.get_running_loop()
            calls.append(f"session:{worker_id}")
            return 1

    class Scheduler:
        def on_disconnected(self, worker_id):
            asyncio.get_running_loop()
            calls.append(f"scheduler:{worker_id}")
            pool.disconnect(worker_id)

    real_forget = pool.breakers.forget_worker

    def forget_on_loop(worker_id):
        asyncio.get_running_loop()
        calls.append(f"breaker:{worker_id}")
        real_forget(worker_id)

    monkeypatch.setattr(service.control_plane, "servicer", Servicer())
    monkeypatch.setattr(service.control_plane, "scheduler", Scheduler())
    monkeypatch.setattr(pool.breakers, "forget_worker", forget_on_loop)

    response = client.delete(f"/workers/{worker.id}")

    assert response.status_code == 200
    assert calls == [
        f"session:{worker.id}",
        f"scheduler:{worker.id}",
        f"breaker:{worker.id}",
    ]


def test_live_fields_still_come_from_the_pool(client, db, monkeypatch, tmp_path):
    worker, pool = _plane_with_connected_worker(monkeypatch, tmp_path)
    pool.get(worker.id).capacity.reserve("e", "m")

    entry = next(w for w in client.get("/workers").json()["workers"] if w["id"] == worker.id)
    assert entry["active_tasks"] == 1
    assert "available_slots" in entry
