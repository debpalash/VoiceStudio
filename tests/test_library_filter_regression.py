import json

import pytest


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    db.init_db()


def test_library_survives_newer_non_longform_jobs(isolated_db):
    from core import job_store
    from api.routers.longform_jobs import build_longform_library
    job_store.create("book", type="audiobook")
    job_store.append_event("book", json.dumps({"type": "done", "output": "book.m4b"}))
    job_store.mark_done("book")
    for i in range(205):
        job_store.create(f"dub{i}", type="dub_generate")
        job_store.mark_done(f"dub{i}")
    library = build_longform_library(job_store.list_jobs, job_store.events_since)
    assert [item["job_id"] for item in library] == ["book"]


def test_job_type_filter_precedes_limit_and_composes(isolated_db):
    from core import job_store
    job_store.create("book", type="audiobook", project_id="p")
    job_store.create("story", type="story", project_id="q")
    job_store.create("dub", type="dub_generate", project_id="p")
    assert [r["id"] for r in job_store.list_jobs(status="active", project_id="p",
                                               types=("audiobook", "story"), limit=1)] == ["book"]
    assert job_store.list_jobs(types=()) == []
    assert len(job_store.list_jobs()) == 3
