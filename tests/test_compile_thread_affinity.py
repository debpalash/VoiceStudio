"""#315 — a cudagraph-compiled model must never be driven from multiple threads.

`torch.compile(mode="reduce-overhead")` captures CUDA graphs, and captured
graph state is thread-local (torch/_inductor/cudagraph_trees keys its tree
manager off the capturing thread). The `_gpu_pool` ThreadPoolExecutor runs up
to 4 workers, so render #1 captured the graph on worker A and render #2,
dispatched to worker B, replayed mismatched cudagraph state — silently
corrupting the audio (static / slowed playback, no exception, so the #278
eager fallback never fired).

These tests pin the contract: when the model is compiled with a cudagraph
mode, every ``model.generate`` call — no matter which pool worker dispatches
it — executes on ONE dedicated thread. Uncompiled models (CPU / MPS /
Windows-no-Triton / compile-disabled) are untouched and keep the full pool.
"""
from __future__ import annotations

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def engine_env(monkeypatch):
    """The *live* services.engine_env, with the session flag isolated
    (same rationale as tests/test_compile_fallback.py)."""
    mod = importlib.import_module("services.engine_env")
    monkeypatch.setattr(mod, "_compile_runtime_failure", None)
    return mod


@pytest.fixture
def model_manager(engine_env):
    return importlib.import_module("services.model_manager")


# ── helpers ─────────────────────────────────────────────────────────────────


class _RecordingModel:
    """Model whose ``generate`` records the thread it actually executes on."""

    def __init__(self, failures=()):
        self.eager_llm = object()
        self.llm = _FakeCompiledLLM(self.eager_llm)
        self.exec_idents: list[int] = []
        self._failures = list(failures)
        self._lock = threading.Lock()

    def generate(self, *args, **kwargs):
        with self._lock:
            self.exec_idents.append(threading.get_ident())
            if self._failures:
                raise self._failures.pop(0)
        return ["audio-tensor"]


class _FakeCompiledLLM:
    """Stands in for torch.compile's OptimizedModule (has ``_orig_mod``)."""

    def __init__(self, orig):
        self._orig_mod = orig


def _fx_trace_exc() -> Exception:
    """The #278 compile-stack failure (message-classified)."""
    return RuntimeError(
        "Detected that you are using FX to symbolically trace "
        "a dynamo-optimized function. This is not supported at the moment."
    )


def _dispatch_from_pool(model, n_calls: int = 8, workers: int = 4):
    """Simulate `_gpu_pool` multi-thread dispatch: fire n_calls generates from
    a multi-worker pool (exactly what the routers' run_in_executor does)."""
    dispatch_idents: set[int] = set()
    results = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gpu-pool-sim") as pool:
        def _call(i):
            dispatch_idents.add(threading.get_ident())
            return model.generate(text=f"render {i}")

        futures = [pool.submit(_call, i) for i in range(n_calls)]
        for fut in futures:
            results.append(fut.result(timeout=30))
    return dispatch_idents, results


# ── the regression: multi-thread dispatch must execute on one thread ────────


def test_all_generate_calls_execute_on_one_dedicated_thread(model_manager):
    model = _RecordingModel()
    model_manager._install_compile_thread_affinity(model)

    dispatch_idents, results = _dispatch_from_pool(model, n_calls=8, workers=4)

    assert results == [["audio-tensor"]] * 8
    # The bug: before the fix, exec idents == dispatch idents (up to 4
    # distinct threads). The contract: exactly ONE executing thread...
    assert len(set(model.exec_idents)) == 1
    # ...which is the dedicated compiled-inference thread, not a pool worker.
    assert set(model.exec_idents) == {model_manager._compiled_inference_thread_ident}
    assert model_manager._compiled_inference_thread_ident not in dispatch_idents


def test_load_model_sync_pins_compiled_model_to_one_thread(
    monkeypatch, engine_env, model_manager
):
    """End-to-end through _load_model_sync: a CUDA load that applies
    torch.compile(mode="reduce-overhead") must return a model whose generate
    is single-threaded even when dispatched from a multi-worker pool."""
    from types import SimpleNamespace

    class _FakeOmniVoiceModel(_RecordingModel):
        def __init__(self):
            super().__init__()
            self.llm = object()  # eager module, pre-compile

    fake_model = _FakeOmniVoiceModel()

    fake_torch = SimpleNamespace(
        float16="float16",
        float32="float32",
        compile=lambda mod, mode: _FakeCompiledLLM(mod),
    )
    fake_omnivoice_cls = SimpleNamespace(
        from_pretrained=lambda *a, **k: fake_model,
    )

    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: fake_torch)
    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: fake_omnivoice_cls)
    monkeypatch.setattr(model_manager, "get_best_device", lambda: "cuda")
    monkeypatch.setattr(engine_env, "should_torch_compile", lambda device: True)

    loaded = model_manager._load_model_sync()

    assert loaded is fake_model
    assert isinstance(loaded.llm, _FakeCompiledLLM)  # compile was applied

    _dispatch_from_pool(loaded, n_calls=6, workers=4)
    assert len(set(loaded.exec_idents)) == 1
    assert set(loaded.exec_idents) == {model_manager._compiled_inference_thread_ident}


def test_uncompiled_model_keeps_caller_threads(
    monkeypatch, engine_env, model_manager
):
    """No behavior change for CPU/MPS/eager paths: when compile is skipped,
    generate runs on whichever pool worker dispatched it."""
    from types import SimpleNamespace

    fake_model = _RecordingModel()
    fake_torch = SimpleNamespace(float16="float16", float32="float32", compile=None)
    fake_omnivoice_cls = SimpleNamespace(from_pretrained=lambda *a, **k: fake_model)

    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: fake_torch)
    monkeypatch.setattr(model_manager, "_lazy_omnivoice", lambda: fake_omnivoice_cls)
    monkeypatch.setattr(model_manager, "get_best_device", lambda: "mps")
    monkeypatch.setattr(engine_env, "should_torch_compile", lambda device: False)

    loaded = model_manager._load_model_sync()

    dispatch_idents, _ = _dispatch_from_pool(loaded, n_calls=6, workers=4)
    # generate executed directly on the dispatching threads — no hop.
    assert set(loaded.exec_idents) <= dispatch_idents


# ── composition & safety properties ─────────────────────────────────────────


def test_composes_with_eager_fallback(engine_env, model_manager):
    """Production install order: fallback first, then affinity. A #278
    compile-stack failure must still trigger the one-shot eager retry — and
    both the failed attempt and the retry run on the dedicated thread."""
    model = _RecordingModel(failures=[_fx_trace_exc()])
    model_manager._install_compile_fallback(model)
    model_manager._install_compile_thread_affinity(model)

    dispatch_idents, results = _dispatch_from_pool(model, n_calls=1, workers=2)

    assert results == [["audio-tensor"]]
    assert len(model.exec_idents) == 2  # compiled attempt + eager retry
    assert set(model.exec_idents) == {model_manager._compiled_inference_thread_ident}
    assert model.llm is model.eager_llm  # fallback swapped the module out
    assert engine_env._compile_runtime_failure is not None


def test_reentrant_generate_does_not_deadlock(model_manager):
    """generate re-entering model.generate on the dedicated thread must run
    inline (a 1-worker executor submitting to itself would deadlock)."""

    class _ReentrantModel:
        def __init__(self):
            self.exec_idents: list[int] = []

        def generate(self, depth=0):
            self.exec_idents.append(threading.get_ident())
            if depth == 0:
                # After install, self.generate is the affinity wrapper.
                return self.generate(depth=1)
            return ["audio-tensor"]

    model = _ReentrantModel()
    model_manager._install_compile_thread_affinity(model)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(model.generate).result(timeout=30)

    assert result == ["audio-tensor"]
    assert len(model.exec_idents) == 2
    assert set(model.exec_idents) == {model_manager._compiled_inference_thread_ident}


def test_errors_propagate_to_the_calling_thread(model_manager):
    """Non-compile errors raised on the dedicated thread must surface
    unchanged to the dispatching caller (future.result re-raises)."""
    model = _RecordingModel(failures=[ValueError("bad preset")])
    model_manager._install_compile_thread_affinity(model)

    with pytest.raises(ValueError, match="bad preset"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: model.generate(text="x")).result(timeout=30)
