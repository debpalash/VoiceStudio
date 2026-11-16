"""Operation ownership and expected outcomes at real batch/dub boundaries."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter, records  # noqa: F401
from test_dub_crash_resume import dub  # noqa: F401


@pytest.mark.parametrize("transition", ["off_to_on", "regrant", "same", "withdraw", "cancel"])
def test_argos_descendant_retains_parent_operation_grant(reporter, monkeypatch, transition):
    from test_dub_translate import _install_fake_argos
    from core import diagnostic_reports as reports
    translation = importlib.import_module("api.routers.dub_translate")
    _install_fake_argos(monkeypatch)
    if transition != "off_to_on":
        reporter.consent(True)
    owner = reports.observe_grant()
    leaf_calls = []

    def fail(*_args):
        leaf_calls.append(reports.inherited_grant())
        raise RuntimeError("synthetic Argos failure")

    monkeypatch.setattr(importlib.import_module("argostranslate.translate"), "translate", fail)
    request = translation.TranslateRequest(
        provider="argos", source_lang="en", target_lang="es", quality="fast",
        segments=[{"id": "fixture", "text": "synthetic fixture"}],
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(translation, "_cpu_pool", pool)
        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            async def parent():
                with reports.grant_scope(owner):
                    entered.set()
                    await release.wait()
                    return await translation.dub_translate(request)
            operation = asyncio.create_task(parent())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if transition in {"off_to_on", "regrant"}:
                    reporter.consent(False)
                    reporter.consent(True)
                elif transition == "withdraw":
                    reporter.consent(False)
                elif transition == "cancel":
                    operation.cancel()
                release.set()
                if transition == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await operation
                else:
                    result = await asyncio.wait_for(operation, 2)
                    assert result["translated"][0]["error"]
                assert len(records(reporter)) == int(transition == "same")
                if transition != "cancel":
                    assert leaf_calls == [owner]
                if transition in {"off_to_on", "regrant"}:
                    # A new standalone request, unlike a descendant, may use
                    # the current grant after the earlier operation returns.
                    result = await translation.dub_translate(request)
                    assert result["translated"][0]["error"]
                    assert leaf_calls[-1] == reporter.epoch
                    assert len(records(reporter)) == 1
            finally:
                release.set()
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        asyncio.run(run())


def test_batch_worker_preserves_real_empty_asr_failure(reporter, monkeypatch, tmp_path):
    batch = importlib.import_module("api.routers.batch")
    asr = importlib.import_module("services.asr_backend")
    ffmpeg = importlib.import_module("services.ffmpeg_utils")
    reporter.consent(True)
    monkeypatch.setattr(batch, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(batch, "_jobs", {})
    monkeypatch.setattr(batch, "_processing_job_ids", set())
    monkeypatch.setattr(ffmpeg, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg, "validate_media_source", lambda *_: None)
    monkeypatch.setattr(ffmpeg, "require_audio_stream", lambda *_: None)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        stderr=b"Duration: 00:00:02.00", stdout=b"",
    ))
    calls = []
    async def empty_asr(*_args, **_kwargs):
        calls.append(True)
        return [], "en"
    monkeypatch.setattr(asr, "run_transcribe_guarded", empty_asr)
    async def run():
        queue = asyncio.Queue()
        monkeypatch.setattr(batch, "_queue", queue)
        job = {"status": "queued", "filename": "fixture.mp4",
               "video_path": str(tmp_path / "fixture.mp4"), "langs": ["en"]}
        batch._jobs["empty-asr"] = job
        queue.put_nowait("empty-asr")
        worker = asyncio.create_task(batch._worker())
        try:
            await asyncio.wait_for(queue.join(), 2)
            assert calls == [True]
            assert job["status"] == "failed"
            assert job["error"] == "Transcription produced no segments"
            assert not job.get("outputs")
            assert job["finished_at"] >= job["started_at"]
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(run())
    assert len(records(reporter)) == 1


def test_journal_keeps_local_entry_when_diagnostic_exception_inspection_raises(reporter, monkeypatch, tmp_path):
    from starlette.exceptions import HTTPException
    from core import error_journal
    class BrokenStatusError(HTTPException):
        @property
        def status_code(self):
            raise RuntimeError("unreadable exception metadata")
        @status_code.setter
        def status_code(self, value):
            pass
        def __str__(self):
            return "synthetic journal failure"
    monkeypatch.setattr(error_journal, "JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(error_journal, "_entries", OrderedDict())
    reporter.consent(True)
    entry = error_journal.record(BrokenStatusError(500), route="/api/generate")
    assert entry["type"] == "BrokenStatusError"
    assert entry in error_journal.recent()
    saved = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert saved == [entry]


@pytest.mark.parametrize("failure_kind", ["engine", "licence", "wrapped_licence"])
def test_actual_dub_stream_licence_refusal_is_local_not_generic_failure(reporter, dub, monkeypatch, failure_kind):
    from core import tasks
    from services.model_acceptance import ModelLicenceNotAccepted
    reporter.consent(True)
    for name in ("create", "mark_running", "mark_failed", "mark_done", "mark_cancelled"):
        monkeypatch.setattr(tasks.job_store, name, lambda *_a, **_kw: None)
    monkeypatch.setattr(tasks.job_store, "append_event", lambda *_a, **_kw: 1)
    monkeypatch.setattr(tasks.run_sentinel, "touch_activity", lambda *_a, **_kw: None)
    resolve = dub.dg._resolve_dub_execution
    async def failing_backend():
        engine, backend = await resolve()
        def generate(**_kwargs):
            if failure_kind != "engine":
                try:
                    raise ModelLicenceNotAccepted([{"repo_id": "fixture/model"}])
                except ModelLicenceNotAccepted as error:
                    if failure_kind == "wrapped_licence":
                        raise RuntimeError("synthetic engine wrapper") from error
                    raise
            raise RuntimeError("synthetic engine failure")
        backend.generate = generate
        return engine, backend
    monkeypatch.setattr(dub.dg, "_resolve_dub_execution", failing_backend)
    async def run():
        manager = tasks.TaskManager()
        monkeypatch.setattr(dub.dg, "task_manager", manager)
        await dub.run_async()
        assert len(manager.active_tasks) == 1
        task = next(iter(manager.active_tasks.values()))
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 3)
            assert task["status"] == "failed"
            frames = [json.loads(value[6:]) for value in task["history"] if value.startswith("data: ")]
            error = next(frame for frame in frames if frame["type"] == "error")
            return error
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    error = asyncio.run(run())
    assert len(records(reporter)) == int(failure_kind == "engine")
    if failure_kind != "engine":
        assert error["error_code"] == "model_licence_required"
