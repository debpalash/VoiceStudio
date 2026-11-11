"""Watermark ops must run in bounded chunks (#1045).

AudioSeal's activation memory grows linearly with input length: embedding a
single multi-minute waveform in one call demanded a >2 GB CPU buffer, which
OOM'd a reporter's 16 GB Windows machine mid-generate ("DefaultCPUAllocator:
not enough memory: you tried to allocate 2202777600 bytes"). embed_watermark
and detect_watermark now slice the audio into ≤ ~30 s chunks so peak memory
is bounded regardless of generation length. Fail-before/pass-after: on the
pre-fix code the fakes below observe one call spanning the whole waveform.
"""
from __future__ import annotations

import pytest
import torch

from services import watermark
from services.watermark import (
    _CHUNK_SECONDS,
    _iter_chunks,
    detect_watermark,
    embed_watermark,
)

SR = 16000


class FakeGenerator:
    """Stands in for the AudioSeal generator; records chunk lengths."""

    def __init__(self):
        self.seen_lengths: list[int] = []

    def __call__(self, audio, sample_rate, message=None):
        self.seen_lengths.append(audio.shape[-1])
        return audio * 2.0  # position-independent transform → order is checkable


def _expected(wave):
    # The residual (here: the chunk itself) is added DC-free per chunk (#2818).
    return torch.cat(
        [seg + (seg - seg.mean(dim=-1, keepdim=True)) for seg in _iter_chunks(wave, SR)],
        dim=-1,
    )


class FakeDetector:
    """Stands in for the AudioSeal detector; confidence peaks on the chunk
    holding the sentinel spike so best-chunk aggregation is observable."""

    def __init__(self):
        self.seen_lengths: list[int] = []

    def detect_watermark(self, audio, sample_rate, message_threshold=0.5):
        self.seen_lengths.append(audio.shape[-1])
        conf = 0.9 if float(audio.abs().max()) > 100.0 else 0.1
        msg = torch.tensor(watermark.OMNI_MESSAGE) if conf > 0.5 else torch.zeros(16)
        return (conf, msg)


@pytest.fixture
def fake_audioseal(monkeypatch):
    gen, det = FakeGenerator(), FakeDetector()
    monkeypatch.setattr(watermark, "_generator", gen)
    monkeypatch.setattr(watermark, "_detector", det)
    monkeypatch.setattr(watermark, "_audioseal_available", True)
    return gen, det


def test_embed_long_audio_is_chunk_bounded(fake_audioseal):
    gen, _ = fake_audioseal
    seconds = 95  # → 30 + 30 + 30 + 5
    wave = torch.arange(SR * seconds, dtype=torch.float32).unsqueeze(0)

    out = embed_watermark(wave, SR, force=True)

    max_chunk = _CHUNK_SECONDS * SR
    assert len(gen.seen_lengths) == 4
    assert all(n <= max_chunk for n in gen.seen_lengths), gen.seen_lengths
    # Every sample processed, in order, shape preserved
    assert out.shape == wave.shape
    assert torch.allclose(out, _expected(wave))


def test_embed_short_audio_single_call(fake_audioseal):
    gen, _ = fake_audioseal
    wave = torch.randn(1, SR * 3)
    out = embed_watermark(wave, SR, force=True)
    assert gen.seen_lengths == [SR * 3]
    assert torch.allclose(out, _expected(wave))


def test_embed_subsecond_tail_folds_into_previous_chunk(fake_audioseal):
    gen, _ = fake_audioseal
    # 30.5 s → a lone 0.5 s tail would embed poorly; folded into chunk 1
    wave = torch.randn(1, int(SR * 30.5))
    embed_watermark(wave, SR, force=True)
    assert gen.seen_lengths == [int(SR * 30.5)]
    assert max(gen.seen_lengths) <= (_CHUNK_SECONDS + 1) * SR


def test_embed_1d_shape_restored(fake_audioseal):
    wave = torch.randn(SR * 65)
    out = embed_watermark(wave, SR, force=True)
    assert out.shape == wave.shape


def test_detect_long_audio_is_chunk_bounded_and_keeps_best_chunk(fake_audioseal):
    _, det = fake_audioseal
    # Spike (→ high confidence) only in the LAST chunk: a whole-file pass or
    # first-chunk-only shortcut would miss it.
    wave = torch.randn(1, SR * 95) * 0.01
    wave[0, -SR:] = 500.0

    result = detect_watermark(wave, SR)

    max_chunk = _CHUNK_SECONDS * SR
    assert len(det.seen_lengths) == 4
    assert all(n <= max_chunk for n in det.seen_lengths), det.seen_lengths
    assert result["is_watermarked"] is True
    assert result["confidence"] == 0.9
    assert result["is_omnivoice"] is True


def test_iter_chunks_covers_everything_exactly_once():
    audio = torch.arange(SR * 95, dtype=torch.float32).reshape(1, 1, -1)
    rejoined = torch.cat(list(_iter_chunks(audio, SR)), dim=-1)
    assert torch.equal(rejoined, audio)


def test_iter_chunks_empty_audio_yields_nothing():
    assert list(_iter_chunks(torch.zeros(1, 1, 0), SR)) == []


# ── Idle release of the AudioSeal models (#1495) ───────────────────────────
#
# They loaded on the first embed and then stayed resident for the life of the
# process — the one model in the app still making that bargain after the TTS
# model and the capture ASR both stopped. CPU-resident, so this is system RAM,
# and the machines that notice are the ones running batches.

def test_idle_release_drops_both_models(monkeypatch):
    monkeypatch.setattr(watermark, "_generator", object())
    monkeypatch.setattr(watermark, "_detector", object())
    monkeypatch.setattr(watermark, "_last_used", 100.0)

    assert watermark.release_idle_models(60.0, now=200.0) is True
    assert watermark._generator is None
    assert watermark._detector is None


def test_idle_release_keeps_a_recently_used_model(monkeypatch):
    generator = object()
    monkeypatch.setattr(watermark, "_generator", generator)
    monkeypatch.setattr(watermark, "_detector", None)
    monkeypatch.setattr(watermark, "_last_used", 100.0)

    assert watermark.release_idle_models(60.0, now=130.0) is False
    assert watermark._generator is generator


def test_idle_release_is_a_noop_when_nothing_loaded(monkeypatch):
    monkeypatch.setattr(watermark, "_generator", None)
    monkeypatch.setattr(watermark, "_detector", None)
    monkeypatch.setattr(watermark, "_last_used", 0.0)

    assert watermark.release_idle_models(0.0) is False


def test_embedding_restarts_the_idle_clock(monkeypatch):
    """Without this the models are released mid-batch: `_last_used` would sit
    at whatever the first embed set it to while the batch kept watermarking."""
    monkeypatch.setattr(watermark, "_generator", object())
    monkeypatch.setattr(watermark, "_last_used", 0.0)
    monkeypatch.setattr(watermark.time, "monotonic", lambda: 500.0)

    watermark._get_generator()

    assert watermark._last_used == 500.0
