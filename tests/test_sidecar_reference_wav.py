"""Sidecar engines receive a reference clip the stdlib ``wave`` module can read (#3688).

PocketTTS reads ``.wav`` references with ``wave`` (integer PCM, format tag 1)
and needs ``soundfile`` for anything else, which its venv does not carry. The
parent backend therefore converts any other reference before it reaches a
sidecar, and leaves plain PCM WAVs untouched.
"""
import os
import struct
import subprocess
import wave
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

RATE = 24000


@pytest.fixture
def rw():
    # Resolved per test: the suite purges and re-imports app modules.
    from services import reference_wav

    return reference_wav


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch, rw):
    directory = tmp_path / "cache"
    monkeypatch.setattr(rw, "_cache_dir", lambda: str(directory))
    return directory


def _tone(seconds=0.5, channels=1):
    signal = (0.3 * np.sin(np.arange(int(RATE * seconds)) * 0.05)).astype(np.float32)
    return np.column_stack([signal] * channels) if channels > 1 else signal


def _format_tag(path):
    with open(path, "rb") as handle:
        data = handle.read()
    offset = data.index(b"fmt ") + 8
    return struct.unpack("<H", data[offset:offset + 2])[0]


def _wave_frames(path):
    """Read ``path`` exactly the way pocket-tts does; raises wave.Error otherwise."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnchannels(), handle.getsampwidth(), handle.getframerate(), handle.getnframes()


def test_pcm16_wav_passes_through_unchanged(tmp_path, cache, rw):
    path = tmp_path / "ref.WAV"
    sf.write(path, _tone(channels=2), RATE, format="WAV", subtype="PCM_16")
    assert rw.is_plain_pcm_wav(str(path))
    assert rw.sidecar_reference_wav(str(path)) == str(path)
    assert not cache.exists()


@pytest.mark.parametrize("subtype", ["PCM_U8", "PCM_24", "PCM_32"])
def test_other_pcm_widths_are_converted(tmp_path, subtype, rw):
    # PocketTTS reads every .wav sample as int16, whatever the header says.
    path = tmp_path / "ref.wav"
    sf.write(path, _tone(), RATE, format="WAV", subtype=subtype)
    assert not rw.is_plain_pcm_wav(str(path))
    converted = rw.sidecar_reference_wav(str(path))
    assert converted != str(path)
    assert _wave_frames(converted)[:3] == (1, 2, RATE)


def test_pcm16_wav_with_another_suffix_is_converted(tmp_path, rw):
    # PocketTTS picks its reader by suffix; non-.wav needs soundfile.
    path = tmp_path / "ref.audio"
    sf.write(path, _tone(), RATE, format="WAV", subtype="PCM_16")
    converted = rw.sidecar_reference_wav(str(path))
    assert converted.endswith(".wav") and converted != str(path)


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("extensible", {"format": "WAVEX", "subtype": "PCM_16"}),  # macOS afconvert LEI16
        ("float", {"format": "WAV", "subtype": "FLOAT"}),
        ("flac", {"format": "FLAC", "subtype": "PCM_16"}),
    ],
)
def test_other_layouts_become_plain_pcm16_mono(tmp_path, label, kwargs, rw):
    path = tmp_path / f"ref-{label}.audio"
    sf.write(path, _tone(channels=2), RATE, **kwargs)
    if label == "extensible":
        assert _format_tag(path) == 0xFFFE  # stdlib wave before 3.12: "unknown format: 65534"
    converted = rw.sidecar_reference_wav(str(path))
    assert converted != str(path)
    assert rw.is_plain_pcm_wav(converted)
    channels, width, rate, frames = _wave_frames(converted)
    assert (channels, width, rate) == (1, 2, RATE)
    assert abs(frames - RATE * 0.5) <= 1


def test_m4a_reference_is_decoded_with_ffmpeg(tmp_path, rw):
    from services.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    assert ffmpeg, "the maintained runtime bundles ffmpeg"
    source = tmp_path / "memo.wav"
    sf.write(source, _tone(seconds=1.0), RATE)
    memo = tmp_path / "memo.m4a"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), str(memo)],
                   check=True, capture_output=True)
    channels, width, rate, frames = _wave_frames(rw.sidecar_reference_wav(str(memo)))
    assert (channels, width, rate) == (1, 2, RATE)
    assert frames >= RATE * 0.9


def test_converted_path_is_stable_until_the_source_changes(tmp_path, rw):
    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    first = rw.sidecar_reference_wav(str(path))
    assert rw.sidecar_reference_wav(str(path)) == first  # voice-state caches keyed by path keep hitting
    sf.write(path, _tone(seconds=0.25), RATE, format="FLAC")
    os.utime(path, ns=(os.stat(first).st_mtime_ns + 10**9,) * 2)
    second = rw.sidecar_reference_wav(str(path))
    assert second != first
    assert _wave_frames(second)[3] == pytest.approx(RATE * 0.25, abs=1)
    assert not os.path.exists(first)  # the superseded take does not linger


def test_reuse_leaves_the_copy_mtime_alone(tmp_path, rw):
    # PocketTTS keys its prepared voice on the file mtime; touching the copy
    # on every reuse would rebuild the voice for each chunk.
    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    converted = rw.sidecar_reference_wav(str(path))
    before = os.stat(converted).st_mtime_ns
    os.utime(converted, ns=(before - 10**9, before - 10**9))
    rw.sidecar_reference_wav(str(path))
    assert os.stat(converted).st_mtime_ns == before - 10**9


def test_cache_is_bounded(tmp_path, cache, monkeypatch, rw):
    monkeypatch.setattr(rw, "MAX_CACHED_REFERENCES", 3)
    for index in range(5):
        path = tmp_path / f"ref-{index}.flac"
        sf.write(path, _tone(seconds=0.1), RATE, format="FLAC")
        latest = rw.sidecar_reference_wav(str(path))
    copies = {p.name for p in cache.glob("*.wav")}
    assert len(copies) == 3
    assert os.path.basename(latest) in copies
    assert {p.stem for p in cache.glob("*.source")} == {name.split("-")[0] for name in copies}
    assert not [p for p in cache.iterdir() if p.suffix not in {".wav", ".source"}]


def test_eviction_skips_the_most_recently_used_copy(tmp_path, cache, monkeypatch, rw):
    monkeypatch.setattr(rw, "MAX_CACHED_REFERENCES", 2)
    paths = []
    for index in range(3):
        paths.append(tmp_path / f"ref-{index}.flac")
        sf.write(paths[-1], _tone(seconds=0.1), RATE, format="FLAC")
    first = rw.sidecar_reference_wav(str(paths[0]))
    rw.sidecar_reference_wav(str(paths[1]))
    assert rw.sidecar_reference_wav(str(paths[0])) == first  # reused: now the newest
    rw.sidecar_reference_wav(str(paths[2]))
    assert os.path.exists(first)


def test_a_held_copy_is_never_evicted(tmp_path, cache, monkeypatch, rw):
    # A generation holds its copy from conversion until the sidecar replies;
    # other generations converting meanwhile must not delete it.
    monkeypatch.setattr(rw, "MAX_CACHED_REFERENCES", 2)
    held_source = tmp_path / "held.flac"
    sf.write(held_source, _tone(seconds=0.1), RATE, format="FLAC")
    with rw.voice_leases.VoiceFileLease() as lease:
        held = rw.sidecar_reference_wav(str(held_source), lease=lease)
        for index in range(4):
            path = tmp_path / f"ref-{index}.flac"
            sf.write(path, _tone(seconds=0.1), RATE, format="FLAC")
            rw.sidecar_reference_wav(str(path))
        assert os.path.exists(held)
    path = tmp_path / "after.flac"
    sf.write(path, _tone(seconds=0.1), RATE, format="FLAC")
    rw.sidecar_reference_wav(str(path))
    assert not os.path.exists(held)  # released: the bound applies again


def test_deleting_the_source_removes_its_copy(tmp_path, cache, rw):
    gone, kept = tmp_path / "gone.flac", tmp_path / "kept.flac"
    for path in (gone, kept):
        sf.write(path, _tone(seconds=0.1), RATE, format="FLAC")
    gone_copy, kept_copy = rw.sidecar_reference_wav(str(gone)), rw.sidecar_reference_wav(str(kept))
    gone.unlink()
    rw.prune_reference_copies()
    assert not os.path.exists(gone_copy)
    assert os.path.exists(kept_copy)
    assert len(list(cache.glob("*.source"))) == 1


def test_prune_without_a_cache_directory_is_a_no_op(cache, rw):
    rw.prune_reference_copies()
    assert not cache.exists()


def test_undecodable_reference_falls_back_to_the_original_path(tmp_path, cache, caplog, rw):
    path = tmp_path / "broken.m4a"
    path.write_bytes(b"not audio at all" * 64)
    assert rw.sidecar_reference_wav(str(path)) == str(path)
    assert "Could not convert reference broken.m4a" in caplog.text
    assert not [p for p in cache.glob("*") if p.is_file()]


def test_conversion_warning_scrubs_home_paths(tmp_path, monkeypatch, caplog, rw):
    from services import audio_io

    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    home_path = os.path.join(os.path.expanduser("~"), "voices", "ref.flac")

    def fail(_source):
        raise RuntimeError(f"cannot read {home_path}")

    monkeypatch.setattr(audio_io, "load_audio", fail)
    assert rw.sidecar_reference_wav(str(path)) == str(path)
    assert "Could not convert reference ref.flac" in caplog.text
    assert home_path not in caplog.text


@pytest.mark.parametrize("value", ["", "/no/such/reference.m4a", "https://example.com/ref.mp3"])
def test_values_that_are_not_local_files_are_left_to_the_sidecar(value, rw):
    assert rw.sidecar_reference_wav(value) == value


def test_subprocess_backend_sends_the_converted_reference(tmp_path, rw):
    from services.subprocess_backend import SubprocessBackend

    path = tmp_path / "ref.flac"
    sf.write(path, _tone(), RATE, format="FLAC")
    sent = {}

    def exchange(msg, deadline_text):
        sent.update(msg, held=rw.voice_leases.in_use(msg["ref_audio"]))

    backend = SimpleNamespace(_check_language=lambda _language: None, _exchange=exchange)
    SubprocessBackend.generate(backend, "hello", ref_audio=str(path), language="en")
    assert sent["held"]  # protected from eviction while the sidecar reads it
    assert not rw.voice_leases.in_use(sent["ref_audio"])
    assert sent["ref_audio"] != str(path)
    assert _wave_frames(sent["ref_audio"])[:3] == (1, 2, RATE)
    assert sent["language"] == "en"
