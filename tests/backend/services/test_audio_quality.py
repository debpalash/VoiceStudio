"""#2375: on-demand, read-only audio-quality warnings (signal checks only)."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

SR = 16000


def _tone(seconds: float, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _write(path, data, sr=SR, subtype="FLOAT"):
    sf.write(str(path), data, sr, subtype=subtype)
    return path


def analyze_audio(*args, **kwargs):
    # Resolved per call so a test that swaps ``services.audio_quality`` in
    # ``sys.modules`` cannot leave this file holding a stale function.
    from services.audio_quality import analyze_audio as current

    return current(*args, **kwargs)


def _kinds(report):
    return [(w.kind, w.start, w.end) for w in report.warnings]


def test_clean_speech_like_audio_has_no_warnings(tmp_path):
    report = analyze_audio(_write(tmp_path / "ok.wav", _tone(3)))
    assert report.warnings == [] and not report.truncated
    assert report.duration == pytest.approx(3.0)


def test_timestamped_silence_is_reported_only_from_one_second(tmp_path):
    audio = np.concatenate([_tone(1), np.zeros(int(1.5 * SR), np.float32), _tone(1)])
    report = analyze_audio(_write(tmp_path / "gap.wav", audio))
    assert _kinds(report) == [("silence", 1.0, 2.5)]
    short = np.concatenate([_tone(1), np.zeros(int(0.5 * SR), np.float32), _tone(1)])
    assert analyze_audio(_write(tmp_path / "short.wav", short)).warnings == []


def test_fully_silent_and_empty_files_are_flagged(tmp_path):
    silent = analyze_audio(_write(tmp_path / "silent.wav", np.zeros(SR, np.float32)))
    assert [w.kind for w in silent.warnings] == ["silence"]
    empty = analyze_audio(_write(tmp_path / "empty.wav", np.zeros(0, np.float32)))
    assert [w.kind for w in empty.warnings] == ["empty"]


def test_clipping_is_flagged(tmp_path):
    audio = _tone(2)
    audio[SR : SR + 400] = 1.0
    report = analyze_audio(_write(tmp_path / "clip.wav", audio))
    assert "clipping" in {w.kind for w in report.warnings}


def test_sustained_quiet_and_loud_stretches_are_relative_to_the_median(tmp_path):
    audio = np.concatenate([_tone(2), _tone(1, 0.01), _tone(2)])
    quiet = analyze_audio(_write(tmp_path / "quiet.wav", audio))
    assert ("quiet", 2.0, 3.0) in _kinds(quiet)
    loud = analyze_audio(_write(tmp_path / "loud.wav", np.concatenate([_tone(2, 0.05), _tone(1, 0.6), _tone(2, 0.05)])))
    assert ("loud", 2.0, 3.0) in _kinds(loud)


def test_invalid_samples_are_flagged_without_modifying_the_file(tmp_path):
    audio = _tone(2)
    audio[SR] = np.nan
    path = _write(tmp_path / "nan.wav", audio)
    before = path.read_bytes()
    report = analyze_audio(path)
    assert "invalid" in {w.kind for w in report.warnings}
    assert path.read_bytes() == before


def test_scan_is_bounded_in_time_and_warning_count(tmp_path):
    path = _write(tmp_path / "long.wav", _tone(10))
    report = analyze_audio(path, max_seconds=4)
    assert report.truncated and report.analyzed_seconds == pytest.approx(4.0, abs=0.11)
    noisy = np.tile(np.concatenate([_tone(1), np.zeros(int(1.5 * SR), np.float32)]), 120)
    capped = analyze_audio(_write(tmp_path / "many.wav", noisy))
    assert len(capped.warnings) == 100 and capped.truncated


def test_louder_channel_is_not_cancelled_by_a_phase_inverted_pair(tmp_path):
    tone = _tone(2)
    stereo = np.stack([tone, -tone], axis=1)
    assert analyze_audio(_write(tmp_path / "phase.wav", stereo)).warnings == []


def test_route_only_reads_generated_wavs_inside_the_outputs_dir(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from api.routers import generation

    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(tmp_path))
    _write(tmp_path / "abcdef01.wav", _tone(2))
    report = generation.generated_audio_quality("abcdef01")
    assert report.warnings == []
    for bad in ("../abcdef01", "abcdef02", "ABCDEF01"):
        with pytest.raises(HTTPException) as err:
            generation.generated_audio_quality(bad)
        assert err.value.status_code == 404
