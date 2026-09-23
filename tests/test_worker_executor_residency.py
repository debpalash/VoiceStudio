"""What the worker executor does between assignments, and to whose engine.

Three defects live here, and all three are invisible to a test that only
checks that a task produced audio:

* **B3** — the executor built a fresh engine instance per assignment, so every
  remote job paid a full cold load. That is also what made "unload models idle
  for 10 minutes" meaningless: nothing was ever resident to unload.
* **B8** — ``model_id`` on the wire was the engine's *display name*, while it
  keys circuit breakers, per-model slots and residency, and is persisted on the
  task row. A copy edit to a label orphaned that history.
* **B14** — remote synthesis never reached ``mark_synthetic``, so audio
  rendered on a worker shipped with no provenance mark at all.

Plus the bound that makes the lease honest: the worker's own ``wait_for`` on
its blocking calls, so a wedged GPU thread ends as a classified timeout rather
than as silence the server has to guess about.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from services import tts_backend
from worker import capabilities
from worker.errors import ErrorClass
from worker.executor import TaskExecutor, TaskFailure
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S


# ── Fixtures ───────────────────────────────────────────────────────────────


class _FakeAssignment:
    """Only the attributes the executor reads — a real pb message needs a ref
    and a session the executor has no business knowing about."""

    class _Deadlines:
        def __init__(self, model_load_seconds: int, execution_seconds: int) -> None:
            self.model_load_seconds = model_load_seconds
            self.execution_seconds = execution_seconds

    def __init__(
        self,
        *,
        engine: str = "fake-engine",
        params_json: str = '{"text": "hello"}',
        model_load_seconds: int = 600,
        execution_seconds: int = 300,
    ) -> None:
        self.operation = "tts"
        self.engine = engine
        self.params_json = params_json
        self.deadlines = self._Deadlines(model_load_seconds, execution_seconds)


class _FakeBackend:
    """Counts its own construction, loads and generations."""

    id = "fake-engine"
    display_name = "Fake Engine (test)"
    sample_rate = 48_000

    constructed = 0
    unloaded = 0

    def __init__(self) -> None:
        type(self).constructed += 1
        self.ready = 0
        self.generated = 0

    def ensure_ready(self) -> None:
        self.ready += 1

    def generate(self, text, **kwargs):
        self.generated += 1
        import torch

        return torch.zeros(240)

    def unload(self) -> None:
        type(self).unloaded += 1


def test_dub_worker_forwards_seed_into_mps_proxy():
    calls = []

    class Proxy:
        sample_rate = 24_000
        applies_own_mastering = False
        supports_native_omnivoice_controls = True

        def generate(self, text, **kwargs):
            calls.append((text, kwargs))
            import torch

            return torch.zeros(1, 240)

    TaskExecutor._synthesize_dub_segment(Proxy(), {
        "text": "hello", "seed": 123, "effect_preset": "raw",
    })

    assert calls[0][1]["seed"] == 123


@pytest.mark.asyncio
async def test_cancelling_execution_drains_the_blocking_engine_thread(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_load(_engine):
        started.set()
        release.wait(BARRIER_WATCHDOG_S)
        finished.set()
        return _FakeBackend()

    monkeypatch.setattr(TaskExecutor, "_load_backend", staticmethod(blocked_load))
    execution = asyncio.create_task(TaskExecutor().execute(_FakeAssignment()))
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=HANG_GUARD_S)

    try:
        execution.cancel()
        await asyncio.sleep(0)
        assert not execution.done(), "authority returned while the load thread was active"
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execution, timeout=HANG_GUARD_S)
    assert finished.is_set()

@pytest.fixture(autouse=True)
def _live_tts_backend():
    """Re-resolve this module's ``tts_backend`` alias against sys.modules.

    ``tests/backend/conftest.py`` purges ``services.*`` after every test it
    runs, so by the time a full-suite run reaches this file the import-time
    alias above points at a dead module object — one whose ``_REGISTRY`` and
    ``_ENGINE_INSTANCES`` nothing under test will ever read, because
    ``executor._load_backend`` imports at call time and gets a fresh module.
    Patching the stale alias is invisible to production code, which is why
    these tests pass alone and fail in the suite. Same hazard the
    ``asr_model_installed`` fixture documents in tests/conftest.py.
    """
    global tts_backend

    import services.tts_backend  # noqa: PLC0415 — must resolve post-purge

    tts_backend = services.tts_backend


@pytest.fixture
def engine_cache(monkeypatch, _live_tts_backend):
    """An empty instance cache plus a registered fake engine, both restored."""
    _FakeBackend.constructed = 0
    _FakeBackend.unloaded = 0
    monkeypatch.setitem(tts_backend._REGISTRY, "fake-engine", _FakeBackend)
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    return tts_backend._ENGINE_INSTANCES


@pytest.fixture
def no_watermark(monkeypatch):
    """Marking is exercised separately; keep it out of the other assertions."""
    from services import watermark

    monkeypatch.setattr(watermark, "mark_synthetic", lambda audio, sr, **kw: audio)


# ── B3: the engine stays resident between tasks ────────────────────────────


@pytest.mark.asyncio
async def test_the_engine_is_built_once_across_tasks(engine_cache, no_watermark):
    """The regression: ``return cls()`` per assignment, cached nowhere."""
    executor = TaskExecutor()

    await executor.execute(_FakeAssignment())
    await executor.execute(_FakeAssignment())

    assert _FakeBackend.constructed == 1, "each task paid its own cold load"
    assert engine_cache[_FakeBackend].generated == 2


@pytest.mark.asyncio
async def test_the_load_phase_actually_loads_the_weights(engine_cache, no_watermark):
    """Adapters load lazily inside generate(). Without ensure_ready() the load
    phase is instant, the cold load runs under the execution budget, and the
    two-phase split the protocol mirrors (#1033/#1037) is decorative."""
    await TaskExecutor().execute(_FakeAssignment())

    assert engine_cache[_FakeBackend].ready == 1


def test_the_assignment_names_the_engine_not_this_machines_preference(engine_cache):
    """``get_active_tts_backend`` resolves the WORKER's own Settings choice. A
    remote assignment for one engine would silently run another, producing
    wrong audio while the control plane's slots and breaker history point at
    the engine it thinks ran."""
    resolved = TaskExecutor._load_backend("fake-engine")

    assert isinstance(resolved, _FakeBackend)


def test_an_unknown_engine_is_a_capability_failure(engine_cache):
    with pytest.raises(TaskFailure) as excinfo:
        TaskExecutor._load_backend("no-such-engine")

    assert excinfo.value.error.code == "MODEL_NOT_INSTALLED"
    assert excinfo.value.error.error_class is ErrorClass.CAPABILITY


def test_the_router_and_the_worker_share_one_cache():
    """Two caches with no coordination is the #1169-adjacent memory bug
    engine_memory.py exists to prevent — and eviction there reaches for the
    router's name."""
    from api.routers import engines

    assert engines._ENGINE_INSTANCES is tts_backend._ENGINE_INSTANCES


# ── Requirement 6: ten-minute idle unload ──────────────────────────────────


def test_an_idle_engine_is_unloaded_and_a_busy_one_is_not(engine_cache):
    tts_backend.get_engine_instance(_FakeBackend, now=0.0)

    assert tts_backend.release_idle_engines(600.0, now=599.0) == []
    assert tts_backend.release_idle_engines(600.0, now=600.0) == ["fake-engine"]
    assert _FakeBackend.unloaded == 1
    assert _FakeBackend not in engine_cache


def test_reuse_restarts_the_idle_clock(engine_cache):
    tts_backend.get_engine_instance(_FakeBackend, now=0.0)
    tts_backend.get_engine_instance(_FakeBackend, now=500.0)

    assert tts_backend.release_idle_engines(600.0, now=1_000.0) == []
    assert tts_backend.release_idle_engines(600.0, now=1_100.0) == ["fake-engine"]


def test_the_sweep_releases_least_recently_used_first(engine_cache):
    class _Other(_FakeBackend):
        id = "other-engine"

    tts_backend.get_engine_instance(_Other, now=0.0)
    tts_backend.get_engine_instance(_FakeBackend, now=1.0)

    assert tts_backend.release_idle_engines(600.0, now=700.0) == [
        "other-engine",
        "fake-engine",
    ]


def test_a_raising_unload_does_not_strand_the_rest(engine_cache):
    class _Stuck(_FakeBackend):
        id = "stuck-engine"

        def unload(self):
            raise RuntimeError("driver wedged")

    tts_backend.get_engine_instance(_Stuck, now=0.0)
    tts_backend.get_engine_instance(_FakeBackend, now=0.0)

    released = tts_backend.release_idle_engines(600.0, now=700.0)

    assert released == ["stuck-engine", "fake-engine"]
    assert dict(engine_cache) == {}, "a stuck unload must not pin the cache"


def test_a_running_job_is_not_unloaded_out_from_under_itself(engine_cache):
    """A 40-minute dub touches the cache once, at the start. On elapsed time
    alone it is indistinguishable from a model nobody wants any more."""
    instance = tts_backend.get_engine_instance(_FakeBackend, now=0.0)

    with tts_backend.engine_in_use(instance, now=5_000.0):
        assert tts_backend.release_idle_engines(600.0, now=4_000.0) == []

    # Idle is measured from when the work FINISHED, not from when it started.
    assert tts_backend.release_idle_engines(600.0, now=5_500.0) == []
    assert tts_backend.release_idle_engines(600.0, now=5_601.0) == ["fake-engine"]


def test_concurrent_jobs_each_hold_the_engine(engine_cache):
    instance = tts_backend.get_engine_instance(_FakeBackend, now=0.0)

    with tts_backend.engine_in_use(instance, now=10.0):
        with tts_backend.engine_in_use(instance, now=5.0):
            pass
        assert tts_backend.release_idle_engines(600.0, now=9_000.0) == []

    assert tts_backend.release_idle_engines(600.0, now=9_000.0) == ["fake-engine"]


def test_concurrent_cache_misses_construct_one_engine(engine_cache):
    import concurrent.futures
    import time

    created = 0

    class _Slow(_FakeBackend):
        id = "slow-engine"

        def __init__(self):
            nonlocal created
            time.sleep(0.01)
            created += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _n: tts_backend.get_engine_instance(_Slow), range(8)))

    assert created == 1
    assert len({id(instance) for instance in instances}) == 1


def test_a_failing_job_releases_its_hold(engine_cache):
    instance = tts_backend.get_engine_instance(_FakeBackend, now=0.0)

    with pytest.raises(RuntimeError):
        with tts_backend.engine_in_use(instance, now=0.0):
            raise RuntimeError("cuda abort")

    assert tts_backend.release_idle_engines(600.0, now=700.0) == ["fake-engine"]


def test_the_sweep_forgets_instances_someone_else_evicted(engine_cache):
    """engine_memory and model_lifecycle pop straight out of the cache; the
    timestamps must not keep those classes alive forever."""
    tts_backend.get_engine_instance(_FakeBackend, now=0.0)
    engine_cache.pop(_FakeBackend)

    assert tts_backend.release_idle_engines(600.0, now=700.0) == []
    assert tts_backend._ENGINE_LAST_USED == {}


# ── The worker-side execution bound ────────────────────────────────────────


def _wedge(monkeypatch, attribute: str):
    """Block one worker thread inside ``attribute`` until the caller releases it.

    The wedge is released by the test rather than left to expire: the event
    loop's teardown joins its own executor threads, so a thread still stuck
    there costs the whole suite that wall time. Releasing it after the
    assertion tests the same thing — the executor gave up while the thread was
    still running, which is the entire point of a bound it cannot cancel.
    """
    import threading

    released = threading.Event()
    monkeypatch.setattr(_FakeBackend, attribute, lambda *a, **k: released.wait(30))
    return released


@pytest.mark.asyncio
async def test_a_wedged_generate_ends_as_a_classified_timeout(engine_cache, monkeypatch):
    """A GPU thread that never returns cannot be cancelled — but the task must
    still end, named, rather than as silence the server has to interpret."""
    released = _wedge(monkeypatch, "generate")
    try:
        with pytest.raises(TaskFailure) as excinfo:
            await TaskExecutor().execute(_FakeAssignment(execution_seconds=1))
    finally:
        released.set()

    assert excinfo.value.error.code == "EXECUTION_TIMEOUT"
    assert excinfo.value.error.error_class is ErrorClass.TIMEOUT


@pytest.mark.asyncio
async def test_a_wedged_load_is_named_as_a_load_timeout(engine_cache, monkeypatch):
    """The load budget is separate from the execution budget on the wire; a
    load reported as EXECUTION_TIMEOUT sends the operator to the wrong place."""
    released = _wedge(monkeypatch, "ensure_ready")
    try:
        with pytest.raises(TaskFailure) as excinfo:
            await TaskExecutor().execute(_FakeAssignment(model_load_seconds=1))
    finally:
        released.set()

    assert excinfo.value.error.code == "MODEL_LOAD_TIMEOUT"


@pytest.mark.asyncio
async def test_an_assignment_without_deadlines_still_runs(engine_cache, no_watermark):
    """Zero means "the server stated none", never "no time at all"."""
    result = await TaskExecutor().execute(
        _FakeAssignment(model_load_seconds=0, execution_seconds=0)
    )

    assert result["payload"]


# ── B14: provenance marking happens on the worker ──────────────────────────


def _marks(monkeypatch) -> list[dict]:
    from services import watermark

    seen: list[dict] = []

    def _record(audio, sample_rate, *, context, force=False):
        seen.append({"sample_rate": sample_rate, "context": context, "force": force})
        return audio

    monkeypatch.setattr(watermark, "mark_synthetic", _record)
    return seen


@pytest.mark.asyncio
async def test_remote_audio_is_provenance_marked_before_encoding(engine_cache, monkeypatch):
    seen = _marks(monkeypatch)

    await TaskExecutor().execute(_FakeAssignment())

    assert seen == [
        {"sample_rate": 48_000, "context": "worker.executor.tts", "force": True}
    ]


@pytest.mark.asyncio
async def test_the_control_planes_answer_governs_not_the_workers(engine_cache, monkeypatch):
    """``force=True`` is the point: the pref belongs to whoever asked for the
    audio, not to whoever owns the GPU that rendered it."""
    seen = _marks(monkeypatch)

    await TaskExecutor().execute(
        _FakeAssignment(params_json='{"text": "hi", "watermark": true}')
    )
    assert [m["force"] for m in seen] == [True]

    await TaskExecutor().execute(
        _FakeAssignment(params_json='{"text": "hi", "watermark": false}')
    )
    assert len(seen) == 1, "the user declined; nothing should have been embedded"


@pytest.mark.asyncio
async def test_marking_never_costs_the_audio(engine_cache, monkeypatch):
    """Degrade, don't block — the same contract generation.py has always had."""
    from services import watermark

    def _boom(*args, **kwargs):
        raise RuntimeError("audioseal exploded")

    monkeypatch.setattr(watermark, "mark_synthetic", _boom)

    result = await TaskExecutor().execute(_FakeAssignment())

    assert result["payload"]


@pytest.mark.asyncio
async def test_the_engines_own_rate_is_the_fallback(engine_cache, no_watermark):
    """A flat 24 kHz default plays a 48 kHz engine's output at half speed."""
    result = await TaskExecutor().execute(_FakeAssignment())

    assert result["meta"]["sample_rate"] == 48_000


# ── B8: model_id is stable, opaque and engine-scoped ───────────────────────


def _entry(**overrides) -> dict:
    entry = {
        "id": "indextts",
        "display_name": "IndexTTS-2",
        "available": True,
        "gpu_compat": ["cuda"],
    }
    entry.update(overrides)
    return entry


def test_model_id_is_not_the_display_name():
    """It keys breakers, per-model slots and residency, and is persisted. A UI
    copy edit must not orphan any of that."""
    assert capabilities.model_id_for(_entry()) == "indextts:default"
    renamed = capabilities.model_id_for(_entry(display_name="IndexTTS 2 (Turbo)"))
    assert renamed == "indextts:default"


def test_model_id_is_engine_scoped():
    """Two engines whose model is called "default" must not share a breaker."""
    assert capabilities.model_id_for(_entry(id="voxcpm2")) != capabilities.model_id_for(_entry())


def test_model_id_never_carries_a_repo_path():
    """The wire carries engine + a closed identifier, never a repo path — so
    there is nothing to validate on arrival and nothing to point at a model
    the worker's catalog does not know."""
    for entry in (_entry(), _entry(id="mlx-audio", active_model_id="kokoro")):
        assert "/" not in capabilities.model_id_for(entry)


def test_a_multiplexing_engine_names_its_configured_model():
    """mlx-audio hides 7+ curated models behind one id (#981); scheduling and
    residency are about the model, not the adapter."""
    assert (
        capabilities.model_id_for(_entry(id="mlx-audio", active_model_id="kokoro"))
        == "mlx-audio:kokoro"
    )


def test_discovery_reports_the_human_label_separately(monkeypatch):
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [_entry()])

    found = capabilities.discover()[0]

    assert found["model_id"] == "indextts:default"
    assert found["display_name"] == "IndexTTS-2"


def test_an_engine_that_is_present_but_not_downloaded_still_reports(monkeypatch):
    """Without the row, "this worker has no such engine" and "it has it but the
    weights are missing" look identical — and the download-first flow has
    nothing to offer."""
    monkeypatch.setattr(
        "services.tts_backend.list_backends", lambda: [_entry(available=False)]
    )

    assert capabilities.discover(include_unavailable=True)[0]["engine"] == "indextts"


# ── The reporters the lease depends on ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_executor_reports_through_the_callbacks_the_client_injects(
    engine_cache, no_watermark
):
    """B1: unwired, these are the frames whose absence expired the lease on any
    task longer than it — starting with the cold load, which happens after
    TaskStarted."""
    progress: list[tuple[float, str]] = []
    loading: list[tuple[float, str]] = []

    async def on_progress(fraction, stage):
        progress.append((fraction, stage))

    async def on_model_loading(fraction, detail):
        loading.append((fraction, detail))

    await TaskExecutor().execute(
        _FakeAssignment(), on_progress=on_progress, on_model_loading=on_model_loading
    )

    assert [stage for _, stage in progress] == ["synthesising", "encoding", "done"]
    assert [detail for _, detail in loading] == ["preparing fake-engine", "model ready"]


def test_the_client_can_see_that_this_executor_takes_reporters():
    """The transport probes the injected executor's signature rather than
    assuming it, so every name here is a wire contract in disguise.

    Spelled out literally rather than compared against the transport's own
    constant: both sides deriving the set from one symbol would agree with each
    other while agreeing with nothing the executor actually accepts.

    Each omission fails silently and differently. Drop ``on_progress`` or
    ``on_model_loading`` and no frame is ever sent — the only symptom is tasks
    dying of an expired lease. Drop ``fetch_input`` and the executor cannot pull
    the reference audio a clone needs, so it renders *something* and returns it
    as success: a plausible wrong result, which is strictly worse.
    """
    from worker.transport.client import _accepted_reporter_kwargs

    assert _accepted_reporter_kwargs(TaskExecutor().execute) == frozenset(
        {"on_progress", "on_model_loading", "fetch_input"}
    )


@pytest.mark.asyncio
async def test_an_executor_driven_without_reporters_still_runs(engine_cache, no_watermark):
    result = await TaskExecutor().execute(_FakeAssignment())

    assert result["meta"]["duration_seconds"] > 0


@pytest.mark.asyncio
async def test_a_task_with_no_text_fails_before_touching_the_gpu(engine_cache):
    with pytest.raises(TaskFailure) as excinfo:
        await TaskExecutor().execute(_FakeAssignment(params_json='{"text": "  "}'))

    assert excinfo.value.error.code == "INVALID_TASK_PARAMS"
    assert _FakeBackend.constructed == 0


def test_engine_ids_never_reach_the_filesystem_through_the_cache(engine_cache):
    """The cache resolves through the registry, so a wire-supplied engine id is
    a lookup miss, never a path."""
    with pytest.raises(ValueError):
        tts_backend.get_engine_instance_for("../../etc/passwd")


@pytest.mark.asyncio
async def test_asyncio_is_not_blocked_while_the_engine_runs(
    engine_cache, no_watermark, monkeypatch
):
    """The blocking calls stay on threads; a worker whose event loop stalls
    stops answering heartbeats and is reaped as dead mid-job."""
    ticks = 0

    async def _tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    def _slow(self, text, **kwargs):
        import time

        import torch

        time.sleep(0.15)
        return torch.zeros(240)

    monkeypatch.setattr(_FakeBackend, "generate", _slow)
    ticker = asyncio.create_task(_tick())
    try:
        await TaskExecutor().execute(_FakeAssignment())
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)

    assert ticks > 5, "the event loop was blocked by the engine call"
