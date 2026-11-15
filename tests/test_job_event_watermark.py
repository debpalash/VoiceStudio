"""Bounded persisted-event snapshots, including pre-existing histories over the current cap."""
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import db, job_store


@pytest.fixture
def events_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "events.db"))
    db.init_db()
    from api.routers.dub_export import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def seed_history(count):
    job_store.create("history", type="dub_generate")
    job_store.mark_running("history")
    # Pre-existing/imported rows can exceed today's append-event retention.
    # Do not claim append_event normally retains more than its current500 rows.
    with db.db_conn() as conn:
        conn.executemany(
            "INSERT INTO job_events(job_id,seq,created_at,payload) VALUES('history',?,?,?)",
            [(i, float(1000 + i), f'data: {{"type":"progress","step":{i}}}\n\n') for i in range(1, count + 1)],
        )


def test_watermark_describes_true_tail_beyond_the_bounded_ascending_page(events_client):
    seed_history(2501)
    result = events_client.get("/jobs/history/events?limit=2000").json()
    assert [row["seq"] for row in result["events"]] == list(range(1, 2001))
    assert result["event_watermark"] == {"seq": 2501, "created_at": 3501.0}
    cursor = events_client.get("/jobs/history/events?after_seq=2500&limit=2000").json()
    assert [row["seq"] for row in cursor["events"]] == [2501]
    assert cursor["event_watermark"] == result["event_watermark"]


def test_current_append_still_prunes_to_500_and_empty_jobs_have_a_zero_watermark(events_client):
    job_store.create("empty", type="dub_generate")
    empty = events_client.get("/jobs/empty/events").json()
    assert empty["events"] == []
    assert empty["event_watermark"] == {"seq": 0, "created_at": None}
    seed_history(2501)
    assert job_store.append_event("history", 'data: {"type":"error"}\n\n') == 2502
    page = events_client.get("/jobs/history/events?limit=2000").json()
    assert len(page["events"]) == 500
    assert page["events"][-1]["seq"] == page["event_watermark"]["seq"] == 2502
    assert events_client.get("/jobs/missing/events").status_code == 404


def test_watermark_and_page_use_one_read_snapshot_while_another_writer_appends(events_client, monkeypatch):
    seed_history(2)
    original = job_store.db_conn
    inserted = False

    @contextmanager
    def concurrent_append():
        with original() as conn:
            def observe(sql):
                nonlocal inserted
                if not inserted and "SELECT" in sql.upper() and "FROM job_events" in sql:
                    inserted = True
                    with original() as writer:
                        writer.execute("INSERT INTO job_events(job_id,seq,created_at,payload) VALUES('history',3,1003,'fresh')")
            conn.set_trace_callback(observe)
            yield conn
    monkeypatch.setattr(job_store, "db_conn", concurrent_append)
    page = events_client.get("/jobs/history/events?limit=2000").json()
    assert inserted
    assert page["event_watermark"] == {"seq": 2, "created_at": 1002.0}
    assert [row["seq"] for row in page["events"]] == [1, 2]
    assert [row["seq"] for row in job_store.events_since("history")] == [1, 2, 3]
