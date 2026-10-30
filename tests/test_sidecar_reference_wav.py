"""Sidecar engines receive a reference clip the stdlib ``wave`` module can read (#3688).

PocketTTS reads ``.wav`` references with ``wave`` (integer PCM, format tag 1)
and needs ``soundfile`` for anything else, which its venv does not carry. The
parent backend therefore converts any other reference before it reaches a
sidecar, and leaves plain PCM WAVs untouched.
"""
import os
import subprocess
import wave
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from services import reference_wav
from services.reference_wav import is_plain_pcm_wav, sidecar_reference_wav

RATE = 24000


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    directory = tmp_path / "cache"
    monkeypatch.setattr(reference_wav, "_cache_dir", lambda: str(directory))
    return directory


def _tone(seconds=0.5, channels=1):
    signal = (0.3 * np.sin(np.arange(int(RATE * seconds)) * 0.05)).astype(np.float32)
    return np.column_stack([signal] * channels) if channels > 1 else signal


def _wave_frames(path):
    """Read ``path`` exactly the way pocket-tts does; raises wave.Error otherwise."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.getnframes()


def test_plain_pcm_wav_passes_through_unchanged(tmp_path, cache):
    path = tmp_path / "ref.wav"
    sf.write(path, _tone(channels=2), RATE, subtype="PCM_24")
    assert is_plain_pcm_wav(str(path))
    assert sidecar_reference_wav(str(path)) == str(path)
    assert not cache.exists()


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("extensible", {"format": "WAVEX", "subtype": "PCM_16"}),  # macOS afconvert LEI16
        ("float", {"format": "WAV", "subtype": "FLOAT"}),
        ("flac", {"format": "FLAC", "subtype": "PCM_16"}),
    ],
)
def test_other_layouts_become_plain_pcm16_mono(tmp_path, label, kwargs):
    path = tmp_path / f"ref-{label}.audio"
    sf.write(path, _tone(channels=2), RATE, **kwargs)
    if label == "extensible":
        with pytest.raises(wave.Error, match="unknown format: 65534"):
            _wave_frames(path)
    converted = sidecar_reference_wav(str(path))
    assert converted != str(path)
    assert is_plain_pcm_wav(converted)
    channels, width, rate, frames = _wave_frames(converted)
    assert (channels, width, rate) == (1, 2, RATE)
    assert abs(frames - RATE * 0.5) <= 1


def test_m4a_reference_is_decoded_with_ffmpeg(tmp_path):
    from services.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    assert ffmpeg, "the maintained runtime bundles ffmpeg"
    source = tmp_path / "memo.wav"
    sf.write(source, _tone(seconds=1.0), RATE)
    memo = tmp_path / "memo.m4a"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), str(memo)],
                   check=True, capture_output=True)
    channels, width, rate, frames = _wave_frames(sidecar_reference_wav(str(memo)))
    assert (channels, width, rate) == (1, 2, RATE)
    assert frames >= RATE * 0.9


def test_converted_path_is_stable_until_the_source_changes(tmp_path):
    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    first = sidecar_reference_wav(str(path))
    assert sidecar_reference_wav(str(path)) == first  # voice-state caches keyed by path keep hitting
    sf.write(path, _tone(seconds=0.25), RATE, format="FLAC")
    os.utime(path, ns=(os.stat(first).st_mtime_ns + 10**9,) * 2)
    second = sidecar_reference_wav(str(path))
    assert second != first
    assert _wave_frames(second)[3] == pytest.approx(RATE * 0.25, abs=1)


def test_cache_is_bounded(tmp_path, cache, monkeypatch):
    monkeypatch.setattr(reference_wav, "MAX_CACHED_REFERENCES", 3)
    for index in range(5):
        path = tmp_path / f"ref-{index}.flac"
        sf.write(path, _tone(seconds=0.1), RATE, format="FLAC")
        latest = sidecar_reference_wav(str(path))
    remaining = sorted(cache.iterdir())
    assert len(remaining) == 3
    assert os.path.basename(latest) in {p.name for p in remaining}
    assert not [p for p in remaining if p.suffix != ".wav"]


def test_undecodable_reference_falls_back_to_the_original_path(tmp_path, cache, caplog):
    path = tmp_path / "broken.m4a"
    path.write_bytes(b"not audio at all" * 64)
    assert sidecar_reference_wav(str(path)) == str(path)
    assert "Could not convert reference broken.m4a" in caplog.text
    assert not [p for p in cache.glob("*") if p.is_file()]


@pytest.mark.parametrize("value", ["", "/no/such/reference.m4a", "https://example.com/ref.mp3"])
def test_values_that_are_not_local_files_are_left_to_the_sidecar(value):
    assert sidecar_reference_wav(value) == value


def test_subprocess_backend_sends_the_converted_reference(tmp_path):
    from services.subprocess_backend import SubprocessBackend

    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    sent = {}
    backend = SimpleNamespace(
        _check_language=lambda _language: None,
        _exchange=lambda msg, deadline_text: sent.update(msg),
    )
    SubprocessBackend.generate(backend, "hello", ref_audio=str(path), language="en")
    assert sent["ref_audio"] != str(path)
    assert _wave_frames(sent["ref_audio"])[:3] == (1, 2, RATE)
    assert sent["language"] == "en"
