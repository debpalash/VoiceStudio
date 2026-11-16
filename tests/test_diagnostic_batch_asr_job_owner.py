"""Batch extraction cannot turn a known-OFF job into a later-consented ASR unit."""
import asyncio
import importlib
import subprocess
import threading
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401


@pytest.mark.parametrize("transition", ["off_to_on", "regrant", "same", "withdraw", "cancel"])
def test_batch_worker_asr_keeps_job_owner_through_extraction(
    reporter, monkeypatch, tmp_path, transition,
):
    batch = importlib.import_module("api.routers.batch")
    asr = importlib.import_module("services.asr_backend")
    ffmpeg = importlib.import_module("services.ffmpeg_utils")
    reports = importlib.import_module("core.diagnostic_reports")
    if transition != "off_to_on":
        reporter.consent(True)
    original = reports.observe_grant()
    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(batch, "_jobs", {})
    monkeypatch.setattr(batch, "_processing_job_ids", set())
    monkeypatch.setattr(ffmpeg, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg, "validate_media_source", lambda *_: None)
    monkeypatch.setattr(ffmpeg, "require_audio_stream", lambda *_: None)

    async def scenario():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        monkeypatch.setattr(batch, "_queue", queue)
        extracted, asr_owners = [], []

        def extract(*args, **kwargs):
            extracted.append(True)
            if len(extracted) == 1:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(3), "Extraction was never released"
            return SimpleNamespace(stderr=b"Duration: 00:00:02.00", stdout=b"")

        async def failing_asr(*args, **kwargs):
            asr_owners.append(reports.inherited_grant())
            error = RuntimeError("private native cleanup detail")
            # A real guarded ASR leaf reports before propagating its terminal
            # error; exercise that owner inside the pipeline's await_owned.
            reports.capture_failure("transcription", error=error)
            raise error

        monkeypatch.setattr(subprocess, "run", extract)
        monkeypatch.setattr(asr, "run_transcribe_guarded", failing_asr)

        def enqueue(job_id):
            job = {
                "status": "queued", "filename": "fixture.mp4",
                "video_path": str(tmp_path / "fixture.mp4"), "langs": ["en"],
            }
            batch._jobs[job_id] = job
            queue.put_nowait(job_id)
            return job

        job = enqueue("original-asr-owner")
        worker = asyncio.create_task(batch._worker())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if transition in {"off_to_on", "regrant"}:
                reporter.consent(False)
                reporter.consent(True)
            elif transition == "withdraw":
                reporter.consent(False)
            elif transition == "cancel":
                job["status"] = "cancelled"
            release.set()
            await asyncio.wait_for(queue.join(), 2)
            assert len(extracted) == 2, "The real extraction/duration path must execute"
            assert job["status"] == ("cancelled" if transition == "cancel" else "failed")
            saved = records(reporter)
            assert len(saved) == int(transition == "same")
            assert asr_owners == ([] if transition == "cancel" else [original])

            if transition in {"off_to_on", "regrant"}:
                # The same long-lived worker must independently observe ON
                # for a genuinely new job, rather than staying permanently OFF.
                fresh = enqueue("fresh-asr-owner")
                await asyncio.wait_for(queue.join(), 2)
                assert fresh["status"] == "failed"
                assert asr_owners[-1] == reporter.epoch
                saved = records(reporter)
                assert len(saved) == 1
            if saved:
                assert saved[0]["props"]["category"] == "generation_failed"
                assert saved[0]["props"]["stage"] == "transcription"
                assert "private native cleanup detail" not in str(saved)
            assert not batch._processing_job_ids
        finally:
            release.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())
