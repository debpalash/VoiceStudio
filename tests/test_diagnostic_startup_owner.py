"""An eager ASGI startup exception is a current failure, even when propagated."""
import asyncio
import contextvars
import importlib
import json
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter  # noqa: F401


def test_failed_diagnostic_finalization_warns_without_private_error_text(monkeypatch):
    main = importlib.import_module('main')
    warnings = []
    monkeypatch.setattr(main.logger, 'warning', lambda message: warnings.append(message))
    def fail():
        raise PermissionError('PRIVATE_PATH_AND_CONTENT')
    asyncio.run(main._finalize_diagnostics(fail))
    assert warnings == ['Automatic failure-report cleanup could not be saved during shutdown.']


@pytest.mark.parametrize('authority', ['same', 'prior', 'off'])
def test_loop_task_factory_preserves_explicit_owned_context(reporter, monkeypatch, authority):
    main = importlib.import_module('main')
    reports = importlib.import_module('core.diagnostic_reports')
    monkeypatch.setattr(reports, 'initialize', lambda: None)
    monkeypatch.setattr(reports, 'shutdown', lambda: None)
    reporter.consent(authority != 'off')
    with reports.grant_scope(reports.observe_grant()):
        owned = contextvars.copy_context()
    if authority != 'same':
        reporter.consent(False)
        reporter.consent(True)
    async def run():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda *_: None)
        async with main._diagnostic_lifespan():
            async def fail():
                raise RuntimeError('PRIVATE_CONTEXT_ERROR')
            task = asyncio.create_task(fail(), context=owned)
            await asyncio.wait({task})
            loop.call_exception_handler({'exception': task.exception(), 'future': task})
            reporter._tick()
            assert len(list(reporter.directory.glob('*.json'))) == int(authority == 'same')
    asyncio.run(run())


@pytest.mark.parametrize('transition', ['off_to_on', 'off_and_regrant', 'same_grant'])
@pytest.mark.parametrize('custom_factory', [False, True])
def test_delayed_unretrieved_task_error_uses_its_own_grant(reporter, monkeypatch, transition, custom_factory):
    main = importlib.import_module('main')
    reports = importlib.import_module('core.diagnostic_reports')
    monkeypatch.setattr(reports, 'initialize', lambda: None)
    monkeypatch.setattr(reports, 'shutdown', lambda: None)
    reporter.consent(transition != 'off_to_on')
    async def run():
        loop = asyncio.get_running_loop()
        local, delegated = [], []
        def previous_handler(_loop, context):
            local.append(context)
        def previous_factory(loop, coro, **kwargs):
            delegated.append(True)
            return asyncio.Task(coro, loop=loop, **kwargs)
        prior = previous_factory if custom_factory else None
        loop.set_task_factory(prior)
        loop.set_exception_handler(previous_handler)
        async with main._diagnostic_lifespan():
            async def fail():
                raise RuntimeError('PRIVATE_DELAYED_FAILURE')
            task = asyncio.create_task(fail())
            await asyncio.wait({task})
            error = task.exception()
            if transition == 'off_to_on':
                reporter.consent(True)
            elif transition == 'off_and_regrant':
                reporter.consent(False)
                reporter.consent(True)
            # The loop may deliver an unretrieved-task context long after the
            # actual failure. Drive its real handler with that completed Task.
            loop.call_exception_handler({'exception': error, 'future': task})
            reporter._tick()
            saved = [json.loads(path.read_text(encoding='utf-8')) for path in reporter.directory.glob('*.json')]
            assert len(saved) == int(transition == 'same_grant')
            assert len(local) == 1
            assert 'PRIVATE_DELAYED_FAILURE' not in json.dumps(saved)
        assert loop.get_exception_handler() is previous_handler
        assert loop.get_task_factory() is prior
        if custom_factory:
            assert delegated
    asyncio.run(run())


def test_unknown_loop_failure_authority_remains_local_after_opt_in(reporter, monkeypatch):
    main = importlib.import_module('main')
    reports = importlib.import_module('core.diagnostic_reports')
    monkeypatch.setattr(reports, 'initialize', lambda: None)
    monkeypatch.setattr(reports, 'shutdown', lambda: None)
    async def run():
        loop = asyncio.get_running_loop()
        local = []
        loop.set_exception_handler(lambda _loop, context: local.append(context))
        error = RuntimeError('PRIVATE_UNKNOWN_FAILURE')
        async with main._diagnostic_lifespan():
            reporter.consent(True)
            loop.call_exception_handler({'exception': error})
            reporter._tick()
            assert not list(reporter.directory.glob('*.json'))
            assert len(local) == 1
    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_propagated_eager_startup_failure_is_reported_only_with_consent(reporter, monkeypatch, enabled):
    main = importlib.import_module("main")
    reports = importlib.import_module("core.diagnostic_reports")
    from core import run_sentinel
    reporter.consent(enabled)
    monkeypatch.setenv("OMNIVOICE_STARTUP_WATCHDOG_S", "0")
    monkeypatch.setattr(main, "_EAGER", True)
    monkeypatch.setattr(main, "_phase_a_build", lambda: None)
    monkeypatch.setattr(main, "_phase_a_finalize", lambda: None)
    monkeypatch.setattr(main, "_start_analytics_lifecycle", lambda *_: None)
    monkeypatch.setattr(run_sentinel, "detect_unclean_shutdown", lambda: None)
    monkeypatch.setattr(run_sentinel, "write_sentinel", lambda: None)
    async def failed(_app):
        raise RuntimeError("PRIVATE_STARTUP_CONTENT")
    monkeypatch.setattr(main, "_phase_b", failed)
    async def run():
        previous = lambda *_: None
        asyncio.get_running_loop().set_exception_handler(previous)
        with pytest.raises(RuntimeError, match="PRIVATE_STARTUP_CONTENT"):
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail("Failed startup must not yield")
        assert asyncio.get_running_loop().get_exception_handler() is previous
        assert reports._reporter is None
    asyncio.run(run())
    saved = [json.loads(path.read_text(encoding="utf-8")) for path in reporter.directory.glob("*.json")]
    assert len(saved) == int(enabled)
    if saved:
        assert saved[0]["props"]["category"] == "backend_crash"
        assert saved[0]["props"]["stage"] == "startup"
        assert saved[0]["props"]["reason"] == "launch_failed"
        assert "PRIVATE_STARTUP_CONTENT" not in json.dumps(saved)
