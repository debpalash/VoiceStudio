"""Subtitle and manuscript uploads decode the encodings Windows tools save.

Notepad's "Unicode" and many subtitle editors write UTF-16 with a BOM, and
older files use the Windows-1252 code page. /dub/import-srt rejected a UTF-16
.srt as having no cues, and /audiobook/import turned a UTF-16 manuscript into
NUL-riddled text and silently dropped every accent, dash and curly quote from a
Windows-1252 one.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import UploadFile

_SRT = (
    "1\r\n00:00:01,000 --> 00:00:02,500\r\nIt’s café time\r\n\r\n"
    "2\r\n00:00:03,000 --> 00:00:04,000\r\nSecond — line\r\n"
)
_BOOK = "# Prologue\r\nCafé crème — it’s late.\r\n# Chapter One\r\nThe end.\r\n"

_ENCODINGS = {
    "utf-8": lambda s: s.encode("utf-8"),
    "utf-8-bom": lambda s: s.encode("utf-8-sig"),
    "utf-16-le-bom": lambda s: s.encode("utf-16"),  # Notepad "Unicode"
    "utf-16-be-bom": lambda s: b"\xfe\xff" + s.encode("utf-16-be"),
    "windows-1252": lambda s: s.encode("cp1252"),
}


def _import_srt(monkeypatch, data: bytes) -> dict:
    from api.routers import dub_core

    job_id = "srt-encoding"
    dub_core._dub_jobs[job_id] = {"duration": 10.0, "segments": []}
    monkeypatch.setattr(dub_core, "_save_job", lambda *_args: None)
    upload = UploadFile(filename="subs.srt", file=io.BytesIO(data))
    try:
        return asyncio.run(dub_core.dub_import_srt(job_id, upload))
    finally:
        dub_core._dub_jobs.pop(job_id, None)


@pytest.mark.parametrize("encoding", sorted(_ENCODINGS))
def test_import_srt_reads_every_common_encoding(monkeypatch, encoding):
    result = _import_srt(monkeypatch, _ENCODINGS[encoding](_SRT))

    assert [s["text"] for s in result["segments"]] == [
        "It’s café time",
        "Second — line",
    ]


@pytest.mark.parametrize("encoding", sorted(_ENCODINGS))
def test_audiobook_import_reads_every_common_encoding(encoding):
    from api.routers import audiobook

    upload = UploadFile(filename="book.txt", file=io.BytesIO(_ENCODINGS[encoding](_BOOK)))
    result = asyncio.run(audiobook.audiobook_import(upload))

    assert result["chapters"] == 2
    assert result["text"].startswith("# Prologue")  # no stray BOM
    assert "Café crème — it’s late." in result["text"]
    assert "\x00" not in result["text"]


def test_bytes_windows_1252_leaves_undefined_still_decode():
    from services.text_upload import decode_text_upload

    # 0x81 has no Windows-1252 mapping; the import must still succeed.
    assert decode_text_upload(b"caf\xe9 \x81") == "café \x81"


def test_an_undefined_windows_1252_byte_leaves_the_rest_of_the_file_intact():
    from services.text_upload import decode_text_upload

    # Only the undefined byte takes its Latin-1 value (as the browser's
    # windows-1252 decoder does); the curly quote beside it stays a quote.
    assert decode_text_upload(b"It\x92s \x81 caf\xe9") == "It’s \x81 café"


def test_decode_text_upload_keeps_valid_utf8_untouched():
    from services.text_upload import decode_text_upload

    text = "Привет, κόσμε — café"
    assert decode_text_upload(text.encode("utf-8")) == text
