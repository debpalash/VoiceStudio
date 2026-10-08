"""Interrupted IEEE-float WAV references must not be reported playable."""
import struct
import numpy as np
import soundfile as sf
import pytest

@pytest.mark.parametrize("subtype", ["FLOAT", "DOUBLE", "PCM_16", "PCM_24", "ULAW", "ALAW"])
def test_declared_wav_container_requires_complete_payload(tmp_path, subtype):
    from core.audio_validation import is_playable_wav
    path = tmp_path / "reference.wav"
    sf.write(path, np.full((100, 2), 0.1), 8000, subtype=subtype)
    assert is_playable_wav(path)
    data = path.read_bytes()
    path.write_bytes(data[:-40])
    assert not is_playable_wav(path)

@pytest.mark.parametrize("subtype", ["FLOAT", "DOUBLE"])
def test_data_chunk_length_is_checked_even_if_riff_size_is_repaired(tmp_path, subtype):
    from core.audio_validation import is_playable_wav
    path = tmp_path / "reference.wav"
    sf.write(path, np.full(100, 0.1), 8000, subtype=subtype)
    data = bytearray(path.read_bytes()[:-40])
    struct.pack_into("<I", data, 4, len(data) - 8)
    path.write_bytes(data)
    assert not is_playable_wav(path)

def test_valid_odd_sized_pcm_wav_is_still_playable(tmp_path):
    from core.audio_validation import is_playable_wav
    import wave
    path = tmp_path / "odd.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 1, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x80" * 3)
    assert is_playable_wav(path)

def test_complete_big_endian_float_wav_remains_playable(tmp_path):
    from core.audio_validation import is_playable_wav
    path = tmp_path / "big-endian.wav"
    sf.write(path, np.full(100, 0.1), 8000, subtype="FLOAT", endian="BIG")
    assert path.read_bytes()[:4] == b"RIFX"
    assert is_playable_wav(path)
