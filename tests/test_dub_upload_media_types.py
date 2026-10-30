"""Unsupported dubbing sources must reach the localized unsupported-input path."""

import asyncio
import io

from fastapi import HTTPException, UploadFile
import pytest


@pytest.mark.parametrize(
    ("input_type", "filename", "kind"),
    [("audio", "clip.mp4", "audio"), ("AuDiO", "CLIP.MP4", "audio"),
     ("audio", "document.pdf", "audio"), ("audio", "no-extension", "audio"),
     ("video", "document.pdf", "video")],
)
def test_unsupported_source_reports_415_before_creating_a_job(tmp_path, monkeypatch, input_type, filename, kind):
    from api.routers import dub_core

    job_dir = tmp_path / "unsupported-job"
    monkeypatch.setattr(dub_core, "_safe_job_dir", lambda _: str(job_dir))
    source = io.BytesIO(b"unread source bytes")
    upload = UploadFile(file=source, filename=filename)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(dub_core.dub_upload(video=upload, job_id="unsupported-job", input_type=input_type, source_lang=None))
        assert error.value.status_code == 415
        assert error.value.detail.startswith(f"Unsupported {kind} file type")
        assert not job_dir.exists()
        assert source.tell() == 0
    finally:
        source.close()
