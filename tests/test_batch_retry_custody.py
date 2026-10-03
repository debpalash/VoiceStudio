"""Public batch retry requests must not duplicate or delete queued work."""
import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def queue(tmp_path, monkeypatch):
    from api.routers import batch
    from services import asr_backend, translation_engines

    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(batch, "_jobs", {})
    monkeypatch.setattr(batch, "_queue", asyncio.Queue())
    monkeypatch.setattr(batch, "_processing_job_ids", set())
    monkeypatch.setattr(batch, "_ensure_queue", lambda: None)
    # Model/provider admission is covered separately. No inference or external
    # provider is needed to race two requests over the real queue and files.
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(translation_engines, "is_ready", lambda _provider: True)
    video = tmp_path / "input.mp4"
    video.write_bytes(b"original input")
    outputs = tmp_path / "batch" / "job"
    outputs.mkdir(parents=True)
    (outputs / "prior.mp4").write_bytes(b"prior output")
    batch._jobs["job"] = {
        "id": "job", "status": "failed", "video_path": str(video),
        "translation_provider": "argos", "langs": ["es"], "attempts": 1,
        "created_at": 0, "filename": "input.mp4", "voice_id": None,
    }
    app = FastAPI()
    app.include_router(batch.router)
    return batch, app, video, outputs


def test_two_retry_requests_enqueue_one_attempt(queue, monkeypatch):
    batch, app, video, _outputs = queue
    entered, release = threading.Event(), threading.Event()
    original = batch._batch_voice

    def gated_voice(voice_id):
        entered.set()
        assert release.wait(5), "preflight was never released"
        return original(voice_id)

    monkeypatch.setattr(batch, "_batch_voice", gated_voice)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = asyncio.create_task(client.post("/batch/jobs/job/retry"))
            assert await asyncio.to_thread(entered.wait, 5)
            try:
                second = await asyncio.wait_for(client.post("/batch/jobs/job/retry"), 5)
                assert second.status_code == 409
            finally:
                release.set()
            responses = [await first, second]
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert batch._queue.qsize() == 1
        assert batch._jobs["job"]["attempts"] == 2
        assert video.read_bytes() == b"original input"

    asyncio.run(scenario())


def test_delete_cannot_remove_input_during_retry_admission(queue, monkeypatch):
    batch, app, video, outputs = queue
    entered, release = threading.Event(), threading.Event()
    original = batch._batch_voice

    def gated_voice(voice_id):
        entered.set()
        assert release.wait(5)
        return original(voice_id)

    monkeypatch.setattr(batch, "_batch_voice", gated_voice)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            retry = asyncio.create_task(client.post("/batch/jobs/job/retry"))
            assert await asyncio.to_thread(entered.wait, 5)
            deletion = await client.delete("/batch/jobs/job")
            # Always settle the task, including a failing regression assertion.
            release.set()
            response = await retry
        assert deletion.status_code == 409
        assert response.status_code == 200
        assert video.read_bytes() == b"original input"
        assert "job" in batch._jobs
        assert batch._queue.qsize() == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status,processing", [("queued", False), ("running", True), ("cancelled", True)])
def test_delete_keeps_active_or_still_stopping_job_files(queue, status, processing):
    batch, app, video, outputs = queue
    batch._jobs["job"]["status"] = status
    if processing:
        batch._processing_job_ids.add("job")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.delete("/batch/jobs/job")
        assert response.status_code == 409
        assert video.exists() and outputs.exists()
        assert "job" in batch._jobs

    asyncio.run(scenario())


def test_settled_terminal_job_can_still_be_deleted(queue):
    batch, app, video, outputs = queue

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.delete("/batch/jobs/job")
        assert response.status_code == 200
        assert not video.exists() and not outputs.exists()
        assert "job" not in batch._jobs

    asyncio.run(scenario())


def test_failed_preflight_releases_job_for_a_later_retry(queue, monkeypatch):
    batch, app, video, outputs = queue
    from services import translation_engines
    monkeypatch.setattr(translation_engines, "is_ready", lambda _provider: False)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            failed = await client.post("/batch/jobs/job/retry")
            assert failed.status_code == 409
            assert batch._jobs["job"]["attempts"] == 1
            assert video.exists() and outputs.exists()
            monkeypatch.setattr(translation_engines, "is_ready", lambda _provider: True)
            retried = await client.post("/batch/jobs/job/retry")
        assert retried.status_code == 200
        assert batch._queue.qsize() == 1
        assert batch._jobs["job"]["attempts"] == 2

    asyncio.run(scenario())


def test_cancelled_retry_keeps_reservation_until_output_cleanup_finishes(queue, monkeypatch):
    batch, app, video, outputs = queue
    entered, release = threading.Event(), threading.Event()
    original = batch.shutil.rmtree

    def slow_cleanup(path):
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(batch.shutil, "rmtree", slow_cleanup)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            retry = asyncio.create_task(client.post("/batch/jobs/job/retry"))
            assert await asyncio.to_thread(entered.wait, 5)
            retry.cancel()
            delivered = asyncio.Event()
            # Task.cancel schedules delivery before this callback. Await its
            # explicit turn boundary before attempting the conflicting action.
            asyncio.get_running_loop().call_soon(delivered.set)
            await delivered.wait()
            try:
                response = await asyncio.wait_for(client.delete("/batch/jobs/job"), 5)
                assert response.status_code == 409
            finally:
                release.set()
            result = (await asyncio.gather(retry, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            assert response.status_code == 409
            assert video.read_bytes() == b"original input"
            assert batch._jobs["job"]["status"] == "failed"
            assert batch._jobs["job"]["attempts"] == 1
            assert batch._queue.qsize() == 0
            assert not outputs.exists()
            # Cancellation is propagated after cleanup, and does not strand
            # the job reservation or prevent an explicit later attempt.
            later = await client.post("/batch/jobs/job/retry")
            assert later.status_code == 200
            assert batch._queue.qsize() == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_cleanup_error_releases_custody_and_preserves_cancellation(queue, monkeypatch, cancelled):
    batch, app, video, outputs = queue
    entered, release = threading.Event(), threading.Event()
    original = batch.shutil.rmtree

    def failing_cleanup(_path):
        entered.set()
        assert release.wait(5)
        # Portable filesystem-error boundary; an independent native proof
        # also reproduces this with a real read-only output directory.
        raise PermissionError("output is in use")

    monkeypatch.setattr(batch.shutil, "rmtree", failing_cleanup)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            retry = asyncio.create_task(client.post("/batch/jobs/job/retry"))
            assert await asyncio.to_thread(entered.wait, 5)
            if cancelled:
                retry.cancel()
                delivered = asyncio.Event()
                asyncio.get_running_loop().call_soon(delivered.set)
                await delivered.wait()
            release.set()
            result = (await asyncio.gather(retry, return_exceptions=True))[0]
            if cancelled:
                assert isinstance(result, asyncio.CancelledError)
            else:
                assert result.status_code == 500
                assert "Could not reset" in result.json()["detail"]
            assert video.exists() and outputs.exists()
            assert batch._jobs["job"]["attempts"] == 1
            assert batch._queue.qsize() == 0
            monkeypatch.setattr(batch.shutil, "rmtree", original)
            later = await client.post("/batch/jobs/job/retry")
            assert later.status_code == 200
            assert batch._queue.qsize() == 1

    asyncio.run(scenario())
