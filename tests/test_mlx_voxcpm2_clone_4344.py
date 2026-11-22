"""#4344: MLX-Audio + VoxCPM2 silently ignored a saved clone profile and
offered Kokoro's preset-voice picker.

VoxCPM2 clones from ref_audio, so it is a declared cloning model; models that
cannot clone must refuse a profile up front, and only Kokoro lists presets.
"""
import importlib
import json
import types

import numpy
import pytest


@pytest.fixture
def tts_backend():
    return importlib.import_module("services.tts_backend")


@pytest.fixture
def generation():
    return importlib.import_module("api.routers.generation")


@pytest.fixture
def MLX(tts_backend):
    return tts_backend.MLXAudioBackend


def _backend(MLX, monkeypatch, key):
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", MLX.CURATED_MODELS[key])
    return MLX()


def test_voxcpm2_is_declared_cloning(MLX, monkeypatch):
    assert "voxcpm2" in MLX.cloning_model_keys
    assert _backend(MLX, monkeypatch, "voxcpm2").supports_cloning is True


def test_voxcpm2_passes_reference_audio_without_transcript(MLX, tts_backend, monkeypatch):
    backend = _backend(MLX, monkeypatch, "voxcpm2")
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


def test_non_cloning_models_refuse_a_reference(MLX, generation, monkeypatch):
    for key in sorted(set(MLX.CURATED_MODELS) - set(MLX.cloning_model_keys)):
        _backend(MLX, monkeypatch, key)
        refusal = generation._conditioning_refusal(MLX, has_ref_audio=True)
        assert refusal and "reference" in refusal, key


def test_cloning_models_accept_a_reference(MLX, generation, monkeypatch):
    for key in sorted(MLX.cloning_model_keys):
        _backend(MLX, monkeypatch, key)
        assert generation._conditioning_refusal(MLX, has_ref_audio=True) is None, key


def test_outetts_cloning_is_declared_and_reaches_its_speaker_file_path(MLX, tts_backend, monkeypatch):
    """main's reference -> speaker-file path must stay reachable (#2700)."""
    backend = _backend(MLX, monkeypatch, "outetts")
    assert backend.supports_cloning is True
    backend._ensure_loaded = lambda: None
    captured = {}
    monkeypatch.setattr(tts_backend, "_outetts_speaker_file", lambda ref: "/spk.json")
    monkeypatch.setattr(tts_backend, "required_reference_transcript", lambda *a, **k: "t")

    def fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=numpy.zeros(4))])

    backend._model = types.SimpleNamespace(generate=fake_generate)
    backend.generate("hello", ref_audio="/tmp/ref.wav")
    assert captured["voice"] == "/spk.json" and "ref_audio" not in captured


def test_local_voxcpm2_folder_is_recognised(MLX, generation, monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "voxcpm2"}))
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", str(tmp_path))
    backend = MLX()
    assert backend._curated_key() == "voxcpm2"
    assert backend.supports_cloning is True
    assert generation._conditioning_refusal(MLX, has_ref_audio=True) is None


def test_presets_only_for_models_that_have_them(MLX, monkeypatch):
    for key in sorted(MLX.CURATED_MODELS):
        voices = _backend(MLX, monkeypatch, key).active_preset_voices()
        assert (voices == MLX.PRESET_VOICES) if key == "kokoro" else voices == [], key
