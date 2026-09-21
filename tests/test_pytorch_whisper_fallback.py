"""PyTorch-Whisper backend must work as a standalone fallback (issue #255).

On machines where WhisperX / faster-whisper can't load cuDNN 8
(`cudnn_ops_infer64_8.dll` missing), the PyTorch-Whisper backend should build
its own transformers pipeline on demand — without OMNIVOICE_PRELOAD_TTS_ASR=1
and without loading the full TTS model.
"""
import sys
import types

import pytest

from services import asr_backend as ab


def test_is_available_when_transformers_present():
    ok, msg = ab.PyTorchWhisperBackend.is_available()
    assert ok is True
    assert msg == "ready"


def test_reuses_constructor_pipe_without_building(monkeypatch):
    sentinel = object()
    be = ab.PyTorchWhisperBackend(asr_pipe=sentinel)

    def _boom(*a, **k):
        raise AssertionError("must not build a pipeline when one was passed in")

    # transformers.pipeline is imported lazily inside _ensure_pipe.
    fake_tf = types.ModuleType("transformers")
    fake_tf.pipeline = _boom
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)

    be._ensure_pipe()
    assert be._pipe is sentinel


def test_lazy_builds_standalone_pipeline(monkeypatch):
    """No preloaded pipe → build a standalone transformers ASR pipeline, with no
    call into the TTS model loader (get_model)."""
    captured = {}

    def fake_pipeline(task, **kw):
        captured["task"] = task
        captured["kw"] = kw
        return lambda *a, **k: {"chunks": []}

    fake_tf = types.ModuleType("transformers")
    fake_tf.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    monkeypatch.setattr("services.model_manager.get_best_device", lambda: "cpu")

    # Guard: building the standalone pipe must NOT pull in the full TTS model.
    import services.model_manager as mm

    def _no_get_model(*a, **k):
        raise AssertionError("standalone ASR build must not call get_model()")

    monkeypatch.setattr(mm, "get_model", _no_get_model, raising=False)

    be = ab.PyTorchWhisperBackend(asr_pipe=None)
    be._ensure_pipe()

    assert be._pipe is not None
    assert captured["task"] == "automatic-speech-recognition"
    assert captured["kw"]["model"]  # a concrete model name was chosen
    assert captured["kw"]["device"] == "cpu"
    assert "device_map" not in captured["kw"]


def test_ensure_loaded_eagerly_builds_pipeline(monkeypatch):
    """Dub preflight must load the fallback before processing every chunk."""
    backend = ab.PyTorchWhisperBackend()
    calls = []
    monkeypatch.setattr(backend, "_ensure_pipe", lambda: calls.append("load"))

    backend.ensure_loaded()

    assert calls == ["load"]


def test_low_free_vram_routes_pytorch_whisper_to_cpu(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_ASR_VRAM_PREFLIGHT", raising=False)
    import torch

    monkeypatch.setattr("services.model_manager.get_best_device", lambda: "cuda:0")
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (3 * 1024**3, 24 * 1024**3))

    assert ab.PyTorchWhisperBackend._pick_device() == "cpu"


def test_sufficient_free_vram_keeps_pytorch_whisper_on_cuda(monkeypatch):
    import torch

    monkeypatch.setattr("services.model_manager.get_best_device", lambda: "cuda:0")
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (6 * 1024**3, 24 * 1024**3))

    assert ab.PyTorchWhisperBackend._pick_device() == "cuda:0"


def test_pytorch_asr_model_overridable_via_env(monkeypatch):
    captured = {}

    def fake_pipeline(task, **kw):
        captured["kw"] = kw
        return object()

    fake_tf = types.ModuleType("transformers")
    fake_tf.pipeline = fake_pipeline
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    monkeypatch.setattr("services.model_manager.get_best_device", lambda: "cpu")
    monkeypatch.setenv("OMNIVOICE_PYTORCH_ASR_MODEL", "openai/whisper-small")

    ab.PyTorchWhisperBackend(asr_pipe=None)._ensure_pipe()
    assert captured["kw"]["model"] == "openai/whisper-small"


@pytest.mark.parametrize('message', [
    'PyTorch should be installed.',
    'CUDA out of memory',
    'Model file is unreadable',
])
def test_pipeline_error_does_not_invent_an_import_or_dependency_mismatch(monkeypatch, message):
    from core.failure import classify
    cause = RuntimeError(message)
    def fail(*args, **kwargs):
        raise cause
    fake = types.ModuleType('transformers')
    fake.pipeline = fail
    monkeypatch.setitem(sys.modules, 'transformers', fake)
    monkeypatch.setattr(ab.PyTorchWhisperBackend, '_pick_device', lambda *args: 'cpu')
    with pytest.raises(RuntimeError) as caught:
        ab.PyTorchWhisperBackend()._ensure_pipe()
    assert caught.value.__cause__ is cause
    assert message in str(caught.value)
    assert 'AutoFeatureExtractor' not in str(caught.value)
    assert 'Reinstall' not in str(caught.value)
    assert classify(str(caught.value)) != 'TRANSFORMERS_IMPORT'


def test_actual_transformers_import_failure_remains_classifiable(monkeypatch):
    from core.failure import classify
    def fail(*args, **kwargs):
        raise ImportError("Could not import module 'AutoFeatureExtractor'")
    fake = types.ModuleType('transformers')
    fake.pipeline = fail
    monkeypatch.setitem(sys.modules, 'transformers', fake)
    monkeypatch.setattr(ab.PyTorchWhisperBackend, '_pick_device', lambda *args: 'cpu')
    with pytest.raises(RuntimeError) as caught:
        ab.PyTorchWhisperBackend()._ensure_pipe()
    assert classify(str(caught.value)) == 'TRANSFORMERS_IMPORT'
