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


@pytest.mark.parametrize('subtype', ['PCM_16', 'PCM_24', 'FLOAT', 'DOUBLE'])
@pytest.mark.parametrize('channels', [1, 2])
def test_partial_final_uncompressed_frame_is_not_playable(tmp_path, subtype, channels):
    from core.audio_validation import is_playable_wav

    path = tmp_path / 'partial-frame.wav'
    sf.write(path, np.full((10, channels), 0.1), 8000, subtype=subtype)
    assert is_playable_wav(path)
    data = bytearray(path.read_bytes())
    offset = 12
    while data[offset:offset + 4] != b'data':
        size = struct.unpack_from('<I', data, offset + 4)[0]
        offset += 8 + size + (size & 1)
    size = struct.unpack_from('<I', data, offset + 4)[0]
    # Keep the RIFF and data length declarations physically complete while
    # removing one byte from the final audio frame.
    del data[offset + 8 + size - 1]
    struct.pack_into('<I', data, offset + 4, size - 1)
    struct.pack_into('<I', data, 4, len(data) - 8)
    path.write_bytes(data)
    assert not is_playable_wav(path)


@pytest.mark.parametrize("legacy_wave", [False, True])
def test_partial_extensible_pcm_frame_is_not_playable(tmp_path, monkeypatch, legacy_wave):
    from core import audio_validation
    from core.audio_validation import is_playable_wav
    import wave

    if legacy_wave:
        def unsupported_extensible(*args, **kwargs):
            raise wave.Error("Python 3.11 cannot decode WAVE_FORMAT_EXTENSIBLE")
        monkeypatch.setattr(audio_validation.wave, "open", unsupported_extensible)

    path = tmp_path / 'extensible.wav'
    pcm_guid = bytes.fromhex('0100000000001000800000aa00389b71')
    fmt = struct.pack('<HHIIHHHHI', 0xFFFE, 1, 8000, 16000, 2, 16, 22, 16, 0) + pcm_guid
    def write(payload):
        chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(payload)) + payload
        path.write_bytes(b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks)
    write(b'\x00' * 4)
    assert sf.info(path).format == 'WAVEX'
    assert is_playable_wav(path)
    write(b'\x00' * 3)
    assert not is_playable_wav(path)
