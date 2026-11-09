"""Unit tests for the archetype-preview quality guard (``api.routers.archetypes``).

Background: the Hype Host / Podcaster / Vlogger previews shipped a loud tonal
*buzz* instead of speech. The renderer pinned ``num_step=16`` + ``seed=42`` and
the "social" sample script collapsed to a near-pure tone at that point; the
old silence-only guard missed it (the buzz is loud, not silent) so the garbage
was cached and served.

These tests cover the fix *without the 5 GB model / a GPU*: they drive the pure
``_spectral_flatness`` / ``_is_unusable_audio`` helpers with synthetic tones and
tracked speech demo renders, and assert the render constants did not regress.
"""
from __future__ import annotations

import math
from pathlib import Path

import soundfile as sf

import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before the router imports OUTPUTS_DIR / VOICES_DIR from
# the REAL core.config (the old sys.modules stub leaked at collection time
# and broke later importers in mixed runs — see conftest.py).
torch = pytest.importorskip("torch")  # noqa: E402

from api.routers import archetypes as arch  # noqa: E402

SR = 24_000
N = SR * 3  # 3 s clips


def _pure_tone(hz: float = 220.0) -> "torch.Tensor":
    t = torch.arange(N, dtype=torch.float32) / SR
    return 0.8 * torch.sin(2 * math.pi * hz * t)


def _white_noise() -> "torch.Tensor":
    g = torch.Generator().manual_seed(0)
    return 0.5 * (torch.rand(N, generator=g) * 2 - 1)


def _two_partial_buzz(f1: float = 200.0, f2: float = 310.0) -> "torch.Tensor":
    """Two unrelated sinusoids: a loud, inharmonic, still purely tonal buzz."""
    t = torch.arange(N, dtype=torch.float32) / SR
    sig = torch.sin(2 * math.pi * f1 * t) + 0.6 * torch.sin(2 * math.pi * f2 * t)
    return 0.7 * sig / sig.abs().max()


def test_flatness_ordering_tone_speech_noise():
    tone = arch._spectral_flatness(_pure_tone())
    speech = arch._spectral_flatness(_speech_like())
    noise = arch._spectral_flatness(_white_noise())
    assert tone < arch._DEGENERATE_FLATNESS < speech < noise


def test_flatness_none_for_short_or_non_finite_input():
    assert arch._spectral_flatness(torch.ones(16)) is None
    bad = _speech_like().clone()
    bad[100] = float("nan")
    assert arch._spectral_flatness(bad) is None


def test_flatness_multichannel_is_mono_mixed():
    stereo = torch.stack([_speech_like(), _speech_like()])
    assert arch._spectral_flatness(stereo) == pytest.approx(
        arch._spectral_flatness(_speech_like()), rel=1e-4)


def _speech_like() -> "torch.Tensor":
    """Broadband + harmonic + amplitude-modulated — a coarse stand-in for voiced
    speech: several harmonics (formant-ish), additive noise (consonants), and a
    syllabic envelope (word gaps). Flatness lands between a pure tone and noise.
    """
    g = torch.Generator().manual_seed(1)
    t = torch.arange(N, dtype=torch.float32) / SR
    harm = sum(torch.sin(2 * math.pi * f * t) / (i + 1)
               for i, f in enumerate((130.0, 260.0, 390.0, 520.0)))
    noise = 0.3 * (torch.rand(N, generator=g) * 2 - 1)
    env = 0.5 + 0.5 * torch.sin(2 * math.pi * 4.0 * t).clamp(min=0)  # ~4 Hz syllables
    sig = (harm + noise) * env
    return 0.7 * sig / sig.abs().max()


# ── _spectral_flatness ──────────────────────────────────────────────────────
def test_flatness_orders_tone_below_speech_below_noise():
    tone = arch._spectral_flatness(_pure_tone())
    speech = arch._spectral_flatness(_speech_like())
    noise = arch._spectral_flatness(_white_noise())
    assert tone is not None and speech is not None and noise is not None
    assert tone < arch._DEGENERATE_FLATNESS < speech < noise


def test_flatness_returns_none_on_too_short_or_nonfinite():
    assert arch._spectral_flatness(torch.zeros(16)) is None
    bad = torch.full((4096,), float("nan"))
    assert arch._spectral_flatness(bad) is None


# ── _is_unusable_audio ──────────────────────────────────────────────────────
def test_pure_tone_is_unusable():
    # The degenerate-buzz failure mode: loud (passes the silence guard) but tonal.
    tone = _pure_tone()
    assert tone.abs().max() > 0.02            # not silent
    assert arch._is_unusable_audio(tone) is True


def test_silence_is_unusable():
    assert arch._is_unusable_audio(torch.zeros(N)) is True


def test_speech_like_is_usable():
    assert arch._is_unusable_audio(_speech_like()) is False


@pytest.mark.parametrize("make", [
    lambda: _pure_tone(220.0),
    lambda: _pure_tone(440.0),
    _two_partial_buzz,
])
def test_tonal_signals_sit_far_below_threshold(make):
    sig = make()
    assert arch._spectral_flatness(sig) * 10 <= arch._DEGENERATE_FLATNESS
    assert arch._is_unusable_audio(sig) is True

_SAMPLES = Path(__file__).resolve().parents[1] / "assets" / "samples"
_SPEECH_FIXTURES = [
    "demo_voice.wav",
    "demo_clone_output.wav",
    *[f"voice_design/demo_voice_design_{name}.wav" for name in (
        "audiobook_uk_narrator", "aussie_podcaster", "bedtime_storyteller",
        "gravelly_villain", "indian_support_agent", "mandarin_sichuan", "us_news_anchor",
    )],
    *[f"dictation/{name}.wav" for name in (
        "en_conversational", "en_technical", "fr_reservation",
    )],
    *[f"demo/dubbing/{name}.src.wav" for name in (
        "source", "dubbed_es", "dubbed_fr", "dubbed_ja", "dubbed_zh",
    )],
]


@pytest.mark.parametrize("fixture", _SPEECH_FIXTURES)
def test_real_shipped_speech_clears_quality_floor(fixture):
    # These are existing tracked demo renders, not synthesized stand-ins or
    # asserted measurements. Loading PCM needs neither a model nor a network.
    audio, _sample_rate = sf.read(_SAMPLES / fixture, dtype="float32", always_2d=True)
    speech = torch.from_numpy(audio.T)
    flatness = arch._spectral_flatness(speech)
    assert flatness is not None
    assert flatness > arch._DEGENERATE_FLATNESS * 10
    assert arch._is_unusable_audio(speech) is False

def test_flatness_does_not_depend_on_clip_length():
    clip = _speech_like()
    short = arch._spectral_flatness(clip)
    long = arch._spectral_flatness(torch.cat([clip] * 4))
    assert abs(long - short) / short < 0.02



# ── Constants didn't regress ────────────────────────────────────────────────
def test_preview_render_constants():
    # 16 steps under-converged on the social script; the fix bumped it.
    assert arch._PREVIEW_NUM_STEP >= 24
    assert 0 < arch._DEGENERATE_FLATNESS < 0.03
