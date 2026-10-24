"""Actual streaming sentence renderer validates generated samples before DSP."""
import importlib
from types import ModuleType, SimpleNamespace
import sys

import pytest
import torch

@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), 0.0])
@pytest.mark.parametrize("own_mastering", [False, True])
def test_streaming_sentence_sanitizes_tail_before_dsp(monkeypatch, invalid, own_mastering):
    # Other collected suites reload route/DSP modules. Patch the exact module
    # the maintained renderer imports at call time, not a stale collection ref.
    audio_dsp = importlib.import_module("services.audio_dsp")
    render_stream_sentence = importlib.import_module("api.routers.tts_stream").render_stream_sentence
    original = torch.tensor([[.5, .25, 0, invalid]])
    seen = []

    def mastering(audio, sample_rate):
        assert bool(torch.isfinite(audio).all())
        seen.append("mastering")
        return audio

    watermark = ModuleType("services.watermark")

    def mark(audio, sample_rate, **kwargs):
        assert bool(torch.isfinite(audio).all())
        seen.append("watermark")
        return audio

    watermark.mark_synthetic = mark
    monkeypatch.setitem(sys.modules, "services.watermark", watermark)
    monkeypatch.setattr(audio_dsp, "apply_mastering", mastering)
    backend = SimpleNamespace(generate=lambda *args, **kwargs: original,
                              sample_rate=24000, applies_own_mastering=own_mastering)
    result, sample_rate, duration = render_stream_sentence(backend, {}, "owned speech")
    assert result.shape == original.shape
    assert sample_rate == 24000
    assert duration >= 0
    assert bool(torch.isfinite(result).all())
    assert result[0, -1] == 0
    assert result[0, 0] == pytest.approx(10 ** (-2 / 20))
    assert seen == (["watermark"] if own_mastering else ["mastering", "watermark"])
    assert torch.isnan(original[0, -1]) if invalid != invalid else original[0, -1] == invalid
