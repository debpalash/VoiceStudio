"""#4344: MLX-Audio + VoxCPM2 silently ignored a saved clone profile and
offered Kokoro's preset-voice picker.

VoxCPM2 clones from ref_audio, so it is a declared cloning model; models that
cannot clone must refuse a profile up front, and only Kokoro lists presets.
"""
import types

import numpy
import pytest

from api.routers import generation
from services import tts_backend

MLX = tts_backend.MLXAudioBackend


def _backend(monkeypatch, key):
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", MLX.CURATED_MODELS[key])
    return MLX()


def test_voxcpm2_is_declared_cloning(monkeypatch):
    assert "voxcpm2" in MLX.cloning_model_keys
    assert _backend(monkeypatch, "voxcpm2").supports_cloning is True


def test_voxcpm2_passes_reference_audio_without_transcript(monkeypatch):
    backend = _backend(monkeypatch, "voxcpm2")
    backend._ensure_loaded = lambda: None
    captured = {}

    def fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=numpy.zeros(4))])

    backend._model = types.SimpleNamespace(generate=fake_generate)
    monkeypatch.setattr(
        tts_backend, "required_reference_transcript",
        lambda *a, **k: pytest.fail("VoxCPM2 must not need a transcript"),
    )
    backend.generate("hello", ref_audio="/tmp/ref.wav")
    assert captured["ref_audio"] == "/tmp/ref.wav"
    assert "ref_text" not in captured


@pytest.mark.parametrize("key", sorted(set(MLX.CURATED_MODELS) - set(MLX.cloning_model_keys)))
def test_non_cloning_model_refuses_a_reference(monkeypatch, key):
    _backend(monkeypatch, key)
    refusal = generation._conditioning_refusal(MLX, has_ref_audio=True)
    assert refusal and "reference" in refusal


@pytest.mark.parametrize("key", sorted(MLX.cloning_model_keys))
def test_cloning_model_accepts_a_reference(monkeypatch, key):
    _backend(monkeypatch, key)
    assert generation._conditioning_refusal(MLX, has_ref_audio=True) is None


@pytest.mark.parametrize("key", sorted(MLX.CURATED_MODELS))
def test_presets_only_for_models_that_have_them(monkeypatch, key):
    voices = _backend(monkeypatch, key).active_preset_voices()
    assert (voices == MLX.PRESET_VOICES) if key == "kokoro" else voices == []
