"""Failure paths must not claim state transitions that did not complete."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_partial_download_cleanup_blocks_unsafe_retry(monkeypatch, tmp_path):
    pipeline = importlib.import_module("services.dub_pipeline")
    stale = tmp_path / "original.partial"
    stale.write_bytes(b"partial")
    monkeypatch.setattr("glob.glob", lambda _pattern: [str(stale)])
    monkeypatch.setattr(
        pipeline.os,
        "remove",
        lambda _path: (_ for _ in ()).throw(PermissionError("/secret/video")),
    )

    with pytest.raises(RuntimeError) as caught:
        pipeline._cleanup_partial_download(str(tmp_path))

    assert "prepare the video download retry" in str(caught.value)
    assert "/secret/video" not in str(caught.value)


def test_mcp_transport_path_failure_stops_server_creation(monkeypatch):
    mcp_server = importlib.import_module("mcp_server")

    class RejectPath:
        transport_security = SimpleNamespace(allowed_hosts=[])

        def __setattr__(self, name, value):
            if name == "streamable_http_path":
                raise RuntimeError("/secret/sdk/path")
            object.__setattr__(self, name, value)

    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            self.settings = RejectPath()

    monkeypatch.setattr(mcp_server, "_ensure_mcp", lambda: FakeFastMCP)
    with pytest.raises(RuntimeError) as caught:
        mcp_server.create_mcp_server()
    assert str(caught.value) == "MCP transport could not be configured."


def test_endpoint_preference_failure_is_fail_closed_to_manual(monkeypatch):
    endpoint_race = importlib.import_module("services.endpoint_race")
    prefs = importlib.import_module("core.prefs")
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("OMNIVOICE_HF_ENDPOINT_MODE", raising=False)
    monkeypatch.setattr(endpoint_race, "explicit_endpoint", lambda: "")
    monkeypatch.setattr(prefs, "get", lambda *_args: (_ for _ in ()).throw(OSError("secret")))
    assert endpoint_race.mode() == "manual"


@pytest.mark.asyncio
async def test_sherpa_ready_delivery_failure_does_not_report_loaded(monkeypatch):
    capture_ws = importlib.import_module("api.routers.capture_ws")
    sherpa = importlib.import_module("services.sherpa_dictation")
    monkeypatch.setattr(sherpa, "is_installed", lambda _spec: True)

    class Socket:
        calls = 0

        async def send_json(self, _payload):
            self.calls += 1
            if self.calls == 2:
                raise ConnectionError("secret socket")

    backend = SimpleNamespace(ensure_loaded=lambda: None)
    assert await capture_ws._sherpa_load_with_status(Socket(), backend, SimpleNamespace(id="test")) is False


def test_audio_validation_failure_does_not_return_unchecked_audio(monkeypatch):
    generation = importlib.import_module("api.routers.generation")
    torch = importlib.import_module("torch")
    audio = torch.tensor([float("nan")])
    monkeypatch.setattr(torch, "isfinite", lambda _audio: (_ for _ in ()).throw(RuntimeError("secret")))
    with pytest.raises(RuntimeError) as caught:
        generation._sanitize_audio(audio)
    assert str(caught.value) == "Generated audio could not be validated. Retry the generation."


@pytest.mark.asyncio
async def test_log_stream_start_failure_returns_stable_error(monkeypatch, tmp_path):
    system = importlib.import_module("api.routers.system")
    log = tmp_path / "private.log"
    log.write_text("old secret\n", encoding="utf-8")
    monkeypatch.setattr(system, "LOG_PATH", str(log))
    monkeypatch.setattr(system.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError(str(log))))
    with pytest.raises(HTTPException) as caught:
        await system.stream_logs(source="backend", interval=1.0)
    assert caught.value.status_code == 503
    assert str(log) not in caught.value.detail


@pytest.mark.asyncio
async def test_log_clear_does_not_hide_notification_reset_failure(monkeypatch, tmp_path):
    system = importlib.import_module("api.routers.system")
    log = tmp_path / "backend.log"
    log.write_text("data", encoding="utf-8")
    monkeypatch.setattr(system, "LOG_PATH", str(log))
    monkeypatch.setattr(system, "CRASH_LOG_PATH", str(tmp_path / "missing.log"))
    monkeypatch.setattr(
        system,
        "prefs_delete",
        lambda _key: (_ for _ in ()).throw(OSError("/secret/prefs")),
    )
    with pytest.raises(HTTPException) as caught:
        await system.clear_system_logs()
    assert caught.value.status_code == 500
    assert "/secret/prefs" not in caught.value.detail


def test_tailscale_disable_reports_off_failure_without_raw_output(monkeypatch):
    tailscale = importlib.import_module("services.tailscale")
    monkeypatch.setattr(tailscale, "_cli", lambda: "/usr/bin/tailscale")
    target = f"http://127.0.0.1:{tailscale.network_share.backend_port()}"
    monkeypatch.setattr(
        tailscale, "_serve_ports", lambda _cli: {10000: {"scheme": "https", "proxies": {target}}}
    )
    monkeypatch.setattr(tailscale, "_run", lambda _args: {"ok": False, "error": "/secret/stderr"})
    result = tailscale.serve_disable()
    assert result["ok"] is False
    assert "/secret/stderr" not in result["error"]


@pytest.mark.asyncio
async def test_network_disable_retains_enabled_state_when_listener_does_not_stop(monkeypatch):
    network_share = importlib.import_module("services.network_share")
    loop = asyncio.get_running_loop()
    task = loop.create_future()
    task.set_exception(PermissionError("secret listener"))
    server = SimpleNamespace(should_exit=False)
    state = network_share.ShareState(True, 3901, "123456", ["192.0.2.1"])
    app = SimpleNamespace(state=SimpleNamespace(network_share=state))
    monkeypatch.setattr(network_share._runtime, "server", server)
    monkeypatch.setattr(network_share._runtime, "task", task)
    monkeypatch.setattr(network_share._runtime, "state", state)

    with pytest.raises(RuntimeError) as caught:
        await network_share.disable(app)

    assert str(caught.value) == "LAN sharing could not be disabled. Retry after active connections close."
    assert network_share.get_state() is state
    assert app.state.network_share is state


def test_dub_abort_failure_keeps_job_retryable(monkeypatch):
    dub_core = importlib.import_module("api.routers.dub_core")
    job = {"id": "job-1"}
    monkeypatch.setitem(dub_core._dub_jobs, "job-1", job)
    monkeypatch.setattr(dub_core, "_kill_job_procs", lambda _job_id: None)
    monkeypatch.setattr(
        dub_core.task_manager,
        "cancel_task",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("secret task")),
    )
    with pytest.raises(HTTPException) as caught:
        dub_core.dub_abort("job-1")
    assert caught.value.status_code == 503
    assert "aborted" not in job
    assert "secret task" not in caught.value.detail


def test_run_sentinel_clear_failure_retains_ownership(monkeypatch):
    sentinel = importlib.import_module("core.run_sentinel")
    monkeypatch.setitem(sentinel._state, "owns", True)
    monkeypatch.setattr(
        sentinel.os,
        "remove",
        lambda _path: (_ for _ in ()).throw(PermissionError("secret sentinel")),
    )
    assert sentinel.clear_sentinel() is False
    assert sentinel._state["owns"] is True


def test_loaded_model_inventory_reports_degraded_source(monkeypatch, caplog):
    lifecycle = importlib.import_module("services.model_lifecycle")
    sidecars = importlib.import_module("services.subprocess_backend")
    monkeypatch.setattr(sidecars, "list_live_sidecars", lambda: (_ for _ in ()).throw(RuntimeError("/secret/model")))
    result = lifecycle.list_loaded()
    assert "sidecars" in result["degraded_sources"]
    assert "/secret/model" not in caplog.text


def test_configured_endpoint_failure_never_probes_official_host(monkeypatch):
    wizard = importlib.import_module("api.routers.setup.wizard")
    failure = importlib.import_module("core.failure")
    endpoint_race = importlib.import_module("services.endpoint_race")
    monkeypatch.setattr(endpoint_race, "mode", lambda: "manual")
    monkeypatch.setattr(failure, "configured_hf_mirror", lambda: (_ for _ in ()).throw(RuntimeError("/secret/config")))
    monkeypatch.setattr(wizard, "_probe_network", lambda *_a, **_k: pytest.fail("must not probe a fallback host"))
    result = wizard._network_check()
    assert result["status"] == "warn"
    assert "secret" not in result["detail"]


def test_persona_cleanup_reports_failure_without_path(monkeypatch, caplog):
    personas = importlib.import_module("api.routers.personas")
    monkeypatch.setattr(personas.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(personas.os, "remove", lambda _p: (_ for _ in ()).throw(PermissionError("/secret/persona")))
    assert personas._cleanup(["/secret/persona"]) is False
    assert "/secret/persona" not in caplog.text


def test_shared_voice_unload_failure_is_stable(monkeypatch):
    tts = importlib.import_module("services.tts_backend")
    manager = importlib.import_module("services.model_manager")
    backend = object.__new__(tts.OmniVoiceBackend)
    backend._model = object()
    monkeypatch.setattr(tts, "clear_clone_prompt_cache", lambda: None)
    monkeypatch.setattr(manager, "model", object())
    monkeypatch.setattr(manager, "free_vram", lambda: (_ for _ in ()).throw(RuntimeError("/secret/gpu")))
    with pytest.raises(RuntimeError) as caught:
        backend.unload()
    assert str(caught.value) == "The voice model was released, but the GPU memory cache could not be flushed."
    # The reference drop is the durable half and must survive a failing cache
    # flush (#1495). This used to assert the opposite — that a raising
    # free_vram() left the model bound for a retry — which was not a decision
    # but a consequence of freeing before clearing, the very ordering that let
    # a worker node hold 3.6 GB through an idle sweep.
    assert manager.model is None
    monkeypatch.setattr(manager, "free_vram", lambda: None)
    backend.unload()
    assert manager.model is None


@pytest.mark.asyncio
async def test_terminal_network_start_failure_resets_state(monkeypatch):
    network_share = importlib.import_module("services.network_share")
    task = asyncio.get_running_loop().create_future()
    task.set_exception(RuntimeError("/secret/listener"))
    server = SimpleNamespace(started=False, should_exit=False, serve=lambda: None)
    monkeypatch.setattr(network_share, "_find_free_port", lambda _base: 3901)
    monkeypatch.setattr(network_share, "_gen_pin", lambda: "123456")
    monkeypatch.setattr(network_share.uvicorn, "Server", lambda _config: server)
    monkeypatch.setattr(network_share.asyncio, "create_task", lambda _coro: task)
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(network_share.asyncio, "sleep", no_sleep)
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(RuntimeError) as caught:
        await network_share.enable(app)
    assert "secret" not in str(caught.value)
    assert network_share.get_state().enabled is False
    assert network_share._runtime.task is None


@pytest.mark.asyncio
async def test_live_network_start_cleanup_can_be_retried_by_disable(monkeypatch):
    network_share = importlib.import_module("services.network_share")
    task = asyncio.get_running_loop().create_future()
    server = SimpleNamespace(started=False, should_exit=False, serve=lambda: None)
    monkeypatch.setattr(network_share, "_find_free_port", lambda _base: 3901)
    monkeypatch.setattr(network_share, "_gen_pin", lambda: "123456")
    monkeypatch.setattr(network_share, "lan_ipv4_addresses", lambda: [])
    monkeypatch.setattr(network_share.uvicorn, "Server", lambda _config: server)
    monkeypatch.setattr(network_share.asyncio, "create_task", lambda _coro: task)
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(network_share.asyncio, "sleep", no_sleep)
    calls = 0
    async def retryable_wait(_awaitable, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.TimeoutError
        return None
    monkeypatch.setattr(network_share.asyncio, "wait_for", retryable_wait)
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(RuntimeError):
        await network_share.enable(app)
    assert network_share.get_state().enabled is True
    await network_share.disable(app)
    assert network_share.get_state().enabled is False


@pytest.mark.asyncio
async def test_cancelled_terminal_network_start_resets_state(monkeypatch):
    network_share = importlib.import_module("services.network_share")
    task = asyncio.get_running_loop().create_future()
    task.cancel()
    server = SimpleNamespace(started=False, should_exit=False, serve=lambda: None)
    monkeypatch.setattr(network_share, "_find_free_port", lambda _base: 3901)
    monkeypatch.setattr(network_share.uvicorn, "Server", lambda _config: server)
    monkeypatch.setattr(network_share.asyncio, "create_task", lambda _coro: task)
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(network_share.asyncio, "sleep", no_sleep)
    app = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(asyncio.CancelledError):
        await network_share.enable(app)
    assert network_share.get_state().enabled is False
    assert network_share._runtime.server is None
    assert network_share._runtime.task is None
    assert app.state.network_share.enabled is False


def test_dub_abort_false_result_stays_retryable(monkeypatch):
    dub_core = importlib.import_module("api.routers.dub_core")
    job = {"id": "job-false"}
    monkeypatch.setitem(dub_core._dub_jobs, "job-false", job)
    monkeypatch.setattr(dub_core, "_kill_job_procs", lambda _job_id: None)
    monkeypatch.setattr(dub_core.task_manager, "cancel_task", lambda _job_id: False)
    result = dub_core.dub_abort("job-false")
    assert result == {
        "aborted": True,
        "had_active_procs": False,
        "had_active_task": False,
    }
    assert job["aborted"] is True


def test_endpoint_pref_read_failure_keeps_manual_mode(monkeypatch):
    endpoint_race = importlib.import_module("services.endpoint_race")
    prefs = importlib.import_module("core.prefs")
    calls = iter([OSError("/secret/pref"), "auto"])
    def read(_key, _default=""):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setattr(prefs, "get", read)
    assert endpoint_race.mode() == "manual"
