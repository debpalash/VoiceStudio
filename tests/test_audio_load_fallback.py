"""Generated audio remains readable without the optional TorchCodec runtime."""
import io

import numpy as np
import pytest
import soundfile as sf
import torch
import torchaudio


@pytest.mark.parametrize("error", [ImportError("TorchCodec missing"), RuntimeError("Could not load libtorchcodec")])
@pytest.mark.parametrize("buffer", [False, True])
def test_load_audio_fallback_preserves_samples_channels_and_rate(tmp_path, monkeypatch, error, buffer):
    from services.audio_io import load_audio

    samples = np.array([[0.25, -0.5], [0.5, -0.25]], dtype=np.float32)
    target = io.BytesIO() if buffer else tmp_path / "segment.wav"
    sf.write(target, samples, 24000, format="WAV", subtype="FLOAT")
    if buffer:
        target.seek(0)

    def unavailable(source):
        if buffer:
            source.read(8)  # A decoder may consume the header before failing.
        raise error

    monkeypatch.setattr(torchaudio, "load", unavailable)
    wave, rate = load_audio(target)
    assert rate == 24000
    assert wave.dtype == torch.float32
    torch.testing.assert_close(wave, torch.from_numpy(samples.T))


def test_unrelated_decoder_errors_are_not_hidden(monkeypatch):
    from services.audio_io import load_audio

    def corrupt(_source):
        raise RuntimeError("corrupt audio")

    monkeypatch.setattr(torchaudio, "load", corrupt)
    with pytest.raises(RuntimeError, match="corrupt audio"):
        load_audio("bad.wav")
