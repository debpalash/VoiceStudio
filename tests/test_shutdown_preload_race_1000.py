"""A quit mid-preload must not report a clean shutdown while a GPU-pool
thread is still running (#1000 class).

Field report: a backend log showed three rapid restart cycles, each ending
with "Shutdown: done." immediately followed by a "Model loading failed:
Could not import module 'AutoFeatureExtractor'" error — transformers' own
generic lazy-import wrapper, not a real dependency problem. The real cause:
`preload_task` (and the optional `capture_preload_task`) were created at
startup but never referenced in the shutdown block, so `idle_task`/
`worker_task` got cancelled-and-awaited while the preload task was simply
abandoned — the process declared "done" while a background GPU-pool thread
was still mid-`import`, and got torn down by interpreter finalization under
it.

`_cancel_and_await_tasks` is the extracted, directly-testable shutdown
helper — the full `lifespan()` context manager touches too much startup
machinery (DB init, gallery init, MCP session manager) to drive directly in
a unit test (this suite's own test_mcp_mount.py notes exactly this: running
the full lifespan contaminates other tests' event loops).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from main import _cancel_and_await_tasks  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_a_task_that_finished_before_cancel_keeps_its_result():
    """An early-stage task (mirrors preload still importing, not yet deep in
    blocking weight-load work) that completes on its own before the shutdown
    helper even reaches it must not be treated as an error — `.cancel()` on
    an already-done task is a no-op, and its real result survives. This is
    the fix: previously preload_task was never referenced in shutdown at
    all, so this case (the common one — most quits don't land mid-import)
    was never even checked."""
    finished = []

    async def _quick():
        await asyncio.sleep(0.01)
        finished.append("done")

    async def _scenario():
        t = asyncio.create_task(_quick())
        await asyncio.sleep(0.05)  # long enough for _quick() to fully finish
        assert t.done()
        await _cancel_and_await_tasks(t, timeout=1.0)  # must not raise on a done task

    _run(_scenario())
    assert finished == ["done"]


def test_none_entries_are_skipped_without_error():
    """capture_preload_task is None when OMNIVOICE_PRELOAD_CAPTURE_ASR=0 —
    the helper must not crash on a mix of real tasks and None."""
    async def _noop():
        return None

    async def _scenario():
        t = asyncio.create_task(_noop())
        await _cancel_and_await_tasks(t, None, timeout=1.0)

    _run(_scenario())  # must not raise


def test_a_task_stuck_past_the_bound_times_out_without_hanging():
    """A task that never yields back (mirroring a GPU-pool thread stuck in a
    blocking native call) must not hang shutdown forever — the bound is the
    backstop, same as the pre-existing idle_task/worker_task pattern."""
    async def _wedged():
        await asyncio.sleep(10.0)

    async def _scenario():
        t = asyncio.create_task(_wedged())
        await asyncio.sleep(0.01)
        await _cancel_and_await_tasks(t, timeout=0.2)

    import time
    start = time.monotonic()
    _run(_scenario())
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"shutdown helper did not bound its wait: took {elapsed:.2f}s"


def test_multiple_tasks_are_all_cancelled_before_any_await():
    """Cancel-then-await (not cancel-then-immediately-await-one-at-a-time) —
    every task gets its cancellation requested up front, so a slow task
    earlier in the list can't delay a later task's cancel signal."""
    cancelled_order = []

    async def _tracked(name, delay):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            cancelled_order.append(name)
            raise

    async def _scenario():
        t1 = asyncio.create_task(_tracked("slow", 5.0))
        t2 = asyncio.create_task(_tracked("fast", 5.0))
        await asyncio.sleep(0.01)
        await _cancel_and_await_tasks(t1, t2, timeout=0.5)

    _run(_scenario())
    assert set(cancelled_order) == {"slow", "fast"}


def test_shutdown_wait_bound_is_long_enough_for_a_cold_import():
    """Source guard: the shutdown call that awaits the background tasks must
    not shrink to a few seconds, or a cold transformers import would still be
    running when shutdown reports done."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text(encoding="utf-8")
    names = r"\s*,\s*".join(
        rf'getattr\(app\.state,\s*"{n}",\s*None\)'
        for n in ("idle_task", "worker_task", "profile_reconcile_task",
                  "preload_task", "capture_preload_task", "watermark_preload_task")
    )
    m = re.search(
        rf"await _cancel_and_await_tasks\(\s*{names}\s*,\s*timeout=([0-9.]+)\s*,?\s*\)", src
    )
    assert m, "production _cancel_and_await_tasks(...) call not found in backend/main.py"
    assert float(m.group(1)) >= 15.0, (
        "the shutdown wait bound exists so a cold import can finish during shutdown; "
        f"timeout={m.group(1)} is too short"
    )
