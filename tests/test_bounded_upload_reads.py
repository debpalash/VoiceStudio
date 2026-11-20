"""Uploads must be read with a size bound, never materialized whole.

``UploadFile.read()`` with no argument returns the entire multipart part, so a
multi-GB body becomes a multi-GB heap allocation in the backend process before any
route-level check can run. Six of the routes that did this are consumer-tier (a
share PIN, a trusted CIDR, or anonymous in bare server mode) and
``POST /preview/upload`` is documented as open to consumers by design, so the
caller need not be the operator.

Two helpers own the rule: ``read_upload_bounded`` for bytes a route needs in
memory, ``copy_upload_bounded`` for the one route that only stores a video.
"""
from __future__ import annotations

import asyncio
import io
import pathlib
import re

import pytest
from fastapi import HTTPException

from core.media_types import (
    MAX_AUDIO_UPLOAD_BYTES,
    copy_upload_bounded,
    read_upload_bounded,
)

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ROUTERS = _ROOT / "backend" / "api" / "routers"

#: ``await something.read()`` with an empty argument list. A call that passes a
#: chunk size is fine; so is routing through one of the two helpers.
_UNBOUNDED = re.compile(r"await\s+[\w\.\[\]]+\.read\(\s*\)")


class _Upload:
    """Minimal UploadFile stand-in: async ``read(size)`` over a byte buffer."""

    def __init__(self, payload: bytes, chunk: int | None = None):
        self._buffer = io.BytesIO(payload)
        self._chunk = chunk

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._buffer.read()
        if self._chunk is not None:
            size = min(size, self._chunk)
        return self._buffer.read(size)


def _executable_source(path: pathlib.Path) -> str:
    """Source with comments and string literals blanked out, line structure intact.

    The rule is about executable calls; a comment that *documents* the old
    ``await UploadFile.read()`` pattern (batch.py explains why it changed) must not
    be reported as a call site. Blanking rather than re-joining the tokens keeps
    the whitespace between them, so ``await file.read()`` still reads as written
    and the reported line numbers stay true.
    """
    import io as _io
    import tokenize

    text = path.read_text(encoding="utf-8")
    try:
        spans = [
            (token.start, token.end)
            for token in tokenize.generate_tokens(_io.StringIO(text).readline)
            if token.type in (tokenize.COMMENT, tokenize.STRING)
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    lines = text.splitlines(keepends=True)
    for (start_row, start_col), (end_row, end_col) in reversed(spans):
        for row in range(start_row, end_row + 1):
            line = lines[row - 1]
            begin = start_col if row == start_row else 0
            finish = end_col if row == end_row else len(line.rstrip("\n"))
            lines[row - 1] = line[:begin] + " " * (finish - begin) + line[finish:]
    return "".join(lines)


def test_the_scan_matches_the_pattern_it_is_meant_to_catch():
    # A vacuous scanner would pass forever; prove it reads both forms.
    assert _UNBOUNDED.search("payload = await file.read()")
    assert _UNBOUNDED.search("    content = await consent_audio.read( )")
    assert not _UNBOUNDED.search("payload = await file.read(UPLOAD_CHUNK_BYTES)")
    assert not _UNBOUNDED.search("read_upload_bounded(file, LIMIT, what='Audio')")


def test_a_comment_is_not_a_call_site(tmp_path):
    snippet = tmp_path / "snippet.py"
    snippet.write_text(
        "# `await file.read()` used to buffer the whole body\n"
        'DOC = "await file.read()"\n'
        "payload = await file.read(UPLOAD_CHUNK_BYTES)\n",
        encoding="utf-8",
    )
    code = _executable_source(snippet)
    assert not _UNBOUNDED.search(code), code


def test_no_router_reads_an_upload_without_a_bound():
    offenders = []
    for path in sorted(_ROUTERS.glob("*.py")):
        code = _executable_source(path)
        for number, line in enumerate(code.splitlines(), 1):
            if _UNBOUNDED.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "These routes read an upload with no size bound, so a huge multipart body "
        "is buffered whole before any check runs. Use read_upload_bounded() (or "
        "copy_upload_bounded() when the bytes only go to disk):\n" + "\n".join(offenders)
    )


def test_read_upload_bounded_returns_the_bytes_below_the_cap():
    payload = b"x" * 3000
    assert asyncio.run(read_upload_bounded(_Upload(payload), 4096, what="Audio")) == payload


def test_read_upload_bounded_refuses_past_the_cap():
    payload = b"x" * 5000
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(read_upload_bounded(_Upload(payload), 4096, what="Audio"))
    assert excinfo.value.status_code == 413
    assert "Audio" in excinfo.value.detail


def test_the_cap_is_enforced_while_reading_not_after():
    """A body far larger than the cap must fail without being buffered whole."""
    read_bytes = {"total": 0}

    class _Counting(_Upload):
        async def read(self, size: int = -1) -> bytes:
            chunk = await super().read(size)
            read_bytes["total"] += len(chunk)
            return chunk

    huge = _Counting(b"x" * (64 * 1024 * 1024))
    with pytest.raises(HTTPException):
        asyncio.run(read_upload_bounded(huge, 1024 * 1024, what="Audio"))
    assert read_bytes["total"] <= 2 * 1024 * 1024, (
        f"read {read_bytes['total']} bytes before refusing; the cap must stop the read"
    )


def test_copy_upload_bounded_streams_and_caps():
    destination = io.BytesIO()
    written = asyncio.run(
        copy_upload_bounded(_Upload(b"y" * 2048), destination, 4096, what="Preview")
    )
    assert written == 2048
    assert destination.getvalue() == b"y" * 2048

    with pytest.raises(HTTPException):
        asyncio.run(copy_upload_bounded(_Upload(b"y" * 9000), io.BytesIO(), 4096, what="Preview"))


def test_the_audio_limit_matches_the_documented_convert_ceiling():
    # voice_convert's own cap is the house number for "an audio upload"; keeping
    # them equal means one message, not two, when a user exceeds it.
    source = (_ROOT / "backend" / "api" / "routers" / "voice_convert.py").read_text(encoding="utf-8")
    assert "_MAX_SOURCE_AUDIO_BYTES = 64 * 1024 * 1024" in source
    assert MAX_AUDIO_UPLOAD_BYTES == 64 * 1024 * 1024
