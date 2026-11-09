"""#3558: the idle reaper froze the backend while it released memory.

``idle_worker`` ran the model unload (gc, cudaFree, allocator cleanup) on the
event loop thread. cudaFree waits for the whole device, so with another job
still on the GPU every request stalled, health checks included, and the app
reported the backend "running but not responding" right after "Idle timeout
reached". Releases now run on worker threads, and a failing one no longer ends
the reaper. The same holds for the explicit unload route and engine eviction.
"""
from __future__ import annotations

import asyncio
import threading

import pytest


@pytest.fixture
def mm():
    import services.model_manager as module

    return module


class _Stop(Exception):
    pass


def _one_tick(mm, monkeypatch):
    """Let idle_worker run exactly one iteration."""
    ticks = iter([None])
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        try:
            next(ticks)
        except StopIteration:
            raise _Stop from None
        await real_sleep(0)

    monkeypatch.setattr(mm.asyncio, "sleep", sleep)


def test_idle_releases_run_off_the_event_loop(mm, monkeypatch):
    threads: dict[str, int] = {}

    def unload():
        threads["model"] = threading.get_ident()
        return True

    def side(idle_timeout):
        threads["side"] = threading.get_ident()

    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_last_used", 0.0)
    monkeypatch.setattr(mm, "_resolve_idle_timeout", lambda: 1.0)
    monkeypatch.setattr(mm, "unload_shared_model", unload)
    monkeypatch.setattr(mm, "_release_idle_side_models", side)
    _one_tick(mm, monkeypatch)

    async def run():
        loop_thread = threading.get_ident()
        with pytest.raises(_Stop):
            await mm.idle_worker()
        return loop_thread

    loop_thread = asyncio.run(run())
    assert set(threads) == {"model", "side"}
    assert loop_thread not in threads.values()


def test_a_failing_idle_unload_does_not_end_the_reaper(mm, monkeypatch):
    calls: list[str] = []

    def boom():
        raise RuntimeError("device busy")

    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_last_used", 0.0)
    monkeypatch.setattr(mm, "_resolve_idle_timeout", lambda: 1.0)
    monkeypatch.setattr(mm, "unload_shared_model", boom)
    monkeypatch.setattr(mm, "_release_idle_side_models", lambda t: calls.append("side"))
    _one_tick(mm, monkeypatch)

    async def run():
        with pytest.raises(_Stop):  # reached the next tick instead of dying
            await mm.idle_worker()

    asyncio.run(run())
    assert calls == ["side"]


def test_explicit_tts_unload_runs_off_the_event_loop(mm, monkeypatch):
    from services import model_lifecycle

    seen: list[int] = []
    # The module model_lifecycle actually calls (it may differ from the
    # fixture's under sys.modules pollution in the parallel suite).
    monkeypatch.setattr(
        model_lifecycle.mm, "unload_shared_model",
        lambda: seen.append(threading.get_ident()) or True,
    )

    async def run():
        result = await model_lifecycle.unload("tts")
        return threading.get_ident(), result

    loop_thread, result = asyncio.run(run())
    assert result == {"unloaded": "tts", "success": True}
    assert seen and seen[0] != loop_thread


def test_a_cancelled_unload_keeps_the_model_lock_until_it_finishes(mm):
    """Cancelling the caller must not free ``_model_lock`` mid-unload."""
    import threading as _threading

    started = _threading.Event()
    release = _threading.Event()
    finished: list[bool] = []

    def slow():
        started.set()
        release.wait(5)
        finished.append(True)

    async def run():
        async def holder():
            async with mm._model_lock:
                await mm.run_release(slow)

        task = asyncio.create_task(holder())
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        # The cancelled holder must not finish while the worker still runs
        # (bounded wait for that negative; the fix holds it indefinitely).
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert not done and not finished
        assert mm._model_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]
        assert not mm._model_lock.locked()

    asyncio.run(run())
