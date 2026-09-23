"""A long audiobook chapter is not abandoned while it is still rendering (#2287).

A whole chapter runs as ONE GPU-pool job with a budget derived from its text
length (300s + 1s per 40 chars past 1200, so 576s is about 12k characters).
Before this fix, ``synthesize_chapter`` never sent the per-chunk progress
heartbeat that /generate sends (#1391). A chapter that was still finishing
chunk after chunk on an 8 GB RTX 4060 was abandoned as "too heavy for the
available compute". The abandoned thread then rendered the rest of the chapter
anyway while it held the device.

Pinned here:
  * every synthesized chunk reports progress, so the guard extends the deadline;
  * an abandoned chapter stops before its next chunk (cooperative cancel);
  * the heartbeat extension cap scales with the job's budget, so chapter
    length is not a hard limit on its own;
  * a wedged chapter (no chunk finishes) still dies on time.

Timings are tenths of seconds via monkeypatched constants.
"""
from __future__ import annotations

import asyncio
import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import torch

SR = 24000


@pytest.fixture
def mm(monkeypatch):
    mod = importlib.import_module("services.model_manager")
    monkeypatch.setattr(mod, "MODEL_LOAD_EXTRA_TIMEOUT_S", 2.0)
    monkeypatch.setattr(mod, "MODEL_LOAD_HEARTBEAT_GRACE_S", 0.5)
    monkeypatch.setattr(mod, "GENERATE_PROGRESS_GRACE_S", 1.0)
    mod._MODEL_LOAD_ACTIVITY.clear()
    yield mod
    mod._MODEL_LOAD_ACTIVITY.clear()


@pytest.fixture
def release():
    ev = threading.Event()
    yield ev
    ev.set()


@pytest.fixture
def pool(release):
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-pool")
    yield ex
    release.set()
    ex.shutdown(wait=True)


def synthesize_chapter(*args, **kwargs):
    # Resolved at call time so sys.modules replacement elsewhere in the suite
    # cannot leave this file testing a stale module.
    return importlib.import_module("services.audiobook").synthesize_chapter(*args, **kwargs)


def _spans(n):
    Span = importlib.import_module("services.audiobook").Span
    # One short sentence per span, so each span is exactly one engine chunk.
    return [Span(voice_id=None, text=f"Sentence number {i}.") for i in range(n)]


async def _run(mm, pool, fn, timeout):
    return await mm.run_on_gpu_pool_guarded(
        fn, what="Audiobook chapter", timeout=timeout, queue_timeout=5.0,
        executor=pool,
    )


def test_every_synthesized_chunk_reports_progress(monkeypatch):
    mod = importlib.import_module("services.model_manager")
    beats = []
    monkeypatch.setattr(mod, "report_generate_progress", lambda: beats.append(1))

    synthesize_chapter(_spans(5), lambda t, v, s=None: torch.zeros(240), SR)

    assert len(beats) == 5


def test_a_failing_progress_report_cannot_break_a_chapter(monkeypatch):
    mod = importlib.import_module("services.model_manager")

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "report_generate_progress", boom)
    _audio, dur = synthesize_chapter(_spans(2), lambda t, v, s=None: torch.zeros(240), SR)
    assert dur > 0


@pytest.mark.asyncio
async def test_a_chapter_that_keeps_finishing_chunks_outlives_its_budget(mm, pool):
    """The reported failure in miniature: GpuJobTimeoutError before the fix."""
    def synth(text, voice, speed=None):
        time.sleep(0.15)
        return torch.zeros(240)

    _audio, dur = await _run(
        mm, pool, lambda: synthesize_chapter(_spans(8), synth, SR), 0.3,
    )
    assert dur > 0


@pytest.mark.asyncio
async def test_an_abandoned_chapter_stops_before_its_next_chunk(mm, pool, release):
    """Before the fix, the abandoned thread rendered every remaining chunk
    and held the device the whole time."""
    calls = []
    done = threading.Event()

    def synth(text, voice, speed=None):
        calls.append(text)
        if len(calls) == 1:
            # Blocked until the guard has abandoned the job.
            release.wait(10)
        return torch.zeros(240)

    def chapter():
        try:
            return synthesize_chapter(_spans(6), synth, SR)
        finally:
            done.set()

    try:
        with pytest.raises(mm.GpuJobTimeoutError):
            await asyncio.wait_for(_run(mm, pool, chapter, 0.3), 5.0)
    finally:
        release.set()
    assert done.wait(5.0)
    assert len(calls) == 1, f"abandoned chapter kept rendering: {len(calls)} chunks"


@pytest.mark.asyncio
async def test_extension_cap_scales_with_the_budget(mm, pool, monkeypatch):
    """A fixed cap turned chapter length into a hard limit however steadily the
    chapter progressed. The cap is now at least PROGRESS_EXTENSION_BUDGETS x
    the budget."""
    monkeypatch.setattr(mm, "MODEL_LOAD_EXTRA_TIMEOUT_S", 0.2)

    def synth(text, voice, speed=None):
        time.sleep(0.1)
        return torch.zeros(240)

    # ~1.6s of steady progress against a 0.8s budget: past budget + the
    # fixed 0.2s cap (1.0s), well within budget + 3 x budget (3.2s).
    _audio, dur = await _run(
        mm, pool, lambda: synthesize_chapter(_spans(16), synth, SR), 0.8,
    )
    assert dur > 0


@pytest.mark.asyncio
async def test_a_wedged_chapter_still_dies_on_time(mm, pool, release):
    def synth(text, voice, speed=None):
        release.wait(30)
        return torch.zeros(240)

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, lambda: synthesize_chapter(_spans(3), synth, SR), 0.3)
    assert time.monotonic() - t0 < 2.0
