"""Batch retry/delete are serialized per job and never touch a live pipeline (#2547)."""
import asyncio
import threading

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


@pytest.fixture
def batch(tmp_path, monkeypatch):
    from api.routers import batch
    from services import asr_backend, translation_engines

    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(batch, "_jobs", {})
    monkeypatch.setattr(batch, "_queue", asyncio.Queue())
    monkeypatch.setattr(batch, "_ensure_queue", lambda: None)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(translation_engines, "is_ready", lambda provider: True)
    return batch


def _job(batch, tmp_path, job_id="j1", status="failed", **extra):
    video = tmp_path / f"{job_id}.mp4"
    video.write_bytes(b"video")
    out = tmp_path / "batch" / job_id
    out.mkdir(parents=True)
    (out / "out.wav").write_bytes(b"audio")
    batch._jobs[job_id] = {
        "status": status, "video_path": str(video), "langs": ["es"],
        "translation_provider": "argos", "attempts": 1, **extra,
    }
    return video, out


def _gated_voice(batch, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def voice(_value):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(batch, "_batch_voice", voice)
    return entered, release


async def _wait(event):
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("preflight never started")


async def test_concurrent_retries_queue_the_job_once(batch, tmp_path, monkeypatch):
    _job(batch, tmp_path)
    entered, release = _gated_voice(batch, monkeypatch)
    first = asyncio.ensure_future(batch.retry_batch_job("j1"))
    await _wait(entered)
    with pytest.raises(HTTPException) as err:
        await batch.retry_batch_job("j1")
    assert err.value.status_code == 409
    release.set()
    assert (await first)["status"] == "queued"
    assert batch._queue.qsize() == 1
    assert batch._jobs["j1"]["attempts"] == 2
    assert "j1" not in batch._job_reservations


async def test_delete_during_retry_admission_keeps_the_upload(batch, tmp_path, monkeypatch):
    video, out = _job(batch, tmp_path)
    entered, release = _gated_voice(batch, monkeypatch)
    retry = asyncio.ensure_future(batch.retry_batch_job("j1"))
    await _wait(entered)
    with pytest.raises(HTTPException) as err:
        await asyncio.to_thread(batch.delete_batch_job, "j1")
    assert err.value.status_code == 409
    assert video.exists()
    release.set()
    await retry
    assert video.exists() and "j1" in batch._jobs


async def test_failed_retry_releases_its_reservation(batch, tmp_path, monkeypatch):
    _job(batch, tmp_path)

    def bad(_value):
        raise ValueError("no such voice")

    monkeypatch.setattr(batch, "_batch_voice", bad)
    with pytest.raises(HTTPException) as err:
        await batch.retry_batch_job("j1")
    assert err.value.status_code == 422
    assert "j1" not in batch._job_reservations
    assert batch.delete_batch_job("j1") == {"deleted": True}


@pytest.mark.parametrize("state", ["queued", "running", "stopping"])
async def test_delete_refuses_active_or_still_stopping_jobs(batch, tmp_path, state):
    extra = {}
    status = state
    if state == "stopping":
        status = "cancelled"
        extra["retry_ready"] = False
        batch._processing_job_ids.add("j1")
    video, out = _job(batch, tmp_path, status=status, **extra)
    try:
        with pytest.raises(HTTPException) as err:
            batch.delete_batch_job("j1")
        assert err.value.status_code == 409
        assert video.exists() and (out / "out.wav").exists() and "j1" in batch._jobs
    finally:
        batch._processing_job_ids.discard("j1")


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
async def test_delete_still_removes_terminal_jobs(batch, tmp_path, status):
    video, out = _job(batch, tmp_path, status=status)
    assert batch.delete_batch_job("j1") == {"deleted": True}
    assert not video.exists() and not out.exists() and "j1" not in batch._jobs
