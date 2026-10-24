"""Longform chunks are finite before joining and both WAV cache layers."""
from pathlib import Path

import pytest
import soundfile as sf
import torch


_SR = 24000
_INVALID = [float("nan"), float("inf"), -float("inf"), None]


def _engine_tail(invalid):
    from services.audio_dsp import trim_trailing_silence

    audio = torch.zeros(1, 12000)
    audio[:, :2400] = .2
    if invalid is not None:
        audio[0, -1] = invalid
    # Same maintained guard used by VoxCPM2's finalizer; no model is loaded.
    return trim_trailing_silence(audio, _SR)


@pytest.mark.parametrize("invalid", _INVALID, ids=["nan", "positive-inf", "negative-inf", "finite"])
def test_engine_samples_are_sanitized_before_the_real_longform_join(monkeypatch, invalid):
    from services import audiobook, chunked_tts

    produced = _engine_tail(invalid)
    original = produced.clone()
    expected = torch.nan_to_num(produced, nan=0., posinf=0., neginf=0.)
    original_join = chunked_tts.join_rendered_chunks
    joined = []

    def checked_join(rendered, sample_rate, **kwargs):
        assert kwargs["trim_edges"] is False
        assert len(rendered) == 1
        torch.testing.assert_close(rendered[0], expected)
        joined.append(True)
        return original_join(rendered, sample_rate, **kwargs)

    monkeypatch.setattr(chunked_tts, "join_rendered_chunks", checked_join)
    spans = [audiobook.Span(None, "One sentence.", pause_ms_after=0)]
    audio, duration = audiobook.synthesize_chapter(
        spans, lambda *_args: produced, _SR,
    )
    assert joined == [True]
    torch.testing.assert_close(audio, expected)
    assert duration == expected.shape[-1] / _SR
    torch.testing.assert_close(produced, original, equal_nan=True)


@pytest.mark.parametrize("invalid", _INVALID, ids=["nan", "positive-inf", "negative-inf", "finite"])
def test_chapter_and_segment_wavs_cache_sanitized_samples(tmp_path, monkeypatch, invalid):
    from api.routers import audiobook as route
    from services.audiobook import Chapter, ExpressiveOptions, Span
    from services import watermark

    monkeypatch.setattr(watermark, "will_mark", lambda: False)
    monkeypatch.setattr(watermark, "mark_synthetic", lambda audio, sr, **kwargs: audio)
    produced = _engine_tail(invalid)
    expected = torch.nan_to_num(produced, nan=0., posinf=0., neginf=0.)
    chapter = Chapter("C", [Span(None, "One sentence.", pause_ms_after=0)])
    calls = []

    def synth(text, *_args):
        calls.append(text)
        return produced

    def resolve(_voice):
        return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}

    def render():
        # Audiobook and Stories both use this maintained shared renderer.
        return route._render_chapter_cached(
            chapter, synth, _SR, "fixture", resolve, str(tmp_path),
            opts=ExpressiveOptions(),
        )

    path, duration, cached, stats = render()
    assert cached is False and stats == {"total": 1, "cached": 0}
    assert duration == expected.shape[-1] / _SR
    waves = list(tmp_path.rglob("*.wav"))
    assert len(waves) == 2  # chapter and segment, using real PCM serialization
    for wave in waves:
        samples, sample_rate = sf.read(wave, dtype="float32", always_2d=True)
        assert sample_rate == _SR
        torch.testing.assert_close(
            torch.from_numpy(samples.T), expected, atol=1 / 32768, rtol=0,
        )
    assert render()[2] is True
    Path(path).unlink()
    _, _, cached, stats = render()
    assert cached is False and stats == {"total": 1, "cached": 1}
    assert calls == ["One sentence."]


def test_validation_failure_stops_longform_before_joining(monkeypatch):
    from services import audiobook, chunked_tts

    def cannot_validate(_audio):
        raise ValueError("fixture validation failure")

    monkeypatch.setattr(torch, "isfinite", cannot_validate)
    monkeypatch.setattr(
        chunked_tts, "join_rendered_chunks",
        lambda *_args, **_kwargs: pytest.fail("invalid audio reached the join"),
    )
    with pytest.raises(RuntimeError, match="Generated audio could not be validated"):
        audiobook.synthesize_chapter(
            [audiobook.Span(None, "One sentence.", pause_ms_after=0)],
            lambda *_args: torch.ones(1, 2400) * .2, _SR,
        )
