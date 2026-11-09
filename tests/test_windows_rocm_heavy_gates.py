"""Unverified Windows MIOpen pipelines never execute, including cached state."""
import sys
from types import SimpleNamespace

import pytest
from services import asr_backend as ab


@pytest.fixture
def windows_hip(monkeypatch):
    from core.windows_rocm import pipelines

    monkeypatch.setattr(pipelines, "windows_hip_installed", lambda: True)
    monkeypatch.setattr(ab, "_LAST_ERRORS", {})
    caps = SimpleNamespace(family="rocm")
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)
    return caps


def test_whisperx_availability_blocks_before_import(windows_hip, monkeypatch):
    monkeypatch.setitem(sys.modules, "whisperx", None)
    ok, reason = ab.WhisperXBackend.is_available()
    assert not ok and "MIOpen" in reason and "CPU" in reason


def test_cached_whisperx_is_gated_and_cpu_retry_not_poisoned(windows_hip, monkeypatch):
    engine = ab.WhisperXBackend()
    engine._asr = object()
    engine._device = "cuda"
    with pytest.raises(ImportError, match="MIOpen"):
        engine._ensure_asr()
    windows_hip.family = "cpu"
    engine._device = "cpu"
    engine._ensure_asr()
    assert engine._asr is not None


def test_transcribe_gates_before_whisperx_import(windows_hip, monkeypatch):
    monkeypatch.setitem(sys.modules, "whisperx", None)
    engine = ab.WhisperXBackend()
    engine._asr = object()
    engine._device = "cuda"
    with pytest.raises(ImportError, match="MIOpen"):
        engine.transcribe("local.wav")


def test_cached_alignment_blocked_without_losing_words(windows_hip, monkeypatch):
    original = [{"text": "hello", "words": [{"word": "hello", "start": 0.2}]}]
    monkeypatch.setitem(ab._ALIGN_CACHE, ("en", "cuda"), (object(), {}))
    monkeypatch.setitem(sys.modules, "whisperx", None)
    assert ab.load_align_model("en", "cuda") is None
    assert ab.forced_align(original, object(), "en", device="cuda") is original
    assert ab._ALIGN_CACHE[("en", "cuda")] is not None


def test_cached_diarization_gated_before_reuse(windows_hip, monkeypatch):
    from services import model_manager as mm

    cached = object()
    monkeypatch.setattr(mm, "_diar_pipeline", cached)
    monkeypatch.setattr("services.diarization_runtime.selected_backend", lambda: "pyannote")
    assert mm.get_diarization_pipeline(True) == (None, mm.DIARIZATION_ERR_LOAD)
    windows_hip.family = "cpu"
    assert mm.get_diarization_pipeline(True) == (cached, None)


def test_explicit_cpu_heavy_pipeline_allowed(windows_hip):
    from core.windows_rocm.pipelines import pipeline_error

    windows_hip.family = "cpu"
    assert pipeline_error() is None
    assert pipeline_error("cpu") is None
    assert "MIOpen" in pipeline_error("cuda")


def test_active_loader_does_not_cache_a_compatibility_rejection(windows_hip, monkeypatch):
    engine = ab.WhisperXBackend()
    engine._asr = object()
    engine._device = "cuda"
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **kwargs: engine)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kwargs: None)
    monkeypatch.setattr(ab, "_asr_backend_pinned", lambda: True)
    monkeypatch.delitem(ab._DEEP_IMPORT_BROKEN, "whisperx", raising=False)
    with pytest.raises(RuntimeError, match="MIOpen"):
        ab.load_active_asr_backend()
    assert "whisperx" not in ab._DEEP_IMPORT_BROKEN
