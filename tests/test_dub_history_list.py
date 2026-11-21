"""``GET /dub/history`` lists rows without their ``job_data`` blobs.

Each blob holds every segment and path of a job (often megabytes); 30 of them
were serialised and shipped on every history refresh. ``?full=1`` restores the
old shape for anything that needs it.
"""
import json
import os
import uuid

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core.db import db_conn, init_db


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from main import app

    init_db()
    return TestClient(app)


def _seed():
    init_db()
    jid = f"h_{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO dub_history (id, filename, duration, segments_count, language,"
            " language_code, tracks, job_data, content_hash, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (jid, "a.mp4", 1.5, 1, "Spanish", "es", "[]",
             json.dumps({"segments": [{"text": "x" * 500}]}), "h", 9e12),
        )
    return jid


def test_list_omits_job_data_by_default(client):
    jid = _seed()
    rows = client.get("/dub/history").json()
    row = next(r for r in rows if r["id"] == jid)
    assert "job_data" not in row
    assert {"id", "filename", "duration", "language", "language_code", "tracks", "created_at"} <= set(row)


def test_list_full_includes_job_data(client):
    jid = _seed()
    rows = client.get("/dub/history", params={"full": 1}).json()
    row = next(r for r in rows if r["id"] == jid)
    assert json.loads(row["job_data"])["segments"][0]["text"] == "x" * 500


def test_holder_scan_does_not_load_job_data(monkeypatch):
    """The prefix scan runs in SQL: the query must not select the blob."""
    from services import dub_pipeline as dp

    seen = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            seen.append(sql)

            class _R:
                @staticmethod
                def fetchall():
                    return []
            return _R()

    monkeypatch.setattr(dp, "db_conn", lambda: _Conn())
    monkeypatch.setattr(dp, "safe_job_dir", lambda j: "/tmp/dub/" + j)
    assert dp.job_dir_referenced_by_others("abc") == []
    select_list = seen[0].split("FROM")[0]
    assert "job_data" not in select_list
