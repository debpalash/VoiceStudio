"""Streamed pcm16 frames must never hard-clip marked audio (#1169).

Streamed chunks are provenance-marked and then encoded. A hard clip at that
last step can damage the mark, so the shared encoder scales an over-full-scale
chunk down instead, and every streaming encoder goes through it.
"""
from pathlib import Path

import importlib

import torch

_ROOT = Path(__file__).resolve().parents[1]


def pcm16_mono(wav):
    # Resolved per call: other tests swap ``services.*`` in sys.modules, and a
    # module-level import would keep exercising a stale copy of the helper.
    return importlib.import_module("services.audio_io").pcm16_mono(wav)


def test_in_range_audio_is_a_plain_cast():
    wav = torch.tensor([[0.0, 0.5, -0.5, 0.25]])
    assert pcm16_mono(wav).tolist() == [0, 16384, -16384, 8192]


def test_over_full_scale_is_scaled_not_clipped():
    wav = torch.tensor([[0.2, 1.6, -0.8, 1.2]])
    pcm = pcm16_mono(wav)
    assert int(pcm.abs().max()) == 32767
    # Proportions survive: nothing else is pinned at the rails.
    assert int((pcm.abs() >= 32767).sum()) == 1
    ratio = pcm.float() / pcm[1].float()
    assert torch.allclose(ratio, wav[0] / 1.6, atol=1e-4)


def test_takes_channel_zero_of_nd_input():
    wav = torch.tensor([[[0.5, 0.5], [0.1, 0.1]]])
    assert pcm16_mono(wav).tolist() == [16384, 16384]


def test_streaming_encoders_use_the_shared_encoder():
    for rel in ("backend/api/routers/generation.py", "backend/api/routers/tts_stream.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert "pcm16_mono(" in src, f"{rel} must encode stream frames with pcm16_mono"
        assert "32767).clamp(" not in src, f"{rel} hard-clips stream frames again"
