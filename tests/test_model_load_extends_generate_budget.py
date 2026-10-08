"""A healthy model download must not be reported as too-slow hardware (#1367).

Every subprocess engine cold-loads its model inside the synthesize handler, so
a first-use generate on a slow connection spends its whole 300s execution
budget downloading — and then fails, blaming the hardware, while the sidecar's
own watchdog was being fed progress frames the entire time. The two clocks
disagreed about a job both could see was alive.

The fix makes the outer clock listen: sidecar progress frames are forwarded to
``report_model_load_activity()``, and the guarded waiter extends the deadline
past the soft budget only while those heartbeats stay fresh, bounded by
``MODEL_LOAD_EXTRA_TIMEOUT_S``. A job that goes SILENT still dies at the
original deadline (± one wait slice) — the guard this runner exists for is
untouched, and that non-regression is most of this file.

Timings here are tenths of seconds via monkeypatched module constants, so the
suite stays fast and none of it races real hardware.
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
    # Fast clocks: 0.4s soft budget, up to +2s of heartbeat extension, 0.5s
    # heartbeat grace. The wait slice is min(remaining, 5.0), so remaining
    # drives it at these scales.
    monkeypatch.setattr(mod, "MODEL_LOAD_EXTRA_TIMEOUT_S", 2.0)
    monkeypatch.setattr(mod, "MODEL_LOAD_HEARTBEAT_GRACE_S", 0.5)
    mod._MODEL_LOAD_ACTIVITY.clear()
    yield mod
    mod._MODEL_LOAD_ACTIVITY.clear()


@pytest.fixture
def pool():
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-pool")
    yield ex
    ex.shutdown(wait=False)


async def _run(mm, pool, fn, timeout):
    return await mm.run_on_gpu_pool_guarded(
        fn, what="test job", timeout=timeout, queue_timeout=5.0, executor=pool,
    )


# ── the fix ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_heartbeating_load_survives_past_the_budget(mm, pool):
    """The reported case in miniature: the job outlives its budget but proves
    liveness throughout, exactly as a downloading sidecar does. Fails with
    GpuJobTimeoutError before the fix."""
    def slow_load():
        # 1s of "download" against a 0.4s budget, heartbeating every 0.1s.
        for _ in range(10):
            mm.report_model_load_activity()
            time.sleep(0.1)
        return "audio"

    assert await _run(mm, pool, slow_load, timeout=0.4) == "audio"


@pytest.mark.asyncio
async def test_the_extension_is_logged_once(mm, pool, caplog):
    """The extension must be visible in the log — a generate that quietly took
    4x its budget is its own diagnosability bug."""
    def slow_load():
        for _ in range(8):
            mm.report_model_load_activity()
            time.sleep(0.1)
        return "ok"

    import logging
    with caplog.at_level(logging.INFO, logger=mm.logger.name):
        await _run(mm, pool, slow_load, timeout=0.4)
    hits = [r for r in caplog.records if "extending while" in r.getMessage()]
    assert len(hits) == 1, "the extension should be announced exactly once"


# ── the guard is untouched ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_silent_job_still_times_out_at_the_original_deadline(mm, pool):
    """The non-regression that matters most: no heartbeats, no extension. A
    wedged job dies at ~timeout, not at the hard cap."""
    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, lambda: time.sleep(10), timeout=0.4)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, (
        f"a silent job survived {elapsed:.1f}s on a 0.4s budget — the wedge "
        f"guard has been weakened"
    )


@pytest.mark.asyncio
async def test_heartbeats_that_stop_kill_the_job_within_the_grace(mm, pool):
    """A download that dies mid-transfer stops heartbeating; the job must not
    coast to the hard cap on stale credit."""
    def dies_mid_download():
        mm.report_model_load_activity()
        time.sleep(10)  # silence forever after

    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, dies_mid_download, timeout=0.4)
    # Budget 0.4 + grace 0.5 + slack; far below the 2s extension cap ceiling.
    assert time.monotonic() - t0 < 1.8


@pytest.mark.asyncio
async def test_the_extension_is_capped(mm, pool):
    """Heartbeats forever must not mean waiting forever — the cap is the
    difference between patience and a hung worker nobody hears about."""
    stop = threading.Event()

    def heartbeats_forever():
        while not stop.wait(0.1):
            mm.report_model_load_activity()

    t0 = time.monotonic()
    try:
        with pytest.raises(mm.GpuJobTimeoutError):
            await _run(mm, pool, heartbeats_forever, timeout=0.4)
        elapsed = time.monotonic() - t0
        assert elapsed < 4.0, (
            f"cap is 0.4+2.0s but the job survived {elapsed:.1f}s"
        )
        assert elapsed > 2.0, "the cap fired before the extension it bounds"
    finally:
        stop.set()


@pytest.mark.asyncio
async def test_a_fast_job_is_unaffected(mm, pool):
    """The overwhelmingly common case must not pick up latency: the sliced
    wait returns as soon as the future resolves."""
    t0 = time.monotonic()
    assert await _run(mm, pool, lambda: "fast", timeout=5.0) == "fast"
    assert time.monotonic() - t0 < 1.0


@pytest.mark.asyncio
async def test_heartbeats_do_not_leak_between_jobs(mm, pool):
    """Thread idents are reused. Job A's heartbeats must not vouch for job B
    running later on the same pool thread — the entry is cleared when the job
    ends."""
    await _run(mm, pool, lambda: mm.report_model_load_activity(), timeout=5.0)
    assert not mm._MODEL_LOAD_ACTIVITY, (
        "a finished job left its heartbeat behind; a later job on the same "
        "thread would inherit unearned extension credit"
    )
    # ...and the silent-job guard still holds on that same thread.
    t0 = time.monotonic()
    with pytest.raises(mm.GpuJobTimeoutError):
        await _run(mm, pool, lambda: time.sleep(10), timeout=0.4)
    assert time.monotonic() - t0 < 1.5


@pytest.mark.asyncio
async def test_another_threads_heartbeat_does_not_extend_this_job(mm, pool):
    """The heartbeat is per worker thread, so activity elsewhere in the
    process is not this job's alibi."""
    stop = threading.Event()

    def other_thread():
        while not stop.wait(0.1):
            mm._MODEL_LOAD_ACTIVITY[999999999] = time.monotonic()

    t = threading.Thread(target=other_thread, daemon=True)
    t.start()
    try:
        t0 = time.monotonic()
        with pytest.raises(mm.GpuJobTimeoutError):
            await _run(mm, pool, lambda: time.sleep(10), timeout=0.4)
        assert time.monotonic() - t0 < 1.5
    finally:
        stop.set()
        mm._MODEL_LOAD_ACTIVITY.pop(999999999, None)


# ── the producer side ─────────────────────────────────────────────────────

def test_the_sidecar_progress_loop_reports_activity(mm, monkeypatch):
    """Pin the wiring: a progress frame arriving in SubprocessBackend.generate
    must bump the heartbeat. Without this, the waiter listens to a phone that
    never rings."""
    sb = importlib.import_module("services.subprocess_backend")

    calls = []
    monkeypatch.setattr(mm, "report_model_load_activity", lambda: calls.append(1))
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)

    class _Backend(sb.SubprocessBackend):
        id = "testengine"
        display_name = "test"

        @classmethod
        def is_available(cls):
            return True, "test"

        @classmethod
        def venv_python(cls):  # pragma: no cover - not spawned
            return sys.executable

        @classmethod
        def sidecar_script(cls):  # pragma: no cover - not spawned
            return __file__

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

    b = _Backend.__new__(_Backend)
    b._lock = threading.Lock()
    frames = [
        {"op": "progress", "stage": "loading_model", "percent": 1},
        {"op": "progress", "stage": "loading_model", "percent": 2},
        {"op": "audio", "audio_pcm_b64": "", "sample_rate": 24000, "n_samples": 0},
    ]
    monkeypatch.setattr(b, "_spawn", lambda: None)
    monkeypatch.setattr(b, "_send", lambda msg: None)
    monkeypatch.setattr(b, "_recv_with_timeout", lambda t: frames.pop(0))

    b.generate("hello")
    assert len(calls) == 2, (
        f"expected one heartbeat per progress frame, got {len(calls)}"
    )


def test_an_off_pool_synthesis_records_no_heartbeat(mm, monkeypatch):
    """The off-pool path (the diagnostic probe) never runs _job(), so its
    thread ident would never be CLEARED — and a pool worker later reusing that
    ident would inherit up to a grace period of unearned extension
    (CodeRabbit). Off-pool progress frames are therefore not heartbeats."""
    sb = importlib.import_module("services.subprocess_backend")

    calls = []
    monkeypatch.setattr(mm, "report_model_load_activity", lambda: calls.append(1))
    # Off-pool: generate() takes the slot-holding path, which needs a pool to
    # occupy — patch it as on-pool=False only for the heartbeat gate by driving
    # the loop with a thread whose name is NOT the pool prefix (the real
    # mechanism), while skipping the slot dance entirely.
    monkeypatch.setattr(mm, "running_on_gpu_pool",
                        lambda: threading.current_thread().name.startswith("gpu-pool"))

    class _Backend(sb.SubprocessBackend):
        id = "testengine2"
        display_name = "test"

        @classmethod
        def is_available(cls):
            return True, "test"

        @classmethod
        def venv_python(cls):  # pragma: no cover - not spawned
            return sys.executable

        @classmethod
        def sidecar_script(cls):  # pragma: no cover - not spawned
            return __file__

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

    b = _Backend.__new__(_Backend)
    b._lock = threading.Lock()
    frames = [
        {"op": "progress", "stage": "loading_model", "percent": 1},
        {"op": "audio", "audio_pcm_b64": "", "sample_rate": 24000, "n_samples": 0},
    ]
    monkeypatch.setattr(b, "_spawn", lambda: None)
    monkeypatch.setattr(b, "_send", lambda msg: None)
    monkeypatch.setattr(b, "_recv_with_timeout", lambda t: frames.pop(0))

    result = {}

    def run_plain():
        # This thread's name is not "gpu-pool*", so the heartbeat gate must
        # refuse even though progress frames arrive. (The off-pool slot dance
        # runs against the real pool — same as the production path it mirrors.)
        try:
            b.generate("hello")
        except Exception as e:  # pragma: no cover - surfaced via result
            result["err"] = e

    t = threading.Thread(target=run_plain, name="not-a-pool-thread")
    t.start(); t.join()
    assert "err" not in result, result.get("err")
    assert calls == [], "an off-pool synthesis recorded a heartbeat it cannot clear"


@pytest.mark.asyncio
async def test_caller_cancellation_consumes_the_future(mm, pool, monkeypatch):
    """The old wait_for cancelled the wrapper as a side effect of its own
    timeout machinery; asyncio.wait does not, so the CancelledError branch has
    to do both halves itself — cancel the wrapper AND register the consumer —
    or the job's eventual result is logged as "Future exception was never
    retrieved" (CodeRabbit). This executes that branch: it fails against a
    version that only re-raises.
    """
    swallowed = []
    monkeypatch.setattr(mm, "_swallow_abandoned",
                        lambda fut: swallowed.append(fut))

    release = threading.Event()

    def slow_job():
        release.wait(5)
        raise RuntimeError("the abandoned job's parting words")

    started = threading.Event()

    def slow_job_started():
        started.set()
        return slow_job()

    task = asyncio.ensure_future(_run(mm, pool, slow_job_started, timeout=30.0))
    # Cancel only once the job is genuinely executing (phase 2), so the branch
    # under test — not the phase-1 queue path — is the one that runs.
    while not started.is_set():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert swallowed, (
        "cancellation did not register a consumer for the abandoned future — "
        "its eventual exception will be logged as never retrieved"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('worker_error', [None, ValueError, TimeoutError])
async def test_completion_wins_when_timeout_wait_resumes_late(mm, pool, monkeypatch, worker_error):
    """A completed worker has cleared its heartbeat before its waiter resumes."""
    from types import SimpleNamespace

    clock = [100.0]
    monkeypatch.setattr(mm, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    release = threading.Event()
    real_wait = asyncio.wait
    calls = 0

    def worker():
        mm.report_model_load_activity()
        assert release.wait(5), 'test did not release worker'
        if worker_error:
            raise worker_error('worker failure')
        return 'completed audio'

    async def delayed_wait(futures, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            # The timeout has fired, but the completed worker's result arrives
            # before this waiter resumes and makes its next deadline decision.
            release.set()
            done, pending = await real_wait(futures, timeout=5)
            assert done and not pending
            clock[0] += 1.0
            return set(), set(futures)
        return await real_wait(futures, **kwargs)

    monkeypatch.setattr(asyncio, 'wait', delayed_wait)
    try:
        if worker_error:
            with pytest.raises(worker_error, match='worker failure'):
                await _run(mm, pool, worker, timeout=0.4)
        else:
            assert await _run(mm, pool, worker, timeout=0.4) == 'completed audio'
    finally:
        release.set()


def test_generating_heartbeats_are_not_credited_as_model_load_progress(mm, monkeypatch):
    """Review (CodeRabbit, #2435): a sidecar's timer-thread heartbeat during
    ``generate`` re-arms the recv watchdog but is not evidence the model is
    loading or synthesising, so it must not extend the GPU worker's budget."""
    sb = importlib.import_module("services.subprocess_backend")
    calls = []
    monkeypatch.setattr(mm, "report_model_load_activity", lambda: calls.append(1))
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)

    class _Backend(sb.SubprocessBackend):
        id = "testengine"
        display_name = "test"

        @classmethod
        def is_available(cls):
            return True, "test"

        @classmethod
        def venv_python(cls):  # pragma: no cover - not spawned
            return sys.executable

        @classmethod
        def sidecar_script(cls):  # pragma: no cover - not spawned
            return __file__

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

    b = _Backend.__new__(_Backend)
    b._lock = threading.Lock()
    frames = [
        {"op": "progress", "stage": "loading_model", "percent": 1},
        {"op": "progress", "stage": "generating", "percent": 2},
        {"op": "progress", "stage": "generating", "percent": 3},
        {"op": "audio", "audio_pcm_b64": "", "sample_rate": 24000, "n_samples": 0},
    ]
    monkeypatch.setattr(b, "_spawn", lambda: None)
    monkeypatch.setattr(b, "_send", lambda msg: None)
    monkeypatch.setattr(b, "_recv_with_timeout", lambda t: frames.pop(0))

    b.generate("hello")
    assert len(calls) == 1, f"only the cold-load frame is load evidence, got {len(calls)}"
