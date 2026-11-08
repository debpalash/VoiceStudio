"""Dub QC scores each line in the window where the dubbed track plays it.

Smart Fit and Stretch Video lay the dubbed audio out on a fitted timeline:
once a line slows the video, every later line starts later in the track than
in the source. The recognized speech is timed on that track, so scoring it
against the original segment times paired lines with their neighbours'
speech and flagged clean lines as drifted.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")

TEXTS = {
    "a": "wir sehen uns morgen früh am bahnhof",
    "b": "vergiss bitte deine fahrkarte nicht",
    "c": "der zug fährt pünktlich um acht uhr ab",
}
# Source timeline (seconds).
ORIGINAL = {"a": (0.0, 2.0), "b": (2.2, 4.0), "c": (4.2, 6.0)}
# Where a Smart Fit render placed each line (plan_fit for German speech that
# runs ~1.5x the source slots: every line is slowed, later ones start later).
FITTED = {"a": (0.0, 2.7037), "b": (2.7537, 5.1724), "c": (5.2224, 7.9353)}


@pytest.fixture
def qc_env(tmp_path, monkeypatch):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline

    job_id = "job-qc-fit"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    (job_dir / "dubbed_de.wav").write_bytes(b"RIFF")
    job = {
        "id": job_id,
        "dubbed_tracks": {"de": {"path": str(job_dir / "dubbed_de.wav")}},
        "segments": [
            {"id": key, "start": start, "end": end, "text": TEXTS[key]}
            for key, (start, end) in ORIGINAL.items()
        ],
    }
    heard: list[dict] = []

    async def _guarded(_pool, fn, **_kw):
        return fn()

    class _Backend:
        id = "fake-asr"

        def transcribe(self, path, **_kw):
            return {"segments": heard}

    monkeypatch.setattr(dub_export, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dub_export, "_get_job", lambda jid: job if jid == job_id else None)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", _guarded)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", lambda: _Backend())
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)
    import services.model_manager as model_manager
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    return {"module": dub_export, "job_id": job_id, "job": job, "heard": heard}


def _hear_at(env, placement):
    env["heard"][:] = [
        {"start": start, "end": end, "text": TEXTS[key]} for key, (start, end) in placement.items()
    ]


def _qc(env):
    return asyncio.run(env["module"].dub_qc_pass(env["job_id"], lang="de", drift_threshold=0.5))


def test_smart_fit_track_is_scored_on_its_fitted_cues(qc_env):
    job = qc_env["job"]
    job["dubbed_tracks"]["de"]["timing_strategy"] = "smart_fit"
    job["fit_plans"] = {"de": {
        "plan": [{"orig_start": 0.0, "orig_end": 2.15, "new_start": 0.0, "new_end": 2.7037}],
        "fitted_segments": [
            {"id": key, "start": start, "end": end} for key, (start, end) in FITTED.items()
        ],
    }}
    _hear_at(qc_env, FITTED)

    res = _qc(qc_env)

    assert res["flagged_count"] == 0
    assert [s["recognized_text"] for s in res["segments"]] == list(TEXTS.values())
    # Annotations only: the stored source timing is untouched.
    assert [(s["start"], s["end"]) for s in job["segments"]] == list(ORIGINAL.values())


def test_stretch_video_track_is_scored_on_its_stretched_timeline(qc_env):
    job = qc_env["job"]
    job["timing_strategy"] = "stretch_video"
    stretched = {"a": (0.0, 2.7), "b": (2.9, 5.4), "c": (5.6, 8.4)}
    job["video_stretch_plans"] = {"de": {"plan": [
        {"orig_start": ORIGINAL[key][0], "orig_end": ORIGINAL[key][1],
         "new_start": start, "new_end": end}
        for key, (start, end) in stretched.items()
    ]}}
    _hear_at(qc_env, stretched)

    res = _qc(qc_env)

    assert res["flagged_count"] == 0
    assert [s["recognized_text"] for s in res["segments"]] == list(TEXTS.values())


def test_unfitted_track_keeps_original_windows(qc_env):
    # Strict Slot / Concise renders play every line at its source time.
    _hear_at(qc_env, ORIGINAL)
    assert _qc(qc_env)["flagged_count"] == 0

    # A stale Smart Fit plan from an earlier render must not move the windows
    # of a track that was regenerated under another strategy.
    qc_env["job"]["fit_plans"] = {"de": {"plan": [], "fitted_segments": [
        {"id": key, "start": start, "end": end} for key, (start, end) in FITTED.items()
    ]}}
    assert _qc(qc_env)["flagged_count"] == 0
