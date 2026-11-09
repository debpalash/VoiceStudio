"""Generated takes must not start or end with the engine's own silence padding.

Engines pad every render with near-silence at both ends; only the dub path
trimmed it, so every Generate / OpenAI-API take opened and closed on a blank.
"""
from types import SimpleNamespace

import pytest
import torch

import services.audio_dsp as dsp
from api.routers import generation as gen
from api.routers import openai_compat as oc

SR = 1000  # 1 sample per ms keeps the arithmetic readable
MARGIN = 50  # trim_speech_padding keeps 50 ms of context


def _padded(lead=400, speech=1000, tail=600):
    """[1, n] take: `lead` ms of engine silence, speech, `tail` ms of silence."""
    return torch.cat([torch.zeros(1, lead), torch.full((1, speech), 0.5),
                      torch.zeros(1, tail)], dim=-1)


@pytest.fixture
def no_dsp(monkeypatch):
    # Isolate the trim from mastering / pedalboard: both become identity.
    monkeypatch.setattr(dsp, "apply_mastering", lambda a, sample_rate=None: a)
    monkeypatch.setattr(dsp, "apply_effects_chain", lambda a, sample_rate, chain: a)


def test_whole_take_trim_strips_engine_padding(no_dsp):
    out = gen._apply_effect_chain(_padded(), SR, "broadcast", trim_edges=True)
    assert out.shape[-1] == 1000 + 2 * MARGIN


def test_preview_chunks_and_raw_keep_their_edges(no_dsp):
    assert gen._apply_effect_chain(_padded(), SR, "broadcast").shape[-1] == 2000
    assert gen._apply_effect_chain(_padded(), SR, "raw", trim_edges=True).shape[-1] == 2000


def test_dead_render_is_not_shrunk(no_dsp):
    silent = torch.zeros(1, 2000)
    assert gen._apply_effect_chain(silent, SR, "broadcast", trim_edges=True).shape[-1] == 2000


@pytest.mark.parametrize("preset,duration,expected", [
    ("broadcast", None, True), (None, None, True), ("raw", None, False),
    ("broadcast", 5.0, False),
])
def test_wants_edge_trim(preset, duration, expected):
    assert gen._wants_edge_trim(preset, duration) is expected


def test_pause_spans_trimmed_so_requested_pause_is_exact():
    segs = [("", 300), ("hello", 500), ("world", 200)]
    out = gen._render_with_pauses(lambda t: _padded(), segs, SR, trim_spans=True)
    span = 1000 + 2 * MARGIN
    # Deliberate leading and trailing pauses survive; engine padding does not.
    assert out.shape[-1] == 300 + span + 500 + span + 200
    assert torch.all(out[:, :300] == 0)
    assert torch.all(out[:, -200:] == 0)


def test_pause_spans_untrimmed_by_default():
    out = gen._render_with_pauses(lambda t: _padded(), [("hi", 0)], SR)
    assert out.shape[-1] == 2000


class _Backend:
    sample_rate = SR
    applies_own_mastering = True

    def generate(self, text, **kw):
        return _padded()


@pytest.fixture
def openai_tts(monkeypatch):
    import services.watermark as wm
    monkeypatch.setattr(wm, "mark_synthetic", lambda w, sr, **k: w)
    return lambda kw: oc._run_tts(_Backend(), "hi", kw)[0]


def test_openai_speech_trims_edges(openai_tts):
    assert openai_tts({}).shape[-1] == 1000 + 2 * MARGIN


def test_openai_speech_keeps_explicit_duration(openai_tts):
    assert openai_tts({"duration": 2.0}).shape[-1] == 2000
