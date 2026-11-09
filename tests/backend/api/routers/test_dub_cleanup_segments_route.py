"""POST /dub/cleanup-segments/{job_id} cleans the editor's segments when sent."""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")

STORED = [
    {"id": "0", "start": 0.0, "end": 2.0, "text": "Stored one.", "text_original": "Stored one."},
    {"id": "1", "start": 5.0, "end": 7.0, "text": "Stored two.", "text_original": "Stored two."},
]


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from core.db import init_db
    from main import app

    init_db()
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def job_id():
    from services.dub_pipeline import _dub_jobs

    jid = "cl" + uuid.uuid4().hex[:8]
    _dub_jobs[jid] = {"segments": [dict(s) for s in STORED], "duration": 10.0}
    yield jid
    _dub_jobs.pop(jid, None)


def test_editor_segments_are_cleaned_with_their_edits(client, job_id):
    from services.dub_pipeline import _dub_jobs

    edited = [
        {"id": "0", "start": 0.0, "end": 2.0, "text": "Edited in the editor.",
         "text_original": "x", "direction": "urgent"},
        {"id": "1", "start": 6.0, "end": 8.0, "text": "Second line.", "text_original": "y"},
    ]
    res = client.post(f"/dub/cleanup-segments/{job_id}", json={"segments": edited})
    assert res.status_code == 200
    data = res.json()
    assert data["before"] == 2
    assert data["after"] == len(data["segments"])
    assert data["segments"][0]["text"] == "Edited in the editor."
    assert data["segments"][0]["direction"] == "urgent"
    # Returned unsaved: the stored copy is untouched.
    assert [s["text"] for s in _dub_jobs[job_id]["segments"]] == ["Stored one.", "Stored two."]


@pytest.mark.parametrize("missing", ["start", "end", "text"])
def test_segments_missing_required_fields_are_rejected(client, job_id, missing):
    seg = {"id": "0", "start": 0.0, "end": 1.0, "text": "hello"}
    del seg[missing]
    res = client.post(f"/dub/cleanup-segments/{job_id}", json={"segments": [seg]})
    assert res.status_code == 422


def test_no_body_cleans_stored_segments(client, job_id):
    res = client.post(f"/dub/cleanup-segments/{job_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["before"] == 2
    assert data["segments"][0]["text"].startswith("Stored one.")
    assert data["after"] == len(data["segments"]) <= 2


def test_unknown_job_is_404(client):
    assert client.post("/dub/cleanup-segments/nosuchjob").status_code == 404
