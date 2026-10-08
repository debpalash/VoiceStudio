"""POST /dub/cleanup-segments/{job_id} cleans the editor's segments when sent,
so edits made since transcription are not replaced by the stored copy."""
from __future__ import annotations

import pytest


@pytest.fixture
def job(monkeypatch):
    from api.routers import dub_core

    stored = {
        "id": "job-clean",
        "segments": [
            {"id": "0", "start": 0.0, "end": 2.0, "text": "stale transcript line"},
        ],
    }
    saved = {}
    monkeypatch.setattr(dub_core, "_get_job", lambda jid: stored if jid == "job-clean" else None)
    monkeypatch.setattr(dub_core, "_save_job", lambda jid, value: saved.update({jid: value}))
    return dub_core, stored, saved


def test_cleans_the_editor_segments_and_keeps_their_edits(job):
    from schemas.requests import CleanupSegmentsRequest

    module, stored, saved = job
    editor = [
        {"id": "0", "start": 0.0, "end": 2.0, "text": "Edited first line here.", "direction": "calm"},
        {"id": "1", "start": 2.0, "end": 2.2, "text": "ok", "translations": {"es": "vale"}},
        {"id": "2", "start": 2.4, "end": 5.0, "text": "A second full sentence follows."},
    ]
    res = module.dub_cleanup_segments("job-clean", CleanupSegmentsRequest(segments=editor))
    assert res["before"] == 3 and res["after"] < 3
    assert res["segments"][0]["text"].startswith("Edited first line here.")
    assert res["segments"][0]["direction"] == "calm"
    assert "stale" not in " ".join(seg["text"] for seg in res["segments"])
    # An undoable editor edit: the job keeps the segments its audio and
    # subtitle exports were generated from until the next generation.
    assert saved == {}
    assert stored["segments"][0]["text"] == "stale transcript line"


@pytest.mark.parametrize("missing", ["start", "end", "text"])
def test_rejects_editor_segments_missing_required_fields(missing):
    from pydantic import ValidationError
    from schemas.requests import CleanupSegmentsRequest

    segment = {"start": 0.0, "end": 1.0, "text": "line"}
    del segment[missing]
    with pytest.raises(ValidationError):
        CleanupSegmentsRequest(segments=[segment])


def test_without_a_body_cleans_the_stored_segments(job):
    module, stored, saved = job
    res = module.dub_cleanup_segments("job-clean")
    assert res["segments"][0]["text"] == "stale transcript line"
    assert saved["job-clean"]["segments"] == res["segments"]
