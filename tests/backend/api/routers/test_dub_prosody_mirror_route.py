"""POST /dub/prosody-mirror/{job_id}: reads the job's vocals, returns
per-segment directions in request order, and never mutates the job."""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

SR = 44100


def _line(f0=120.0, amp=0.1, syllables_hz=4.0, vibrato=0.0, dur_s=2.0):
    t = np.arange(int(dur_s * SR)) / SR
    freq = f0 * (1 + vibrato * np.sin(2 * np.pi * 1.5 * t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    carrier = sum(np.sin(k * phase) / k for k in range(1, 6))
    envelope = 0.5 * (1 - np.cos(2 * np.pi * syllables_hz * t))
    return (amp * carrier * envelope).astype(np.float32)


# ── Route ──────────────────────────────────────────────────────────────────

@pytest.fixture
def job_env(tmp_path, monkeypatch):
    from api.routers import dub_export

    job_id = "job-prosody"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    job = {"id": job_id}
    monkeypatch.setattr(dub_export, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dub_export, "_get_job", lambda jid: job if jid == job_id else None)
    return {"job_id": job_id, "job_dir": job_dir, "job": job, "module": dub_export}


def _request(n=5, **extra):
    from schemas.requests import ProsodyMirrorRequest

    segments = [{"id": f"s{i}", "start": 2.0 * i, "end": 2.0 * i + 2.0, "speaker_id": "A"} for i in range(n)]
    return ProsodyMirrorRequest(segments=segments, **extra)


def _call(env, job_id=None, req=None):
    return asyncio.run(env["module"].dub_prosody_mirror(job_id or env["job_id"], req or _request()))


def test_route_404_without_job_or_audio(job_env):
    from fastapi import HTTPException

    for job_id in ("nope", job_env["job_id"]):
        with pytest.raises(HTTPException) as exc:
            _call(job_env, job_id=job_id)
        assert exc.value.status_code == 404


def test_route_prefers_vocals(job_env):
    vocals, mix = job_env["job_dir"] / "vocals.wav", job_env["job_dir"] / "audio.wav"
    parts = [_line(amp=0.1)] * 4 + [_line(amp=0.4, f0=170.0, syllables_hz=7.0, vibrato=0.1)]
    sf.write(str(vocals), np.concatenate(parts), SR)
    sf.write(str(mix), np.zeros(SR * 10, dtype=np.float32), SR)
    job_env["job"].update(vocals_path=str(vocals), audio_path=str(mix))

    res = _call(job_env)
    assert res["source"] == "vocals"
    assert [s["id"] for s in res["segments"]] == [f"s{i}" for i in range(5)]
    assert res["segments"][4]["direction"] == "urgent, quick"
    assert res["segments"][0]["direction"] == ""


def test_route_reports_mix_when_separation_did_not_run(job_env):
    mix = job_env["job_dir"] / "audio.wav"
    sf.write(str(mix), np.concatenate([_line()] * 5), SR)
    job_env["job"].update(vocals_path=str(mix), audio_path=str(mix))
    assert _call(job_env)["source"] == "mix"


def test_route_unreadable_audio_is_422(job_env):
    from fastapi import HTTPException

    broken = job_env["job_dir"] / "vocals.wav"
    broken.write_bytes(b"not audio")
    job_env["job"]["vocals_path"] = str(broken)
    with pytest.raises(HTTPException) as exc:
        _call(job_env)
    assert exc.value.status_code == 422


def test_request_rejects_duplicate_ids_and_bad_times():
    from schemas.requests import ProsodyMirrorRequest

    with pytest.raises(ValidationError):
        ProsodyMirrorRequest(segments=[{"id": "a", "start": 0, "end": 1}] * 2)
    with pytest.raises(ValidationError):
        ProsodyMirrorRequest(segments=[{"id": "a", "start": float("nan"), "end": 1}])
    with pytest.raises(ValidationError):
        ProsodyMirrorRequest(segments=[])
    for start, end in ((1.0, 1.0), (2.0, 1.0)):
        with pytest.raises(ValidationError):
            ProsodyMirrorRequest(segments=[{"id": "a", "start": start, "end": end}])


def test_route_refuses_overlapping_work_beyond_the_track(job_env):
    """20k unique ids over the same minute must not buy hours of analysis."""
    from fastapi import HTTPException
    from schemas.requests import ProsodyMirrorRequest

    vocals = job_env["job_dir"] / "vocals.wav"
    sf.write(str(vocals), np.concatenate([_line()] * 5), SR)
    job_env["job"]["vocals_path"] = str(vocals)
    req = ProsodyMirrorRequest(
        segments=[{"id": str(i), "start": 0.0, "end": 10.0} for i in range(20)]
    )
    with pytest.raises(HTTPException) as exc:
        _call(job_env, req=req)
    assert exc.value.status_code == 413
