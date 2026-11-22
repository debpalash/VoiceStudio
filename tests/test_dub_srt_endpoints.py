"""POST /dub/validate-srt and /dub/from-srt (#4219) through the real routers.

Persistence is real (the hermetic test data dir); only the TTS engine and the
task runner are stubbed for the render step.
"""
import asyncio
import json
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

SRT = (b"1\n00:00:02,000 --> 00:00:04,000\nHola\n\n"
       b"2\n00:00:07,000 --> 00:00:09,500\nAdios\n")


@pytest.fixture
def api():
    from api.routers import dub_core, dub_export, dub_generate
    from core.db import init_db

    init_db()
    app = FastAPI()
    for module in (dub_core, dub_generate, dub_export):
        app.include_router(module.router)
    created = []
    # A loopback peer is the admin; Starlette's default "testclient" peer is not.
    admin = TestClient(app, client=("127.0.0.1", 50000))
    other = TestClient(app)
    yield SimpleNamespace(admin=admin, other=other, created=created, core=dub_core, gen=dub_generate)
    from core.config import DUB_DIR
    from services import dub_pipeline

    for job_id in created:
        dub_pipeline._dub_jobs.pop(job_id, None)
        shutil.rmtree(os.path.join(DUB_DIR, job_id), ignore_errors=True)
        admin.delete(f"/dub/history/{job_id}")


def _create(api, data=SRT, lang="es"):
    response = api.admin.post("/dub/from-srt", files={"file": ("a.srt", data)}, data={"source_lang": lang})
    if response.status_code == 200:
        api.created.append(response.json()["job_id"])
    return response


def test_validate_srt_counts_cues_and_creates_nothing(api):
    before = api.admin.get("/dub/history").json()
    response = api.admin.post("/dub/validate-srt", files={"file": ("a.srt", SRT)})
    assert response.status_code == 200
    assert response.json() == {"imported": 2}
    assert api.admin.get("/dub/history").json() == before


@pytest.mark.parametrize("payload", [b"", b"not subtitles at all", b"1\n00:00:xx --> nope\nHi\n"])
def test_validate_srt_rejects_empty_and_malformed_with_400(api, payload):
    response = api.admin.post("/dub/validate-srt", files={"file": ("a.srt", payload)})
    assert response.status_code == 400


def test_validate_srt_413_over_two_megabytes(api):
    big = b"1\n00:00:01,000 --> 00:00:02,000\n" + b"a" * 2_000_001 + b"\n"
    assert api.admin.post("/dub/validate-srt", files={"file": ("a.srt", big)}).status_code == 413
    assert _create(api, big).status_code == 413


@pytest.mark.parametrize("path", ["/dub/validate-srt", "/dub/from-srt"])
def test_non_admin_caller_is_refused(api, path):
    response = api.other.post(path, files={"file": ("a.srt", SRT)}, data={"source_lang": "es"})
    assert response.status_code == 403
    assert not api.admin.get("/dub/history").json() or all(
        row["filename"] != "a.srt" for row in api.admin.get("/dub/history").json()
    )


def test_from_srt_requires_source_language_and_valid_cues(api):
    assert api.admin.post("/dub/from-srt", files={"file": ("a.srt", SRT)}).status_code == 400
    assert _create(api, b"garbage").status_code == 400


def test_from_srt_persists_a_real_job_that_survives_a_restart(api):
    response = _create(api)
    assert response.status_code == 200
    body = response.json()
    job_id = body["job_id"]
    assert body["duration"] == 9.5 and body["source_lang"] == "es"
    assert [s["text"] for s in body["segments"]] == ["Hola", "Adios"]

    from core.config import DUB_DIR
    from core.db import db_conn
    from services import dub_pipeline

    assert os.path.isdir(os.path.join(DUB_DIR, job_id))
    with db_conn() as conn:
        row = conn.execute("SELECT job_data, segments_count FROM dub_history WHERE id=?", (job_id,)).fetchone()
    stored = json.loads(row["job_data"])
    assert row["segments_count"] == 2
    assert stored["subtitle_only"] is True and stored["input_type"] == "audio"
    assert not stored.get("video_path") and not stored.get("audio_path")

    # Drop the in-memory copy: the job must hydrate from SQLite alone.
    dub_pipeline._dub_jobs.pop(job_id, None)
    assert api.admin.get(f"/dub/srt/{job_id}").text.count("-->") == 2
    assert api.admin.get(f"/dub/txt/{job_id}").status_code == 200
    assert any(r["id"] == job_id for r in api.admin.get("/dub/history").json())


@pytest.mark.parametrize("path", [
    "/dub/media/{id}", "/dub/audio/{id}", "/dub/thumb/{id}", "/dub/onsets/{id}",
    "/dub/preview/{id}/0",
])
def test_every_media_read_on_a_subtitle_only_job_is_a_clean_404(api, path):
    job_id = _create(api).json()["job_id"]
    response = api.admin.get(path.format(id=job_id))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("path", ["/dub/download/{id}", "/dub/export-stems/{id}", "/dub/export-segments/{id}",
                                  "/dub/download-audio/{id}", "/dub/download-mp3/{id}"])
def test_exporting_before_any_dub_exists_is_a_clean_400(api, path):
    job_id = _create(api).json()["job_id"]
    assert api.admin.get(path.format(id=job_id)).status_code == 400


def test_transcribing_a_subtitle_only_job_fails_clearly_not_with_a_crash(api):
    job_id = _create(api).json()["job_id"]
    response = api.admin.post(f"/dub/transcribe/{job_id}")
    assert response.status_code < 500, response.text


def test_generate_then_export_is_speech_only_end_to_end(api, monkeypatch):
    job_id = _create(api).json()["job_id"]
    gen = api.gen
    generated = []

    def generate(**kwargs):
        generated.append(kwargs)
        return torch.ones(1, 24000) * 0.1

    backend = SimpleNamespace(sample_rate=24000, generate=generate, applies_own_mastering=True,
                              model_identity=lambda: "stub")

    async def resolve():
        return "omnivoice", backend

    class Tasks:
        def is_cancelled(self, *_):
            return False

        async def add_task(self, task_id, kind, func, *args):
            async for _ in func(*args):
                pass

    monkeypatch.setattr(gen, "_resolve_dub_execution", resolve)
    monkeypatch.setattr(gen, "task_manager", Tasks())
    monkeypatch.setattr(gen, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(gen, "mark_synthetic", lambda a, *args, **kw: a)
    monkeypatch.setattr(gen, "mark_synthetic_async", AsyncMock(side_effect=lambda a, *args, **kw: a))
    monkeypatch.setattr(gen, "get_effect_chain", lambda _: None)
    monkeypatch.setattr(gen, "normalize_audio", lambda a, **kw: a)

    from schemas.requests import DubRequest

    request = DubRequest(
        segments=[dict(start=2, end=4, text="Ola"), dict(start=7, end=9.5, text="Adeus")],
        segment_ids=["0", "1"], language="Portuguese", language_code="pt", num_step=4,
    )
    asyncio.run(gen.dub_generate(job_id, request))
    assert [g["text"] for g in generated] == ["Ola", "Adeus"]
    # No source audio exists, so no clone reference: the engine default voice.
    assert all(g["ref_audio"] is None for g in generated)

    from services import dub_pipeline

    dub_pipeline._dub_jobs.pop(job_id, None)  # reload from SQLite
    assert "pt" in api.admin.get(f"/dub/tracks/{job_id}").json()["tracks"]
    assert api.admin.get(f"/dub/media/{job_id}").status_code == 404

    from api.routers.dub_export import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg unavailable")
    for out_format in ("wav", "m4a"):
        response = api.admin.get(f"/dub/download/{job_id}", params={"out_format": out_format})
        assert response.status_code == 200, response.text
        assert len(response.content) > 1000
    assert api.admin.get(f"/dub/download-audio/{job_id}").status_code == 200


def test_deleting_a_subtitle_only_job_removes_row_and_directory(api):
    job_id = _create(api).json()["job_id"]
    from core.config import DUB_DIR

    assert api.admin.delete(f"/dub/history/{job_id}").status_code == 200
    assert not os.path.exists(os.path.join(DUB_DIR, job_id))
    assert all(row["id"] != job_id for row in api.admin.get("/dub/history").json())
