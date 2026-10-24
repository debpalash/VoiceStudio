"""RIFF header inspection must not allocate a claimed fmt chunk wholesale."""
import io
import struct

import pytest



class BoundedReads(io.BytesIO):
    def read(self, size=-1):
        assert 0 <= size <= 16, f"unbounded header read: {size}"
        return super().read(size)


def _header(fmt_size=16, body=None):
    if body is None:
        body = struct.pack("<HHIIHH", 1, 2, 24000, 96000, 4, 16)
        body += bytes(max(0, fmt_size - 16))
    return (b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt "
            + struct.pack("<I", fmt_size) + body + bytes(fmt_size % 2)
            + b"data" + struct.pack("<I", 48))


@pytest.mark.parametrize("fmt_size", [16, 17, 40, 4096])
def test_format_extensions_are_skipped_with_bounded_reads(fmt_size):
    from services.audio_io import _riff_wav_header
    stream = BoundedReads(b"prefix" + _header(fmt_size))
    stream.seek(6)
    info = _riff_wav_header(stream)
    assert (info.sample_rate, info.num_channels, info.bits_per_sample, info.num_frames) == (24000, 2, 16, 12)
    assert stream.tell() == 6


def test_audio_info_uses_the_bounded_header_path():
    from services.audio_io import audio_info
    stream = BoundedReads(_header(4096))
    assert audio_info(stream).num_frames == 12
    assert stream.tell() == 0


def test_huge_claim_on_a_short_file_does_not_request_huge_allocation():
    from services.audio_io import _riff_wav_header
    body = struct.pack("<HHIIHH", 1, 2, 24000, 96000, 4, 16)
    stream = BoundedReads(_header(2**32 - 1, body))
    assert _riff_wav_header(stream) is None
    assert stream.tell() == 0


@pytest.mark.parametrize("fmt_size,body", [(0, b""), (8, bytes(8)), (16, bytes(4))])
def test_short_format_chunks_remain_unreadable_and_restore_position(fmt_size, body):
    from services.audio_io import _riff_wav_header
    stream = BoundedReads(_header(fmt_size, body))
    assert _riff_wav_header(stream) is None
    assert stream.tell() == 0
