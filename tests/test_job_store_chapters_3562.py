"""#3562: resume progress counts every finished chapter, past the event cap.

The resume list counted ``chapter`` events in the job's event tail, which is
capped at 500 rows, so a 600-chapter book showed 500 done. Finished chapter
indices are now recorded in the job's metadata with each chapter event.
"""
import json
import os
import uuid

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import job_store
from core.db import init_db


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _job(meta=None) -> str:
    jid = f"lf_{uuid.uuid4().hex[:12]}"
    job_store.create(jid, type="audiobook", meta=meta)
    return jid


def _chapter(jid, index, *, record=True, kind="chapter"):
    payload = json.dumps({"type": kind, "index": index, "total": 999})
    job_store.append_event(jid, payload, completed_chapter=index if record else None)


def test_finished_chapters_outlive_the_event_cap():
    from api.routers.audiobook import _chapters_done

    jid = _job()
    for i in range(600):
        _chapter(jid, i)
    assert len(job_store.events_since(jid, 0, limit=10_000)) == job_store._EVENT_CAP_PER_JOB
    assert job_store.completed_chapters(jid) == set(range(600))
    assert _chapters_done(jid) == 600


def test_duplicates_count_once_and_failures_never_count():
    jid = _job(meta={"title": "Book"})
    _chapter(jid, 3)
    _chapter(jid, 3)
    job_store.append_event(jid, json.dumps({"type": "chapter_error", "index": 4}))
    _chapter(jid, 5)
    assert job_store.completed_chapters(jid) == {3, 5}
    # Other metadata is kept.
    assert json.loads(job_store.get(jid)["meta_json"])["title"] == "Book"


def test_a_job_from_an_older_build_uses_and_then_seeds_from_its_tail():
    jid = _job()
    for i in range(3):
        _chapter(jid, i, record=False)  # written before the durable record existed
    job_store.append_event(jid, "not json")
    job_store.append_event(jid, json.dumps({"type": "chapter", "index": "x"}))
    assert job_store.completed_chapters(jid) == {0, 1, 2}
    _chapter(jid, 7)
    assert job_store.completed_chapters(jid) == {0, 1, 2, 7}


def test_jobs_do_not_share_progress():
    a, b = _job(), _job()
    _chapter(a, 0)
    _chapter(b, 1)
    assert job_store.completed_chapters(a) == {0}
    assert job_store.completed_chapters(b) == {1}
    assert job_store.completed_chapters(f"missing_{uuid.uuid4().hex}") == set()
