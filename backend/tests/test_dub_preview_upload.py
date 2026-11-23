from __future__ import annotations

import asyncio
import io
import threading

import pytest
from fastapi import UploadFile


@pytest.mark.asyncio
async def test_preview_ffmpeg_does_not_block_event_loop(monkeypatch, tmp_path):
    from api.routers import dub_core

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def slow_ffmpeg(*_args, **_kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=2)

    monkeypatch.setattr(dub_core, "PREVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(dub_core, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dub_core.subprocess, "run", slow_ffmpeg)
    upload = UploadFile(filename="preview.mp4", file=io.BytesIO(b"video"))

    before = loop.time()
    task = asyncio.create_task(dub_core.preview_upload(upload))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert loop.time() - before < 0.5
    finally:
        release.set()

    result = await task
    assert result["audioUrl"].endswith(".wav")


class _ChunkOnlyFile(io.BytesIO):
    """An upload file that fails if the whole body is requested at once."""

    def read(self, size=-1):
        if size is None or size < 0:
            raise AssertionError("the route read the whole upload into memory")
        return super().read(size)


@pytest.mark.asyncio
async def test_preview_upload_copies_the_body_in_chunks(monkeypatch, tmp_path):
    from api.routers import dub_core

    monkeypatch.setattr(dub_core, "PREVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(dub_core, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dub_core.subprocess, "run", lambda *_a, **_k: None)
    payload = b"\x00\x00\x00\x18ftypisom" + b"x" * (8 * 1024 * 1024)
    upload = UploadFile(filename="preview.mp4", file=_ChunkOnlyFile(payload))

    result = await dub_core.preview_upload(upload)

    assert result["audioUrl"].endswith(".wav")
    written = [path for path in tmp_path.iterdir() if path.suffix == ".mp4"]
    assert written and written[0].stat().st_size == len(payload)


@pytest.mark.asyncio
async def test_preview_upload_leaves_no_file_behind_for_a_manifest(monkeypatch, tmp_path):
    from api.routers import dub_core

    monkeypatch.setattr(dub_core, "PREVIEW_DIR", str(tmp_path))
    monkeypatch.setattr(dub_core, "find_ffmpeg", lambda: "ffmpeg")
    upload = UploadFile(
        filename="preview.mp4",
        file=io.BytesIO(b"#EXTM3U\n#EXTINF:1,\nhttps://example.invalid/clip.ts\n"),
    )

    with pytest.raises(dub_core.InvalidMediaFileError):
        await dub_core.preview_upload(upload)

    assert not list(tmp_path.iterdir())
