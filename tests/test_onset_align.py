"""Tests for speech-onset alignment (#280, item 1).

Whisper-family ASR often stretches the first segment's start back over
leading music/silence (speech at 0:03, transcript says 0.0 → the dub
plays 3 s early). `services.onset_align.snap_segment_starts` post-snaps
segment starts forward to the first speech-like frame in the audio.

All tests use synthetic audio — pure NumPy, no models, no platform code.
"""
from __future__ import annotations

import numpy as np
import pytest

from services.onset_align import (
    MIN_SEG_DUR_S,
    PRE_ROLL_S,
    detect_speech_onset,
    detect_speech_onsets,
    snap_segment_starts,
)

SR = 16000


def _tone(duration_s: float, sr: int = SR, freq: float = 220.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(duration_s * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(duration_s: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(duration_s * sr), dtype=np.float32)


def _speech_after_silence(lead_s: float, speech_s: float = 2.0) -> np.ndarray:
    """`lead_s` of silence, then `speech_s` of loud tone."""
    return np.concatenate([_silence(lead_s), _tone(speech_s)])


def _noise_burst(duration_s: float, sr: int = SR, amp: float = 0.35, seed: int = 0) -> np.ndarray:
    """Short broadband burst — the energy shape of a footstep / door thud."""
    rng = np.random.default_rng(seed)
    return (amp * rng.standard_normal(int(duration_s * sr))).astype(np.float32)


# ── detect_speech_onset ─────────────────────────────────────────────────────


def test_detect_onset_finds_speech_after_leading_silence():
    audio = _speech_after_silence(2.5)
    onset = detect_speech_onset(audio, SR, 0.0, 4.5)
    assert onset == pytest.approx(2.5, abs=0.06)


def test_detect_onset_silent_window_returns_none():
    audio = _silence(3.0)
    assert detect_speech_onset(audio, SR, 0.0, 3.0) is None


def test_detect_onset_empty_or_invalid_window():
    audio = _tone(1.0)
    assert detect_speech_onset(audio, SR, 2.0, 1.0) is None   # end < start
    assert detect_speech_onset(audio, SR, 5.0, 6.0) is None   # past audio end
    assert detect_speech_onset(audio, 0, 0.0, 1.0) is None    # bad sr


def test_detect_onset_rejects_transient_burst():
    # #963: a 100 ms footstep-like burst at 1.0 s must not read as the
    # onset — the sustained speech at 3.0 s is the real one.
    audio = np.concatenate([
        _silence(1.0), _noise_burst(0.1), _silence(1.9), _tone(2.0),
    ])
    onset = detect_speech_onset(audio, SR, 0.0, 5.0)
    assert onset == pytest.approx(3.0, abs=0.06)


def test_detect_onset_transient_only_window_returns_none():
    # #963: a window containing nothing but a transient has no speech onset.
    audio = np.concatenate([_silence(1.0), _noise_burst(0.1), _silence(2.0)])
    assert detect_speech_onset(audio, SR, 0.0, 3.1) is None


# ── snap_segment_starts ─────────────────────────────────────────────────────


def test_snap_shifts_first_segment_to_speech_onset():
    """The issue-280 case: speech starts at 3.0 s but the transcript's first
    segment claims start=0.0 — the dub then plays 3 s early."""
    audio = _speech_after_silence(3.0, speech_s=3.0)
    segs = [{"start": 0.0, "end": 6.0, "text": "It's a nice day today"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    # Snapped to just before the onset (pre-roll keeps breaths/plosives).
    assert segs[0]["start"] == pytest.approx(3.0 - PRE_ROLL_S, abs=0.08)
    assert segs[0]["end"] == 6.0  # ends are untouched


def test_snap_never_moves_start_backward():
    # Segment starts mid-speech: onset is at/before seg start → no change.
    audio = _speech_after_silence(1.0, speech_s=5.0)
    segs = [{"start": 2.0, "end": 5.5, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 0
    assert segs[0]["start"] == 2.0


def test_snap_ignores_sub_threshold_shift():
    # Speech begins 0.1 s into the segment — below MIN_SHIFT_S, leave alone.
    audio = _speech_after_silence(1.1, speech_s=3.0)
    segs = [{"start": 1.0, "end": 4.0, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 0
    assert segs[0]["start"] == 1.0


def test_snap_skips_silent_segment():
    audio = _silence(5.0)
    segs = [{"start": 0.5, "end": 4.0, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 0
    assert segs[0]["start"] == 0.5


def test_snap_preserves_minimum_duration():
    # Onset is very late in the slot: shift is capped so the segment keeps
    # at least MIN_SEG_DUR_S of audio.
    audio = np.concatenate([_silence(3.8), _tone(0.4)])
    segs = [{"start": 0.0, "end": 4.0, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    assert segs[0]["start"] <= 4.0 - MIN_SEG_DUR_S + 1e-6
    assert segs[0]["end"] - segs[0]["start"] >= MIN_SEG_DUR_S - 1e-6


def test_snap_skips_too_short_segments():
    audio = _speech_after_silence(0.2, speech_s=0.4)
    segs = [{"start": 0.0, "end": 0.3, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 0


def test_snap_handles_multiple_segments_independently():
    # seg A: 2 s silence then speech; seg B: speech immediately.
    audio = np.concatenate([
        _silence(2.0), _tone(2.0),   # 0–4 s   (speech at 2.0)
        _tone(3.0),                  # 4–7 s   (speech immediately)
    ])
    segs = [
        {"start": 0.0, "end": 4.0, "text": "a"},
        {"start": 4.0, "end": 7.0, "text": "b"},
    ]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    assert segs[0]["start"] == pytest.approx(2.0 - PRE_ROLL_S, abs=0.08)
    assert segs[1]["start"] == 4.0


def test_snap_accepts_stereo_audio():
    mono = _speech_after_silence(2.0, speech_s=2.0)
    stereo = np.stack([mono, mono], axis=1)
    segs = [{"start": 0.0, "end": 4.0, "text": "x"}]
    assert snap_segment_starts(segs, stereo, SR) == 1
    assert segs[0]["start"] == pytest.approx(2.0 - PRE_ROLL_S, abs=0.08)


def test_snap_no_audio_is_noop():
    segs = [{"start": 0.0, "end": 4.0, "text": "x"}]
    assert snap_segment_starts(segs, np.zeros(0, dtype=np.float32), SR) == 0
    assert snap_segment_starts(segs, None, SR) == 0
    assert segs[0]["start"] == 0.0


def test_snap_ignores_footstep_before_speech():
    """#963: a footstep/sigh-like transient before the dialogue must not be
    'interpreted as the start of the conversation' (reporter's theory) —
    the start snaps to the sustained speech onset instead. The burst also
    must not block the long silence-trim (it is <10% of the skipped span)."""
    audio = np.concatenate([
        _silence(1.0), _noise_burst(0.1), _silence(1.9), _tone(3.0),
    ])
    segs = [{"start": 0.0, "end": 6.0, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    assert segs[0]["start"] == pytest.approx(3.0 - PRE_ROLL_S, abs=0.08)


def test_snap_refuses_long_jump_over_audible_content():
    # #963 snap-distance cap: quiet-but-audible speech (below the relative
    # onset threshold) fills the first 3 s, loud speech follows. Jumping
    # >MAX_SNAP_S forward would skip the quiet speech wholesale and play
    # the dub seconds LATE — long jumps are only trusted over true silence.
    audio = np.concatenate([_tone(3.0, amp=0.04), _tone(3.0, amp=0.5)])
    segs = [{"start": 0.0, "end": 6.0, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 0
    assert segs[0]["start"] == 0.0


def test_snap_small_shift_over_audible_content_still_allowed():
    # Same quiet-then-loud shape, but the loud onset is within MAX_SNAP_S:
    # bounded corrections stay enabled even when the lead isn't silent.
    audio = np.concatenate([_tone(1.2, amp=0.04), _tone(3.0, amp=0.5)])
    segs = [{"start": 0.0, "end": 4.2, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    assert segs[0]["start"] == pytest.approx(1.2 - PRE_ROLL_S, abs=0.08)


def test_snap_long_jump_over_true_silence_still_snaps():
    # The #280 regression case restated against the cap: a >MAX_SNAP_S jump
    # over genuine silence (Demucs stripped the leading music from the
    # vocals track) must still be trimmed in full.
    audio = _speech_after_silence(4.0, speech_s=3.0)
    segs = [{"start": 0.0, "end": 7.0, "text": "x"}]
    n = snap_segment_starts(segs, audio, SR)
    assert n == 1
    assert segs[0]["start"] == pytest.approx(4.0 - PRE_ROLL_S, abs=0.08)


def test_snap_skipped_on_mixed_audio():
    # #963 source-awareness: when Demucs was skipped/failed the track still
    # contains music and ambience — sustained energy there is as likely the
    # score as the speaker, so snapping is disabled outright and whisper's
    # own timestamps stand (even for the classic #280-shaped signal).
    audio = _speech_after_silence(3.0, speech_s=3.0)
    segs = [{"start": 0.0, "end": 6.0, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR, separated_vocals=False) == 0
    assert segs[0]["start"] == 0.0


def test_snap_tolerates_malformed_segment_entries():
    audio = _speech_after_silence(2.0)
    segs = [
        {"start": "bogus", "end": 4.0},
        {"end": 4.0},  # missing start → defaults to 0.0, still valid
        {"start": 0.0, "end": 4.0, "text": "ok"},
    ]
    # Must not raise; the well-formed entries still get processed.
    n = snap_segment_starts(segs, audio, SR)
    assert n >= 1
    assert segs[2]["start"] == pytest.approx(2.0 - PRE_ROLL_S, abs=0.08)


# ── detect_speech_onsets (full-track, #280 item 3) ──────────────────────────


def test_onsets_silence_returns_empty():
    assert detect_speech_onsets(_silence(3.0), SR) == []


def test_onsets_empty_or_invalid_audio():
    assert detect_speech_onsets(np.zeros(0, dtype=np.float32), SR) == []
    assert detect_speech_onsets(_tone(1.0), 0) == []


def test_onsets_two_bursts_yield_two_onsets():
    # 1 s silence, 1 s tone, 1 s silence, 1 s tone — exactly two rises.
    audio = np.concatenate([_silence(1.0), _tone(1.0), _silence(1.0), _tone(1.0)])
    onsets = detect_speech_onsets(audio, SR)
    assert len(onsets) == 2
    assert onsets[0] == pytest.approx(1.0, abs=0.06)
    assert onsets[1] == pytest.approx(3.0, abs=0.06)


def test_onsets_speech_at_t0_registers():
    audio = np.concatenate([_tone(1.0), _silence(2.0)])
    onsets = detect_speech_onsets(audio, SR)
    assert len(onsets) == 1
    assert onsets[0] == pytest.approx(0.0, abs=0.06)


def test_onsets_hysteresis_ignores_short_dip():
    # A 60 ms dip inside a burst (< MIN_ONSET_GAP_S of 150 ms below the
    # threshold) must NOT register a second onset.
    audio = np.concatenate([
        _silence(1.0), _tone(0.5), _silence(0.06), _tone(0.5),
    ])
    onsets = detect_speech_onsets(audio, SR)
    assert len(onsets) == 1
    assert onsets[0] == pytest.approx(1.0, abs=0.06)


def test_onsets_hysteresis_long_gap_registers_new_onset():
    # A 400 ms gap (> MIN_ONSET_GAP_S) re-arms the detector.
    audio = np.concatenate([
        _silence(1.0), _tone(0.5), _silence(0.4), _tone(0.5),
    ])
    onsets = detect_speech_onsets(audio, SR)
    assert len(onsets) == 2
    assert onsets[1] == pytest.approx(1.9, abs=0.06)


def test_onsets_stereo_audio_accepted():
    mono = np.concatenate([_silence(1.0), _tone(1.0)])
    stereo = np.stack([mono, mono], axis=1)
    onsets = detect_speech_onsets(stereo, SR)
    assert len(onsets) == 1
    assert onsets[0] == pytest.approx(1.0, abs=0.06)


def test_onsets_sorted_ascending():
    audio = np.concatenate(
        [_silence(0.5), _tone(0.3)] * 4
    )
    onsets = detect_speech_onsets(audio, SR)
    assert onsets == sorted(onsets)
    assert len(onsets) == 4


# ── #3419: VAD-gated starts and ends ───────────────────────────────────────
#
# Speech is voiced; sighs, breaths and rustles are not. A *sustained*
# unvoiced sound passes the #963 energy-sustain guard, so before #3419 it
# could still set a line's start, and trailing breaths/silence padded the end.

from services.onset_align import (  # noqa: E402
    CONSONANT_LEAD_S,
    TAIL_PAD_S,
    snap_segments_to_speech,
    trim_segment_ends,
)


def _breath(duration_s: float, amp: float = 0.3, seed: int = 3) -> np.ndarray:
    """Band-limited noise (500–3000 Hz): the spectrum of a sigh or breath."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * SR)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spec[(freqs < 500) | (freqs > 3000)] = 0
    x = np.fft.irfft(spec, n)
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


def _voice(duration_s: float, f0: float = 140.0, amp: float = 0.5) -> np.ndarray:
    """Harmonic-rich voiced tone, a stand-in for a vowel."""
    t = np.arange(int(duration_s * SR)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


def test_sustained_sigh_before_the_line_does_not_set_its_start():
    audio = np.concatenate([_silence(0.3), _breath(0.6), _silence(0.1), _voice(2.0)])
    segs = [{"start": 0.0, "end": 3.0, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 1
    # The line starts at the voice (1.0 s), not at the sigh (0.3 s).
    assert segs[0]["start"] == pytest.approx(1.0 - PRE_ROLL_S, abs=0.08)


def test_sigh_running_into_the_first_word_keeps_only_a_consonant_lead():
    audio = np.concatenate([_breath(0.8), _voice(2.0)])
    segs = [{"start": 0.0, "end": 2.8, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 1
    assert 0.8 - 0.15 - PRE_ROLL_S - 0.03 <= segs[0]["start"] <= 0.8


def test_long_rustle_before_the_first_word_is_skipped():
    # More than MAX_SNAP_S of audible but unvoiced sound: #963 refused this
    # jump; with no voiced speech in the skipped span it is safe to take.
    audio = np.concatenate([_breath(2.5, amp=0.25), _silence(0.2), _voice(2.0)])
    segs = [{"start": 0.0, "end": 4.7, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 1
    assert segs[0]["start"] == pytest.approx(2.7 - PRE_ROLL_S, abs=0.08)


def test_whispered_line_keeps_the_energy_rule():
    # No voiced frames at all: the energy onset still applies.
    audio = np.concatenate([_silence(1.0), _breath(2.0)])
    segs = [{"start": 0.0, "end": 3.0, "text": "x"}]
    assert snap_segment_starts(segs, audio, SR) == 1
    assert segs[0]["start"] == pytest.approx(1.0 - PRE_ROLL_S, abs=0.08)


def test_trailing_breath_and_silence_are_trimmed_from_the_end():
    audio = np.concatenate([_voice(1.5), _silence(0.1), _breath(0.7), _silence(1.0)])
    segs = [{"start": 0.0, "end": 3.3, "text": "x"}]
    assert trim_segment_ends(segs, audio, SR) == 1
    assert segs[0]["end"] == pytest.approx(1.5 + TAIL_PAD_S, abs=0.08)
    assert segs[0]["start"] == 0.0


def test_end_trim_keeps_a_final_consonant():
    audio = np.concatenate([_voice(1.5), _breath(0.1), _silence(1.0)])
    segs = [{"start": 0.0, "end": 2.6, "text": "x"}]
    assert trim_segment_ends(segs, audio, SR) == 1
    assert segs[0]["end"] == pytest.approx(1.6 + TAIL_PAD_S, abs=0.08)


def test_end_trim_leaves_whispered_lines_and_tight_ends_alone():
    whispered = [{"start": 0.0, "end": 3.0, "text": "x"}]
    assert trim_segment_ends(whispered, np.concatenate([_breath(2.0), _silence(1.0)]), SR) == 0
    assert whispered[0]["end"] == 3.0
    tight = [{"start": 0.0, "end": 1.6, "text": "x"}]
    assert trim_segment_ends(tight, _voice(1.6), SR) == 0
    assert tight[0]["end"] == 1.6


def test_end_trim_is_skipped_on_mixed_audio():
    audio = np.concatenate([_voice(1.5), _silence(1.5)])
    segs = [{"start": 0.0, "end": 3.0, "text": "x"}]
    assert trim_segment_ends(segs, audio, SR, separated_vocals=False) == 0
    assert segs[0]["end"] == 3.0


def test_snap_segments_to_speech_moves_both_edges():
    audio = np.concatenate([_breath(0.6), _silence(0.2), _voice(1.5), _breath(0.5), _silence(0.5)])
    segs = [{"start": 0.0, "end": 3.3, "text": "x"}]
    assert snap_segments_to_speech(segs, audio, SR) == 2
    assert segs[0]["start"] == pytest.approx(0.8 - PRE_ROLL_S, abs=0.08)
    # A breath right after the last vowel may be a final consonant: at most
    # CONSONANT_LEAD_S of it is kept, the rest of the breath is trimmed.
    assert segs[0]["end"] == pytest.approx(2.3 + CONSONANT_LEAD_S + TAIL_PAD_S, abs=0.08)
