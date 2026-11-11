"""The watermark must not shift the audio's DC level (#2818).

The AudioSeal 16-bit generator's residual carries a constant bias of about
-7 LSB at int16 alongside its 1–2 kHz mark. Added as-is, every pause in a
marked take sat at -7 LSB DC while unmarked output sat at 0. embed_watermark
now removes the residual's mean before adding it; the voice is untouched.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import torch

SR = 24000
LSB = 1 / 32768
BIAS = -7 * LSB  # measured on the real AudioSeal residual


@pytest.fixture
def watermark():
    """Resolve the app module at test time, never at collection time."""
    return importlib.import_module("services.watermark")


def _tone(n: int, rate: int, hz: float = 1500.0, amp: float = 0.005) -> torch.Tensor:
    return amp * torch.sin(2 * torch.pi * hz * torch.arange(n) / rate)


class _BiasedGenerator:
    """AudioSeal-shaped residual: a 1.5 kHz mark plus a constant DC bias."""

    def __call__(self, audio, sample_rate, message):
        return audio + _tone(audio.shape[-1], sample_rate) + BIAS


def _voice_with_pauses() -> tuple[torch.Tensor, list[slice]]:
    """Two 'words' (harmonic tones) separated by 1 s digital-silence pauses,
    peak-normalized to -2 dBFS."""
    t = torch.arange(SR) / SR
    word = sum(torch.sin(2 * torch.pi * f * t) / (i + 1) for i, f in enumerate((180, 360, 540, 3000)))
    word = word * torch.hann_window(SR, periodic=False)
    pause = torch.zeros(SR)
    voice = torch.cat([pause, word, pause, word, pause]).unsqueeze(0)
    voice = voice / voice.abs().max() * 10 ** (-2 / 20)
    # Interior of each pause, clear of resampler edge ringing at the joins.
    pauses = [slice(k * SR + 2400, (k + 1) * SR - 2400) for k in (0, 2, 4)]
    return voice, pauses


@pytest.fixture
def biased_generator(monkeypatch, watermark):
    monkeypatch.setattr(watermark, "_check_available", lambda: True)
    monkeypatch.setattr(watermark, "_get_generator", _BiasedGenerator)


def test_pauses_carry_no_dc_after_watermarking(biased_generator, watermark):
    voice, pauses = _voice_with_pauses()
    marked = watermark.embed_watermark(voice, SR, force=True)
    pcm = torch.round(marked * 32767)
    for p in pauses:
        assert abs(float(pcm[0, p].mean())) <= 1.0, float(pcm[0, p].mean())


def test_voice_is_untouched_apart_from_the_mark(biased_generator, watermark):
    voice, _ = _voice_with_pauses()
    marked = watermark.embed_watermark(voice, SR, force=True)
    added = marked - voice
    assert abs(float(added.mean())) < 0.05 * LSB
    inner = slice(200, -200)
    assert torch.allclose(added[0, inner], _tone(voice.shape[-1], SR)[inner], atol=3e-4)


def test_remove_dc_is_per_channel_and_keeps_empty_input(watermark):
    residual = torch.stack([_tone(SR, SR) + 0.01, _tone(SR, SR) - 0.02]).unsqueeze(0)
    clean = watermark._remove_dc(residual)
    assert torch.allclose(clean.mean(dim=-1), torch.zeros(1, 2), atol=1e-7)
    assert torch.allclose(clean, residual - torch.tensor([0.01, -0.02]).view(1, 2, 1), atol=1e-6)
    empty = torch.zeros(1, 1, 0)
    assert watermark._remove_dc(empty).shape == empty.shape


def _block_model_downloads(monkeypatch) -> None:
    """Let the loader read cached checkpoints but fail any real download.

    ``HF_HUB_OFFLINE`` does not cover the cards' https URLs, which go through
    ``torch.hub.load_state_dict_from_url``; that call also serves the cached
    file, so it is guarded rather than replaced.
    """
    real_hub_load = torch.hub.load_state_dict_from_url

    def _cached_only(url, *args, model_dir=None, file_name=None, **kwargs):
        if not model_dir or not file_name or not (Path(model_dir) / file_name).is_file():
            raise AssertionError(f"test tried to download {url}")
        return real_hub_load(url, *args, model_dir=model_dir, file_name=file_name, **kwargs)

    def _no_hf_download(*args, **kwargs):
        raise AssertionError(f"test tried to download {args} {kwargs}")

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", _cached_only)
    hf_hub = importlib.import_module("huggingface_hub")
    monkeypatch.setattr(hf_hub, "hf_hub_download", _no_hf_download)


def test_real_audioseal_mark_is_dc_free_and_still_detected(monkeypatch, watermark):
    """Runs only when BOTH real checkpoints are already cached; never downloads."""
    if not watermark._check_available():
        pytest.skip("audioseal not installed")
    missing = [
        card for card in (watermark._GENERATOR_CARD, watermark._DETECTOR_CARD)
        if not watermark._checkpoint_cached(card)
    ]
    if missing:
        pytest.skip(f"AudioSeal checkpoints not cached: {missing}")
    _block_model_downloads(monkeypatch)
    monkeypatch.setattr(watermark, "_generator", None)
    monkeypatch.setattr(watermark, "_detector", None)
    monkeypatch.setattr(watermark, "_generator_failed_at", None)

    voice, pauses = _voice_with_pauses()
    marked = watermark.embed_watermark(voice, SR, force=True)
    assert marked is not voice, "embed fell open; the real generator did not load"
    pcm = torch.round(marked * 32767)
    for p in pauses:
        assert abs(float(pcm[0, p].mean())) <= 1.0, float(pcm[0, p].mean())
    result = watermark.detect_watermark(pcm / 32767, SR)
    assert result["is_watermarked"], result
    assert result["is_omnivoice"], result
