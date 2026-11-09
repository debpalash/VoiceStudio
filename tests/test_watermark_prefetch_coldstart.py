"""Background warm-up + thread safety for the AudioSeal watermark models.

The 2026-08-17 cold-start report on a macOS deployment: the first
``mark_synthetic`` serialized the audioseal import + generator load (~42 s on
a cold filesystem) INSIDE the first synthesis, and a 90 s client timeout
missed the audio by 3 s. The generator now warms on a background thread
during startup; because that thread races the first embed, the lazy getters
must be thread-safe — exactly one load, no torn state.
"""
from __future__ import annotations

import importlib
import sys
import threading
import types
from types import SimpleNamespace

import pytest


@pytest.fixture
def watermark():
    """Resolve app state after per-test setup, never at collection time."""
    return importlib.import_module("services.watermark")


@pytest.fixture(autouse=True)
def _reset_models(monkeypatch, watermark):
    # Reset ALL lifecycle globals (CodeRabbit, PR #1577): a stale warm-up
    # stamp or availability cache from a prior test changes this test's
    # conditions.
    watermark._generator = None
    watermark._detector = None
    watermark._last_used = 0.0
    watermark._prefetched_unused = False
    watermark._generator_failed_at = None
    monkeypatch.setattr(watermark, "_audioseal_available", None, raising=False)
    yield
    watermark._generator = None
    watermark._detector = None
    watermark._last_used = 0.0
    watermark._prefetched_unused = False
    watermark._generator_failed_at = None


def _fake_audioseal(monkeypatch, load_s: float) -> list[int]:
    """Install a fake ``audioseal`` module whose load blocks ``load_s`` and
    records every invocation. The block is what makes a missing lock fail
    reliably instead of winning the interleaving lottery."""
    calls: list[int] = []
    import time

    def _slow_load(name):
        calls.append(1)
        time.sleep(load_s)
        return SimpleNamespace(eval=lambda: None)

    fake = types.ModuleType("audioseal")
    fake.AudioSeal = SimpleNamespace(load_generator=_slow_load)
    monkeypatch.setitem(sys.modules, "audioseal", fake)
    return calls


def test_get_generator_loads_exactly_once_under_concurrency(monkeypatch, watermark):
    """A background prefetch thread + the first embed race the lazy load;
    both must share ONE generator build, not one each."""
    calls = _fake_audioseal(monkeypatch, load_s=0.05)

    results = []
    errors = []

    def _hit():
        try:
            results.append(watermark._get_generator())
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=_hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert calls == [1], f"load_generator ran {len(calls)}x; the lazy load races"
    assert all(g is results[0] for g in results)


def test_prefetch_generator_loads_when_watermarking_is_on(monkeypatch, watermark):
    """prefetch_generator() must build the generator eagerly (the startup
    warm-up path) while the pref is enabled and audioseal is importable."""
    calls = _fake_audioseal(monkeypatch, load_s=0)
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)

    watermark.prefetch_generator(allow_download=True)

    assert calls == [1]
    assert watermark._generator is not None


def test_prefetch_generator_no_ops_when_disabled_or_absent(monkeypatch, watermark):
    """Pref disabled, or audioseal not installed: the warm-up must touch
    nothing — no import attempts, no model, no exception."""
    calls = _fake_audioseal(monkeypatch, load_s=0)
    monkeypatch.setattr(watermark, "is_enabled", lambda: False)
    watermark.prefetch_generator()
    assert calls == []
    assert watermark._generator is None

    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    monkeypatch.setattr(watermark, "_check_available", lambda: False)
    watermark.prefetch_generator()
    assert calls == []
    assert watermark._generator is None


def test_prefetch_generator_degrades_silently_on_failure(monkeypatch, watermark):
    """A failed warm-up must never take the backend down or wedge the lazy
    path: log, leave _generator None; the first embed retries inline."""
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    monkeypatch.setattr(watermark, "_check_available", lambda: True)

    def _boom():
        raise RuntimeError("hub exploded (test)")

    monkeypatch.setattr(watermark, "_get_generator", _boom)
    watermark.prefetch_generator(allow_download=True)  # must not raise
    assert watermark._generator is None


def test_prefetch_generator_does_not_download_with_empty_offline_cache(
    monkeypatch, tmp_path, watermark
):
    """Default startup stays local-first even when watermarking is enabled."""
    monkeypatch.setenv("AUDIOSEAL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(watermark, "will_mark", lambda: True)
    calls = []
    monkeypatch.setattr(watermark, "_get_generator", lambda **kw: calls.append(kw))

    watermark.prefetch_generator()

    assert calls == []


def test_prefetch_generator_loads_cached_checkpoint_offline(
    monkeypatch, tmp_path, watermark
):
    cache_dir = tmp_path / "audioseal"
    cache_dir.mkdir()
    (cache_dir / "generator_base.pth").touch()
    monkeypatch.setenv("AUDIOSEAL_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(watermark, "will_mark", lambda: True)
    calls = []
    monkeypatch.setattr(watermark, "_get_generator", lambda **kw: calls.append(kw))

    watermark.prefetch_generator()

    assert calls == [{"mark_prefetched": True}]


def test_detector_load_is_not_blocked_by_a_generator_prefetch(monkeypatch, watermark):
    """Per-model locks (review finding): a ~42s generator build in the
    prefetch thread must not stall an unrelated detector load — with the old
    single shared lock, _get_detector queued behind the whole build."""
    gen_started = threading.Event()
    gen_release = threading.Event()
    det_done = threading.Event()

    fake = types.ModuleType("audioseal")

    def _slow_gen(name):
        gen_started.set()
        gen_release.wait(10)
        return SimpleNamespace(eval=lambda: None)

    def _fast_det(name):
        det_done.set()
        return SimpleNamespace(eval=lambda: None)

    fake.AudioSeal = SimpleNamespace(load_generator=_slow_gen, load_detector=_fast_det)
    monkeypatch.setitem(sys.modules, "audioseal", fake)

    t = threading.Thread(target=watermark._get_generator)
    t.start()
    assert gen_started.wait(5)
    det = watermark._get_detector()  # must NOT queue behind the generator build
    assert det_done.wait(1), "detector load blocked behind the generator load"
    gen_release.set()
    t.join(timeout=10)


def test_watermark_pool_rebuilds_after_shutdown_drain():
    """The lifespan shutdown drains the watermark pool; a process that keeps
    running afterwards (the test suite) must get a FRESH pool on next use,
    not "cannot schedule new futures after shutdown" (CI, PR #1577)."""
    from services.model_manager import (
        begin_watermark_pool_lifecycle,
        get_watermark_pool,
        shutdown_watermark_pool,
    )

    begin_watermark_pool_lifecycle()
    pool_before = get_watermark_pool()
    shutdown_watermark_pool()
    with pytest.raises(RuntimeError):
        # The drained pool refuses new work…
        pool_before.submit(lambda: None).result(timeout=5)
    # …but the next app lifespan hands out a live replacement.
    begin_watermark_pool_lifecycle()
    assert get_watermark_pool().submit(lambda: "ok").result(timeout=5) == "ok"
    # Clean up the replacement so later tests start from a fresh pool too.
    shutdown_watermark_pool()


@pytest.mark.parametrize("timeout", [None, 0.5])
def test_watermark_submission_racing_shutdown_returns_finished_audio(
    monkeypatch, watermark, timeout
):
    """Shutdown after admission must fail open in both dispatch paths."""
    import asyncio
    import torch

    class _ShutdownRaceExecutor:
        def submit(self, _fn, /, *_args, **_kwargs):
            raise RuntimeError("cannot schedule new futures after shutdown")

        def is_shutdown(self):
            return True

    pool = _ShutdownRaceExecutor()
    model_manager = importlib.import_module("services.model_manager")
    monkeypatch.setattr(model_manager, "get_watermark_pool", lambda: pool)

    audio = torch.zeros(1, 240)
    marked = asyncio.run(
        watermark.mark_synthetic_async(
            audio,
            24000,
            context="test.shutdown_submission_race",
            timeout=timeout,
        )
    )

    assert marked is audio


@pytest.mark.parametrize("timeout", [None, 0.5])
def test_queued_watermark_cancelled_by_shutdown_returns_finished_audio(
    monkeypatch, watermark, timeout
):
    """Teardown cancellation of admitted work is lifecycle fail-open."""
    import asyncio
    from concurrent.futures import Future

    import torch

    class _ShutdownCancellingExecutor:
        def submit(self, _fn, /, *_args, **_kwargs):
            future = Future()
            future.cancel()
            return future

        def is_shutdown(self):
            return True

    pool = _ShutdownCancellingExecutor()
    model_manager = importlib.import_module("services.model_manager")
    monkeypatch.setattr(model_manager, "get_watermark_pool", lambda: pool)

    audio = torch.zeros(1, 240)
    marked = asyncio.run(
        watermark.mark_synthetic_async(
            audio,
            24000,
            context="test.shutdown_queue_cancellation",
            timeout=timeout,
        )
    )

    assert marked is audio


def test_watermark_preserves_caller_cancellation(monkeypatch, watermark):
    """A live pool must not misclassify caller cancellation as teardown."""
    import asyncio
    from concurrent.futures import Future

    import torch

    class _LivePendingExecutor:
        def submit(self, _fn, /, *_args, **_kwargs):
            return Future()

        def is_shutdown(self):
            return False

    pool = _LivePendingExecutor()
    model_manager = importlib.import_module("services.model_manager")
    monkeypatch.setattr(model_manager, "get_watermark_pool", lambda: pool)

    async def _cancel():
        task = asyncio.create_task(
            watermark.mark_synthetic_async(
                torch.zeros(1, 240),
                24000,
                context="test.caller_cancellation",
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel())


@pytest.mark.parametrize("error_name", ["GpuJobTimeoutError", "GpuPoolBusyError"])
def test_timed_watermark_deadline_returns_finished_audio(
    monkeypatch, watermark, error_name
):
    """Both guarded deadline phases are watermark-only fail-open outcomes."""
    import asyncio
    import torch

    model_manager = importlib.import_module("services.model_manager")
    error_type = getattr(model_manager, error_name)

    async def _expired(*_args, **_kwargs):
        raise error_type("watermark deadline expired")

    pool = model_manager.get_watermark_pool()
    monkeypatch.setattr(model_manager, "get_watermark_pool", lambda: pool)
    monkeypatch.setattr(model_manager, "run_on_gpu_pool_guarded", _expired)

    audio = torch.zeros(1, 240)
    marked = asyncio.run(
        watermark.mark_synthetic_async(
            audio,
            24000,
            context="test.watermark_typed_deadline",
            timeout=0.01,
        )
    )

    assert marked is audio


def test_watermark_pool_shutdown_waits_for_active_worker():
    """Lifespan teardown cannot finish while AudioSeal is still loading."""
    from services.model_manager import (
        begin_watermark_pool_lifecycle,
        get_watermark_pool,
        shutdown_watermark_pool,
    )

    begin_watermark_pool_lifecycle()
    started = threading.Event()
    release = threading.Event()
    shutdown_done = threading.Event()

    def _blocking_load():
        started.set()
        release.wait(5)

    get_watermark_pool().submit(_blocking_load)
    assert started.wait(1)

    shutdown_thread = threading.Thread(
        target=lambda: (shutdown_watermark_pool(), shutdown_done.set())
    )
    shutdown_thread.start()
    assert not shutdown_done.wait(0.1), "shutdown returned while worker was active"
    release.set()
    shutdown_thread.join(timeout=2)
    assert shutdown_done.is_set()


def test_watermark_pool_shutdown_deadline_bounds_stuck_worker():
    """A stuck AudioSeal import cannot hang backend shutdown forever."""
    import time

    from services.model_manager import (
        begin_watermark_pool_lifecycle,
        get_watermark_pool,
        shutdown_watermark_pool,
    )

    started = threading.Event()
    release = threading.Event()

    def _stuck_load():
        started.set()
        release.wait(5)

    begin_watermark_pool_lifecycle()
    pool = get_watermark_pool()
    pool.submit(_stuck_load)
    assert started.wait(1)

    before = time.monotonic()
    shutdown_watermark_pool(timeout=0.05)
    elapsed = time.monotonic() - before

    assert elapsed < 0.5
    assert pool._thread is not None and pool._thread.daemon
    release.set()
    pool._thread.join(timeout=1)


def test_watermark_pool_cannot_be_replaced_while_timed_out_worker_is_alive(watermark):
    """A producer racing bounded shutdown cannot create an undrained pool."""
    from services.model_manager import (
        begin_watermark_pool_lifecycle,
        get_watermark_pool,
        shutdown_watermark_pool,
    )

    started = threading.Event()
    release = threading.Event()

    def _stuck_load():
        started.set()
        release.wait(5)

    begin_watermark_pool_lifecycle()
    pool = get_watermark_pool()
    pool.submit(_stuck_load)
    assert started.wait(1)

    try:
        shutdown_watermark_pool(timeout=0.01)
        with pytest.raises(RuntimeError, match="shutting down"):
            get_watermark_pool()
        # A deliberate in-process relaunch must not overlap the retired
        # worker: both touch the process-global AudioSeal model state.
        begin_watermark_pool_lifecycle()
        with pytest.raises(RuntimeError, match="shutting down"):
            get_watermark_pool()
        # Finished synthesis must still be returned unchanged: pool admission
        # is outside mark_synthetic's synchronous fail-open boundary.
        import asyncio
        import torch

        audio = torch.zeros(1, 240)
        marked = asyncio.run(
            watermark.mark_synthetic_async(
                audio, 24000, context="test.restart_during_shutdown"
            )
        )
        assert marked is audio
    finally:
        release.set()
        pool._thread.join(timeout=1)

    # Once the retired worker really exits, the reopened lifecycle becomes
    # usable without requiring another startup signal.
    replacement = get_watermark_pool()
    assert replacement is not pool
    assert replacement.submit(lambda: "ok").result(timeout=1) == "ok"
    shutdown_watermark_pool()


def test_prefetched_model_gets_one_extra_idle_window(monkeypatch, watermark):
    """Review finding: the reaper freed the prefetch-warmed, never-used
    generator at the first idle tick, re-imposing the cold start the prefetch
    exists to hide. It now survives ONE extra window; real use clears the
    grace entirely.

    Each phase re-establishes its module state IMMEDIATELY before its
    release_idle_models call and passes an explicit far-future ``now=``: a
    leaked idle reaper (a test lifespan that exits without shutdown keeps
    idle_worker running) mutates these same globals from another thread, and
    re-stamping _last_used mid-test made the real-assertions flake on CI.
    With preconditions set adjacent to each call and now pinned, an
    interleaved tick cannot change the outcome.
    """
    import time

    # Full isolation from a leaked idle reaper (idle_worker resolves
    # watermark.release_idle_models per call): divert it to a no-op for the
    # duration of this test, and call the real function via the saved ref.
    real_release = watermark.release_idle_models
    # The test owns the reaper's decisions for its duration.
    monkeypatch.setattr(watermark, "release_idle_models", lambda *a, **k: False)

    far_future = time.monotonic() + 1_000_000

    def _given(generator_set: bool, grace: bool):
        watermark._generator = SimpleNamespace(eval=lambda: None) if generator_set else None
        watermark._prefetched_unused = grace
        watermark._last_used = 0.0

    # First reaper pass on a prefetched-never-used model: grace, model kept.
    _given(generator_set=True, grace=True)
    assert real_release(900, now=far_future) is False
    # Second pass: grace consumed, model released.
    _given(generator_set=True, grace=False)
    assert real_release(900, now=far_future) is True
    # After grace was consumed, an idle model with no models at all is a no-op.
    _given(generator_set=False, grace=False)
    assert real_release(900, now=far_future) is False

    # Real use clears the grace: embed (even a failing one) resets the flag,
    # so the next reaper pass releases without a second window.
    import torch as _torch
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    monkeypatch.setattr(watermark, "_check_available", lambda: True)
    watermark._generator = SimpleNamespace(eval=lambda: None)
    watermark._prefetched_unused = True
    watermark.embed_watermark(_torch.zeros(1, 2400), 24000)
    # The embed call itself must have cleared the grace — assert it, don't
    # re-establish it, or a failing embed would pass unnoticed.
    assert watermark._prefetched_unused is False
    assert real_release(900, now=far_future) is True


def test_failed_generator_load_is_not_retried_on_every_embed(monkeypatch, watermark, caplog):
    """A host that cannot fetch the AudioSeal checkpoint paid the network
    timeout (~3 min) on EVERY render, though the audio passed through
    unmarked each time (#2929 comment). One failed load must be remembered
    for a cooldown, then retried so a transient outage does not disable
    marking for the whole session."""
    import torch as _torch

    calls: list[int] = []

    def _failing_load(name):
        calls.append(1)
        raise OSError("<urlopen error [Errno 60] Operation timed out>")

    fake = types.ModuleType("audioseal")
    fake.AudioSeal = SimpleNamespace(load_generator=_failing_load)
    monkeypatch.setitem(sys.modules, "audioseal", fake)
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    monkeypatch.setattr(watermark, "_check_available", lambda: True)

    audio = _torch.zeros(1, 2400)
    assert watermark.embed_watermark(audio, 24000) is audio
    caplog.clear()
    assert watermark.embed_watermark(audio, 24000) is audio
    assert len(calls) == 1
    # An expected skip during the cooldown logs without a traceback.
    assert caplog.records and not any(r.exc_info for r in caplog.records)

    # Once the cooldown has elapsed the load is attempted again.
    watermark._generator_failed_at -= watermark._GENERATOR_RETRY_SECONDS + 1
    assert watermark.embed_watermark(audio, 24000) is audio
    assert len(calls) == 2
