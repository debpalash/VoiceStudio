"""Tests for batch dubbing API endpoints.

These tests create a minimal FastAPI app with only the batch router,
avoiding the heavy main app import chain. The batch module is
lightweight — it only imports os, uuid, time, asyncio, logging,
fastapi, and pydantic at module level.
"""
import io
import pytest
# These tests exercise ASR-consumer mechanics and assume ASR weights are
# installed - neutralize the no-ASR preflight (its own suite:
# tests/test_asr_model_missing.py).
pytestmark = pytest.mark.usefixtures("asr_model_installed")


# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before the batch router imports the REAL core.config (the
# old sys.modules stub leaked at collection time and broke mixed runs).
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def batch():
    """The batch module, imported at TEST time — never at collection time.

    Clears in-memory state between tests and replaces the worker with a
    no-op so jobs stay queued. Everything in this file (including the app
    under test) MUST go through this one module object: earlier suite
    members (e.g. tests/smoke/test_boot_smoke.py) purge ``api.*`` from
    ``sys.modules``, so a collection-time ``from api.routers.batch import
    router`` leaves the app serving STALE-module handlers while the fixture
    patches a fresh re-import — the stale handlers then start the REAL
    worker/pipeline, whose cancelled-but-swallowing task used to hang the
    TestClient portal teardown forever at ~97% of a full-suite run.
    """
    import api.routers.batch as batch
    batch._jobs.clear()
    batch._queue = None
    if batch._worker_task and not batch._worker_task.done():
        batch._worker_task.cancel()
    batch._worker_task = None

    # Monkey-patch _ensure_queue to use a no-op worker so jobs stay queued
    original_ensure = batch._ensure_queue

    def _test_ensure_queue():
        if batch._queue is None:
            import asyncio

            async def _noop():
                while True:
                    job_id = await batch._queue.get()
                    batch._queue.task_done()

            batch._queue = asyncio.Queue()
            batch._worker_task = asyncio.ensure_future(_noop())

    batch._ensure_queue = _test_ensure_queue
    yield batch
    batch._ensure_queue = original_ensure
    batch._jobs.clear()


@pytest.fixture
def client(batch):
    app = FastAPI()
    app.include_router(batch.router)
    return TestClient(app)


@pytest.fixture
def fake_video():
    return b"\x00\x00\x00\x1c\x66\x74\x79\x70" + b"\x00" * 1016  # 1KB


def _enqueue(client, video_bytes, langs="es", voice_id="", preserve_bg="true"):
    return client.post(
        "/batch/enqueue",
        files={"video": ("test.mp4", io.BytesIO(video_bytes), "video/mp4")},
        data={"langs": langs, "preserve_bg": preserve_bg, **({"voice_id": voice_id} if voice_id else {})},
    )


class TestEnqueue:
    def test_returns_job_id(self, client, fake_video):
        resp = _enqueue(client, fake_video, "es,fr")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "queued"

    def test_empty_langs_fails(self, client, fake_video):
        """Empty langs string should return 400."""
        # Send with no langs field at all
        resp = client.post(
            "/batch/enqueue",
            files={"video": ("test.mp4", io.BytesIO(fake_video), "video/mp4")},
            data={"langs": ",,,", "preserve_bg": "true"},
        )
        assert resp.status_code == 400

    def test_multi_lang_splits(self, client, fake_video):
        resp = _enqueue(client, fake_video, "es,fr,de")
        job_id = resp.json()["job_id"]
        job = client.get(f"/batch/jobs/{job_id}").json()
        assert job["langs"] == ["es", "fr", "de"]

    @pytest.mark.parametrize("langs", ["es,../../x", "fr,..\\..\\x", "es,de/../x", "es,C:x"])
    def test_rejects_language_codes_that_are_not_plain(self, batch, client, fake_video, langs):
        # Each code names dubbed_{lang}.wav / output_{lang}.mp4 on disk.
        resp = _enqueue(client, fake_video, langs)
        assert resp.status_code == 400, resp.text
        assert not batch._jobs

    @pytest.mark.parametrize("name", ["clip.mp4:stream", "clip.m\\..\\x", "clip.mp4\x01"])
    def test_rejects_upload_names_with_unusable_extensions(self, batch, client, fake_video, name):
        resp = client.post(
            "/batch/enqueue",
            files={"video": (name, io.BytesIO(fake_video), "video/mp4")},
            data={"langs": "es", "preserve_bg": "true"},
        )
        assert resp.status_code == 415, resp.text
        assert not batch._jobs

    def test_preserves_filename(self, client, fake_video):
        resp = _enqueue(client, fake_video)
        job_id = resp.json()["job_id"]
        job = client.get(f"/batch/jobs/{job_id}").json()
        assert job["filename"] == "test.mp4"

    @pytest.mark.asyncio
    async def test_upload_is_persisted_in_bounded_chunks(self, batch, tmp_path):
        class RecordingUpload:
            def __init__(self):
                self.read_sizes = []
                self.remaining = b"video"

            async def read(self, size):
                self.read_sizes.append(size)
                chunk, self.remaining = self.remaining[:size], self.remaining[size:]
                return chunk

        upload = RecordingUpload()
        destination = tmp_path / "video.mp4"
        await batch._save_upload(upload, str(destination))

        assert destination.read_bytes() == b"video"
        assert upload.read_sizes == [batch._UPLOAD_CHUNK_BYTES, batch._UPLOAD_CHUNK_BYTES]

    @pytest.mark.asyncio
    async def test_failed_upload_removes_partial_file(self, batch, tmp_path):
        class FailingUpload:
            calls = 0

            async def read(self, _size):
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("upload interrupted")

        destination = tmp_path / "video.mp4"
        with pytest.raises(OSError, match="upload interrupted"):
            await batch._save_upload(FailingUpload(), str(destination))

        assert not destination.exists()


class TestListJobs:
    def test_empty(self, client):
        resp = client.get("/batch/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_enqueued(self, client, fake_video):
        _enqueue(client, fake_video)
        _enqueue(client, fake_video)
        jobs = client.get("/batch/jobs").json()
        assert len(jobs) == 2

    def test_filter_active(self, client, fake_video):
        r1 = _enqueue(client, fake_video).json()
        r2 = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r2['job_id']}/cancel")

        active = client.get("/batch/jobs?status=active").json()
        assert len(active) == 1
        assert active[0]["id"] == r1["job_id"]

    def test_filter_cancelled(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r['job_id']}/cancel")

        cancelled = client.get("/batch/jobs?status=cancelled").json()
        assert len(cancelled) == 1


class TestGetJob:
    def test_not_found(self, client):
        assert client.get("/batch/jobs/nope").status_code == 404

    def test_found(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        job = client.get(f"/batch/jobs/{r['job_id']}").json()
        assert job["id"] == r["job_id"]
        assert job["status"] == "queued"


class TestCancelJob:
    def test_cancel_queued(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        resp = client.post(f"/batch/jobs/{r['job_id']}/cancel")
        assert resp.json()["cancelled"] is True
        job = client.get(f"/batch/jobs/{r['job_id']}").json()
        assert job["status"] == "cancelled"

    def test_cancel_already_done(self, client, batch, fake_video):
        r = _enqueue(client, fake_video).json()
        batch._jobs[r["job_id"]]["status"] = "done"
        resp = client.post(f"/batch/jobs/{r['job_id']}/cancel")
        assert resp.json()["already"] == "done"

    def test_cancel_not_found(self, client):
        assert client.post("/batch/jobs/nope/cancel").status_code == 404


class TestDeleteJob:
    def test_delete_cancelled(self, client, fake_video):
        r = _enqueue(client, fake_video).json()
        client.post(f"/batch/jobs/{r['job_id']}/cancel")
        resp = client.delete(f"/batch/jobs/{r['job_id']}")
        assert resp.json()["deleted"] is True
        assert client.get(f"/batch/jobs/{r['job_id']}").status_code == 404

    def test_delete_not_found(self, client):
        response = client.delete("/batch/jobs/nope")
        assert response.status_code == 404


class TestSetProgress:
    def test_basic(self, batch):
        job = {}
        batch._set_progress(job, "transcribe", 50, segments_count=10)
        assert job["progress"]["stage"] == "transcribe"
        assert job["progress"]["percent"] == 50
        assert job["progress"]["segments_count"] == 10

    def test_overwrite(self, batch):
        job = {"progress": {"stage": "extract", "percent": 100}}
        batch._set_progress(job, "generate", 25, current_lang="es")
        assert job["progress"]["stage"] == "generate"
        assert job["progress"]["current_lang"] == "es"


class TestWorkerShutdown:
    def test_worker_task_terminates_on_cancel_mid_job(self, batch, monkeypatch):
        """Regression guard: `_worker` used to swallow CancelledError and
        re-enter `_queue.get()`, leaving an immortal task. Event-loop
        teardown (app shutdown, TestClient per-request portal exit) then
        hung forever in `_cancel_all_tasks` — the full-suite freeze at ~97%.
        Cancellation arriving mid-pipeline must mark the job cancelled AND
        terminate the task."""
        import asyncio

        async def scenario():
            started = asyncio.Event()

            async def fake_pipeline(job_id, job):
                started.set()
                await asyncio.sleep(3600)

            monkeypatch.setattr(batch, "_run_batch_pipeline", fake_pipeline)
            batch._queue = asyncio.Queue()
            task = asyncio.ensure_future(batch._worker())
            batch._jobs["j1"] = {"status": "queued", "filename": "x.mp4"}
            await batch._queue.put("j1")
            await asyncio.wait_for(started.wait(), timeout=5)

            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=2)
            assert task in done, (
                "worker task must terminate when cancelled mid-job "
                "(swallowing CancelledError makes shutdown hang forever)"
            )
            assert batch._jobs["j1"]["status"] == "cancelled"

        asyncio.run(scenario())
