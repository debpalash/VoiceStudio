"""POST /dub/prosody-mirror/{job_id}: read-only delivery analysis."""
from __future__ import annotations

import os
import shutil
import uuid

import numpy as np
import pytest
import soundfile as sf

os.environ.setdefault("OMNIVOICE_MODEL", "test")


def _tone(f0=150.0, dur=1.0, amp=0.3, am=4.0, sr=16000):
    t = np.arange(int(dur * sr)) / sr
    wave = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 5))
    return (amp * wave * (0.55 + 0.45 * np.sin(2 * np.pi * am * t)) / 2).astype(np.float32)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from core.db import init_db
    from main import app

    init_db()
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def make_job():
    from core.config import DUB_DIR
    from services.dub_pipeline import _dub_jobs

    created = []

    def make(*, vocals=False, mix=True, mix_bytes=None):
        job_id = "pm" + uuid.uuid4().hex[:8]
        job_dir = os.path.join(DUB_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        job = {"segments": [], "duration": 8.0}
        audio = np.concatenate([_tone(am=3), np.zeros(4000, np.float32), _tone(am=3, amp=0.05)] * 4)
        if mix:
            path = os.path.join(job_dir, "audio.wav")
            if mix_bytes is not None:
                with open(path, "wb") as f:
                    f.write(mix_bytes)
            else:
                sf.write(path, audio, 16000)
            job["audio_path"] = path
        if vocals:
            path = os.path.join(job_dir, "vocals.wav")
            sf.write(path, audio, 16000)
            job["vocals_path"] = path
        _dub_jobs[job_id] = job
        created.append((job_id, job_dir))
        return job_id

    yield make
    for job_id, job_dir in created:
        _dub_jobs.pop(job_id, None)
        shutil.rmtree(job_dir, ignore_errors=True)


def _body(n=4):
    return {"segments": [
        {"id": str(i), "start": i * 1.4, "end": i * 1.4 + 1.0, "speaker_id": "A"}
        for i in range(n)
    ]}


def test_unknown_job_is_404(client):
    res = client.post("/dub/prosody-mirror/nosuchjob", json=_body())
    assert res.status_code == 404


def test_bad_job_id_is_400(client):
    assert client.post("/dub/prosody-mirror/bad%20id", json=_body()).status_code == 400


def test_job_without_audio_is_404(client, make_job):
    job_id = make_job(mix=False)
    assert client.post(f"/dub/prosody-mirror/{job_id}", json=_body()).status_code == 404


def test_prefers_vocals(client, make_job):
    job_id = make_job(vocals=True)
    res = client.post(f"/dub/prosody-mirror/{job_id}", json=_body())
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "vocals"
    assert [r["id"] for r in data["segments"]] == ["0", "1", "2", "3"]
    assert all(r["measured"] for r in data["segments"])
    assert set(data["segments"][0]) == {"id", "direction", "tokens", "z", "measured"}


def test_mix_source_when_no_vocals(client, make_job):
    job_id = make_job()
    res = client.post(f"/dub/prosody-mirror/{job_id}", json=_body())
    assert res.status_code == 200 and res.json()["source"] == "mix"


def test_does_not_modify_job(client, make_job):
    from services.dub_pipeline import _dub_jobs

    job_id = make_job()
    before = dict(_dub_jobs[job_id])
    client.post(f"/dub/prosody-mirror/{job_id}", json=_body())
    assert _dub_jobs[job_id] == before


def test_unreadable_audio_is_422(client, make_job):
    job_id = make_job(mix_bytes=b"this is not audio")
    res = client.post(f"/dub/prosody-mirror/{job_id}", json=_body())
    assert res.status_code == 422
    assert "re-import" in res.json()["detail"].lower()


def test_overlapping_segments_over_budget_is_413(client, make_job):
    job_id = make_job()
    segments = [{"id": f"s{i}", "start": 0.0, "end": 8.0} for i in range(30)]
    res = client.post(f"/dub/prosody-mirror/{job_id}", json={"segments": segments})
    assert res.status_code == 413
    assert "overlapping" in res.json()["detail"]


@pytest.mark.parametrize("body", [
    {"segments": []},
    {"segments": [{"id": "", "start": 0, "end": 1}]},
    {"segments": [{"id": "a", "start": 2, "end": 1}]},
    {"segments": [{"id": "a", "start": 1, "end": 1}]},
    {"segments": [{"id": "a", "start": -1, "end": 1}]},
    {"segments": [{"id": "a", "start": 0, "end": 1, "speaker_id": "x" * 129}]},
    {"segments": [{"id": "a", "start": 0, "end": 1}, {"id": "a", "start": 1, "end": 2}]},
    {},
])
def test_request_validation(client, make_job, body):
    job_id = make_job()
    assert client.post(f"/dub/prosody-mirror/{job_id}", json=body).status_code == 422
