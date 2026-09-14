"""``load_audio`` must survive torchaudio 2.9 without TorchCodec (#1931).

torchaudio >= 2.9 routes ``load()`` through TorchCodec, which needs FFmpeg
*shared libraries* on the system. Where those are absent the call raises
``ImportError`` — but ``load_audio`` caught only ``(RuntimeError, OSError)``,
so the pydub fallback written for precisely this situation never ran. Every
voice-clone reference read, watermark check and dub segment load then failed
with "TorchCodec is required for load_with_torchcodec".

This is the read-side twin of the ``_safe_torchaudio_save`` regression in
``tests/backend/services/test_audio_io.py``, and it reaches the same users:
#1931 guarded ``set_audio_backend()`` against torchaudio 2.9 but left
``load()`` unprotected. arm64 CUDA hosts reach it unavoidably, since torch
2.8.0 publishes no aarch64 wheel.

Note that ``backend="soundfile"`` does not avoid this — torchaudio 2.9
accepts that argument and ignores it.
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf
import torch

from omnivoice.utils.audio import load_audio


def _write_sine_wav(path, *, seconds: float = 0.5, sample_rate: int = 24000):
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float32) / sample_rate
    sf.write(str(path), 0.5 * np.sin(2 * np.pi * 440.0 * t), sample_rate,
             subtype="PCM_16")
    return sample_rate


def _torchcodec_missing(*_a, **_kw):
    raise ImportError(
        "TorchCodec is required for load_with_torchcodec. "
        "Please install torchcodec to use this function."
    )


def test_load_audio_falls_back_when_torchcodec_missing(tmp_path, monkeypatch):
    """Without the ImportError catch this raises instead of returning audio."""
    import torchaudio

    ref = tmp_path / "ref.wav"
    sample_rate = _write_sine_wav(ref)
    monkeypatch.setattr(torchaudio, "load", _torchcodec_missing)

    waveform = load_audio(str(ref), sample_rate)

    assert isinstance(waveform, torch.Tensor)
    assert waveform.ndim == 2, f"expected (1, T), got {tuple(waveform.shape)}"
    assert waveform.shape[0] == 1, "load_audio must return mono"
    assert waveform.shape[-1] > 0
    assert waveform.abs().max() > 0.05, "fallback produced silence"


def test_load_audio_fallback_resamples_to_target(tmp_path, monkeypatch):
    """The fallback path must still honour the requested sampling rate."""
    import torchaudio

    ref = tmp_path / "ref_16k.wav"
    _write_sine_wav(ref, seconds=0.5, sample_rate=16000)
    monkeypatch.setattr(torchaudio, "load", _torchcodec_missing)

    waveform = load_audio(str(ref), 24000)

    # 0.5 s resampled 16k -> 24k is ~12000 samples; allow resampler edge slack.
    assert abs(waveform.shape[-1] - 12000) <= 64, (
        f"expected ~12000 samples at 24 kHz, got {waveform.shape[-1]}"
    )


@pytest.mark.parametrize("subtype", ["PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT"])
def test_load_audio_fallback_amplitude_matches_bit_depth(
    tmp_path, monkeypatch, subtype
):
    """The fallback must scale by the decoded width, not a fixed 32768.

    pydub reports 8-bit as ``sample_width`` 1 and widens 24-bit to a
    full-range int32 (``sample_width`` 4, contrary to the stale comment in
    its own source). Dividing every decode by 32768 therefore returned 24-
    and 32-bit references 32768x too loud and 8-bit ones 256x too quiet.
    Nothing downstream clamps, so a clone reference silently became noise.

    Before the ImportError catch this path was rare; on torchaudio >= 2.9
    without TorchCodec it is the only path, which is what makes it a bug
    worth fixing here.
    """
    import torchaudio

    sample_rate = 24000
    peak = 0.5
    t = np.arange(sample_rate // 2, dtype=np.float64) / sample_rate
    ref = tmp_path / f"ref_{subtype.lower()}.wav"
    sf.write(str(ref), peak * np.sin(2 * np.pi * 440.0 * t), sample_rate,
             subtype=subtype)
    monkeypatch.setattr(torchaudio, "load", _torchcodec_missing)

    waveform = load_audio(str(ref), sample_rate)

    assert waveform.abs().max().item() == pytest.approx(peak, abs=0.02), (
        f"{subtype} decoded at the wrong scale: peak "
        f"{waveform.abs().max().item():.6f}, expected ~{peak}"
    )
