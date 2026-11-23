import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile


def test_subtitle_only_creates_durable_audio_job_without_media(monkeypatch):
    from api.routers import dub_core

    saved = {}
    monkeypatch.setattr(dub_core, "_save_job", lambda jid, job, **kw: saved.update(id=jid, job=job))
    upload = UploadFile(filename="test.srt", file=io.BytesIO(
        b"1\n00:00:02,000 --> 00:00:04,000\nHola\n\n"
        b"2\n00:00:07,000 --> 00:00:09,500\nAdios\n"))
    result = asyncio.run(dub_core.dub_from_srt(upload, "es"))
    try:
        assert result["duration"] == 9.5
        assert result["segments"][0]["start"] == 2
        assert result["source_lang"] == "es"
        assert saved["job"]["input_type"] == "audio"
        assert saved["job"]["subtitle_only"] is True
        assert not saved["job"].get("video_path")
        assert not saved["job"].get("audio_path")
        assert saved["job"]["dubbed_tracks"] == {}
    finally:
        dub_core._dub_jobs.pop(result["job_id"], None)


def test_invalid_subtitles_do_not_create_a_job(monkeypatch):
    from api.routers import dub_core

    monkeypatch.setattr(dub_core, "_save_job", lambda *a, **kw: pytest.fail("invalid job persisted"))
    upload = UploadFile(filename="bad.srt", file=io.BytesIO(b"not subtitles"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(dub_core.dub_from_srt(upload, "es"))
    assert error.value.status_code == 400
