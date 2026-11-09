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
@pytest.mark.parametrize("limited_signature", [False, True])
def test_generated_audio_keeps_its_duration_and_pitch(backend, rate, limited_signature):
    def generate(**kwargs):
        yield SimpleNamespace(audio=_tone(rate), sample_rate=rate)

    def generate_limited(text, speed):
        yield SimpleNamespace(audio=_tone(rate), sample_rate=rate)

    backend._model = SimpleNamespace(
        generate=generate_limited if limited_signature else generate,
    )
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


@pytest.mark.parametrize("limited_signature", [False, True])
@pytest.mark.parametrize("rates", [(44100, 44100), (44100, 44100, 48000, 48000)])
def test_contiguous_chunks_share_resampling_context(backend, limited_signature, rates):
    import torch
    import torchaudio
    pieces = [_tone(rate)[:101] for rate in rates]

    def generate(**kwargs):
        for audio, rate in zip(pieces, rates):
            yield SimpleNamespace(audio=audio, sample_rate=rate)

    def generate_limited(text, speed):
        for audio, rate in zip(pieces, rates):
            yield SimpleNamespace(audio=audio, sample_rate=rate)

    backend._model = SimpleNamespace(
        generate=generate_limited if limited_signature else generate,
    )
    expected = []
    for start in range(0, len(rates), 2):
        joined = torch.from_numpy(np.concatenate(pieces[start:start + 2]))
        expected.append(torchaudio.functional.resample(joined, rates[start], 24000))
    np.testing.assert_allclose(backend.generate("chunks", voice="speaker")[0].numpy(),
                               torch.cat(expected).numpy(), atol=1e-6)
