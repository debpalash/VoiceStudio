"""#2394: two cold TTS loads must never overlap, whichever route started them.

A reporter's backend died 16 s after startup with
``exit code -1073741819`` — ``0xC0000005`` STATUS_ACCESS_VIOLATION — right
after two ``generate:start (audio)`` clicks, with ``Preloading TTS model in
background…`` still live in the captured log.

There were two cold-load routes into the same native
``VoiceStudio.from_pretrained``:

  * the startup preload (and any server-loop ``get_model()``) held
    ``_model_lock`` and ran the load *in the GPU pool*;
  * a generate reaching ``OmniVoiceBackend._ensure_loaded()`` on a pool worker
    could not await ``_model_lock`` (bound to the server loop, #1417), so it
    took ``_model_load_thread_lock`` instead and loaded INLINE.

Those two locks are disjoint, and ``_pick_gpu_workers()`` gives CUDA hosts up
to four pool workers (#567) — so a generate landing on worker #2 while the
preload still held only ``_model_lock`` found the thread lock free, saw
``model is None``, and entered the native load CONCURRENTLY with the preload.
Two overlapping torch loads in one process is what Windows answers with an
access violation; #1669 is the same class on the ASR side.

Fail-before: two concurrent ``_load_model_sync`` calls, and the second caller
wastes a full load. Pass-after: exactly one load, and the waiter adopts it.

The test drives the real shape — a preload on the server loop holding
``_model_lock``, plus a generate on a pool-named thread — and asserts on
overlap rather than on lock identity, so it still fails if the exclusion is
re-implemented wrongly rather than only if it is removed.
"""

from __future__ import annotations

import asyncio
import importlib
import threading
import time

import pytest


@pytest.fixture
def mm():
    """Resolve per test — a collection-time binding can go stale when another
    suite rebinds ``services.model_manager`` in ``sys.modules``."""
    return importlib.import_module("services.model_manager")


class _ConcurrentLoadProbe:
    """Stands in for ``_load_model_sync`` and records whether two ever overlap.

    ``max_in_flight`` is the assertion that matters: a real native load that
    overlaps another is unrecoverable, so the fix has to make the *count* one,
    not merely make both callers eventually succeed.

    Explicit coordination, no sleeps (#2394 review): the FIRST load parks on
    ``release`` until the test has confirmed the second route actually reached
    the load boundary and was excluded there. A timed wait would only prove
    the overlap if scheduling happened to cooperate — on a loaded CI runner the
    second thread can be descheduled past the window and the broken
    implementation would sail through.
    """

    def __init__(self, on_enter=None) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.max_in_flight = 0
        self.model = object()
        self._on_enter = on_enter
        #: Set by the first load once it is inside; the test waits on this.
        self.entered = threading.Event()
        #: The first load parks here until the test releases it.
        self.release = threading.Event()

    def __call__(self):
        with self._lock:
            self.calls += 1
            index = self.calls
            self.max_in_flight = max(self.max_in_flight, index)
        # Counted here, at the native loader itself, not at the lock — this is
        # the overlap that kills the process. Only entries BEYOND the first
        # count as a violation; the first is the load doing the work.
        if index > 1 and self._on_enter is not None:
            self._on_enter()
        self.entered.set()
        # Only the first load parks; a second one that wrongly gets in returns
        # at once so the overlap it represents is visible immediately.
        if self.calls == 1:
            assert self.release.wait(30), "test never released the first load"
        return self.model


def _drive_both_cold_routes(mm):
    """Run the preload and generate cold routes concurrently.

    The generate side runs on a thread named like a pool worker, which is what
    ``running_on_gpu_pool()`` keys on (#1417) — the real thing is reached from
    ``OmniVoiceBackend._ensure_loaded()``, which is already on that pool.
    """
    results: dict[str, object] = {}
    # Instrumentation for the exclusion itself: a second route that reaches the
    # load boundary must be seen arriving there, and it must not get past it
    # while the first load is parked. `arrived` counts boundary arrivals,
    # `past` counts entrants into the native loader.
    lock = threading.RLock()
    stats = {"arrived": 0, "past": 0}
    probe = _ConcurrentLoadProbe(on_enter=lambda: stats.__setitem__("past", stats["past"] + 1))

    class _ObservedLock:
        """The load lock, instrumented — still a real RLock underneath."""

        def __init__(self, inner):
            self._inner = inner

        def acquire(self, *a, **kw):
            with lock:
                stats["arrived"] += 1
            return self._inner.acquire(*a, **kw)

        def release(self):
            return self._inner.release()

    def _call(key: str) -> None:
        try:
            results[key] = asyncio.run(mm.get_model())
        except BaseException as exc:  # noqa: BLE001 — the failure IS the subject
            results[key] = exc

    original_lock = mm._model_load_thread_lock
    original_loader = mm._load_model_sync
    mm._model_load_thread_lock = _ObservedLock(original_lock)
    mm._load_model_sync = probe
    try:
        preload = threading.Thread(target=_call, args=("preload",), name="server-loop")
        generate = threading.Thread(
            target=_call, args=("generate",), name=f"{mm._GPU_POOL_THREAD_PREFIX}1"
        )
        # Start the preload and wait until it is genuinely inside the native
        # loader — not merely launched — so the second route has something real
        # to be excluded from.
        preload.start()
        assert probe.entered.wait(30), "the preload never entered the native loader"

        generate.start()
        # The second route must reach the load boundary and be refused there.
        # Poll the instrumented lock rather than sleeping a fixed interval.
        deadline = time.monotonic() + 30
        while stats["arrived"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stats["arrived"] >= 2, (
            "the generate route never reached the load boundary, so the "
            "exclusion was never exercised"
        )
        # Give the (correctly blocked) second route a real chance to be
        # scheduled while the first load is still parked — without this, a
        # broken implementation could pass simply by never being given the CPU.
        settled = time.monotonic() + 2
        while time.monotonic() < settled:
            if stats["past"] > 0:
                break
            time.sleep(0.01)
        assert stats["past"] == 0, (
            f"{stats['past']} route(s) entered the native load while the "
            "preload still held it — exclusion is not working"
        )
    finally:
        # Release the parked first load before anything is asserted, or the
        # worker thread would outlive the test and hold module state.
        probe.release.set()
        preload.join(timeout=30)
        generate.join(timeout=30)
        mm._model_load_thread_lock = original_lock
        mm._load_model_sync = original_loader

    return (
        results.get("preload"),
        results.get("generate"),
        probe,
        preload,
        generate,
    )


def _isolate(mm, monkeypatch, probe=None):
    """Fresh locks + a probe loader, so no other test's state can mask a race."""
    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(mm, "_model_load_thread_lock", threading.RLock(), raising=False)
    # Abandonment state is process-global; a leftover from another test would
    # make every load here fail fast for the wrong reason.
    for flag in ("_load_in_progress", "_load_abandoned"):
        if hasattr(mm, flag):
            getattr(mm, flag).clear()
    if probe is not None:
        monkeypatch.setattr(mm, "_load_model_sync", probe, raising=False)
    # Reclaim touches real memory/disk probes; keep the leaf's other work out.
    monkeypatch.setattr(mm, "_make_room_before_tts_load", lambda: None, raising=False)


def test_a_generate_cannot_enter_the_native_load_during_a_background_preload(
    mm, monkeypatch
):
    """THE CRASH. Fail-before: two overlapping ``from_pretrained`` calls."""
    _isolate(mm, monkeypatch)

    preload_result, generate_result, probe, preload, generate = _drive_both_cold_routes(mm)

    # A timed-out thread is still inside `get_model()`; touching module state
    # under it would leak a live thread into later tests.
    assert not preload.is_alive() and not generate.is_alive(), (
        "a cold load never returned — the routes are deadlocking instead of "
        "excluding each other"
    )
    monkeypatch.setattr(mm, "model", None, raising=False)

    assert not isinstance(preload_result, BaseException), preload_result
    assert not isinstance(generate_result, BaseException), generate_result

    assert probe.max_in_flight == 1, (
        f"{probe.max_in_flight} native loads ran at once — overlapping torch "
        "loads in one process is the Windows access violation (#2394)"
    )
    assert probe.calls == 1, (
        f"the native load ran {probe.calls} times; the waiter must adopt the "
        "model the first load published, not start its own"
    )
    # Both callers get the one published model — the fix is exclusion, not a
    # second load that happens not to crash.
    assert preload_result is probe.model
    assert generate_result is probe.model


def test_a_lone_cold_load_still_loads_and_publishes(mm, monkeypatch):
    """The exclusion must not turn into "never load"."""
    probe = _ConcurrentLoadProbe()
    probe.release.set()  # nothing else will arrive to exclude
    _isolate(mm, monkeypatch, probe)
    reclaimed = []
    monkeypatch.setattr(
        mm, "_make_room_before_tts_load", lambda: reclaimed.append(1), raising=False
    )

    loaded = asyncio.run(mm.get_model())

    assert loaded is probe.model
    assert mm.model is probe.model
    assert probe.calls == 1
    # Reclaim moved into the shared leaf, so the inline pool-worker route gets
    # it too — it used to keep a private second copy that could drift.
    assert reclaimed == [1], (
        "the pre-load reclaim must run once, immediately before the load"
    )


def test_a_warm_model_never_takes_the_load_lock(mm, monkeypatch):
    """The guard must not serialise the hot path behind a load lock."""
    probe = _ConcurrentLoadProbe()
    resident = object()
    monkeypatch.setattr(mm, "model", resident, raising=False)
    monkeypatch.setattr(mm, "_load_model_sync", probe, raising=False)

    async def _no_heal():
        return None

    monkeypatch.setattr(mm, "_heal_tts_placement", _no_heal, raising=False)
    monkeypatch.setattr(mm, "make_room_before_generate", lambda: None, raising=False)

    assert asyncio.run(mm.get_model()) is resident
    assert probe.calls == 0, "a resident model must not re-enter the cold load"


def test_a_retry_after_a_timed_out_load_must_not_block_forever(mm, monkeypatch):
    """#2394 follow-up: the exclusion must not outlive the timeout it caused.

    ``asyncio.wait_for`` cancels the *await*, not the thread: the worker stays
    inside the native ``from_pretrained`` and keeps holding the load lock. The
    pool reset drops the pool, not that thread. So a wedged load pins the lock
    indefinitely, and because the timeout error advises "then retry", every
    retry queues on a lock nobody will ever release — turning one visible
    failure into a backend that never answers again.

    Fail-before: this second call never returns. The assertion that matters is
    the join timeout below; without it a regression would hang the suite rather
    than fail it.
    """
    from concurrent.futures import ThreadPoolExecutor

    wedged = threading.Event()
    release = threading.Event()

    def _never_finishes():
        wedged.set()
        release.wait(30.0)  # the native load that cannot be interrupted
        return object()

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(mm, "_model_load_thread_lock", threading.RLock(), raising=False)
    monkeypatch.setattr(mm, "_load_model_sync", _never_finishes, raising=False)
    monkeypatch.setattr(mm, "_make_room_before_tts_load", lambda: None, raising=False)
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.3, raising=False)

    # Two workers, as a CUDA pool has (#567). One wedged worker must not stop
    # the retry from reaching the lock — otherwise this test would prove the
    # queueing, not the locking.
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-pool")
    monkeypatch.setattr(mm, "_get_gpu_pool", lambda: pool, raising=False)

    outcome: dict[str, object] = {}

    def _first_attempt():
        try:
            asyncio.run(mm.get_model())
        except BaseException as exc:  # noqa: BLE001 — the failure IS the subject
            outcome["first"] = exc

    try:
        first = threading.Thread(target=_first_attempt, daemon=True)
        first.start()
        first.join(timeout=30)
        assert wedged.wait(10), "the load never reached the native loader"
        assert isinstance(outcome.get("first"), RuntimeError), outcome

        # The retry the timeout error tells the user to perform.
        def _retry():
            outcome["second"] = _run_capture(mm)

        second = threading.Thread(target=_retry, daemon=True)
        second.start()
        second.join(timeout=15)
        assert not second.is_alive(), (
            "a retry after a timed-out load never returned: it re-queued on the "
            "load lock the abandoned worker still holds"
        )

        retry_error = outcome.get("second")
        assert isinstance(retry_error, RuntimeError), retry_error
        # It must name the real remedy. Telling this user to "check your
        # connection, then retry" is exactly what made the original report
        # unactionable: the retry cannot succeed, because the load it waits on
        # was already abandoned and cannot be interrupted.
        assert "restart" in str(retry_error).lower(), (
            "a retry behind an abandoned load must say to restart the backend, "
            f"not to retry again: {retry_error}"
        )
    finally:
        # Let the wedged worker finish so the interpreter can exit; a
        # non-daemon pool worker would otherwise hang the whole suite.
        release.set()
        monkeypatch.setattr(mm, "model", None, raising=False)
        pool.shutdown(wait=False)


def _run_capture(mm):
    try:
        return asyncio.run(mm.get_model())
    except BaseException as exc:  # noqa: BLE001 — the failure IS the subject
        return exc


def _wedge_setup(mm, monkeypatch, loader):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(mm, "_model_load_thread_lock", threading.RLock(), raising=False)
    monkeypatch.setattr(mm, "_load_model_sync", loader, raising=False)
    monkeypatch.setattr(mm, "_make_room_before_tts_load", lambda: None, raising=False)
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.3, raising=False)
    mm._load_abandoned.clear()
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-pool")
    monkeypatch.setattr(mm, "_get_gpu_pool", lambda: pool, raising=False)
    return pool


def test_an_abandoned_load_that_later_fails_does_not_strand_the_backend(mm, monkeypatch):
    """Review (Greptile P1): the abandoned verdict must die with its loader.

    A timed-out load that eventually FAILS releases the lock but used to leave
    ``_load_abandoned`` set, so every later cold load was refused until restart
    although nothing was stuck any more.
    """
    wedged, release = threading.Event(), threading.Event()
    calls = []

    def _loader():
        calls.append(1)
        if len(calls) == 1:
            wedged.set()
            release.wait(30)
            raise RuntimeError("late native load failure")
        return object()

    pool = _wedge_setup(mm, monkeypatch, _loader)
    try:
        first = _run_capture(mm)
        assert isinstance(first, mm.ModelLoadAbandoned), first
        assert wedged.is_set()
        assert mm._load_abandoned.is_set()

        release.set()  # the wedged loader now dies on its own
        deadline = time.monotonic() + 15
        while mm._load_abandoned.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not mm._load_abandoned.is_set(), (
            "the abandoned flag outlived its loader; the backend stays refused"
        )
        monkeypatch.setattr(mm, "_get_gpu_pool", lambda: pool, raising=False)
        retry = _run_capture(mm)
        assert not isinstance(retry, BaseException), retry
    finally:
        release.set()
        monkeypatch.setattr(mm, "model", None, raising=False)
        pool.shutdown(wait=False)


def test_a_caller_queued_behind_another_load_does_not_brand_it_abandoned(mm, monkeypatch):
    """Review (Greptile P1): blame the worker that timed out, not whichever
    load happens to be running.

    Caller B times out while still queued behind caller A's healthy, in-flight
    load. A's load must not be marked abandoned.
    """
    in_native, release = threading.Event(), threading.Event()
    published = object()

    def _loader():
        in_native.set()
        release.wait(30)
        return published

    pool = _wedge_setup(mm, monkeypatch, _loader)
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 5.0, raising=False)
    try:
        # A: an inline-route load that holds the lock with no deadline.
        a_result: dict[str, object] = {}
        a = threading.Thread(
            target=lambda: a_result.setdefault("r", mm._load_model_exclusive()),
            daemon=True,
        )
        a.start()
        assert in_native.wait(10)

        # B: a pool route whose deadline (shortened) passes while queued.
        monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.3, raising=False)
        b = _run_capture(mm)
        assert isinstance(b, RuntimeError), b
        assert not mm._load_abandoned.is_set(), (
            "a queued caller's timeout branded another caller's live load as stuck"
        )
        release.set()
        a.join(timeout=15)
        assert a_result.get("r") is published
    finally:
        release.set()
        monkeypatch.setattr(mm, "model", None, raising=False)
        pool.shutdown(wait=False)


def test_a_queued_caller_hears_about_an_abandonment_that_happens_while_it_waits(
    mm, monkeypatch
):
    """Review (Greptile P1): a generate that began waiting behind a healthy
    preload had already passed the up-front abandonment check. When the preload
    is then abandoned it must fail promptly, not sit out its whole own budget."""
    in_native, release = threading.Event(), threading.Event()

    def _loader():
        in_native.set()
        release.wait(30)
        return object()

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "_model_load_thread_lock", threading.RLock(), raising=False)
    monkeypatch.setattr(mm, "_load_model_sync", _loader, raising=False)
    monkeypatch.setattr(mm, "_make_room_before_tts_load", lambda: None, raising=False)
    mm._load_abandoned.clear()

    holder = threading.Thread(target=lambda: mm._load_model_exclusive(60.0), daemon=True)
    holder.start()
    assert in_native.wait(10)

    outcome: dict[str, object] = {}
    waiting = threading.Event()
    real_acquire = mm._acquire_load_lock

    def _observed(deadline):
        waiting.set()  # the caller is past the up-front check and is queueing
        return real_acquire(deadline)

    monkeypatch.setattr(mm, "_acquire_load_lock", _observed)

    def _queued():
        try:
            mm._load_model_exclusive(60.0)
        except BaseException as exc:  # noqa: BLE001 - the failure IS the subject
            outcome["error"] = exc

    queued = threading.Thread(target=_queued, daemon=True)
    try:
        queued.start()
        assert waiting.wait(10)
        mm._load_abandoned.set()  # the preload times out and is given up on
        queued.join(timeout=10)
        assert not queued.is_alive(), "the queued caller kept waiting its whole budget"
        assert isinstance(outcome.get("error"), mm.ModelLoadAbandoned), outcome
        assert "restart" in str(outcome["error"]).lower()
    finally:
        release.set()
        holder.join(timeout=10)
        mm._load_abandoned.clear()
        monkeypatch.setattr(mm, "model", None, raising=False)
