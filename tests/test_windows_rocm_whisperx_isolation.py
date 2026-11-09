"""WhisperX must not execute CT2 in Torch's incompatible ROCm 10 process."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def gate(monkeypatch):
    pipelines = importlib.import_module("core.windows_rocm.pipelines")
    isolation = importlib.import_module("core.windows_rocm.isolation_policy")
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    return pipelines


@pytest.mark.parametrize("device", ["cpu", "cuda", "rocm", None])
def test_whisperx_refuses_incompatible_openmp_before_device_probe(gate, monkeypatch, device):
    """Reject incompatible WhisperX OpenMP libraries before generic device probing."""
    def forbidden(*args):
        pytest.fail("WhisperX touched the generic device gate before refusing unsafe CT2")
    monkeypatch.setattr(gate, "pipeline_error", forbidden)
    reason = gate.whisperx_error(device)
    assert "OpenMP" in reason and "faster-whisper" in reason


def test_other_recipes_keep_the_existing_whisperx_gate(gate, monkeypatch):
    isolation = importlib.import_module("core.windows_rocm.isolation_policy")
    monkeypatch.setattr(isolation, "isolation_required", lambda: False)
    monkeypatch.setattr(gate, "pipeline_error", lambda device: f"prior gate: {device}")
    assert gate.whisperx_error("cpu") == "prior gate: cpu"


def test_pure_torch_cpu_pipelines_remain_available(gate, monkeypatch):
    monkeypatch.setattr(gate, "windows_hip_installed", lambda: True)
    assert gate.pipeline_error("cpu") is None


def test_cached_whisperx_pipeline_cannot_bypass_the_gate(gate):
    backend = importlib.import_module("services.asr_backend").WhisperXBackend
    instance = object.__new__(backend)
    instance._device = "cpu"
    instance._asr = SimpleNamespace()
    with pytest.raises(gate.PipelineCompatibilityError, match="OpenMP"):
        instance._ensure_asr()
