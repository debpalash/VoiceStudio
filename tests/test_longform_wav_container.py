"""Cached chapters must remain readable as complete PCM WAV containers."""

import struct
import wave


def test_cache_checks_the_riff_extent_and_complete_sample_frames(tmp_path):
    from services.longform_render import wav_is_complete

    path = tmp_path / "chapter.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 8_000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 4)
    original = path.read_bytes()
    assert wav_is_complete(str(path))

    wrong_riff_size = bytearray(original)
    struct.pack_into("<I", wrong_riff_size, 4, len(original))
    path.write_bytes(wrong_riff_size)
    assert not wav_is_complete(str(path))

    incomplete_frame = bytearray(original)
    struct.pack_into("<I", incomplete_frame, 40, 7)
    path.write_bytes(incomplete_frame)
    assert not wav_is_complete(str(path))
