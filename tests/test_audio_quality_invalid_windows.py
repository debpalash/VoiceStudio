"""Corrupt samples are evidence of invalid audio, not silence or volume drift."""
import numpy as np
import pytest
import soundfile as sf


SR = 8000


def _report(tmp_path, samples):
    from services.audio_quality import analyze_audio
    path = tmp_path / "invalid.wav"
    sf.write(path, samples, SR, subtype="FLOAT")
    before = path.read_bytes()
    report = analyze_audio(path)
    assert path.read_bytes() == before
    return report


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_fully_invalid_audio_is_not_reported_as_silence(tmp_path, value):
    report = _report(tmp_path, np.full(SR * 2, value, dtype=np.float32))
    assert [(item.kind, item.start, item.end) for item in report.warnings] == [("invalid", 0, 2)]


def test_invalid_windows_do_not_shift_relative_volume_baseline(tmp_path):
    t = np.arange(SR * 6) / SR
    samples = (.1 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    samples[SR:5 * SR] *= 8
    samples[SR:5 * SR:SR // 10] = np.nan  # every louder window is corrupt
    report = _report(tmp_path, samples)
    assert [(item.kind, item.start, item.end) for item in report.warnings] == [("invalid", 1, 5)]


def test_valid_silence_next_to_invalid_audio_keeps_its_own_bounds(tmp_path):
    report = _report(tmp_path, np.concatenate([
        np.zeros(SR), np.full(SR, np.nan), np.zeros(SR),
    ]).astype(np.float32))
    assert [(item.kind, item.start, item.end) for item in report.warnings] == [
        ("silence", 0, 1), ("invalid", 1, 2), ("silence", 2, 3),
    ]
