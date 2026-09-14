"""MLX results must match the adapter's declared rate before stitching/export."""
import importlib
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def backend(monkeypatch):
    cls = importlib.import_module("services.tts_backend").MLXAudioBackend
    be = cls.__new__(cls)
    be._model_id = "mlx-community/Dia-1.6B"
    be._sr = 24000
    monkeypatch.setattr(be, "_ensure_loaded", lambda: None)
    return be


def _tone(rate):
    return np.sin(2 * np.pi * 440 * np.arange(rate) / rate).astype(np.float32)


@pytest.mark.parametrize("rate", [24000, 44100, 48000])
@pytest.mark.parametrize("fallback", [False, True])
def test_generated_audio_keeps_its_duration_and_pitch(backend, rate, fallback):
    def generate(**kwargs):
        if fallback and "voice" in kwargs:
            raise TypeError("unexpected keyword argument 'voice'")
        yield SimpleNamespace(audio=_tone(rate), sample_rate=rate)

    backend._model = SimpleNamespace(generate=generate)
    declared_rate = backend.sample_rate  # callers can read this before generate
    wav = backend.generate("Hello", voice="speaker")

    assert backend.sample_rate == declared_rate
    assert wav.shape == (1, declared_rate)
    spectrum = np.abs(np.fft.rfft(wav[0].numpy()))
    assert np.fft.rfftfreq(wav.shape[-1], 1 / declared_rate)[spectrum.argmax()] == 440


def test_each_piece_is_resampled_before_concatenation(backend):
    def generate(**kwargs):
        for rate in (44100, 24000):
            yield SimpleNamespace(audio=_tone(rate), sample_rate=rate)

    backend._model = SimpleNamespace(generate=generate)
    assert backend.generate("Two pieces").shape == (1, 48000)


def test_raw_audio_without_rate_metadata_keeps_the_existing_contract(backend):
    audio = _tone(24000)
    backend._model = SimpleNamespace(generate=lambda **kwargs: iter([audio]))
    np.testing.assert_array_equal(backend.generate("Hello")[0].numpy(), audio)
