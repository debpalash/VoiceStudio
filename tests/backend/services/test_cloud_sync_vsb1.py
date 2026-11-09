"""VSB1 blob encryption (desktop-app-guide §9)."""
import os

import pytest

from services.cloud_sync import vsb1

KEY = bytes(range(32))
BLOB = "Bl9xC7vB5nM3qW1e"
SMALL = 16  # chunk size for fast tests


@pytest.mark.parametrize("size", [0, 1, SMALL, SMALL + 1, 3 * SMALL])
def test_round_trip_and_size(size):
    data = os.urandom(size)
    obj = vsb1.encrypt(KEY, BLOB, data, chunk_size=SMALL)
    assert obj[:4] == b"VSB1"
    assert len(obj) == vsb1.ciphertext_size(size, chunk_size=SMALL)
    assert vsb1.decrypt(KEY, BLOB, obj, chunk_size=SMALL) == data


def test_the_size_formula_at_the_real_chunk_size():
    assert vsb1.ciphertext_size(0) == 12 + 16
    assert vsb1.ciphertext_size(1843244) == 1843272
    assert vsb1.ciphertext_size(4194304) == 12 + 4194304 + 16
    assert vsb1.ciphertext_size(4194305) == 12 + 4194305 + 32


def test_real_chunk_size_round_trip():
    data = os.urandom(vsb1.CHUNK_SIZE + 5)
    assert vsb1.decrypt(KEY, BLOB, vsb1.encrypt(KEY, BLOB, data)) == data


def test_a_tampered_byte_fails_the_whole_file():
    obj = bytearray(vsb1.encrypt(KEY, BLOB, b"x" * 40, chunk_size=SMALL))
    obj[20] ^= 1
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(KEY, BLOB, bytes(obj), chunk_size=SMALL)


def test_truncation_fails_because_the_final_flag_is_missing():
    obj = vsb1.encrypt(KEY, BLOB, b"x" * 40, chunk_size=SMALL)
    cut = obj[: 12 + 2 * (SMALL + 16)]  # drop the last chunk
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(KEY, BLOB, cut, chunk_size=SMALL)


def test_swapped_chunks_fail():
    obj = vsb1.encrypt(KEY, BLOB, b"a" * SMALL + b"b" * SMALL + b"c", chunk_size=SMALL)
    step = SMALL + 16
    first, second = obj[12:12 + step], obj[12 + step:12 + 2 * step]
    swapped = obj[:12] + second + first + obj[12 + 2 * step:]
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(KEY, BLOB, swapped, chunk_size=SMALL)


@pytest.mark.parametrize("obj", [b"", b"VSB2" + bytes(30), b"VSB1" + bytes(5)])
def test_a_wrong_header_fails(obj):
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(KEY, BLOB, obj, chunk_size=SMALL)


def test_a_different_key_or_blob_id_fails():
    obj = vsb1.encrypt(KEY, BLOB, b"secret", chunk_size=SMALL)
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(bytes(32), BLOB, obj, chunk_size=SMALL)
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt(KEY, "Bl0000000000000x", obj, chunk_size=SMALL)


def test_range_decrypt_reads_only_the_chunks_it_needs():
    data = b"a" * SMALL + b"b" * SMALL + b"c" * 5
    obj = vsb1.encrypt(KEY, BLOB, data, chunk_size=SMALL)
    offset = vsb1.chunk_offset(1, chunk_size=SMALL)
    piece = obj[offset:offset + SMALL + 16]
    assert vsb1.decrypt_chunk(KEY, BLOB, obj[:12], 1, piece, last=False) == b"b" * SMALL
    with pytest.raises(vsb1.Vsb1Error):
        vsb1.decrypt_chunk(KEY, BLOB, obj[:12], 1, piece, last=True)


def test_streaming_encryption_matches_the_one_shot_format():
    data = os.urandom(3 * SMALL + 3)
    prefix = os.urandom(8)
    pieces = list(vsb1.encrypt_stream(KEY, BLOB, iter([data[:10], data[10:40], data[40:]]), len(data),
                                      chunk_size=SMALL, nonce_prefix=prefix))
    assert b"".join(pieces) == vsb1.encrypt(KEY, BLOB, data, chunk_size=SMALL, nonce_prefix=prefix)


def test_bad_key_or_blob_id_is_refused_before_encrypting():
    with pytest.raises(ValueError):
        vsb1.encrypt(b"short", BLOB, b"x")
    with pytest.raises(ValueError):
        vsb1.encrypt(KEY, "not-sixteen", b"x")
