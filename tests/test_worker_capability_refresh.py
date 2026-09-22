import asyncio
import json
import threading

import pytest

from worker.identity import WorkerKeypair
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.transport import client as client_module
from worker.transport.client import WorkerClient, WorkerConfig
from worker.transport.server import WorkerServicer
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S


def _client(probe):
    return WorkerClient(
        WorkerConfig(endpoint="unused", cert_fingerprint="", certificate_pem=b"",
                     keypair=WorkerKeypair.generate()),
        execute=lambda _assignment: None,
        capability_probe=probe,
    )


@pytest.mark.asyncio
async def test_blocked_telemetry_probe_is_not_restarted(monkeypatch):
    """A stuck driver call occupies one worker thread, never one per heartbeat."""
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def sample():
        nonlocal calls
        calls += 1
        started.set()
        release.wait(BARRIER_WATCHDOG_S)
        return 10.0, 20, 30.0

    monkeypatch.setattr(client_module, "_heartbeat_resources", sample)
    client = _client(lambda: [])
    await client._refresh_telemetry()
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    for _ in range(3):
        await client._refresh_telemetry()
    assert calls == 1

    release.set()
    await asyncio.wait_for(client._telemetry_task, timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_partial_telemetry_failure_retains_other_last_good_values(monkeypatch):
    samples = iter(((10.0, 20, 30.0), (None, None, 40.0)))
    monkeypatch.setattr(
        client_module, "_heartbeat_resources", lambda: next(samples, (None, None, None))
    )
    client = _client(lambda: [])

    await client._refresh_telemetry()
    await asyncio.wait_for(client._telemetry_task, timeout=HANG_GUARD_S)
    await client._refresh_telemetry()
    await asyncio.wait_for(client._telemetry_task, timeout=HANG_GUARD_S)
    await client._refresh_telemetry()

    assert client._telemetry == (10.0, 20, 40.0)


@pytest.mark.asyncio
async def test_refresh_sends_capability_update():
    client = _client(lambda: [{
        "engine": "omnivoice", "model_id": "omnivoice:default",
        "operations": ["tts"], "supported": True, "installed": True,
        "downloaded": True, "repo_ids": ["k2-fsa/OmniVoice"],
    }])
    await client.refresh_capabilities()
    frame = await client._outbox.get()
    assert frame.WhichOneof("payload") == "capabilities"
    assert frame.capabilities.capabilities[0].downloaded is True


@pytest.mark.asyncio
async def test_register_and_refresh_share_one_off_loop_capability_probe():
    started = threading.Event()
    release = threading.Event()
    main_thread = threading.current_thread()
    calls = 0

    def probe():
        nonlocal calls
        assert threading.current_thread() is not main_thread
        calls += 1
        started.set()
        release.wait(BARRIER_WATCHDOG_S)
        return [{
            "engine": "omnivoice",
            "model_id": "omnivoice:default",
            "operations": ["tts"],
            "supported": True,
            "installed": True,
            "downloaded": True,
        }]

    client = _client(probe)
    registering = asyncio.create_task(client.build_register_request())
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)
    refreshing = asyncio.create_task(client.refresh_capabilities())
    await asyncio.sleep(0)

    try:
        assert calls == 1
    finally:
        release.set()
    request = await asyncio.wait_for(registering, timeout=HANG_GUARD_S)
    await asyncio.wait_for(refreshing, timeout=HANG_GUARD_S)

    assert calls == 1
    assert request.capabilities[0].model_id == "omnivoice:default"
    frame = await client._outbox.get()
    assert frame.capabilities.capabilities[0].model_id == "omnivoice:default"


@pytest.mark.asyncio
async def test_cancelled_slow_probe_keeps_control_responsive_and_drains_thread():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def probe():
        started.set()
        release.wait()
        finished.set()
        return []

    client = _client(probe)
    refreshing = asyncio.create_task(client.refresh_capabilities())
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    await client._on_server_message(pb.ServerMessage(ping=pb.Ping(nonce=42)))
    pong = await asyncio.wait_for(client._outbox.get(), timeout=HANG_GUARD_S)
    assert pong.pong.nonce == 42
    assert not refreshing.done()

    refreshing.cancel()
    await asyncio.sleep(0)
    assert not refreshing.done(), "probe thread was abandoned on cancellation"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(refreshing, timeout=HANG_GUARD_S)

    assert finished.is_set()
    assert client._outbox.empty(), "cancelled probe published a late capability frame"


@pytest.mark.asyncio
async def test_task_slot_stays_reserved_until_its_capability_probe_finishes():
    started = threading.Event()
    release = threading.Event()

    def probe():
        started.set()
        release.wait()
        return []

    async def execute(_assignment):
        return {"meta": {}, "payload": b""}

    client = _client(probe)
    client._execute = execute
    first = pb.TaskAssignment(
        ref=pb.TaskRef(task_id="first", attempt_id="attempt-1")
    )
    await client._on_assignment(first)
    first_task = client._running["first/attempt-1"]
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    heartbeat = client.heartbeat_message().heartbeat
    assert heartbeat.active_tasks == 1
    assert heartbeat.available_slots == 0

    second = pb.TaskAssignment(
        ref=pb.TaskRef(task_id="second", attempt_id="attempt-2")
    )
    await client._on_assignment(second)
    assert "second/attempt-2" not in client._running
    frames = []
    while not client._outbox.empty():
        frames.append(await client._outbox.get())
    assert any(frame.WhichOneof("payload") == "rejected" for frame in frames)

    release.set()
    await asyncio.wait_for(first_task, timeout=HANG_GUARD_S)
    assert client._running == {}


@pytest.mark.asyncio
async def test_prewarm_resolves_catalog_repo_and_refreshes(monkeypatch):
    loaded = []
    client = _client(lambda: [{
        "engine": "omnivoice", "model_id": "omnivoice:default",
        "operations": ["tts"], "supported": True, "installed": True,
        "downloaded": True, "repo_ids": ["k2-fsa/OmniVoice"],
    }])
    client.config.capabilities = client._capability_probe()
    monkeypatch.setattr(
        "worker.executor.TaskExecutor._load_backend", lambda engine: loaded.append(engine)
    )
    await client._on_prewarm(pb.PrewarmRequest(
        model_id="omnivoice:default", download_if_missing=False,
    ))
    assert loaded == ["omnivoice"]
    assert (await client._outbox.get()).WhichOneof("payload") == "capabilities"


@pytest.mark.asyncio
async def test_authority_loss_cancels_and_drains_a_blocked_prewarm(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    probes = 0

    def probe():
        nonlocal probes
        probes += 1
        return [{
            "engine": "omnivoice",
            "model_id": "omnivoice:default",
            "operations": ["tts"],
            "supported": True,
            "installed": True,
            "downloaded": False,
            "repo_ids": ["k2-fsa/OmniVoice"],
        }]

    client = _client(probe)
    client.config.capabilities = probe()

    async def blocked_install(_repo_id):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(client, "_install_catalog_repo", blocked_install)
    await client._on_server_message(pb.ServerMessage(prewarm=pb.PrewarmRequest(
        model_id="omnivoice:default", download_if_missing=True,
    )))
    await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_S)

    await client.stop()

    assert cancelled.is_set()
    assert client._maintenance == set()
    assert probes == 1, "cancelled prewarm must not publish a late capability refresh"
    assert client._outbox.empty()


@pytest.mark.asyncio
async def test_model_install_cancel_stops_only_the_matching_prewarm(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    capability = {
        "engine": "omnivoice",
        "model_id": "omnivoice:default",
        "operations": ["tts"],
        "supported": True,
        "installed": True,
        "downloaded": False,
        "repo_ids": ["k2-fsa/OmniVoice"],
    }
    client = _client(lambda: [capability])
    client.config.capabilities = [capability]

    async def blocked_install(_repo_id):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(client, "_install_catalog_repo", blocked_install)
    await client._on_server_message(
        pb.ServerMessage(
            prewarm=pb.PrewarmRequest(
                model_id="omnivoice:default", download_if_missing=True
            )
        )
    )
    await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_S)

    await client._on_server_message(
        pb.ServerMessage(
            model_install_cancel=pb.ModelInstallCancelRequest(
                model_id="omnivoice:default"
            )
        )
    )
    await asyncio.wait_for(
        asyncio.gather(*tuple(client._maintenance), return_exceptions=True),
        timeout=HANG_GUARD_S,
    )
    await asyncio.sleep(0)

    assert cancelled.is_set()
    assert client._prewarms == {}
    frame = await asyncio.wait_for(client._outbox.get(), timeout=HANG_GUARD_S)
    assert frame.WhichOneof("payload") == "download_progress"
    event = json.loads(frame.download_progress.event_json)
    assert event["repo_id"] == "k2-fsa/OmniVoice"
    assert event["phase"] == "install_cancelled"


@pytest.mark.asyncio
async def test_authority_loss_waits_for_a_blocking_prewarm_thread(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    probes = 0

    def probe():
        nonlocal probes
        probes += 1
        return [{
            "engine": "omnivoice",
            "model_id": "omnivoice:default",
            "operations": ["tts"],
            "supported": True,
            "installed": True,
            "downloaded": True,
        }]

    def blocked_load(_engine):
        started.set()
        release.wait()
        finished.set()

    client = _client(probe)
    client.config.capabilities = probe()
    monkeypatch.setattr("worker.executor.TaskExecutor._load_backend", blocked_load)
    await client._on_server_message(pb.ServerMessage(prewarm=pb.PrewarmRequest(
        model_id="omnivoice:default",
    )))
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    stopping = asyncio.create_task(client.stop())
    await asyncio.sleep(0)
    assert not stopping.done(), "authority returned while the load thread was active"
    release.set()
    await asyncio.wait_for(stopping, timeout=HANG_GUARD_S)

    assert finished.is_set()
    assert probes == 1
    assert client._outbox.empty()


@pytest.mark.asyncio
async def test_blocked_prewarm_does_not_delay_active_task_cancellation():
    client = _client(lambda: [])
    maintenance_cancelled = asyncio.Event()
    release_maintenance = asyncio.Event()
    task_cancelled = asyncio.Event()

    async def blocked_maintenance():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            maintenance_cancelled.set()
            await release_maintenance.wait()

    async def active_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task_cancelled.set()
            raise

    maintenance = asyncio.create_task(blocked_maintenance())
    running = asyncio.create_task(active_task())
    client._maintenance.add(maintenance)
    client._running["task/attempt"] = running
    await asyncio.sleep(0)

    stopping = asyncio.create_task(client.stop())
    await asyncio.wait_for(maintenance_cancelled.wait(), timeout=HANG_GUARD_S)
    await asyncio.wait_for(task_cancelled.wait(), timeout=HANG_GUARD_S)
    assert not stopping.done()

    release_maintenance.set()
    await asyncio.wait_for(stopping, timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_cancelled_remote_install_waits_for_its_background_task(monkeypatch):
    from api.routers.setup import download as setup_download
    from utils import download_aggregator, hf_progress

    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    repo_id = "k2-fsa/OmniVoice"

    async def background_install():
        started.set()
        await release.wait()
        finished.set()

    async def fake_install(req):
        task = asyncio.create_task(background_install())
        setup_download._install_tasks.add(task)
        setup_download._install_tasks_by_repo[req.repo_id] = task
        task.add_done_callback(setup_download._install_tasks.discard)
        return {"status": "install_started", "repo_id": req.repo_id}

    monkeypatch.setattr(setup_download, "install_model", fake_install)
    monkeypatch.setattr(hf_progress, "install", lambda: None)
    monkeypatch.setattr(download_aggregator, "install", lambda: None)
    client = _client(lambda: [])
    installing = asyncio.create_task(client._install_catalog_repo(repo_id))
    await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_S)

    installing.cancel()
    await asyncio.sleep(0)
    assert not installing.done(), "authority returned while the install was active"
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(installing, timeout=HANG_GUARD_S)
    assert finished.is_set()
    setup_download._install_tasks_by_repo.pop(repo_id, None)
    setup_download._cancelled.discard(repo_id)


@pytest.mark.asyncio
async def test_remote_download_reuses_installer_and_pipes_fake_progress(monkeypatch):
    """Offline producer: no Hub access, while exercising the real listener path."""
    from api.routers.setup import download as setup_download
    from utils import download_aggregator
    from utils import hf_progress

    calls = []

    async def fake_install(req):
        calls.append((req.repo_id, req.target))
        hf_progress.emit({
            "repo_id": req.repo_id, "phase": "aggregate",
            "bytes_done": 5, "total_bytes": 10,
        })
        hf_progress.emit({"repo_id": req.repo_id, "phase": "install_done"})
        return {"status": "install_started"}

    monkeypatch.setattr(setup_download, "install_model", fake_install)
    monkeypatch.setattr(hf_progress, "install", lambda: None)
    monkeypatch.setattr(download_aggregator, "install", lambda: None)
    client = _client(list)

    await asyncio.wait_for(
        client._install_catalog_repo("k2-fsa/OmniVoice"), timeout=HANG_GUARD_S
    )

    assert calls == [("k2-fsa/OmniVoice", "local")]
    first = await asyncio.wait_for(client._outbox.get(), timeout=HANG_GUARD_S)
    second = await asyncio.wait_for(client._outbox.get(), timeout=HANG_GUARD_S)
    assert first.WhichOneof("payload") == "download_progress"
    assert '"phase":"aggregate"' in first.download_progress.event_json
    assert second.WhichOneof("payload") == "download_progress"


@pytest.mark.asyncio
async def test_remote_download_without_terminal_progress_times_out(monkeypatch):
    from api.routers.setup import download as setup_download
    from utils import download_aggregator, hf_progress

    repo_id = "k2-fsa/OmniVoice"
    finished = asyncio.Event()

    async def background_install():
        while repo_id not in setup_download._cancelled:
            await asyncio.sleep(0)
        finished.set()

    async def fake_install(req):
        task = asyncio.create_task(background_install())
        setup_download._install_tasks.add(task)
        setup_download._install_tasks_by_repo[req.repo_id] = task

        def retire(completed):
            setup_download._install_tasks.discard(completed)
            setup_download._install_tasks_by_repo.pop(req.repo_id, None)

        task.add_done_callback(retire)
        return {"status": "install_started", "repo_id": req.repo_id}

    monkeypatch.setattr(setup_download, "install_model", fake_install)
    monkeypatch.setattr(hf_progress, "install", lambda: None)
    monkeypatch.setattr(download_aggregator, "install", lambda: None)
    client = _client(lambda: [])
    monkeypatch.setitem(
        client._install_catalog_repo.__func__.__globals__,
        "_FALLBACK_MODEL_LOAD_SECONDS",
        0.01,
    )

    with pytest.raises(TimeoutError):
        await client._install_catalog_repo(repo_id)

    assert finished.is_set()
    assert repo_id not in setup_download._install_tasks_by_repo
    assert repo_id not in setup_download._cancelled


@pytest.mark.asyncio
async def test_control_plane_stamps_authenticated_target_on_progress(monkeypatch):
    from services import gpu_gateway
    from utils import hf_progress

    monkeypatch.setattr(gpu_gateway, "_remote_downloads", {})
    events = []
    listener_id = hf_progress.register_listener(events.append)
    try:
        session = type("Session", (), {"worker_id": "gpu2", "revoked": False})()
        servicer = object.__new__(WorkerServicer)
        servicer._sessions = {"gpu2": session}
        await WorkerServicer._handle(
            servicer,
            session,
            pb.WorkerMessage(download_progress=pb.DownloadProgress(
                event_json=json.dumps({
                    "repo_id": "k2-fsa/OmniVoice", "target": "forged",
                    "phase": "aggregate", "bytes_done": 5, "total_bytes": 10,
                })
            )),
        )
    finally:
        hf_progress.unregister_listener(listener_id)
    assert events[-1]["target"] == "gpu2"
    assert gpu_gateway.remote_download_jobs() == [{
        "repo_id": "k2-fsa/OmniVoice",
        "target": "gpu2",
        "state": "downloading",
        "phase": "aggregate",
        "bytes_done": 5,
        "total_bytes": 10,
    }]


@pytest.mark.asyncio
async def test_blocked_telemetry_does_not_block_drain_stop_or_duplicate_on_reconnect(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def sample():
        calls.append(threading.current_thread())
        started.set()
        release.wait(BARRIER_WATCHDOG_S)
        return 1.0, 2, 3.0

    monkeypatch.setattr(client_module, "_heartbeat_resources", sample)
    client = _client(lambda: [])
    await client._refresh_telemetry()
    await asyncio.wait_for(asyncio.to_thread(started.wait), HANG_GUARD_S)
    probe = client._telemetry_task
    try:
        client._draining = True
        client._maybe_finish_drain()
        assert client._reconnect_requested.is_set()
        await asyncio.wait_for(client._cancel_active_work(), HANG_GUARD_S)
        client._accepting_assignments = True
        await client._refresh_telemetry()
        assert client._telemetry_task is probe
        assert len(calls) == 1
        assert calls[0].daemon
        await asyncio.wait_for(client.stop(), HANG_GUARD_S)
        await client._refresh_telemetry()
        assert client._telemetry_task is probe
        assert not client._maintenance
    finally:
        release.set()
        await asyncio.wait_for(probe, HANG_GUARD_S)
