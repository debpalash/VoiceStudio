"""A render that keeps finishing chunks is not "too heavy for the compute" (#1391).

The largest open cluster (#1338, #1348, #1391) is one shape: a long text on
modest hardware, rendered chunk by chunk under a single execution budget, dying
at 300s with "the backend is running, but this job was too heavy for the
available compute" — after most of its chunks had already rendered. The user
sees a hardware verdict about a job that was working the whole time.

#1367 solved the same disagreement for model DOWNLOADS: the guard now listens
to heartbeats instead of only its own clock. This applies that to synthesis,
where the proof of life is a completed chunk.

The distinction the guard must keep making: SLOW (chunks keep landing, however
far apart) versus WEDGED (nothing lands at all). The second half of this file
is that non-regression — the wedge guard is why the budget exists.

Timings are tenths of seconds via monkeypatched constants, so nothing here
races real hardware.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture
def mm(monkeypatch):
    mod = importlib.import_module("services.model_manager")
    monkeypatch.setattr(mod, "MODEL_LOAD_EXTRA_TIMEOUT_S", 2.0)
    monkeypatch.setattr(mod, "MODEL_LOAD_HEARTBEAT_GRACE_S", 0.5)
    # Chunks are coarse: the synthesis grace is deliberately far longer than
    # the load grace, and these tests depend on that being true.
    monkeypatch.setattr(mod, "GENERATE_PROGRESS_GRACE_S", 1.0)
    mod._MODEL_LOAD_ACTIVITY.clear()
    yield mod
    mod._MODEL_LOAD_ACTIVITY.clear()


@pytest.fixture
def release():
    """Set to let a deliberately-wedged worker return.

    Sleeping a wedged job for a fixed 30s and walking away leaks the thread
    past its own test: it keeps running, keeps writing to the shared
    heartbeat registry, and delays interpreter shutdown (CodeRabbit). That is
    the exact shape of the suite-wide flake #1390 fixed — a background thread
    scribbling into a finished test's state — so it does not get to be
    reintroduced here.
    """
    ev = threading.Event()
    yield ev
    ev.set()


@pytest.fixture
def pool(release):
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-pool")
    yield ex
    # Release first, then wait: every worker this file starts observes the
    # event, so shutdown really does join them instead of returning while
    # they run on.
    release.set()
    ex.shutdown(wait=True)


async def _run(mm, pool, fn, timeout):
    return await mm.run_on_gpu_pool_guarded(
        fn, what="TTS generate", timeout=timeout, queue_timeout=5.0, executor=pool,
    )


# ── the fix ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_render_that_keeps_completing_chunks_outlives_its_budget(mm, pool):
    """The reported case in miniature. Fails with GpuJobTimeoutError before."""
    rendered = []

    def render():
        for i in range(8):
            time.sleep(0.15)
            rendered.append(i)
            mm.report_generate_progress()
        return "audio"

    # 0.3s budget for what takes ~1.2s — the shape of a 300s budget against a
    # multi-minute render.
    assert await _run(mm, pool, render, 0.3) == "audio"
    assert rendered == list(range(8))


@pytest.mark.asyncio
async def test_chunks_may_be_far_slower_than_a_load_heartbeat(mm, pool, monkeypatch):
    """A single chunk on a modest GPU takes minutes, not the ~5s between
    sidecar download frames. Judging synthesis by the LOAD grace would call
    every slow-but-healthy render wedged — which is the bug, not the fix."""
    def render():
        for _ in range(3):
            # Longer than MODEL_LOAD_HEARTBEAT_GRACE_S (0.5), inside
            # GENERATE_PROGRESS_GRACE_S (1.0).
            time.sleep(0.7)
            mm.report_generate_progress()
        return "audio"

    # Room for the whole render inside the extension cap — this test is about
    # the GRACE window, and a cap that cut it short would prove nothing.
    monkeypatch.setattr(mm, "MODEL_LOAD_EXTRA_TIMEOUT_S", 6.0)
    # Budget outlasts the FIRST chunk: extension is earned by evidence, so a
    # job with no completed chunk yet has nothing to vouch for it and still
    # dies on time (that case is covered below).
    assert await _run(mm, pool, render, 0.8) == "audio"


@pytest.mark.asyncio
async def test_the_extension_is_still_capped(mm, pool, release):
    """A render that heartbeats forever must not hold a worker forever."""
    def never_ends():
        # Heartbeats until the test releases it — no fixed iteration count to
        # tune, and no thread left running afterwards.
        while not release.wait(0.05):
            mm.report_generate_progress()

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, never_ends, 0.3)
    # Bounded by budget + MODEL_LOAD_EXTRA_TIMEOUT_S (2.0), not unbounded.
    assert time.monotonic() - t0 < 6.0


# ── the guard this budget exists for, untouched ────────────────────────────


@pytest.mark.asyncio
async def test_a_wedged_render_still_dies_on_time(mm, pool, release):
    """No chunk ever completes: exactly what the deadline is for. It must not
    have become survivable just because SOME jobs can now prove liveness."""
    def wedged():
        release.wait(30)

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, wedged, 0.3)
    # Dies at the original deadline, ± one wait slice — not extended.
    assert time.monotonic() - t0 < 2.0


@pytest.mark.asyncio
async def test_a_render_that_stops_completing_chunks_dies(mm, pool, release):
    """Started fine, then hung — the half-wedged case. The last heartbeat must
    expire rather than vouch for the job indefinitely."""
    def stalls():
        mm.report_generate_progress()
        release.wait(30)

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, stalls, 0.3)
    # Budget (0.3) + one grace window (1.0) + a slice — not the 2.0 cap.
    assert time.monotonic() - t0 < 2.5


@pytest.mark.asyncio
async def test_load_heartbeats_keep_their_own_shorter_grace(mm, pool, release):
    """The two signals must not collapse into one window: a load that stops
    reporting is wedged after 0.5s, and giving it synthesis's 1.0s grace would
    weaken the #1367 guard."""
    def load_then_hang():
        mm.report_model_load_activity()
        release.wait(30)

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, load_then_hang, 0.3)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.6, f"load heartbeat was granted the synthesis grace ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_a_stale_ident_cannot_vouch_for_the_next_job(mm, pool, release):
    """Thread idents are reused. A heartbeat left behind by a finished render
    must not buy time for whatever runs next on that worker."""
    def quick():
        mm.report_generate_progress()
        return "done"

    assert await _run(mm, pool, quick, 5.0) == "done"

    def wedged():
        release.wait(30)

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, wedged, 0.3)
    assert time.monotonic() - t0 < 2.0


# ── the render loops actually emit it ──────────────────────────────────────


def test_every_multi_part_render_loop_reports_progress():
    """The guard can only listen if the render speaks. Each loop that produces
    one part at a time — native chunks, adapter chunks, pause spans — must
    report, or that path silently keeps the old failure."""
    import ast

    src = open(
        os.path.join(os.path.dirname(__file__), "..",
                     "backend/api/routers/generation.py"),
        encoding="utf-8",
    ).read()
    tree = ast.parse(src)

    def _calls_progress(node) -> bool:
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_note_generate_progress"
            for n in ast.walk(node)
        )

    def _renders_inline(node) -> bool:
        """A loop that SYNTHESIZES each part itself, inside one pool job.

        Excludes the silence-padding loop (no synthesis) and the streaming
        loop, which dispatches every chunk as its own pool job with its own
        budget and so cannot overrun a shared one.
        """
        for n in ast.walk(node):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Name) and f.id in {"_gen", "gen_span"}:
                return True
            if (isinstance(f, ast.Name) and f.id == "trace_call"
                    and len(n.args) > 1):
                f = n.args[1]  # instrumentation still calls the same generator
            if isinstance(f, ast.Attribute) and f.attr == "generate":
                return True
        return False

    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For) and _renders_inline(n)]
    assert len(loops) == 3, f"expected the three inline render loops, found {len(loops)}"
    for loop in loops:
        assert _calls_progress(loop), (
            f"the render loop at line {loop.lineno} appends parts without "
            f"reporting progress — that path still dies mid-render (#1391)"
        )


def test_the_progress_report_cannot_break_a_render():
    """A liveness signal that can raise is worse than no signal at all."""
    import importlib as _il

    gen = _il.import_module("api.routers.generation")
    mm_mod = _il.import_module("services.model_manager")
    original = mm_mod.report_generate_progress

    def explode():
        raise RuntimeError("boom")

    mm_mod.report_generate_progress = explode
    try:
        gen._note_generate_progress()  # must not raise
    finally:
        mm_mod.report_generate_progress = original
