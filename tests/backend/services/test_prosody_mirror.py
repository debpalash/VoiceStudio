"""Prosody mirror: measure source delivery and map it onto director words."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from services import director
from services import prosody_mirror as pm
from services.prosody_mirror import (
    AnalysisBudgetExceeded,
    Baseline,
    MirrorResult,
    ProsodyFeatures,
    SegmentSpan,
    build_baseline,
    extract_features,
    infer_direction,
    mirror,
    mirror_file,
    to_mono_analysis_rate,
)


def tone(f0, dur=1.5, sr=16000, am=4.0, amp=0.3, glide_st=0.0):
    t = np.arange(int(dur * sr)) / sr
    freq = f0 * 2 ** (glide_st * t / dur / 12) if glide_st else f0 * np.ones_like(t)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    wave = sum(np.sin(k * phase) / k for k in range(1, 6))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * am * t)
    return (amp * wave * envelope / 2).astype(np.float32)


def feat(**kw):
    base = dict(loudness_db=-20.0, rate_hz=4.0, voiced_ratio=0.9, active_s=2.0,
                pitch_st=0.0, pitch_spread_st=3.0)
    base.update(kw)
    return ProsodyFeatures(**base)


# ---- measurement ---------------------------------------------------------

@pytest.mark.parametrize("f0", [70, 100, 150, 220, 300, 400])
def test_pitch_within_half_a_semitone(f0):
    got = extract_features(tone(f0), 16000)
    assert got is not None and got.pitch_st is not None
    assert abs(got.pitch_st - 12 * np.log2(f0 / 100)) < 0.5


def test_louder_input_is_louder():
    quiet = extract_features(tone(150, amp=0.05), 16000)
    loud = extract_features(tone(150, amp=0.4), 16000)
    assert loud.loudness_db > quiet.loudness_db + 10


def test_faster_modulation_gives_higher_rate():
    slow = extract_features(tone(150, am=2.0), 16000)
    fast = extract_features(tone(150, am=7.0), 16000)
    assert fast.rate_hz > slow.rate_hz + 2


def test_gliding_pitch_has_larger_spread():
    steady = extract_features(tone(120), 16000)
    glide = extract_features(tone(120, glide_st=12), 16000)
    assert glide.pitch_spread_st > steady.pitch_spread_st + 3


def test_noise_has_no_pitch_and_low_voicing():
    rng = np.random.default_rng(1)
    got = extract_features((0.1 * rng.standard_normal(24000)).astype(np.float32), 16000)
    assert got is not None
    assert got.pitch_st is None and got.pitch_spread_st is None
    assert got.voiced_ratio < 0.1


def test_silence_and_short_clips_are_unmeasured():
    assert extract_features(np.zeros(16000, dtype=np.float32), 16000) is None
    assert extract_features(tone(150)[:300], 16000) is None
    assert extract_features(tone(150, dur=0.2), 16000) is None


@pytest.mark.parametrize("sr", [22050, 44100, 48000])
def test_resampling_keeps_measurements(sr):
    ref = extract_features(tone(150), 16000)
    got = extract_features(tone(150, sr=sr), sr)
    assert abs(got.pitch_st - ref.pitch_st) < 0.5
    assert abs(got.loudness_db - ref.loudness_db) < 1.0
    assert abs(got.rate_hz - ref.rate_hz) < 1.0


def test_duplicated_channels_do_not_change_result():
    mono = tone(150)
    ref = extract_features(mono, 16000)
    stereo = extract_features(np.stack([mono, mono], axis=1), 16000)
    assert stereo.loudness_db == pytest.approx(ref.loudness_db, abs=0.1)
    assert stereo.pitch_st == pytest.approx(ref.pitch_st, abs=0.1)


def test_out_of_phase_stereo_is_not_silence():
    mono = tone(150)
    got = extract_features(np.stack([mono, -mono], axis=1), 16000)
    assert got is not None and got.pitch_st is not None


def test_to_mono_passthrough_cases():
    empty = np.zeros(0, dtype=np.float32)
    assert to_mono_analysis_rate(empty, 44100).size == 0
    x = tone(150)
    assert np.array_equal(to_mono_analysis_rate(x, 16000), x)
    out = to_mono_analysis_rate(np.zeros((48000, 2), dtype=np.float32), 48000)
    assert out.ndim == 1 and abs(out.size - 16000) <= 1


def test_long_line_is_processed_in_blocks():
    # 90 s: more frames than one block; must still produce sane values.
    got = extract_features(tone(150, dur=90.0, am=3.0), 16000)
    assert got is not None
    assert got.active_s > 80
    assert got.pitch_st == pytest.approx(7.02, abs=0.5)


# ---- baselines -----------------------------------------------------------

def test_baseline_median_and_floors():
    samples = [feat(loudness_db=-20.0 + i * 0.01, rate_hz=4.0, pitch_st=1.0, voiced_ratio=0.9)
               for i in range(5)]
    base = build_baseline(samples, pitch_comparable=True)
    assert base.centre["loudness_db"] == pytest.approx(-19.98)
    assert base.spread == {
        "pitch_st": 1.0, "pitch_spread_st": 1.0, "loudness_db": 2.0,
        "rate_hz": 0.5, "voiced_ratio": 0.08,
    }


def test_baseline_uses_scaled_mad_above_floor():
    samples = [feat(loudness_db=v) for v in (-30, -25, -20, -15, -10)]
    base = build_baseline(samples, pitch_comparable=True)
    assert base.spread["loudness_db"] == pytest.approx(5 * 1.4826)


def test_baseline_skips_features_without_values():
    samples = [feat(pitch_st=None, pitch_spread_st=None) for _ in range(3)]
    base = build_baseline(samples, pitch_comparable=True)
    assert "pitch_st" not in base.centre and "pitch_spread_st" not in base.centre


def test_z_skips_pitch_when_not_comparable():
    base = build_baseline([feat() for _ in range(3)], pitch_comparable=False)
    z = base.z(feat(pitch_st=5.0))
    assert "pitch_st" not in z and "pitch_spread_st" not in z
    assert "loudness_db" in z


def test_z_drops_pitch_on_second_voice():
    base = build_baseline([feat() for _ in range(3)], pitch_comparable=True)
    z = base.z(feat(pitch_st=12.0))
    assert "pitch_st" not in z and "pitch_spread_st" not in z
    assert base.z(feat(pitch_st=3.0))["pitch_st"] == pytest.approx(3.0)


# ---- direction table -----------------------------------------------------

def words(z):
    return infer_direction(z).tokens


def test_whispered_only():
    assert words({"voiced_ratio": -2.5, "loudness_db": -1.5, "rate_hz": 2.0}) == {
        "intimacy": ["whispered"]}


def test_urgent_needs_loud_fast_and_pitch_if_present():
    assert words({"loudness_db": 1.2, "rate_hz": 1.2}) == {
        "energy": ["urgent"], "pace": ["quick"]}
    assert "energy" not in words({"loudness_db": 1.2, "rate_hz": 1.2, "pitch_st": 0.0})
    assert words({"loudness_db": 1.2, "rate_hz": 1.2, "pitch_st": 0.6})["energy"] == ["urgent"]


def test_energetic_and_calm():
    assert words({"loudness_db": 1.0, "pitch_st": 1.0, "pitch_spread_st": 1.0})["energy"] == [
        "energetic"]
    assert words({"loudness_db": -1.0})["energy"] == ["calm"]
    assert words({"loudness_db": -0.4}) == {}


def test_pitch_alone_gives_no_energy_word():
    assert words({"pitch_st": 3.0, "pitch_spread_st": 3.0}) == {}


def test_announcing_and_pace():
    got = words({"loudness_db": 1.6, "rate_hz": -1.2, "pitch_st": 1.0})
    assert got["intimacy"] == ["announcing"] and got["pace"] == ["slow"]
    assert words({"rate_hz": 1.0}) == {"pace": ["quick"]}
    assert words({"rate_hz": -1.0}) == {"pace": ["slow"]}
    assert words({"rate_hz": 0.9}) == {}


def test_direction_metadata_and_never_emotion():
    d = infer_direction({"loudness_db": 2.0, "rate_hz": 2.0, "pitch_st": 2.0})
    assert d.source == "prosody-mirror" and d.method == "prosody"
    assert "emotion" not in d.tokens


def test_every_word_round_trips_through_director():
    zs = [
        {"voiced_ratio": -3, "loudness_db": -2},
        {"loudness_db": 2, "rate_hz": 2},
        {"loudness_db": 1, "pitch_st": 1, "pitch_spread_st": 1},
        {"loudness_db": -2, "pitch_st": -1},
        {"loudness_db": 2, "rate_hz": -2},
    ]
    for z in zs:
        d = infer_direction(z)
        again = director._heuristic_parse(d.instruct_prompt())
        for dim, vals in d.tokens.items():
            for v in vals:
                assert v in again.tokens.get(dim, [])


# ---- mirror --------------------------------------------------------------

def _lines(n, **kw):
    return {f"s{i}": feat(**kw) for i in range(n)}


def test_fewer_than_three_measured_lines_gives_nothing():
    f = {"a": feat(), "b": feat(), "c": None}
    out = mirror(f, {})
    assert set(out) == {"a", "b", "c"}
    assert all(r.direction == "" and not r.measured for r in out.values())


def test_unmeasured_lines_stay_empty():
    f = _lines(5)
    f["x"] = None
    out = mirror(f, {})
    assert out["x"].measured is False and out["x"].direction == ""
    assert out["s0"].measured is True


def test_outlier_line_gets_direction_and_norm_does_not():
    f = _lines(6)
    f["loud"] = feat(loudness_db=-5.0, rate_hz=7.0, pitch_st=None, pitch_spread_st=None)
    out = mirror(f, {})
    assert out["s0"].direction == ""
    assert "urgent" in out["loud"].direction and "quick" in out["loud"].direction
    assert out["loud"].to_dict()["z"]["loudness_db"] > 1


def test_speaker_relative_baseline():
    f = {f"q{i}": feat(loudness_db=-40.0 + i * 0.1) for i in range(6)}
    f.update({f"l{i}": feat(loudness_db=-10.0 + i * 0.1) for i in range(3)})
    spk = {k: ("quiet" if k.startswith("q") else "loud") for k in f}
    # Each speaker is judged against their own norm: nobody stands out.
    assert all(r.direction == "" for r in mirror(f, spk).values())
    # Pooled together the loud speaker is far above the typical line.
    pooled = mirror(f, {k: "" for k in f})
    assert pooled["l0"].direction != ""


def test_pooled_baseline_without_pitch_for_several_speakers():
    f = {f"s{i}": feat(pitch_st=float(i)) for i in range(4)}
    spk = {"s0": "a", "s1": "a", "s2": "b", "s3": "b"}
    out = mirror(f, spk)
    assert all("pitch_st" not in r.z for r in out.values())
    single = mirror(f, {k: "a" for k in f})
    assert all("pitch_st" in r.z for r in single.values())
    with_unknown = mirror(f, {"s0": "a", "s1": "a", "s2": "a", "s3": ""})
    # The unknown line falls back to the pooled baseline, which has no pitch.
    assert "pitch_st" not in with_unknown["s3"].z
    assert "pitch_st" in with_unknown["s0"].z


def test_result_to_dict_rounds_z():
    d = MirrorResult(id="a", z={"loudness_db": 1.23456}, measured=True).to_dict()
    assert d == {"id": "a", "direction": "", "tokens": {}, "z": {"loudness_db": 1.23},
                 "measured": True}


# ---- files ---------------------------------------------------------------

def _write(path, parts, sr=16000):
    sf.write(str(path), np.concatenate(parts), sr)


def test_mirror_file_returns_results_in_input_order(tmp_path):
    gap = np.zeros(8000, dtype=np.float32)
    path = tmp_path / "a.wav"
    _write(path, [tone(150), gap, tone(150, amp=0.02), gap, tone(150), gap, tone(150)])
    spans = [
        SegmentSpan("c", 3.4, 4.9), SegmentSpan("a", 0.0, 1.5, "x"),
        SegmentSpan("late", 100.0, 101.0), SegmentSpan("b", 2.0, 3.0, "x"),
    ]
    out = mirror_file(str(path), spans)
    assert [r.id for r in out] == ["c", "a", "late", "b"]
    assert out[2].measured is False


def test_mirror_file_reads_only_the_first_minute_of_a_span(tmp_path, monkeypatch):
    path = tmp_path / "long.wav"
    sf.write(str(path), tone(150, dur=130.0, am=3.0), 16000)
    sizes = []
    real = pm.extract_features

    def spy(audio, sr):
        sizes.append(len(audio))
        return real(audio, sr)

    monkeypatch.setattr(pm, "extract_features", spy)
    mirror_file(str(path), [SegmentSpan("a", 0.0, 125.0)])
    assert sizes == [int(60 * 16000)]


def test_mirror_file_budget(tmp_path):
    path = tmp_path / "short.wav"
    sf.write(str(path), tone(150, dur=2.0), 16000)
    spans = [SegmentSpan(f"s{i}", 0.0, 2.0) for i in range(40)]
    with pytest.raises(AnalysisBudgetExceeded) as err:
        mirror_file(str(path), spans)
    assert isinstance(err.value, ValueError)
    # within the budget (2 x 2 s + 60 s) is fine
    mirror_file(str(path), spans[:20])


def test_mirror_file_unreadable_raises_runtime_or_os_error(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises((OSError, RuntimeError)):
        mirror_file(str(bad), [SegmentSpan("a", 0, 1)])
