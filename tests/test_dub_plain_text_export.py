"""Dubbing plain text uses the selected transcript and never needs TTS output."""
import uuid

import pytest


@pytest.fixture()
def transcript_job():
    from services.dub_pipeline import _dub_jobs
    job_id = uuid.uuid4().hex[:8]
    job = {
        "filename": "interview.mp4",
        "segments": [
            {"id": "a", "start": 0.25, "end": 1.5, "text": "Última traducción", "text_original": "Original & <literal>"},
            {"id": "b", "start": 1.5, "end": 3, "text": "Última línea", "text_original": "Second line"},
        ],
        "segments_i18n": {"fr": {"a": "Bonjour & <monde>", "b": "À bientôt"}},
        "dubbed_tracks": {"fr": {"path": "/nonexistent/fr.wav"}},
    }
    _dub_jobs[job_id] = job
    yield job_id, job
    _dub_jobs.pop(job_id, None)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    from core.db import init_db
    init_db()
    # These route assertions inject saved jobs; no runtime workers are needed.
    instance = TestClient(app, client=("127.0.0.1", 50000))
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("lang,expected", [
    ("fr", "Bonjour & <monde>\nÀ bientôt\n"),
    ("es", "Última traducción\nÚltima línea\n"),
])
def test_txt_get_returns_exact_selected_utf8_without_timestamps(client, transcript_job, lang, expected):
    job_id, job = transcript_job
    before = [dict(row) for row in job["segments"]]
    response = client.get(f"/dub/txt/{job_id}", params={"lang": lang, "dual": True})
    assert response.status_code == 200, response.text
    assert response.content == expected.encode("utf-8")
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert ".txt" in response.headers["content-disposition"]
    assert job["segments"] == before


def test_txt_exports_original_without_a_dub_and_cannot_write_save_path(client, transcript_job, tmp_path):
    job_id, job = transcript_job
    job["dubbed_tracks"] = {}
    job["segments"] = [
        {"id": "a", "start": 0.25, "end": 1.5, "text": "Original & <literal>", "text_original": "Earlier draft"},
        {"id": "b", "start": 1.5, "end": 3, "text": "Second line"},
    ]
    target = tmp_path / "not-authorized.txt"
    response = client.get(f"/dub/txt/{job_id}", params={"lang": "original", "save_path": str(target)})
    assert response.status_code == 200, response.text
    assert response.content == b"Original & <literal>\nSecond line\n"
    assert not target.exists()


def test_txt_requires_a_job_and_nonempty_transcript(client, transcript_job):
    job_id, job = transcript_job
    assert client.get(f"/dub/txt/{uuid.uuid4().hex[:8]}").status_code == 404
    job["segments"] = []
    assert client.get(f"/dub/txt/{job_id}").status_code == 400
    job["segments"] = [{"text": "  "}]
    assert client.get(f"/dub/txt/{job_id}").status_code == 400
