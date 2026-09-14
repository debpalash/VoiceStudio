"""#278 — torch.compile failures must fall back to eager, never fail generation.

On a GPU architecture missing from the running torch build's arch list,
`torch.compile` succeeds at load time but the *first generation* dies inside
the Dynamo/FX/Inductor stack (an AssertionError out of
torch/_inductor/cudagraph_trees.py) and was mislabeled as an OOM.

Blackwell sm_120 was the reported case and is no longer an example: the pinned
torch 2.8.0+cu128 lists sm_120, and compile, Triton 3.4.0 and the
cudagraph_trees path all run correctly there (checked on an sm_120 device,
compiled output matching eager). #278 also quotes "Detected that you are using
FX to symbolically trace a dynamo-optimized function" — that one is not an arch
symptom at all; Dynamo raises it whenever FX traces a compiled function, CPU
included.

These tests pin the contract: compile is an optimization, never a point of
failure — a compile-stack error during generation triggers a one-shot eager
retry, disables compile for the session, and genuine model errors (real OOM,
validation) still propagate unchanged.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def engine_env(monkeypatch):
    """The *live* services.engine_env, with the session flag isolated.

    Resolved at test time (not module import time): other tests (e.g.
    tests/backend/test_perf_settings.py) delete and re-import the whole
    ``services`` package mid-session, and the production fallback wrapper's
    runtime ``from services import engine_env`` always resolves the fresh
    module — a module-level import here would assert against a stale one.
    """
    mod = importlib.import_module("services.engine_env")
    monkeypatch.setattr(mod, "_compile_runtime_failure", None)
    return mod


@pytest.fixture
def model_manager(engine_env):
    """The *live* services.model_manager (same rationale as engine_env)."""
    return importlib.import_module("services.model_manager")


# ── helpers ─────────────────────────────────────────────────────────────────


def _dynamo_exc() -> Exception:
    """An exception whose type lives in the torch._dynamo namespace."""

    class TorchRuntimeError(RuntimeError):
        pass

    TorchRuntimeError.__module__ = "torch._dynamo.exc"
    return TorchRuntimeError("backend='inductor' raised")


def _fx_trace_exc() -> Exception:
    """The exact failure mode from issue #278's logs (message-based)."""
    return RuntimeError(
        "Detected that you are using FX to symbolically trace "
        "a dynamo-optimized function. This is not supported at the moment."
    )


def _cudagraph_assertion() -> BaseException:
    """A bare AssertionError raised from torch/_inductor/cudagraph_trees.py.

    Compiles a snippet under that filename so the traceback frame carries the
    inductor path — exactly what the real cudagraph_trees failure looks like
    (no message, builtin type; only the traceback identifies it).
    """
    src = "def boom():\n    raise AssertionError\n"
    ns: dict = {}
    exec(compile(src, "/x/site-packages/torch/_inductor/cudagraph_trees.py", "exec"), ns)
    try:
        ns["boom"]()
    except AssertionError as e:
        return e
    raise RuntimeError("unreachable")


class _FakeCompiledLLM:
    """Stands in for torch.compile's OptimizedModule (has ``_orig_mod``)."""

    def __init__(self, orig):
        self._orig_mod = orig


class _FakeModel:
    """Model whose ``generate`` raises the given exceptions, in order, then
    succeeds."""

    def __init__(self, failures):
        self.eager_llm = object()
        self.llm = _FakeCompiledLLM(self.eager_llm)
        self.calls = 0
        self._failures = list(failures)

    def generate(self, *args, **kwargs):
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return ["audio-tensor"]


# ── _is_compile_runtime_failure classification ──────────────────────────────


def test_detects_dynamo_module_exception(model_manager):
    assert model_manager._is_compile_runtime_failure(_dynamo_exc()) is True


def test_detects_fx_symbolic_trace_message(model_manager):
    assert model_manager._is_compile_runtime_failure(_fx_trace_exc()) is True


def test_detects_inductor_traceback_frames(model_manager):
    # Bare AssertionError — only the traceback file path identifies it.
    assert model_manager._is_compile_runtime_failure(_cudagraph_assertion()) is True


def test_detects_compile_error_wrapped_in_chain(model_manager):
    try:
        try:
            raise _fx_trace_exc()
        except RuntimeError as inner:
            raise RuntimeError("TTS engine stopped mid-generation") from inner
    except RuntimeError as outer:
        assert model_manager._is_compile_runtime_failure(outer) is True


def test_real_oom_is_not_classified_as_compile_failure(model_manager):
    exc = RuntimeError("CUDA out of memory. Tried to allocate 2.50 GiB")
    assert model_manager._is_compile_runtime_failure(exc) is False


def test_validation_error_is_not_classified(model_manager):
    assert model_manager._is_compile_runtime_failure(ValueError("bad preset")) is False


# ── generate() fallback wrapper ─────────────────────────────────────────────


def test_compile_failure_falls_back_to_eager_and_succeeds(engine_env, model_manager):
    model = _FakeModel(failures=[_fx_trace_exc()])
    model_manager._install_compile_fallback(model)

    result = model.generate(text="hello")

    assert result == ["audio-tensor"]
    assert model.calls == 2  # compiled attempt + eager retry
    assert model.llm is model.eager_llm  # compiled module swapped out
    # Compile is disabled for the rest of the session...
    assert engine_env._compile_runtime_failure is not None
    # ...so the next load goes straight to eager.
    assert engine_env.should_torch_compile("cuda") is False


def test_cudagraph_assertion_falls_back_to_eager(engine_env, model_manager):
    model = _FakeModel(failures=[_cudagraph_assertion()])
    model_manager._install_compile_fallback(model)

    assert model.generate(text="hello") == ["audio-tensor"]
    assert model.calls == 2
    assert model.llm is model.eager_llm


def test_non_compile_error_propagates_unchanged(engine_env, model_manager):
    model = _FakeModel(failures=[ValueError("bad input")])
    model_manager._install_compile_fallback(model)

    with pytest.raises(ValueError, match="bad input"):
        model.generate(text="hello")

    assert model.calls == 1  # no retry
    assert isinstance(model.llm, _FakeCompiledLLM)  # compiled module kept
    assert engine_env._compile_runtime_failure is None  # compile stays enabled


def test_no_fallback_when_already_eager(engine_env, model_manager):
    """If llm has no ``_orig_mod`` (already eager) the error propagates."""
    model = _FakeModel(failures=[_fx_trace_exc()])
    model.llm = object()  # no _orig_mod
    model_manager._install_compile_fallback(model)

    with pytest.raises(RuntimeError):
        model.generate(text="hello")
    assert model.calls == 1


def test_eager_retry_failure_is_not_misclassified(engine_env, model_manager):
    """If the eager retry then hits a *real* error (e.g. OOM), the propagated
    exception must not be classified as a compile failure via the chained
    original compile error."""
    real_oom = RuntimeError("CUDA out of memory. Tried to allocate 2.50 GiB")
    model = _FakeModel(failures=[_dynamo_exc(), real_oom])
    model_manager._install_compile_fallback(model)

    with pytest.raises(RuntimeError) as excinfo:
        model.generate(text="hello")

    assert excinfo.value is real_oom
    assert model_manager._is_compile_runtime_failure(excinfo.value) is False
