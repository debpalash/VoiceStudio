"""A model load interrupted by interpreter/app shutdown must be recognised as
a benign teardown, not logged as a crash (#1174).

When the backend is torn down mid-load (SIGTERM from the desktop shell,
uvicorn stopping, a failed port bind, or the user closing the app while the
model loads), transformers' materializer raises ``RuntimeError: cannot
schedule new futures after interpreter shutdown`` from its own thread pool.
That used to surface as a scary "Model loading failed" error + full traceback
in the crash report — and on shutdown paths where the error escapes the serve
stack, a nonzero exit code that the desktop shell toasts as "the backend
crashed". `_is_interpreter_shutdown_error` classifies it so the loader
converts it to :class:`ModelLoadInterruptedByShutdown` (INFO, no traceback)
instead. See model_manager.py and main.py's lifespan wiring
(``begin_shutdown``/``reset_shutdown_flag``).
"""
import asyncio
import contextlib
import logging
import os
import sys
import threading
import types

import pytest

import services.model_manager as mm
from services.model_manager import (
    ModelLoadInterruptedByShutdown,
    _is_interpreter_shutdown_error,
)

_PURGED_PREFIXES = ("core.", "api.", "services.")
_PURGED_NAMES = ("main", "core", "api", "services")


@contextlib.contextmanager
def _reimported_backend_modules():
    """Run the block against FRESHLY imported ``main``/``core``/``api``/
    ``services`` modules, then put the originals back.

    The module-level ``import services.model_manager as mm`` above binds at
    COLLECTION time. ``tests/backend/conftest.py`` purges exactly these names
    from ``sys.modules`` after every test it owns, so in a combined
    ``pytest tests/ backend/tests/`` run a later ``import main`` here builds its
    lifespan on a NEW ``services.model_manager`` object — and the ``mm`` alias
    is a stale copy whose globals nothing reads. ``monkeypatch.setattr(mm, ...)``
    then patched nothing: the real ``preload_model`` ran, found no fake loader,
    and the test failed on ``assert started.is_set()`` while passing alone
    (#1269 residual).

    Tests that only exercise model_manager stay self-consistent on the alias.
    Any test that patches model_manager and then drives *main* must resolve the
    live module — this context manager makes that path deterministic in
    isolation, so the bug reproduces without needing the sibling suite.
    """
    saved = {
        name: mod for name, mod in sys.modules.items()
        if name in _PURGED_NAMES or name.startswith(_PURGED_PREFIXES)
    }
    def _purge():
        for name in [n for n in sys.modules
                     if n in _PURGED_NAMES or n.startswith(_PURGED_PREFIXES)]:
            sys.modules.pop(name, None)
    _purge()
    try:
        yield
    finally:
        # Drop whatever the block imported, then restore the originals — a
        # half-restored tree (fresh submodule under a stale package) is exactly
        # the split-import hazard this guards against.
        _purge()
        sys.modules.update(saved)


@pytest.fixture(autouse=True)
def _fresh_shutdown_flag():
    """The shutting-down flag is process-global; never leak it across tests."""
    mm.reset_shutdown_flag()
    yield
    mm.reset_shutdown_flag()


@pytest.fixture
def _loading_detail_guard():
    """Snapshot/restore the module-global loading-detail dict."""
    before = dict(mm._loading_detail)
    yield mm._loading_detail
    mm._loading_detail.clear()
    mm._loading_detail.update(before)


def test_direct_interpreter_shutdown_runtimeerror():
    exc = RuntimeError("cannot schedule new futures after interpreter shutdown")
    assert _is_interpreter_shutdown_error(exc) is True


def test_shutdown_error_wrapped_in_cause_chain():
    # transformers wraps the original error several layers deep.
    root = RuntimeError("cannot schedule new futures after interpreter shutdown")
    try:
        try:
            raise root
        except RuntimeError as e:
            raise ImportError("Could not import module VoiceStudio") from e
    except ImportError as wrapped:
        assert _is_interpreter_shutdown_error(wrapped) is True


def test_shutdown_error_via_implicit_context():
    root = RuntimeError("cannot schedule new futures after interpreter shutdown")
    try:
        try:
            raise root
        except RuntimeError:
            raise ValueError("secondary")  # sets __context__, not __cause__
    except ValueError as chained:
        assert _is_interpreter_shutdown_error(chained) is True


def test_plain_pool_shutdown_is_not_interpreter_shutdown():
    # A single pool being reset ("after shutdown", no "interpreter") is a real
    # fault we must NOT silence.
    exc = RuntimeError("cannot schedule new futures after shutdown")
    assert _is_interpreter_shutdown_error(exc) is False


def test_unrelated_error_is_not_shutdown():
    assert _is_interpreter_shutdown_error(OSError("disk full")) is False
    assert _is_interpreter_shutdown_error(None) is False


def test_cause_cycle_terminates():
    # A self-referential cause chain must not loop forever.
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert _is_interpreter_shutdown_error(a) is False


def test_stringified_interpreter_shutdown_matches():
    """transformers ≥5 aggregates materializer-worker errors into NEW
    exceptions whose message embeds the original traceback as TEXT
    (log_conversion_errors → SkipParameters → summary raise): the type
    changes and the cause chain is severed. This was the classifier miss
    behind 'Model loading failed: cannot schedule new futures after
    interpreter shutdown' being logged as an ERROR during teardown (#1174)."""
    exc = ValueError(
        "Loading weights failed:\n"
        "Traceback (most recent call last):\n"
        '  File "core_model_loading.py", line 803, in spawn_materialize\n'
        "    return thread_pool.submit(_job)\n"
        "RuntimeError: cannot schedule new futures after interpreter shutdown\n"
        "Error: on tensors destined for llm.layers.0"
    )
    assert _is_interpreter_shutdown_error(exc) is True


def test_stringified_plain_pool_shutdown_still_not_matched():
    # The stringified match must not loosen the plain-pool case (#589 class).
    exc = ValueError("... RuntimeError: cannot schedule new futures after shutdown ...")
    assert _is_interpreter_shutdown_error(exc) is False


# ── _load_model_sync: benign cancelled-load conversion (#1174) ─────────────


def _stub_load(monkeypatch, error):
    """Wire _load_model_sync's collaborators so `_load()` raises `error`
    without importing torch or touching the network."""
    fake_torch = types.SimpleNamespace(float16="f16", float32="f32")

    class _FakeOV:
        @staticmethod
        def from_pretrained(*a, **k):
            raise error

    monkeypatch.setattr(mm, "_lazy_torch", lambda: fake_torch)
    monkeypatch.setattr(mm, "_lazy_omnivoice", lambda: _FakeOV)
    monkeypatch.setattr(mm, "get_best_device", lambda: "cpu")
    monkeypatch.setattr(mm, "should_preload_tts_asr", lambda: False)


def test_load_sync_converts_interpreter_shutdown_to_benign(
    monkeypatch, caplog, _loading_detail_guard
):
    """Fail-before/pass-after (#1174): the raw RuntimeError used to escape
    _load_model_sync (re-raised), reaching whichever teardown machinery ran
    next — traceback noise and, on some shutdown paths, a nonzero exit."""
    _stub_load(
        monkeypatch,
        RuntimeError("cannot schedule new futures after interpreter shutdown"),
    )
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        with pytest.raises(ModelLoadInterruptedByShutdown):
            mm._load_model_sync()
    # Benign: no ERROR record, no /model/status phantom error.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert mm._loading_detail.get("error") is None


def test_load_sync_converts_wrapped_stringified_error(
    monkeypatch, caplog, _loading_detail_guard
):
    """The transformers-5 aggregated form (severed chain, non-RuntimeError)."""
    _stub_load(
        monkeypatch,
        OSError(
            "weights conversion failed: RuntimeError: cannot schedule new "
            "futures after interpreter shutdown"
        ),
    )
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        with pytest.raises(ModelLoadInterruptedByShutdown):
            mm._load_model_sync()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert mm._loading_detail.get("error") is None


def test_load_sync_plain_pool_shutdown_benign_only_during_app_shutdown(
    monkeypatch, caplog, _loading_detail_guard
):
    """Once the lifespan flipped begin_shutdown(), even the plain single-pool
    rejection is benign — our own _reset_gpu_pool() caused it."""
    _stub_load(monkeypatch, RuntimeError("cannot schedule new futures after shutdown"))
    mm.begin_shutdown()
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        with pytest.raises(ModelLoadInterruptedByShutdown):
            mm._load_model_sync()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert mm._loading_detail.get("error") is None


def test_load_sync_plain_pool_shutdown_stays_loud_outside_shutdown(
    monkeypatch, caplog, _loading_detail_guard
):
    """Outside app shutdown the plain-pool rejection is the #589-class real
    fault: it must keep the ERROR log + /model/status error."""
    _stub_load(monkeypatch, RuntimeError("cannot schedule new futures after shutdown"))
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        with pytest.raises(RuntimeError):
            mm._load_model_sync()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert mm._loading_detail.get("error")


def test_load_sync_real_failure_stays_loud(monkeypatch, caplog, _loading_detail_guard):
    _stub_load(monkeypatch, ValueError("boom"))
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        with pytest.raises(ValueError):
            mm._load_model_sync()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert mm._loading_detail.get("error")


def test_load_sync_bails_before_torch_when_already_shutting_down(monkeypatch):
    """A load that reaches the pool AFTER shutdown began (queued behind a
    warmup on a 1-worker pool) must not start a multi-GB import/load."""

    def _must_not_import():
        raise AssertionError("torch must not be imported during shutdown")

    monkeypatch.setattr(mm, "_lazy_torch", _must_not_import)
    mm.begin_shutdown()
    with pytest.raises(ModelLoadInterruptedByShutdown):
        mm._load_model_sync()


# ── preload_model: shutdown-interrupted preload is INFO, not WARNING ──────


def test_preload_interrupted_by_shutdown_logs_info_only(monkeypatch, caplog):
    from core import device_caps
    # Exercise native preload even on hosts that normally route TTS to a sidecar.
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: types.SimpleNamespace(family="cpu"))
    async def _boom():
        raise ModelLoadInterruptedByShutdown("shutdown during load")

    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock())
    monkeypatch.setattr(mm, "_headless_worker", lambda: False)
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda c: True)
    monkeypatch.setattr(mm, "_load_model_with_timeout", _boom)
    with caplog.at_level(logging.INFO, logger="omnivoice.model"):
        asyncio.run(mm.preload_model())
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("shutdown during load" in r.getMessage() for r in caplog.records)
    assert mm.model is None


# ── lifespan shutdown ordering + run-sentinel interaction (#1174/#1164) ────


def test_cancel_and_await_tasks_swallows_task_errors():
    """A background task dying with a real error during teardown must not
    abort the lifespan shutdown: uvicorn would mark the application shutdown
    failed and the process exits crash-shaped for a deliberate SIGTERM."""
    import main as main_mod

    async def scenario():
        async def boom():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise RuntimeError("cannot schedule new futures after shutdown")

        t = asyncio.create_task(boom())
        await asyncio.sleep(0)  # let it start
        await main_mod._cancel_and_await_tasks(t, None, timeout=2.0)

    asyncio.run(scenario())  # must not raise


def test_lifespan_shutdown_mid_load_is_clean_and_clears_sentinel(
    monkeypatch, tmp_path
):
    """SIGTERM (lifespan shutdown) while a model load is in flight on a
    GPU-pool thread: the shutdown must complete, flip the model_manager into
    shutdown mode, and clear the run sentinel — a deliberate quit mid-load
    must NEVER be recorded as an unclean crash by the next startup (#1164
    interaction the #1174 fix has to preserve)."""
    from fastapi import FastAPI

    with _reimported_backend_modules():
        import main as main_mod
        from core import run_sentinel
        from core import device_caps
        monkeypatch.setattr(device_caps, "detect_host_caps", lambda: types.SimpleNamespace(family="cpu"))

        # The module object main's lifespan actually loads through — NOT the
        # module-level `mm` alias, which a sibling suite's sys.modules purge can
        # leave stale (see _reimported_backend_modules).
        live_mm = sys.modules["services.model_manager"]
        monkeypatch.setattr(live_mm, "_headless_worker", lambda: False)

        monkeypatch.setattr(run_sentinel, "SENTINEL_PATH", str(tmp_path / "run_sentinel.json"))
        monkeypatch.setattr(run_sentinel, "CRASH_RECORD_PATH", str(tmp_path / "last_run_crash.json"))
        monkeypatch.setattr(run_sentinel, "LOG_PATH", str(tmp_path / "omnivoice.log"))
        run_sentinel._reset_for_tests()

        fake_torch = types.SimpleNamespace(
            float16="f16",
            cuda=types.SimpleNamespace(is_available=lambda: False),
            backends=types.SimpleNamespace(),
        )
        monkeypatch.setattr(live_mm, "_lazy_torch", lambda: fake_torch)
        monkeypatch.setattr(live_mm, "model", None)
        monkeypatch.setattr(live_mm, "_model_lock", asyncio.Lock())
        monkeypatch.setattr(live_mm, "_checkpoint_in_local_cache", lambda c: True)
        monkeypatch.setenv("OMNIVOICE_PRELOAD_CAPTURE_ASR", "0")
        # A fresh import inherits nothing from the previous lifespan, but an
        # in-place one would: arm loads explicitly so a leaked shutdown flag
        # can't make the preload bail before it starts.
        live_mm.reset_shutdown_flag()

        started = threading.Event()
        release = threading.Event()

        def _wedged_load():
            started.set()
            release.wait(30)
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

        async def _fake_load_with_timeout():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(live_mm._get_gpu_pool(), _wedged_load)

        monkeypatch.setattr(live_mm, "_load_model_with_timeout", _fake_load_with_timeout)

        async def scenario():
            app = FastAPI()
            async with main_mod.lifespan(app):
                # The preload's load really is in flight on a pool thread. Poll
                # asynchronously — a blocking Event.wait would starve the loop the
                # preload task needs to reach run_in_executor.
                for _ in range(200):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.05)
                assert started.is_set()
            # Lifespan shutdown completed while that thread was still wedged.

        try:
            asyncio.run(scenario())
            # The clean shutdown retired the sentinel…
            assert not os.path.exists(run_sentinel.SENTINEL_PATH)
            # …so the next startup must NOT see a crash.
            assert run_sentinel.detect_unclean_shutdown() is None
            # And the model_manager was flipped into shutdown mode first, so the
            # wedged load classifies executor rejections as benign.
            assert live_mm.is_shutting_down() is True
        finally:
            release.set()
            run_sentinel._reset_for_tests()


def test_request_during_shutdown_gets_503_not_a_crash_shaped_500():
    """A user-initiated request that triggers a load while the backend is
    shutting down must answer 503, not 500 (#1276).

    #1174 made this benign for the background preload, but a request took the
    generic unhandled-exception path: crash log, ERROR traceback, and an
    error-journal entry that feeds the bug-report pipeline. Quitting the app
    with a generate queued therefore surfaced "500 Internal Server Error:
    model load skipped: backend shutting down" and offered to file a bug for
    a normal teardown.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod

    app = FastAPI()

    @app.get("/boom")
    async def _boom():
        raise ModelLoadInterruptedByShutdown("model load skipped: backend shutting down")

    app.add_exception_handler(Exception, main_mod.global_exception_handler)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/boom")

    assert resp.status_code == 503, resp.status_code
    # Answer a shutting-down server owes a client, so the UI can retry rather
    # than treat it as a fault.
    assert resp.headers.get("Retry-After") == "5"
    detail = resp.json()["detail"]
    # Cross-layer contract: the UI keys off this marker to drop the "Report"
    # action (utils/errorToast.jsx). It must NOT key off the 503 status alone —
    # a real engine-load timeout and an unavailable engine are 503 too, and
    # those are reportable bugs. Renaming this marker breaks that; keep in sync.
    assert "[shutting_down]" in detail
    # Actionable, and free of the internal phrasing that read as a crash.
    assert "shutting down" in detail
    assert "Reopen the app" in detail
    assert "Internal Server Error" not in detail


def test_shutdown_request_is_not_written_to_the_crash_log_or_journal(tmp_path, monkeypatch):
    """The same teardown must leave no crash-log entry and no journal record —
    those are what the auto bug reporter reads (#1276)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod
    from core import error_journal

    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_mod, "CRASH_LOG_PATH", str(crash_log))

    recorded = []
    monkeypatch.setattr(
        error_journal, "record", lambda *a, **k: recorded.append(a) or {}
    )

    app = FastAPI()

    @app.get("/boom")
    async def _boom():
        raise ModelLoadInterruptedByShutdown("model load skipped: backend shutting down")

    app.add_exception_handler(Exception, main_mod.global_exception_handler)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 503

    assert not crash_log.exists(), crash_log.read_text()
    assert recorded == []


def test_shutdown_class_is_matched_across_duplicate_imports():
    """The handler must recognise the shutdown class even when it arrives from
    a second copy of the module.

    ``services.model_manager`` gets imported under more than one module name
    depending on which sys.path root is active (and in the frozen build), so
    ``ModelLoadInterruptedByShutdown`` can exist as two distinct class objects.
    A bare ``isinstance`` silently fails there and the user is back to a 500 —
    which is exactly what happened when both test suites ran in one session.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import main as main_mod

    # A same-named class from a *different* module object — what a duplicate
    # import produces.
    Impostor = type(
        "ModelLoadInterruptedByShutdown", (RuntimeError,), {"__module__": "other.copy"}
    )
    assert not isinstance(Impostor("x"), ModelLoadInterruptedByShutdown)

    app = FastAPI()

    @app.get("/boom")
    async def _boom():
        raise Impostor("model load skipped: backend shutting down")

    app.add_exception_handler(Exception, main_mod.global_exception_handler)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 503
