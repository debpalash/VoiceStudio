"""Real startup, install-worker and GPU-owner boundaries from review5911468694."""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401
from test_model_preload_diagnostic_owner import preload  # noqa: F401


@pytest.fixture(autouse=True)
def no_app_intake(monkeypatch):
    """These local diagnostic assertions must never select build credentials."""
    transport = importlib.import_module("core.diagnostic_app_transport")
    monkeypatch.setattr(transport, "app_destination", lambda: None)


@pytest.mark.parametrize("change", ["off_to_on", "regrant", "same", "withdraw", "cancel"])
def test_startup_tts_wait_owns_permission_before_task_submission(
    reporter, preload, monkeypatch, change,
):
    import main
    from core import diagnostic_reports

    if change != "off_to_on":
        reporter.consent(True)
    original = diagnostic_reports.observe_grant()
    monkeypatch.setattr(main, "preload_model", preload.preload_model)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        leaf_calls = []

        async def reconcile(_app):
            entered.set()
            await release.wait()

        async def failing_load():
            leaf_calls.append(diagnostic_reports.inherited_grant())
            raise ImportError("private model path")

        monkeypatch.setattr(main, "_await_profile_reconcile", reconcile)
        monkeypatch.setattr(preload, "_load_model_with_timeout", failing_load)
        app = SimpleNamespace(state=SimpleNamespace())
        # This is the actual installed task factory, not a synthetic attribute.
        async with main._diagnostic_lifespan():
            main._start_tts_preload(app)
            task = app.state.preload_task
            assert task._voicestudio_diagnostic_epoch == original
            await asyncio.wait_for(entered.wait(), 2)
            if change in {"off_to_on", "regrant"}:
                reporter.consent(False)
                reporter.consent(True)
            elif change == "withdraw":
                reporter.consent(False)
            elif change == "cancel":
                task.cancel()
            release.set()
            if change == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert leaf_calls == []
            else:
                await task
                assert len(leaf_calls) == 1
            saved = records(reporter)
            assert len(saved) == (1 if change == "same" else 0)
            if change in {"off_to_on", "regrant"}:
                # A genuinely new startup unit can use the current grant.
                main._start_tts_preload(app)
                await app.state.preload_task
                fresh = records(reporter)
                assert len(fresh) == 1
                assert leaf_calls[-1] == reporter.epoch
                assert fresh[0]["props"]["category"] == "model_load_failed"

    asyncio.run(scenario())


@pytest.mark.parametrize("inherited_off", [False, True])
def test_startup_tts_binds_before_the_task_first_executes(
    reporter, preload, monkeypatch, inherited_off,
):
    import main
    from core import diagnostic_reports

    if inherited_off:
        reporter.consent(True)
    monkeypatch.setattr(main, "preload_model", preload.preload_model)

    async def scenario():
        seen = []

        async def reconcile(_app):
            pass

        async def failing_load():
            seen.append(diagnostic_reports.inherited_grant())
            raise ImportError("private model path")

        monkeypatch.setattr(main, "_await_profile_reconcile", reconcile)
        monkeypatch.setattr(preload, "_load_model_with_timeout", failing_load)
        app = SimpleNamespace(state=SimpleNamespace())
        async with main._diagnostic_lifespan():
            with diagnostic_reports.grant_scope(None):
                main._start_tts_preload(app)
            if not inherited_off:
                reporter.consent(True)
            await app.state.preload_task
            assert seen == [None]
            assert records(reporter) == []
            main._start_tts_preload(app)
            await app.state.preload_task
            assert seen[-1] == reporter.epoch
            assert len(records(reporter)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["prewarm", "run"])
@pytest.mark.parametrize("failure", ["compute", "network_timeout", "connection", "busy", "cancel"])
def test_gpu_deadline_is_compute_failure_and_transport_errors_keep_their_classification(
    reporter, monkeypatch, entry, failure,
):
    from services import gpu_gateway, model_manager

    reporter.consent(True)
    errors = {
        "compute": model_manager.GpuJobTimeoutError,
        "network_timeout": TimeoutError,
        "connection": ConnectionError,
        "busy": model_manager.GpuPoolBusyError,
        "cancel": asyncio.CancelledError,
    }
    error = errors[failure]("private operation detail")
    calls = []

    async def guarded(fn, **kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(model_manager, "run_on_gpu_pool_guarded", guarded)

    async def scenario():
        if entry == "prewarm":
            await gpu_gateway.prewarm(backend=SimpleNamespace(ensure_ready=lambda: None), engine="omnivoice", timeout=1)
        else:
            await gpu_gateway.run(local=gpu_gateway.LocalCall(fn=lambda: None, timeout=1))

    expected = gpu_gateway.ModelLoadTimeout if entry == "prewarm" and failure in {"compute", "network_timeout"} else type(error)
    with pytest.raises(expected) as caught:
        asyncio.run(scenario())
    assert calls, "The real guarded GPU boundary must execute"
    if expected is gpu_gateway.ModelLoadTimeout:
        assert caught.value.__cause__ is error
    saved = records(reporter)
    if failure in {"busy", "cancel"}:
        assert saved == []
    else:
        assert len(saved) == 1
        assert saved[0]["props"]["error_code"] == ("GPU_JOB_TIMEOUT" if failure == "compute" else "NETWORK_ERROR")
        assert saved[0]["props"]["category"] == ("model_load_failed" if entry == "prewarm" else "engine_failed")
        assert "private operation detail" not in str(saved)


@pytest.mark.parametrize("change", ["same", "off_to_on", "regrant", "cancel"])
def test_disk_preflight_reports_terminal_download_under_its_original_worker_grant(
    reporter, monkeypatch, change,
):
    import huggingface_hub
    from utils import hf_progress

    download = importlib.import_module("api.routers.setup.download")
    if change != "off_to_on":
        reporter.consent(True)
    for name in ("_active_installs", "_install_tasks", "_cancelled"):
        monkeypatch.setattr(download, name, set())
    for name in ("_install_failures", "_install_cooldowns", "_install_tasks_by_repo"):
        monkeypatch.setattr(download, name, {})
    monkeypatch.setattr(download, "_download_endpoint", lambda **_kwargs: None)
    monkeypatch.setattr(download, "apply_xet_env", lambda: None)
    monkeypatch.setattr(download, "compute_plan", lambda _files: {
        "total_bytes": 4096, "cached_bytes": 0, "to_download_bytes": 4096, "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda _bytes: "Not enough disk space: fixture needs 4096 bytes")
    repo = download.KNOWN_MODELS[0]["repo_id"]
    calls, events = [], []

    def snapshot(**kwargs):
        calls.append(kwargs)
        assert kwargs.get("dry_run"), "Disk refusal must never transfer model bytes"
        if change in {"off_to_on", "regrant"}:
            reporter.consent(False)
            reporter.consent(True)
        elif change == "cancel":
            download._cancelled.add(repo)
        return []

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    listener = hf_progress.register_listener(events.append)

    async def scenario():
        result = await download.install_model(download.InstallModelRequest(repo_id=repo))
        assert result["status"] == "install_started"
        await asyncio.gather(*list(download._install_tasks))

    try:
        asyncio.run(scenario())
    finally:
        hf_progress.unregister_listener(listener)
    assert calls, "The real preflight must execute"
    saved = records(reporter)
    assert len(saved) == (1 if change == "same" else 0)
    if change == "same":
        assert saved[0]["props"]["category"] == "download_failed"
        assert saved[0]["props"]["error_code"] == "DISK_FULL"
        assert download._install_failures[repo]["docs_topic"] == "DISK_SPACE_LOW"
    assert any(event.get("phase") == ("install_cancelled" if change == "cancel" else "install_error") for event in events)
    assert not download._active_installs
    assert not download._install_cooldowns


@pytest.mark.parametrize("entry", ["task", "preload", "capture"])
@pytest.mark.parametrize("kind", ["compute", "subclass", "spoof", "timeout", "connection"])
def test_local_compute_timeout_classification_is_central(
    reporter, preload, monkeypatch, entry, kind,
):
    from core import diagnostic_reports, tasks
    from services import model_manager

    class ComputeDeadline(model_manager.GpuJobTimeoutError):
        pass

    spoof = type("GpuJobTimeoutError", (TimeoutError,), {"__module__": "services.model_manager"})
    error = {"compute": model_manager.GpuJobTimeoutError, "subclass": ComputeDeadline,
             "spoof": spoof, "timeout": TimeoutError, "connection": ConnectionError}[kind]("private detail")
    reporter.consent(True)
    if entry == "capture":
        import builtins
        original_import = builtins.__import__

        def no_engine_import(name, *args, **kwargs):
            assert name not in {"services", "services.model_manager"}, "capture imported engine code"
            return original_import(name, *args, **kwargs)

        with monkeypatch.context() as capture_guard:
            capture_guard.setattr(builtins, "__import__", no_engine_import)
            assert diagnostic_reports.capture_failure("dub", error=error)
    elif entry == "preload":
        async def fail():
            raise error
        monkeypatch.setattr(preload, "_load_model_with_timeout", fail)
        asyncio.run(preload.preload_model())
    else:
        for name in ("create", "mark_running", "mark_failed", "mark_done", "mark_cancelled", "append_event"):
            monkeypatch.setattr(tasks.job_store, name, lambda *args, **kwargs: None)
        monkeypatch.setattr(tasks.run_sentinel, "touch_activity", lambda *args, **kwargs: None)

        async def run():
            async def fail():
                raise error
            manager = tasks.TaskManager()
            await manager.add_task("compute-timeout", "dub_generate", fail)
            worker = asyncio.create_task(manager.worker())
            try:
                await asyncio.wait_for(manager.queue.join(), 2)
                assert manager.active_tasks["compute-timeout"]["status"] == "failed"
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        asyncio.run(run())
    saved = records(reporter)
    assert len(saved) == 1
    assert saved[0]["props"]["error_type"] == ("ConnectionError" if kind == "connection" else "TimeoutError")
    assert saved[0]["props"]["error_code"] == ("GPU_JOB_TIMEOUT" if kind in {"compute", "subclass"} else "NETWORK_ERROR")
