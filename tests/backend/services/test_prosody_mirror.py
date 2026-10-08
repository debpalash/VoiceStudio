"""Prosody Mirror: source-delivery measurement → director taxonomy.

Synthetic "speech" (a harmonic tone with syllable-rate amplitude modulation)
gives exact ground truth for pitch, loudness and syllable rate, so every
mapping is checked against known inputs rather than recorded fixtures.
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from services import director, prosody_mirror as pm

SR = 44100


def _line(f0=120.0, amp=0.1, syllables_hz=4.0, dur_s=2.0, sr=SR, vibrato=0.0, whisper=False, seed=0):
    t = np.arange(int(dur_s * sr)) / sr
    envelope = 0.5 * (1 - np.cos(2 * np.pi * syllables_hz * t))
    if whisper:
        carrier = np.random.default_rng(seed).standard_normal(t.size) * 0.5
    else:
        freq = f0 * (1 + vibrato * np.sin(2 * np.pi * 1.5 * t))
        phase = 2 * np.pi * np.cumsum(freq) / sr
        carrier = sum(np.sin(k * phase) / k for k in range(1, 6))
    return (amp * carrier * envelope).astype(np.float32)


def _features(**kwargs) -> pm.ProsodyFeatures:
    feat = pm.extract_features(_line(**kwargs), SR)
    assert feat is not None
    return feat


# ── Feature extraction ──────────────────────────────────────────────────────

@pytest.mark.parametrize("f0", [90.0, 120.0, 180.0, 300.0])
def test_pitch_matches_fundamental(f0):
    assert _features(f0=f0).pitch_st == pytest.approx(12 * np.log2(f0 / 100.0), abs=0.3)


def test_loudness_tracks_amplitude():
    quiet, loud = _features(amp=0.05), _features(amp=0.2)
    assert loud.loudness_db - quiet.loudness_db == pytest.approx(20 * np.log10(4), abs=0.5)


def test_rate_tracks_syllables():
    slow, normal, fast = (_features(syllables_hz=hz).rate_hz for hz in (2.5, 4.0, 7.0))
    assert slow < normal < fast
    assert normal == pytest.approx(4.0, abs=0.6)
    assert fast == pytest.approx(7.0, abs=0.8)


def test_pitch_movement_measured():
    assert _features(vibrato=0.15).pitch_spread_st > 3.0
    assert _features().pitch_spread_st < 0.5


def test_whisper_has_no_pitch():
    feat = _features(whisper=True, amp=0.02)
    assert feat.voiced_ratio == 0.0
    assert feat.pitch_st is None and feat.pitch_spread_st is None


def test_silence_and_short_audio_are_unmeasurable():
    assert pm.extract_features(np.zeros(SR, dtype=np.float32), SR) is None
    assert pm.extract_features(_line(dur_s=0.2), SR) is None
    assert pm.extract_features(np.zeros(10, dtype=np.float32), SR) is None


@pytest.mark.parametrize("sr", [16000, 22050, 48000])
def test_sample_rate_and_channels_do_not_change_measurement(sr):
    mono = _line(f0=150.0, sr=sr)
    stereo = np.stack([mono, mono], axis=1)
    feat = pm.extract_features(stereo, sr)
    assert feat.pitch_st == pytest.approx(12 * np.log2(1.5), abs=0.3)


# ── Baseline + taxonomy mapping ─────────────────────────────────────────────

def test_out_of_phase_stereo_is_not_cancelled_to_silence():
    mono = _line()
    expected = pm.extract_features(mono, SR)
    stereo = np.stack([mono, -mono], axis=1)
    got = pm.extract_features(stereo, SR)
    assert got is not None and expected is not None
    assert got.loudness_db == pytest.approx(expected.loudness_db, abs=0.5)
    # In-phase stereo keeps its plain average.
    in_phase = pm.extract_features(np.stack([mono, mono], axis=1), SR)
    assert in_phase.loudness_db == pytest.approx(expected.loudness_db, abs=0.5)


def test_baseline_spread_is_floored():
    rows = [_features()] * 5
    base = pm.build_baseline(rows, pitch_comparable=True)
    assert base.spread["loudness_db"] == pm._FEATURE_FLOORS["loudness_db"]
    assert base.spread["rate_hz"] == pm._FEATURE_FLOORS["rate_hz"]


def test_pitch_ignored_when_not_comparable():
    base = pm.build_baseline([_features(f0=100.0), _features(f0=200.0)], pitch_comparable=False)
    z = base.z(_features(f0=300.0))
    assert "pitch_st" not in z and "pitch_spread_st" not in z
    assert "loudness_db" in z


@pytest.mark.parametrize(
    ("z", "expected"),
    [
        ({"loudness_db": 1.5, "rate_hz": 1.5, "pitch_st": 1.0, "pitch_spread_st": 1.0}, "urgent, quick"),
        ({"loudness_db": 1.2, "rate_hz": 0.0, "pitch_st": 1.2, "pitch_spread_st": 1.2}, "energetic"),
        ({"loudness_db": 1.5, "rate_hz": 1.5, "pitch_st": 0.0, "pitch_spread_st": 0.0}, "quick"),
        ({"loudness_db": 1.5, "rate_hz": 1.5}, "urgent, quick"),
        ({"loudness_db": -1.2, "rate_hz": -1.5, "pitch_st": -1.0, "pitch_spread_st": -1.0}, "calm, slow"),
        ({"loudness_db": -1.5, "voiced_ratio": -3.0}, "whispered"),
        ({"loudness_db": 2.0, "rate_hz": -1.0}, "energetic, slow, announcing"),
        ({"loudness_db": 0.3, "rate_hz": -0.2, "pitch_st": 0.4, "pitch_spread_st": -0.3}, ""),
        ({"loudness_db": 0.0, "rate_hz": 0.0, "pitch_st": 3.5, "pitch_spread_st": 0.5}, ""),
        ({"loudness_db": 0.0, "rate_hz": 0.0, "pitch_st": -3.5, "pitch_spread_st": -0.5}, ""),
        ({}, ""),
    ],
)
def test_infer_direction(z, expected):
    assert pm.infer_direction(z).instruct_prompt() == expected


def test_every_mirrored_token_round_trips_through_director():
    """The mirror's output must mean the same thing when parsed back without
    an LLM — that is what generation does with a segment's direction."""
    extremes = (-3.0, 0.0, 3.0)
    for loud in extremes:
        for rate in extremes:
            for pitch in extremes:
                for voicing in extremes:
                    direction = pm.infer_direction({
                        "loudness_db": loud, "rate_hz": rate, "pitch_st": pitch,
                        "pitch_spread_st": pitch, "voiced_ratio": voicing,
                    })
                    parsed = director._heuristic_parse(direction.instruct_prompt())
                    assert parsed.tokens == direction.tokens
                    for dim, values in direction.tokens.items():
                        assert set(values) <= set(director.TAXONOMY[dim])


# ── Whole-job mirroring ────────────────────────────────────────────────────

def _job(lines):
    feats = {sid: pm.extract_features(audio, SR) for sid, (_, audio) in lines.items()}
    speakers = {sid: speaker for sid, (speaker, _) in lines.items()}
    return pm.mirror(feats, speakers)


def test_outlier_lines_get_directions_and_typical_lines_do_not():
    lines = {str(i): ("A", _line(amp=0.1, seed=i)) for i in range(5)}
    lines["shout"] = ("A", _line(amp=0.4, f0=170.0, syllables_hz=7.0, vibrato=0.1))
    lines["whisper"] = ("A", _line(amp=0.02, whisper=True))
    out = _job(lines)
    assert out["shout"].direction == "urgent, quick"
    assert out["whisper"].direction == "whispered"
    assert all(out[str(i)].direction == "" for i in range(5))
    assert all(result.measured for result in out.values())


def test_directions_are_relative_to_each_speaker():
    """A deep, quiet speaker's ordinary lines are not "calm" just because a
    louder, higher speaker shares the video."""
    lines = {f"a{i}": ("A", _line(f0=95.0, amp=0.05)) for i in range(4)}
    lines.update({f"b{i}": ("B", _line(f0=220.0, amp=0.2)) for i in range(4)})
    out = _job(lines)
    assert all(result.direction == "" for result in out.values())


def test_second_voice_under_one_label_is_not_energetic():
    """Diarization off: every line is "Speaker 1", but two actors speak."""
    lines = {f"low{i}": ("Speaker 1", _line(f0=115.0)) for i in range(8)}
    lines.update({f"high{i}": ("Speaker 1", _line(f0=205.0)) for i in range(3)})
    out = _job(lines)
    assert all(result.direction == "" for result in out.values())
    assert "pitch_st" not in out["high0"].z


def test_lines_without_speaker_do_not_share_a_pitch_baseline():
    lines = {f"low{i}": ("", _line(f0=115.0)) for i in range(4)}
    lines.update({f"high{i}": ("", _line(f0=140.0)) for i in range(3)})
    out = _job(lines)
    assert all("pitch_st" not in result.z for result in out.values())
    assert all(result.direction == "" for result in out.values())


def test_long_line_is_measured_in_bounded_blocks():
    feat = pm.extract_features(_line(dur_s=60.0, sr=16000), 16000)
    assert feat.pitch_st == pytest.approx(12 * np.log2(1.2), abs=0.3)
    assert feat.rate_hz == pytest.approx(4.0, abs=0.6)


def test_mirror_file_reads_at_most_max_line_per_span(tmp_path, monkeypatch):
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.tile(_line(sr=16000), 45), 16000)
    seen = []
    monkeypatch.setattr(pm, "extract_features", lambda audio, sr: seen.append(len(audio) / sr))
    pm.mirror_file(str(path), [pm.SegmentSpan(id="long", start=0.0, end=90.0)])
    assert seen == [pytest.approx(pm.MAX_LINE_S)]


def test_mirror_file_budget_admits_real_overlap_and_refuses_repeats(tmp_path, monkeypatch):
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.tile(_line(sr=16000), 5), 16000)
    monkeypatch.setattr(pm, "extract_features", lambda audio, sr: None)
    talk_over = [pm.SegmentSpan(id=f"{who}{i}", start=2.0 * i, end=2.0 * i + 2.0)
                 for who in "ab" for i in range(5)]
    assert len(pm.mirror_file(str(path), talk_over)) == 10
    repeats = [pm.SegmentSpan(id=str(i), start=0.0, end=10.0) for i in range(20)]
    with pytest.raises(pm.AnalysisBudgetExceeded):
        pm.mirror_file(str(path), repeats)


def test_speaker_with_few_lines_uses_pooled_baseline_without_pitch():
    lines = {f"a{i}": ("A", _line(f0=100.0)) for i in range(3)}
    lines["b0"] = ("B", _line(f0=250.0))
    out = _job(lines)
    assert "pitch_st" not in out["b0"].z
    assert out["b0"].direction == ""


def test_too_few_measured_lines_yield_no_directions():
    out = _job({"0": ("A", _line(amp=0.4)), "1": ("A", _line(amp=0.05))})
    assert all(not result.measured and result.direction == "" for result in out.values())


def test_unmeasurable_line_is_reported_unmeasured():
    lines = {str(i): ("A", _line()) for i in range(3)}
    lines["silent"] = ("A", np.zeros(SR, dtype=np.float32))
    out = _job(lines)
    assert out["silent"].measured is False and out["silent"].direction == ""


def test_mirror_file_reads_spans_in_order(tmp_path):
    parts = [_line(amp=0.1)] * 4 + [_line(amp=0.4, f0=170.0, syllables_hz=7.0, vibrato=0.1)]
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.concatenate(parts), SR)
    spans = [pm.SegmentSpan(id=f"s{i}", start=2.0 * i, end=2.0 * i + 2.0, speaker_id="A") for i in range(5)]
    spans.append(pm.SegmentSpan(id="past-end", start=30.0, end=31.0, speaker_id="A"))
    results = pm.mirror_file(str(path), list(reversed(spans)))
    assert [r.id for r in results] == [s.id for s in reversed(spans)]
    by_id = {r.id: r for r in results}
    assert by_id["s4"].direction == "urgent, quick"
    assert by_id["past-end"].measured is False
