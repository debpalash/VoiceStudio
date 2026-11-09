"""VSB1: the app-side encryption of sync blobs (vssaas desktop-app-guide §9).

object  = "VSB1" || nonce_prefix (8 random bytes) || chunk_0 || … || chunk_{n-1}
chunk_i = AES-256-GCM(data_key, nonce = nonce_prefix || uint32_be(i),
                      aad = "VSB1" || blob_id || uint32_be(i) || final_flag)
Plaintext is cut into 4 194 304-byte chunks (an empty file is one empty chunk).
Any tag failure, or a final flag that is not on the last chunk, fails the
whole file. The data key lives in memory only; never write or log it.
"""
from __future__ import annotations

import math
import os
import struct
from typing import Iterable, Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VSB1"
CHUNK_SIZE = 4_194_304
TAG_SIZE = 16
HEADER_SIZE = 12


class Vsb1Error(ValueError):
    """The object is not a valid VSB1 file for this key and blob."""


def _check(data_key: bytes, blob_id: str) -> bytes:
    if not isinstance(data_key, (bytes, bytearray)) or len(data_key) != 32:
        raise ValueError("data_key must be 32 bytes")
    raw = blob_id.encode("ascii") if isinstance(blob_id, str) else b""
    if len(raw) != 16 or not raw.isalnum():
        raise ValueError("blob_id must be 16 Base62 characters")
    return raw


def chunk_count(plaintext_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    return max(1, math.ceil(plaintext_size / chunk_size))


def ciphertext_size(plaintext_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    return HEADER_SIZE + plaintext_size + TAG_SIZE * chunk_count(plaintext_size, chunk_size)


def chunk_offset(index: int, chunk_size: int = CHUNK_SIZE) -> int:
    """Where chunk ``index`` starts in the object (for Range reads)."""
    return HEADER_SIZE + index * (chunk_size + TAG_SIZE)


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">I", index)


def _aad(blob: bytes, index: int, last: bool) -> bytes:
    return MAGIC + blob + struct.pack(">I", index) + (b"\x01" if last else b"\x00")


def encrypt_stream(data_key: bytes, blob_id: str, pieces: Iterable[bytes], plaintext_size: int, *,
                   chunk_size: int = CHUNK_SIZE, nonce_prefix: Optional[bytes] = None) -> Iterator[bytes]:
    """Yield the object piece by piece; ``plaintext_size`` fixes which chunk is last."""
    blob = _check(data_key, blob_id)
    prefix = nonce_prefix if nonce_prefix is not None else os.urandom(8)
    if len(prefix) != 8:
        raise ValueError("nonce_prefix must be 8 bytes")
    aes = AESGCM(bytes(data_key))
    total = chunk_count(plaintext_size, chunk_size)
    yield MAGIC + prefix
    buffer = b""
    index = 0
    seen = 0
    for piece in pieces:
        seen += len(piece)
        buffer += piece
        while len(buffer) >= chunk_size and index < total - 1:
            chunk, buffer = buffer[:chunk_size], buffer[chunk_size:]
            yield aes.encrypt(_nonce(prefix, index), chunk, _aad(blob, index, False))
            index += 1
    if seen != plaintext_size or len(buffer) > chunk_size:
        raise ValueError("plaintext_size does not match the data")
    yield aes.encrypt(_nonce(prefix, index), buffer, _aad(blob, index, True))


def encrypt(data_key: bytes, blob_id: str, plaintext: bytes, *, chunk_size: int = CHUNK_SIZE,
            nonce_prefix: Optional[bytes] = None) -> bytes:
    return b"".join(encrypt_stream(data_key, blob_id, [plaintext], len(plaintext),
                                   chunk_size=chunk_size, nonce_prefix=nonce_prefix))


def decrypt_chunk(data_key: bytes, blob_id: str, header: bytes, index: int, chunk: bytes, *, last: bool) -> bytes:
    """Decrypt one chunk (Range reads); ``last`` must say whether it is the final one."""
    blob = _check(data_key, blob_id)
    if len(header) < HEADER_SIZE or header[:4] != MAGIC:
        raise Vsb1Error("not a VSB1 object")
    try:
        return AESGCM(bytes(data_key)).decrypt(_nonce(header[4:12], index), chunk, _aad(blob, index, last))
    except InvalidTag as error:
        raise Vsb1Error("chunk failed authentication") from error


def decrypt(data_key: bytes, blob_id: str, obj: bytes, *, chunk_size: int = CHUNK_SIZE) -> bytes:
    if len(obj) < HEADER_SIZE + TAG_SIZE or obj[:4] != MAGIC:
        raise Vsb1Error("not a VSB1 object")
    header, body = obj[:HEADER_SIZE], obj[HEADER_SIZE:]
    step = chunk_size + TAG_SIZE
    count = max(1, math.ceil(len(body) / step))
    out = []
    for index in range(count):
        chunk = body[index * step:(index + 1) * step]
        if len(chunk) < TAG_SIZE:
            raise Vsb1Error("truncated chunk")
        out.append(decrypt_chunk(data_key, blob_id, header, index, chunk, last=index == count - 1))
    return b"".join(out)
