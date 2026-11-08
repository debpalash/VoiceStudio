"""API tests for durable longform resume — GET /audiobook/jobs + the resume
404 paths. The resume *happy path* drives real synthesis (no model in CI), so it
is covered by the manifest round-trip (tests/test_longform_resume.py) + the
existing render tests, not here.

Mounts only the audiobook router (no ``main`` import) so it runs locally
without the main+torch segfault; conftest.py provides the hermetic data dir.
"""
from __future__ import annotations

import json

import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before this module imports the REAL core.config (the old
# sys.modules stub leaked at collection time and broke mixed runs).
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import job_store  # noqa: E402
from core.db import init_db  # noqa: E402
from services import longform_resume as lr  # noqa: E402
from api.routers import audiobook as ab  # noqa: E402

init_db()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(ab.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def _seed(job_id, job_type, status, *, manifest=True, chapters_done=0, total=3):
    job_store.create(job_id, type=job_type)
    if status == "running":
        job_store.mark_running(job_id)
    elif status == "failed":
        job_store.mark_failed(job_id, "boom")
    elif status == "done":
        job_store.mark_done(job_id)
    for i in range(chapters_done):
        job_store.append_event(job_id, json.dumps({"type": "chapter", "index": i}))
    if manifest:
        lr.write_manifest(lr.build_manifest(
            job_id=job_id, job_type=job_type, title=f"T-{job_id}",
            plan_chapters=[{"title": f"C{i}", "spans": [
                {"voice_id": "v", "text": "x", "pause_ms_after": 0, "speed": None}]}
                for i in range(total)],
            params={"default_voice": "v", "fmt": "m4b"}))


def test_jobs_lists_interrupted_with_progress(client):
    _seed("run1", "audiobook", "running", chapters_done=2, total=5)
    jobs = client.get("/audiobook/jobs").json()["jobs"]
    j = next(x for x in jobs if x["job_id"] == "run1")
    assert j["type"] == "audiobook" and j["status"] == "running"
    assert j["title"] == "T-run1"
    assert j["total_chapters"] == 5 and j["chapters_done"] == 2


def test_jobs_includes_failed_with_manifest(client):
    _seed("fail1", "story", "failed", total=2)
    ids = [x["job_id"] for x in client.get("/audiobook/jobs").json()["jobs"]]
    assert "fail1" in ids


def test_jobs_excludes_done_and_manifestless(client):
    _seed("done1", "audiobook", "done", manifest=True)   # done → manifest cleared in prod; force-clear
    lr.clear_manifest("audiobook", "done1")
    _seed("run_nomani", "audiobook", "running", manifest=False)
    ids = [x["job_id"] for x in client.get("/audiobook/jobs").json()["jobs"]]
    assert "done1" not in ids        # completed jobs aren't resumable
    assert "run_nomani" not in ids   # no manifest → can't resume


def test_jobs_excludes_non_longform_types(client):
    _seed("dub1", "dub", "running")   # a dub job, even with a stray manifest dir
    ids = [x["job_id"] for x in client.get("/audiobook/jobs").json()["jobs"]]
    assert "dub1" not in ids


def test_resume_unknown_id_404(client):
    assert client.post("/audiobook/resume/does-not-exist").status_code == 404


def test_resume_job_without_manifest_404(client):
    _seed("nomani2", "audiobook", "running", manifest=False)
    assert client.post("/audiobook/resume/nomani2").status_code == 404
