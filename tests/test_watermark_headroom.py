"""The provenance mark must survive the fixed-point exit (#1169 follow-up).

Every route encodes the marked tensor to PCM16/WAV/MP3, which clips anything
over full scale. When the watermark residual pushed a near-0 dBFS take over
1.0, the encoder clipped the marked samples after the mark and the streamed
preview stopped detecting as watermarked (surfaced once the #3412 leveler made
takes denser at the same -2 dBFS peak). embed_watermark now attenuates the
host before marking so the marked take fits under full scale; nothing is
processed after the mark.
"""
import torch


def _wm():
    from services import watermark
    return watermark


class _Offset:
    """Additive residual that lifts every sample, like a mark riding a peak."""

    def __call__(self, audio, sample_rate, message):
        return audio + 0.05


def test_marked_take_never_exceeds_full_scale(monkeypatch):
    wm = _wm()
    monkeypatch.setattr(wm, "_check_available", lambda: True)
    monkeypatch.setattr(wm, "_get_generator", _Offset)
    wave = torch.full((1, 16000), 0.98)  # host at -0.2 dBFS, 16 kHz: no resampling
    marked = wm.embed_watermark(wave, 16000, force=True)
    assert float(marked.abs().max()) <= 1.0 + 1e-6
    # The mark itself is intact: marked - attenuated host is the residual.
    gain = (float(marked[0, 0]) - 0.05) / 0.98
    assert 0.0 < gain < 1.0
    assert torch.allclose(marked - wave * gain, torch.full_like(wave, 0.05), atol=1e-5)


def test_take_with_headroom_is_not_attenuated(monkeypatch):
    wm = _wm()
    monkeypatch.setattr(wm, "_check_available", lambda: True)
    monkeypatch.setattr(wm, "_get_generator", _Offset)
    wave = torch.full((1, 16000), 0.5)
    marked = wm.embed_watermark(wave, 16000, force=True)
    assert torch.allclose(marked, wave + 0.05)
