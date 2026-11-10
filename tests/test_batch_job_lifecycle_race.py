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


async def test_cancelled_batch_is_retryable_when_running_pipeline_times_out(batch, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def pipeline(job_id, job):
        started.set()
        await release.wait()
        raise TimeoutError("GPU generation timed out")

    monkeypatch.setattr(batch, "_run_batch_pipeline", pipeline)
    job = batch._jobs["j1"] = {"status": "queued", "filename": "input.mp4"}
    batch._queue.put_nowait("j1")
    worker = asyncio.create_task(batch._worker())
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert batch.cancel_batch_job("j1") == {"cancelled": True}
        assert job["retry_ready"] is False
        release.set()
        await asyncio.wait_for(batch._queue.join(), 2)
        assert job["status"] == "cancelled"
        assert job["retry_ready"] is True
        assert not job.get("error")
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker


async def test_busy_engine_wait_stops_when_the_job_is_cancelled(batch, monkeypatch):
    from services.model_manager import GpuPoolBusyError

    monkeypatch.setattr(batch, "_BUSY_BACKOFF_S", (0.01,))
    job = {"status": "running"}
    calls = []

    async def busy():
        calls.append(1)
        job["status"] = "cancelled"
        raise GpuPoolBusyError("busy", retry_after=1)

    with pytest.raises(GpuPoolBusyError):
        await batch._await_free_engine(job, busy, budget_s=30)
    assert calls == [1]


async def test_busy_engine_does_not_retry_after_cancellation_during_backoff(batch, monkeypatch):
    from services.model_manager import GpuPoolBusyError

    job = {"status": "running"}
    calls = []

    async def render():
        calls.append(1)
        if len(calls) == 1:
            raise GpuPoolBusyError("busy", retry_after=1)
        pytest.fail("cancelled job attempted another render")

    async def cancel_during_backoff(delay):
        assert delay > 0
        job["status"] = "cancelled"

    monkeypatch.setattr(batch.asyncio, "sleep", cancel_during_backoff)
    with pytest.raises(GpuPoolBusyError):
        await batch._await_free_engine(job, render, budget_s=30)
    assert calls == [1]
