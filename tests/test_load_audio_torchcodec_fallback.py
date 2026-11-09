"""load_audio must still decode when torchaudio's TorchCodec route is unusable.

torchaudio >= 2.9 raises ImportError from ``load()`` on hosts without FFmpeg
shared libraries. These tests force that failure and check that the pydub
fallback returns the same kind of tensor: mono, (1, T), float, correctly scaled
for every WAV sample width.
"""
import numpy as np
import pytest
import soundfile as sf
import torch

from omnivoice.utils import audio as audio_utils

PEAK = 0.5


def _write_sine(path, *, subtype, rate=16000, seconds=0.5, channels=1):
    t = np.arange(int(rate * seconds)) / rate
    wave = PEAK * np.sin(2 * np.pi * 440 * t)
    data = np.stack([wave] * channels, axis=1) if channels > 1 else wave
    sf.write(str(path), data, rate, subtype=subtype)
    return rate


@pytest.fixture
def no_torchcodec(monkeypatch):
    def fail(*args, **kwargs):
        raise ImportError("TorchCodec could not find FFmpeg libraries")

    monkeypatch.setattr(audio_utils.torchaudio, "load", fail)


def test_fallback_returns_mono_non_silent_tensor(tmp_path, no_torchcodec):
    path = tmp_path / "in.wav"
    _write_sine(path, subtype="PCM_16")
    wave = audio_utils.load_audio(str(path), 16000)
    assert wave.shape[0] == 1
    assert wave.dtype == torch.float32
    assert wave.abs().max() > 0.1


def test_fallback_resamples_to_requested_rate(tmp_path, no_torchcodec):
    path = tmp_path / "in.wav"
    _write_sine(path, subtype="PCM_16", rate=16000, seconds=1.0)
    wave = audio_utils.load_audio(str(path), 24000)
    assert abs(wave.shape[1] - 24000) <= 48


def test_fallback_downmixes_stereo(tmp_path, no_torchcodec):
    path = tmp_path / "stereo.wav"
    _write_sine(path, subtype="PCM_16", channels=2)
    assert audio_utils.load_audio(str(path), 16000).shape[0] == 1


@pytest.mark.parametrize("subtype", ["PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT"])
def test_fallback_scale_follows_sample_width(tmp_path, no_torchcodec, subtype):
    path = tmp_path / f"{subtype}.wav"
    _write_sine(path, subtype=subtype)
    wave = audio_utils.load_audio(str(path), 16000)
    assert float(wave.abs().max()) == pytest.approx(PEAK, abs=0.02)


@pytest.mark.parametrize("subtype", ["PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT"])
def test_audiosegment_to_tensor_keeps_amplitude_for_stereo(tmp_path, subtype):
    from pydub import AudioSegment

    path = tmp_path / f"st_{subtype}.wav"
    _write_sine(path, subtype=subtype, rate=24000, channels=2)
    tensor = audio_utils.audiosegment_to_tensor(AudioSegment.from_file(str(path)))
    assert tensor.shape[0] == 2
    assert float(tensor.abs().max()) == pytest.approx(PEAK, abs=0.02)
