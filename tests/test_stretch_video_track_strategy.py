"""A Stretch Video track keeps its retime after another language is dubbed.

``job["timing_strategy"]`` only records the most recent generate. Dubbing
German with Stretch Video and then Spanish with Strict Slot used to drop the
German retime: its preview and MP4 played the stretched dub over the
unstretched video, and its subtitles kept the source times. Each track
records the strategy it was rendered with, and that is what decides.
"""
from __future__ import annotations

import uuid

import pytest

# German was rendered with Stretch Video: both lines run long, so the video
# slows and the second line starts at 3.0 s instead of 2.5 s.
_DE_PLAN = [
    {"orig_start": 0.0, "orig_end": 2.0, "new_start": 0.0, "new_end": 2.5, "stretch_ratio": 1.25},
    {"orig_start": 2.5, "orig_end": 4.0, "new_start": 3.0, "new_end": 5.0, "stretch_ratio": 1.3333},
]


def _job(*, last_strategy="strict_slot", de_strategy="stretch_video"):
    de_track = {"path": "/nonexistent/dubbed_de.wav", "language_code": "de", "duration": 5.0}
    if de_strategy is not None:
        de_track["timing_strategy"] = de_strategy
    return {
        "duration": 4.0,
        "filename": "clip.mp4",
        # Spanish was generated last, with Strict Slot.
        "timing_strategy": last_strategy,
        "segments": [
            {"id": "a", "start": 0.0, "end": 2.0, "text": "Guten Morgen zusammen"},
            {"id": "b", "start": 2.5, "end": 4.0, "text": "Bis bald"},
        ],
        "dubbed_tracks": {
            "de": de_track,
            "es": {"path": "/nonexistent/dubbed_es.wav", "language_code": "es",
                   "duration": 4.0, "timing_strategy": "strict_slot"},
        },
        "video_stretch_plans": {"de": {"plan": [dict(p) for p in _DE_PLAN],
                                       "total_duration": 5.0, "orig_duration": 4.0}},
    }


def test_stretch_track_keeps_its_retime_after_a_later_strict_slot_track():
    from api.routers.dub_export import _video_retime_plan_for

    job = _job()
    # Preview and MP4 export retime the video from this resolution.
    kind, entry = _video_retime_plan_for(job, "de")
    assert kind == "stretch_video"
    assert entry is job["video_stretch_plans"]["de"]
    assert _video_retime_plan_for(job, "es") is None


def test_stale_stretch_plan_ignored_after_track_regenerated():
    from api.routers.dub_export import _video_retime_plan_for

    # German was re-dubbed with Strict Slot, then French with Stretch Video:
    # the job-level strategy is stretch_video, but German's plan is stale.
    job = _job(last_strategy="stretch_video", de_strategy="strict_slot")
    assert _video_retime_plan_for(job, "de") is None


def test_track_without_recorded_strategy_uses_job_strategy():
    from api.routers.dub_export import _video_retime_plan_for

    assert _video_retime_plan_for(_job(last_strategy="stretch_video", de_strategy=None), "de")[0] == "stretch_video"
    assert _video_retime_plan_for(_job(last_strategy="strict_slot", de_strategy=None), "de") is None


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def seeded_job():
    from services.dub_pipeline import _dub_jobs
    job_id = f"svt_{uuid.uuid4().hex[:8]}"
    _dub_jobs[job_id] = _job()
    yield job_id
    _dub_jobs.pop(job_id, None)


def test_stretch_track_subtitles_follow_its_stretched_timeline(client, seeded_job):
    srt = client.get(f"/dub/srt/{seeded_job}", params={"lang": "de"})
    assert srt.status_code == 200
    assert "00:00:00,000 --> 00:00:02,500" in srt.text
    assert "00:00:03,000 --> 00:00:05,000" in srt.text

    # The Strict Slot track keeps the source times.
    srt = client.get(f"/dub/srt/{seeded_job}", params={"lang": "es"})
    assert "00:00:02,500 --> 00:00:04,000" in srt.text
