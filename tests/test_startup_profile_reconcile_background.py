"""Profile reconciliation probes every engine runtime (argostranslate alone is
8-54 s to import), so it must not hold /health at 503 during startup."""
import asyncio
import inspect
import os
import threading
from types import SimpleNamespace

import importlib

import main

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_phase_b_does_not_reconcile_inline():
    assert "reconcile_active_profile" not in inspect.getsource(main._phase_b)
    assert "_start_profile_reconcile(app)" in inspect.getsource(main._phase_b)


def test_warmups_wait_for_reconcile_in_background(monkeypatch):
    release = threading.Event()
    finished = []

    def slow_reconcile():
        release.wait(5)
        finished.append(True)
        return {"asr": {}}

    monkeypatch.setattr(importlib.import_module("services.performance_profiles"), "reconcile_active_profile", slow_reconcile)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_profile_reconcile(app)
        waiter = asyncio.create_task(main._await_profile_reconcile(app))
        await asyncio.sleep(0.05)
        # The caller (startup) got control back while reconcile is still running.
        assert not app.state.profile_reconcile_task.done()
        assert not waiter.done()
        release.set()
        await waiter
        assert finished == [True]

    asyncio.run(scenario())


def test_reconcile_failure_is_non_fatal_and_does_not_block_warmups(monkeypatch):
    def broken():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(importlib.import_module("services.performance_profiles"), "reconcile_active_profile", broken)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_profile_reconcile(app)
        await main._await_profile_reconcile(app)
        assert app.state.profile_reconcile_task.exception() is None
        assert app.state.profile_reconcile_worker.done()

    asyncio.run(scenario())


def test_await_without_a_task_returns_immediately():
    asyncio.run(main._await_profile_reconcile(SimpleNamespace(state=SimpleNamespace())))


def test_inventory_probe_does_not_import_engine_runtimes():
    """argostranslate takes 8-54 s to import; the inventory only needs to know
    whether it is installed."""
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, 'backend');"
        "from services import translation_engines as t;"
        "t.is_ready('argos', import_check=False);"
        "sys.exit(1 if 'argostranslate.translate' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=_REPO).returncode == 0


def test_import_check_default_still_imports(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "importlib.import_module", lambda name: seen.append(name) or object(),
    )
    from services import translation_engines as t

    entry = {"id": "x", "probe_module": "somepkg.sub"}
    assert t._probe(entry) == (True, None)
    assert seen == ["somepkg.sub"]
    assert t._probe({"id": "x", "probe_module": "definitely_not_installed_pkg"}, import_check=False)[0] is False


def test_eager_boots_keep_the_classic_synchronous_startup():
    """Engine probes read at lifespan open must not race a reconcile that is
    importing the same runtimes (tests/probe/test_probe_engines.py)."""
    source = inspect.getsource(main._phase_b)
    guarded = source.split("if _EAGER:", 1)[1].split("\n\n", 1)[0]
    assert "_await_profile_reconcile(app)" in guarded


def test_shutdown_waits_for_a_running_reconcile_thread():
    source = inspect.getsource(main.lifespan)
    assert "_wait_for_profile_reconcile_thread(app)" in source


def test_each_boot_waits_for_its_own_reconcile_after_task_cancellation(monkeypatch):
    """A completed first boot must not suppress the second worker's shutdown wait."""
    releases = [threading.Event(), threading.Event()]
    entered = [threading.Event(), threading.Event()]
    boot = iter(range(2))

    def reconcile():
        index = next(boot)
        entered[index].set()
        releases[index].wait(5)
        return {}

    monkeypatch.setattr(importlib.import_module("services.performance_profiles"),
                        "reconcile_active_profile", reconcile)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        previous = None
        for index, release in enumerate(releases):
            main._start_profile_reconcile(app)
            try:
                assert await asyncio.to_thread(entered[index].wait, 2)
                worker = app.state.profile_reconcile_worker
                assert worker is not previous
                previous = worker
                task = app.state.profile_reconcile_task
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                waiter = asyncio.create_task(main._wait_for_profile_reconcile_thread(app))
                await asyncio.sleep(0.05)
                assert not waiter.done()
                assert not worker.done()
                release.set()
                await asyncio.wait_for(waiter, 2)
                assert worker.done()
                assert not worker.cancelled()
            finally:
                release.set()

    asyncio.run(scenario())


def test_shutdown_before_reconcile_scheduled_returns_immediately():
    app = SimpleNamespace(state=SimpleNamespace())
    asyncio.run(main._wait_for_profile_reconcile_thread(app))


def test_shutdown_cancels_unsubmitted_reconcile(monkeypatch):
    calls = []
    monkeypatch.setattr(importlib.import_module("services.performance_profiles"),
                        "reconcile_active_profile", lambda: calls.append(True))

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_profile_reconcile(app)
        # No yield: the wrapper has not submitted executor work yet.
        await main._wait_for_profile_reconcile_thread(app)
        assert app.state.profile_reconcile_worker is None
        assert app.state.profile_reconcile_task.cancelled()
        assert calls == []

    asyncio.run(scenario())


def test_shutdown_waits_for_queued_reconcile_without_cancelling_it(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    release_executor = threading.Event()
    calls = []
    monkeypatch.setattr(importlib.import_module("services.performance_profiles"),
                        "reconcile_active_profile", lambda: calls.append(True))

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = loop.run_in_executor(None, release_executor.wait, 5)
        app = SimpleNamespace(state=SimpleNamespace())
        try:
            main._start_profile_reconcile(app)
            await asyncio.sleep(0)  # submitted behind the occupied executor
            worker = app.state.profile_reconcile_worker
            assert worker is not None
            waiter = asyncio.create_task(main._wait_for_profile_reconcile_thread(app))
            await asyncio.sleep(0.05)
            assert not waiter.done()
            assert not worker.cancelled()
            assert calls == []
            release_executor.set()
            await asyncio.wait_for(waiter, 2)
            await blocker
            assert calls == [True]
            assert worker.done()
        finally:
            release_executor.set()

    asyncio.run(scenario())


def test_shutdown_cancellation_stops_reconcile_dependent_warmups(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def reconcile():
        entered.set()
        release.wait(5)
        return {}

    monkeypatch.setattr(importlib.import_module("services.performance_profiles"),
                        "reconcile_active_profile", reconcile)

    async def scenario():
        app = SimpleNamespace(state=SimpleNamespace())
        main._start_profile_reconcile(app)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            warmup = asyncio.create_task(main._await_profile_reconcile(app))
            shutdown = asyncio.create_task(main._wait_for_profile_reconcile_thread(app))
            await asyncio.sleep(0.05)
            assert warmup.cancelled()
            assert not shutdown.done()
            release.set()
            await asyncio.wait_for(shutdown, 2)
        finally:
            release.set()

    asyncio.run(scenario())


def test_completed_reconcile_handles_from_closed_loop_do_not_break_shutdown():
    loop = asyncio.new_event_loop()

    async def complete():
        return None

    task = loop.create_task(complete())
    loop.run_until_complete(task)
    worker = loop.create_future()
    worker.set_result(None)
    loop.close()
    app = SimpleNamespace(state=SimpleNamespace(
        profile_reconcile_task=task, profile_reconcile_worker=worker,
    ))
    asyncio.run(main._wait_for_profile_reconcile_thread(app))
    asyncio.run(main._await_profile_reconcile(app))
