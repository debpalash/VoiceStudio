"""An idle unload has to give the memory back, not just report that it did.

Seen on an RTX 5090 Docker host: ``GET /model/status`` answered
``{"status": "idle", "loaded": false}`` while the backend still held ~3 GB of
VRAM. ``unload_shared_model()`` cleared ``model_manager.model`` — but that was
not the only reference, and the model was not the only thing left behind:

* The cached ``OmniVoiceBackend`` adapters (``tts_backend._active_instance``
  for ``/v1/audio/speech``, ``_ENGINE_INSTANCES`` for the engine self-test,
  explicit-engine ``/generate`` and worker assignments) stored the model on
  their first generate and kept it. The adapter then skipped ``get_model()``
  for good, so it never touched the idle clock either: an API-only user had
  the model "unloaded" mid-traffic, the adapter carried on with the orphan,
  and the next native generate loaded a second copy beside it.
* ``torch.compile(mode="reduce-overhead")`` keeps CUDA-graph pools and
  compiled-code caches that outlive the model. Nothing reset them — and the
  reset only works on the thread that captured the graphs.
* FlashInfer's module-global context kept the last attention workspace.
* The wav2vec2 aligners (``asr_backend._ALIGN_CACHE``) were never released:
  ``WhisperXBackend.unload()`` cleared a per-instance dict nothing ever filled.
* The pyannote diarization pipeline had a manual unload but no idle release.

Fail-before: the adapters kept the unloaded model (and kept using it after a
reload), the warm adapter path left ``_last_used`` alone, no unload path ran
``torch._dynamo.reset()`` or cleared FlashInfer's context, WhisperX's unload
left the aligners resident, and ``idle_worker`` released neither the aligners
nor the diarization pipeline.
"""
from __future__ import annotations

import asyncio
import gc
import importlib
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import torch


class _Weights:
    """Stands in for the model. Identity and collectability are all we need."""


class _Compiled:
    """What ``torch.compile`` leaves on ``model.llm``: an OptimizedModule."""

    def __init__(self):
        self._orig_mod = object()


@pytest.fixture
def mods(monkeypatch):
    # Resolved per test: other suites pop and reimport services.*, and the
    # adapter imports model_manager at call time, so patch what is live now.
    tb = importlib.import_module("services.tts_backend")
    mm = importlib.import_module("services.model_manager")
    monkeypatch.setattr(mm, "model", None)
    tb.reset_active_backend()
    yield tb, mm
    tb.reset_active_backend()
    with tb._ENGINE_CACHE_LOCK:
        tb._ENGINE_INSTANCES.pop(tb.OmniVoiceBackend, None)
        tb._ENGINE_LAST_USED.pop(tb.OmniVoiceBackend, None)


def _quiet_manager(monkeypatch, mm, flushes=None):
    """No real allocator, placement or make-room work — just the references."""
    monkeypatch.setattr(mm, "free_vram", lambda: flushes.append(mm.model) if flushes is not None else None)
    monkeypatch.setattr(mm, "release_tts_side_caches", lambda: None)
    monkeypatch.setattr(mm, "make_room_before_generate", lambda: None)
    monkeypatch.setattr(mm, "_stranded_tts_target", lambda: None)


def _record_generations(monkeypatch, tb):
    used: list = []

    def _fake(model, **_kw):
        used.append(model)
        return [torch.zeros(1)]

    monkeypatch.setattr(tb, "generate_with_cached_ref", _fake)
    return used


def _active_adapter(monkeypatch, tb):
    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    # The in-process adapter, as on CUDA/ROCm/CPU hosts; an MPS host runs
    # OmniVoice in a sidecar process that owns its own memory instead.
    monkeypatch.setattr(tb, "_effective_backend_class", lambda _bid, cls, host_family=None: cls)
    return tb.get_active_tts_backend()


def _engine_adapter(_monkeypatch, tb):
    return tb.get_engine_instance(tb.OmniVoiceBackend)


_ADAPTERS = pytest.mark.parametrize(
    "adapter", [_active_adapter, _engine_adapter], ids=["active-instance", "engine-instances"],
)


# ── The cached adapters must not own the shared model ──────────────────────

@_ADAPTERS
def test_cached_adapter_lets_the_unload_free_the_model(monkeypatch, mods, adapter):
    tb, mm = mods
    _quiet_manager(monkeypatch, mm)
    used = _record_generations(monkeypatch, tb)
    weights = _Weights()
    monkeypatch.setattr(mm, "model", weights)
    backend = adapter(monkeypatch, tb)

    backend.generate("hello")
    assert used == [weights]

    gone = weakref.ref(weights)
    del weights
    used.clear()
    assert mm.unload_shared_model() is True
    gc.collect()

    assert gone() is None, "a cached adapter still holds the model the unload released"


@_ADAPTERS
def test_cached_adapter_uses_the_reloaded_model_not_the_released_one(monkeypatch, mods, adapter):
    """Two copies resident at once: the adapter's orphan plus a fresh load."""
    tb, mm = mods
    _quiet_manager(monkeypatch, mm)
    used = _record_generations(monkeypatch, tb)
    monkeypatch.setattr(mm, "model", _Weights())
    backend = adapter(monkeypatch, tb)
    backend.generate("before the idle unload")

    mm.unload_shared_model()
    reloaded = _Weights()
    monkeypatch.setattr(mm, "model", reloaded)  # what the next /generate loads
    backend.generate("after it")

    assert used[-1] is reloaded


def test_cached_adapter_cold_loads_through_the_manager_after_an_unload(monkeypatch, mods):
    tb, mm = mods
    _quiet_manager(monkeypatch, mm)
    used = _record_generations(monkeypatch, tb)
    loaded = _Weights()

    def _load():
        mm.model = loaded
        return loaded

    monkeypatch.setattr(mm, "_load_model_with_timeout", lambda: asyncio.sleep(0, _load()))
    monkeypatch.setattr(importlib.import_module("core.run_sentinel"), "touch_activity", lambda *_a: None)
    backend = _active_adapter(monkeypatch, tb)

    backend.generate("cold")

    assert used == [loaded]
    assert mm.model is loaded


def test_warm_cached_adapter_keeps_the_idle_clock_running(monkeypatch, mods):
    """Otherwise steady /v1/audio/speech traffic looks idle to idle_worker."""
    tb, mm = mods
    _quiet_manager(monkeypatch, mm)
    _record_generations(monkeypatch, tb)
    monkeypatch.setattr(mm, "model", _Weights())
    backend = _active_adapter(monkeypatch, tb)
    backend.generate("first")

    monkeypatch.setattr(mm, "_last_used", 0.0)
    backend.generate("second")

    assert mm._last_used > time.time() - 60


def test_cached_adapter_reports_the_resident_model(monkeypatch, mods):
    tb, mm = mods
    backend = _active_adapter(monkeypatch, tb)
    monkeypatch.setattr(mm, "model", SimpleNamespace(sampling_rate=48000))
    assert backend.sample_rate == 48000
    monkeypatch.setattr(mm, "model", None)
    assert backend.sample_rate == tb.OmniVoiceBackend._DEFAULT_SAMPLE_RATE


def test_explicit_model_view_still_uses_its_model(monkeypatch, mods):
    tb, mm = mods
    _quiet_manager(monkeypatch, mm)
    used = _record_generations(monkeypatch, tb)
    monkeypatch.setattr(mm, "model", _Weights())
    pinned = _Weights()

    tb.OmniVoiceBackend(model=pinned).generate("hi")

    assert used == [pinned]


# ── Compiled-model state goes with the model ───────────────────────────────

@pytest.fixture
def infer_thread(monkeypatch, mods):
    """A stand-in for the single compiled-inference thread (#315)."""
    _, mm = mods
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compiled-infer-test")
    ident = executor.submit(threading.get_ident).result()
    monkeypatch.setattr(mm, "_compiled_inference_executor", executor)
    monkeypatch.setattr(mm, "_compiled_inference_thread_ident", ident)
    yield executor, ident
    executor.shutdown(wait=True)


def _watch_resets(monkeypatch, mm):
    """Record dynamo resets and flushes: (event, thread, what mm.model held)."""
    events: list = []
    dynamo = SimpleNamespace(reset=lambda: events.append(("reset", threading.get_ident(), mm.model)))
    monkeypatch.setattr(mm, "_lazy_torch", lambda: SimpleNamespace(_dynamo=dynamo))
    monkeypatch.setattr(mm, "free_vram", lambda: events.append(("free", threading.get_ident(), mm.model)))
    monkeypatch.setattr(mm, "release_tts_side_caches", lambda: None)
    return events


def _compiled_weights():
    weights = _Weights()
    weights.llm = _Compiled()
    return weights


def test_unload_resets_compiled_state_on_the_thread_that_captured_it(monkeypatch, mods, infer_thread):
    """CUDA-graph trees are thread-local: reset_cudagraph_trees() from any other
    thread asserts out and the graph pools stay allocated."""
    _, mm = mods
    _, ident = infer_thread
    events = _watch_resets(monkeypatch, mm)
    monkeypatch.setattr(mm, "model", _compiled_weights())

    assert mm.unload_shared_model() is True

    assert [e[0] for e in events] == ["reset", "free"]
    assert {e[1] for e in events} == {ident}
    assert all(e[2] is None for e in events), "reset/flush ran while the model was referenced"


def test_unload_resets_inline_without_a_compiled_inference_thread(monkeypatch, mods):
    """Non-cudagraph compile modes (pre-Ampere "default") never start the thread."""
    _, mm = mods
    monkeypatch.setattr(mm, "_compiled_inference_executor", None)
    events = _watch_resets(monkeypatch, mm)
    monkeypatch.setattr(mm, "model", _compiled_weights())

    assert mm.unload_shared_model() is True

    assert [(e[0], e[1]) for e in events] == [
        ("reset", threading.get_ident()), ("free", threading.get_ident()),
    ]


def test_unload_of_an_eager_model_leaves_dynamo_alone(monkeypatch, mods, infer_thread):
    """A reset is process-wide; an uncompiled unload must not cost other
    engines their compile caches."""
    _, mm = mods
    events = _watch_resets(monkeypatch, mm)
    monkeypatch.setattr(mm, "model", _Weights())

    assert mm.unload_shared_model() is True

    assert [e[0] for e in events] == ["free"]


def test_unload_from_the_inference_thread_does_not_wait_on_itself(monkeypatch, mods, infer_thread):
    _, mm = mods
    executor, ident = infer_thread
    events = _watch_resets(monkeypatch, mm)
    monkeypatch.setattr(mm, "model", _compiled_weights())

    assert executor.submit(mm.unload_shared_model).result(timeout=5) is True

    assert [(e[0], e[1]) for e in events] == [("reset", ident), ("free", ident)]


def test_unload_behind_a_running_render_flushes_now_and_resets_after(monkeypatch, mods, infer_thread):
    """The reset queues behind the render that owns the thread; the unload must
    not block the event loop on it, and the late reset still hands memory back."""
    _, mm = mods
    executor, ident = infer_thread
    events = _watch_resets(monkeypatch, mm)
    monkeypatch.setattr(mm, "_COMPILE_RESET_WAIT_S", 0.05)
    monkeypatch.setattr(mm, "model", _compiled_weights())
    render = threading.Event()
    executor.submit(render.wait, 10)

    started = time.monotonic()
    assert mm.unload_shared_model() is True
    assert time.monotonic() - started < 5
    assert [(e[0], e[1]) for e in events] == [("free", threading.get_ident())]

    render.set()
    executor.submit(lambda: None).result(timeout=5)
    assert [(e[0], e[1]) for e in events[1:]] == [("reset", ident), ("free", ident)]


def test_unload_clears_flashinfer_context_on_the_inference_thread(monkeypatch, mods, infer_thread):
    """Its attention calls read the module-global context on every layer, so it
    must be cleared on the thread they run on, never under a render."""
    _, mm = mods
    _, ident = infer_thread
    _watch_resets(monkeypatch, mm)
    writers: set = set()

    class _Ctx(dict):
        def __setitem__(self, key, value):
            writers.add(threading.get_ident())
            super().__setitem__(key, value)

    ctx = _Ctx(wrapper=object(), pos_ids=object(), doc_slots=object())
    monkeypatch.setitem(sys.modules, "omnivoice.models.omnivoice_flashinfer", SimpleNamespace(_CTX=ctx))
    weights = _Weights()
    weights._fi_graph_cache = {}
    monkeypatch.setattr(mm, "model", weights)

    assert mm.unload_shared_model() is True

    assert dict(ctx) == {"wrapper": None, "pos_ids": None, "doc_slots": None}
    assert writers == {ident}


# ── wav2vec2 aligners ──────────────────────────────────────────────────────

@pytest.fixture
def ab():
    return importlib.import_module("services.asr_backend")


def test_whisperx_unload_releases_the_shared_aligners(monkeypatch, ab):
    monkeypatch.setattr(ab.WhisperXBackend, "_pick_device", staticmethod(lambda: ("cpu", "int8")))
    aligner = _Weights()
    gone = weakref.ref(aligner)
    monkeypatch.setattr(ab, "_ALIGN_CACHE", {("en", "cpu"): (aligner, {}), ("xx", "cpu"): None})
    del aligner

    ab.WhisperXBackend().unload()
    gc.collect()

    assert gone() is None, "WhisperX unloaded but its aligner stayed resident"
    # "No aligner for this language" is a memo, not memory: keep it so the
    # next transcription doesn't probe for one again.
    assert ab._ALIGN_CACHE == {("xx", "cpu"): None}


def test_idle_aligners_are_released(monkeypatch, ab):
    monkeypatch.setattr(ab, "_ALIGN_CACHE", {("en", "cpu"): (object(), {})})
    monkeypatch.setattr(ab, "_align_last_used", 100.0)

    assert ab.release_idle_align_models(60.0, now=200.0) is True
    assert ab._ALIGN_CACHE == {}


def test_recently_used_aligners_are_kept(monkeypatch, ab):
    held = (object(), {})
    monkeypatch.setattr(ab, "_ALIGN_CACHE", {("en", "cpu"): held})
    monkeypatch.setattr(ab, "_align_last_used", 100.0)

    assert ab.release_idle_align_models(60.0, now=130.0) is False
    assert ab._ALIGN_CACHE == {("en", "cpu"): held}


def test_idle_aligner_release_is_a_noop_with_only_memos(monkeypatch, ab):
    monkeypatch.setattr(ab, "_ALIGN_CACHE", {("xx", "cpu"): None})
    monkeypatch.setattr(ab, "_align_last_used", 0.0)

    assert ab.release_idle_align_models(0.0, now=1e9) is False
    assert ab._ALIGN_CACHE == {("xx", "cpu"): None}


def test_aligning_restarts_the_idle_clock(monkeypatch, ab):
    monkeypatch.setattr(ab, "_ALIGN_CACHE", {("en", "cpu"): (object(), {})})
    monkeypatch.setattr(ab, "_align_last_used", 0.0)
    monkeypatch.setattr(ab.time, "monotonic", lambda: 500.0)

    ab.load_align_model("en", "cpu")

    assert ab._align_last_used == 500.0


# ── pyannote diarization ───────────────────────────────────────────────────

def test_idle_diarization_pipeline_is_released(monkeypatch, mods):
    _, mm = mods
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    monkeypatch.setattr(mm, "_diar_pipeline", object())
    monkeypatch.setattr(mm, "_diar_last_used", 100.0)

    assert mm.release_idle_diarization_pipeline(60.0, now=200.0) is True
    assert mm._diar_pipeline is None


def test_recently_used_diarization_pipeline_is_kept(monkeypatch, mods):
    _, mm = mods
    pipeline = object()
    monkeypatch.setattr(mm, "_diar_pipeline", pipeline)
    monkeypatch.setattr(mm, "_diar_last_used", 100.0)

    assert mm.release_idle_diarization_pipeline(60.0, now=130.0) is False
    assert mm._diar_pipeline is pipeline


def test_idle_diarization_release_is_a_noop_when_nothing_loaded(monkeypatch, mods):
    _, mm = mods
    monkeypatch.setattr(mm, "_diar_pipeline", None)
    assert mm.release_idle_diarization_pipeline(0.0, now=1e9) is False


def test_getting_the_pipeline_restarts_the_idle_clock(monkeypatch, mods):
    _, mm = mods
    runtime = importlib.import_module("services.diarization_runtime")
    monkeypatch.setattr(runtime, "selected_backend", lambda: runtime.PYANNOTE)
    pipeline = object()
    monkeypatch.setattr(mm, "_diar_pipeline", pipeline)
    monkeypatch.setattr(mm, "_diar_last_used", 0.0)
    monkeypatch.setattr(mm.time, "monotonic", lambda: 500.0)

    assert mm.get_diarization_pipeline() is pipeline
    assert mm._diar_last_used == 500.0


# ── idle_worker wires all of it in ─────────────────────────────────────────

class _Stop(Exception):
    pass


def test_idle_worker_releases_idle_aligners_and_diarization(monkeypatch, mods, ab):
    _, mm = mods
    calls: list = []
    monkeypatch.setattr(mm, "_resolve_idle_timeout", lambda: 42.0)
    monkeypatch.setattr(mm, "free_vram", lambda: calls.append("flush"))
    monkeypatch.setattr(ab, "release_idle_capture_backend", lambda _s: False)
    monkeypatch.setattr(ab, "release_idle_align_models", lambda s: calls.append(("aligners", s)) or True)
    monkeypatch.setattr(mm, "release_idle_diarization_pipeline", lambda s: calls.append(("diarization", s)) or True)
    monkeypatch.setattr(importlib.import_module("services.watermark"), "release_idle_models", lambda _s: False)

    ticks: list = []

    async def _one_tick(_seconds):
        if ticks:
            raise _Stop
        ticks.append(1)

    monkeypatch.setattr(mm, "asyncio", SimpleNamespace(sleep=_one_tick))

    with pytest.raises(_Stop):
        asyncio.run(mm.idle_worker())

    assert ("aligners", 42.0) in calls
    assert calls[calls.index(("aligners", 42.0)) + 1] == "flush"
    assert ("diarization", 42.0) in calls
