"""
Regression tests for #3412: "Even out volume" must level, not squash.

The Clone toggle maps to the ``broadcast`` effect preset. It used to run a
3:1 / 5 ms / 80 ms compressor at an absolute -18 dB threshold plus
pedalboard's ``Limiter``, which hides a fixed 4:1 compressor at -10 dBFS and
adds makeup gain. On a normally loud take that flattened every peak and lifted
decay tails (heard as padded, reverberant speech) while barely evening out
quiet and loud passages. These tests pin the intended behaviour on a
synthetic, speech-like signal with loud and quiet phrases.
"""

import numpy as np
import pytest
import torch
from services.audio_dsp import (
    apply_effects_chain,
    apply_mastering,
    get_effect_chain,
    level_speech,
    normalize_audio,
    peak_limit,
)

SR = 24000
SYLLABLE_S, TAIL_S, GAP_S, PAUSE_S = 0.22, 0.15, 0.05, 0.3
PHRASE_S = 4 * (SYLLABLE_S + TAIL_S + GAP_S) + PAUSE_S


def _speech_like(levels_db, seed=0):
    """Four voiced syllables per phrase, each followed by a decaying room tail.

    Syllables are harmonic tones with a plosive-like onset; ``levels_db`` sets
    each phrase's RMS. The tail sits 30 dB under its syllable.
    """
    rng = np.random.default_rng(seed)
    parts = []
    for level in levels_db:
        for _ in range(4):
            t = np.arange(int(SYLLABLE_S * SR)) / SR
            f0 = 120 + 40 * rng.random()
            voiced = sum(np.sin(2 * np.pi * f0 * k * t + 6 * rng.random()) / k for k in range(1, 12))
            env = np.sin(np.pi * t / t[-1]) ** 0.6
            env[: int(0.01 * SR)] *= 1.8
            voiced = voiced * env
            voiced *= 10 ** (level / 20) / np.sqrt(np.mean(voiced ** 2))
            n_tail = int(TAIL_S * SR)
            tail = (rng.standard_normal(n_tail) * 10 ** ((level - 30) / 20)
                    * np.exp(-np.arange(n_tail) / (0.04 * SR)))
            parts += [voiced, tail, np.zeros(int(GAP_S * SR))]
        parts.append(np.zeros(int(PAUSE_S * SR)))
    return torch.from_numpy(np.concatenate(parts).astype(np.float32)).unsqueeze(0)


def _phrases(audio):
    x = audio.squeeze(0).double().numpy()
    step = int(PHRASE_S * SR)
    return [x[i * step:(i + 1) * step] for i in range(len(x) // step)]


def _rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)))


def _crest_db(x):
    """Peak-to-RMS of one phrase: falls when peaks are flattened."""
    return 20 * np.log10(np.abs(x).max() / np.sqrt(np.mean(x ** 2)))


def _tail_ratio_db(audio):
    """Median level of the room tails relative to their syllables."""
    x = audio.squeeze(0).double().numpy()
    syl, tail, gap = (int(s * SR) for s in (SYLLABLE_S, TAIL_S, GAP_S))
    ratios, pos = [], 0
    while pos + syl + tail <= len(x) - int(PAUSE_S * SR):
        for _ in range(4):
            voiced = x[pos:pos + syl]
            decay = x[pos + syl + int(0.03 * SR):pos + syl + tail]
            ratios.append(_rms_db(decay) - _rms_db(voiced))
            pos += syl + tail + gap
        pos += int(PAUSE_S * SR)
    return float(np.median(ratios))


def _even_out_volume(audio):
    """The toggle-on path of /generate (generation._apply_effect_chain)."""
    out = apply_mastering(audio, sample_rate=SR)
    out = apply_effects_chain(out, SR, get_effect_chain("broadcast"))
    return normalize_audio(out, target_dBFS=-2.0)


# A normally loud take: phrase RMS -16/-30/-12 dBFS, peaks between -8 and -1 dBFS.
LOUD_TAKE = (-16.0, -30.0, -12.0)


class TestEvenOutVolume:
    def test_keeps_peaks_of_every_phrase(self):
        """Each phrase keeps its peak-to-RMS within 1.5 dB (was 3+ dB lost)."""
        pytest.importorskip("pedalboard")
        audio = _speech_like(LOUD_TAKE)
        out = _even_out_volume(audio)
        for before, after in zip(_phrases(audio), _phrases(out)):
            assert _crest_db(before) - _crest_db(after) < 1.5

    def test_actually_evens_out_phrase_volume(self):
        """The loud/quiet phrase spread shrinks by at least half."""
        pytest.importorskip("pedalboard")
        audio = _speech_like(LOUD_TAKE)
        out = _even_out_volume(audio)
        def spread(a):
            return np.ptp([_rms_db(p) for p in _phrases(a)])

        assert spread(out) <= 0.5 * spread(audio)

    def test_broadcast_chain_is_level_independent(self):
        """The same take 10 dB quieter comes out the same after normalization."""
        pytest.importorskip("pedalboard")
        audio = _speech_like(LOUD_TAKE)
        chain = get_effect_chain("broadcast")
        loud = normalize_audio(apply_effects_chain(audio, SR, chain))
        quiet = normalize_audio(apply_effects_chain(audio * 0.316, SR, chain))
        assert torch.allclose(loud, quiet, atol=1e-3)


class TestLeveler:
    def test_does_not_lift_room_tails(self):
        """Tails keep their level relative to the speech (no padded reverb)."""
        audio = _speech_like(LOUD_TAKE)
        out = level_speech(audio, SR)
        assert abs(_tail_ratio_db(out) - _tail_ratio_db(audio)) < 0.5

    def test_silence_is_returned_unchanged(self):
        audio = torch.full((1, SR), 1e-4)
        assert level_speech(audio, SR) is audio

    def test_keeps_shape_dtype_and_runs_without_pedalboard_stages(self):
        audio = _speech_like(LOUD_TAKE).repeat(2, 1)
        out = apply_effects_chain(audio, SR, [{"type": "leveler"}])
        assert out.shape == audio.shape and out.dtype == audio.dtype


class TestLimiterIsACeiling:
    def test_audio_below_threshold_is_untouched(self):
        """No hidden compression or makeup gain under the ceiling."""
        audio = _speech_like(LOUD_TAKE)
        audio = audio * (10 ** (-3 / 20) / audio.abs().max())  # peak -3 dBFS
        out = apply_effects_chain(audio, SR, [{"type": "limiter", "threshold_db": -1.0}])
        assert torch.equal(out, audio)

    def test_overs_are_held_at_the_ceiling_and_the_rest_is_kept(self):
        audio = _speech_like(LOUD_TAKE)
        audio = audio * (10 ** (3 / 20) / audio.abs().max())  # peak +3 dBFS
        out = peak_limit(audio, SR, threshold_db=-1.0)
        assert out.abs().max().item() <= 10 ** (-1 / 20) + 1e-6
        quiet = _phrases(audio)[1]  # 18 dB below the loud phrase: no overs
        assert np.allclose(_phrases(out)[1], quiet)

    def test_attenuation_starts_within_the_lookahead_before_a_peak(self):
        """An isolated over must not duck the speech before it by the hold time."""
        t = np.arange(SR) / SR
        audio = 0.1 * np.sin(2 * np.pi * 200 * t)
        peak = SR // 2
        audio[peak] = 2.0
        audio = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
        out = peak_limit(audio, SR, threshold_db=-1.0, lookahead_ms=5.0, release_ms=40.0)
        def gain(i):
            return abs(out[0, i].item() / audio[0, i].item())

        before = peak - int(0.006 * SR)  # just outside the 5 ms lookahead
        before += int(np.argmax(np.abs(audio[0, before - 60:before].numpy()))) - 60
        assert gain(before) == pytest.approx(1.0, abs=1e-6)
        assert out[0, peak].abs().item() <= 10 ** (-1 / 20) + 1e-6
        after = peak + int(0.06 * SR)  # recovered after ramp + hold
        assert gain(after) == pytest.approx(1.0, abs=1e-6)


def _sustained_then_tail():
    """A loud passage, then one 1.5 s sustained phrase and a 1 s room tail."""
    rng = np.random.default_rng(3)
    t = np.arange(int(1.5 * SR)) / SR
    phrase = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 10))
    phrase *= 10 ** (-24 / 20) / np.sqrt(np.mean(phrase ** 2))
    n_tail = SR
    tail = (rng.standard_normal(n_tail) * 10 ** (-54 / 20)
            * np.exp(-np.arange(n_tail) / (0.3 * SR)))
    loud = _speech_like((-14.0,)).squeeze(0).numpy()
    x = np.concatenate([loud, phrase, tail]).astype(np.float32)
    return torch.from_numpy(x).unsqueeze(0), len(loud), len(phrase)


class TestLevelerPauses:
    def test_tail_after_a_sustained_phrase_keeps_the_phrase_gain(self):
        """The tail must not inherit a gain picked up while the window drained."""
        audio, start, n_phrase = _sustained_then_tail()
        out = level_speech(audio, SR)
        end = start + n_phrase
        body = slice(end - SR // 2, end - SR // 10)
        tail = slice(end + int(0.05 * SR), end + int(0.6 * SR))
        x, y = audio[0].double().numpy(), out[0].double().numpy()
        before = _rms_db(x[tail]) - _rms_db(x[body])
        after = _rms_db(y[tail]) - _rms_db(y[body])
        assert abs(after - before) < 1.0


class TestLongTakes:
    @pytest.mark.parametrize("stage", ["leveler", "limiter"])
    def test_chunked_output_matches_one_pass(self, stage, monkeypatch):
        import services.audio_dsp as dsp

        audio = _speech_like(LOUD_TAKE * 3).repeat(2, 1)
        audio = audio * (10 ** (3 / 20) / audio.abs().max())  # overs to limit
        fn = level_speech if stage == "leveler" else peak_limit
        whole = fn(audio, SR)
        monkeypatch.setattr(dsp, "_CHUNK_SAMPLES", 4801)
        chunked = fn(audio, SR)
        assert torch.allclose(whole, chunked, atol=1e-6)

    @pytest.mark.parametrize("stage", ["leveler", "limiter"])
    def test_memory_stays_bounded_on_a_long_take(self, stage):
        """A 10-minute take needs about one output copy, not many float64 copies."""
        import tracemalloc

        rng = np.random.default_rng(0)
        n = 600 * SR
        audio = torch.from_numpy((rng.standard_normal(n) * 0.3).astype(np.float32)).unsqueeze(0)
        fn = level_speech if stage == "leveler" else peak_limit
        tracemalloc.start()
        try:
            fn(audio, SR)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 2 * audio.numel() * audio.element_size()
