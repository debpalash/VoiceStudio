from __future__ import annotations

import asyncio
import io

from fastapi import UploadFile


def test_srt_cues_inherit_best_overlap_voice_metadata():
    from api.routers.dub_core import _carry_srt_voice_metadata

    existing = [
        {
            "id": "left",
            "start": 0.0,
            "end": 2.0,
            "text": "old one",
            "speaker_id": "Speaker 1",
            "profile_id": "auto:speaker_1",
            "speed": 1.1,
            "translations": {"fr": "stale"},
        },
        {
            "id": "right",
            "start": 2.0,
            "end": 5.0,
            "text": "old two",
            "speaker_id": "Speaker 2",
            "profile_id": "auto-seg:right",
            "effect_preset": "radio",
        },
    ]
    cues = [
        {"id": 0, "start": 0.2, "end": 1.8, "text": "new one", "speaker_id": "Speaker 1"},
        {"id": 1, "start": 1.8, "end": 4.8, "text": "new two", "speaker_id": "Speaker 1"},
    ]
    clone = {"ref_audio": "/job/right.wav", "duration": 2.8}

    merged, clones = _carry_srt_voice_metadata(
        cues,
        existing,
        {"right": clone},
    )

    assert merged[0]["speaker_id"] == "Speaker 1"
    assert merged[0]["profile_id"] == "auto:speaker_1"
    assert merged[0]["speed"] == 1.1
    assert "translations" not in merged[0]
    assert merged[1]["speaker_id"] == "Speaker 2"
    assert merged[1]["profile_id"] == "auto-seg:1"
    assert merged[1]["effect_preset"] == "radio"
    assert merged[1]["text_original"] == "new two"
    assert clones["1"] is clone


def test_unmatched_replacement_cue_does_not_inherit_colliding_old_clone():
    from api.routers.dub_core import _carry_srt_voice_metadata

    clone = {"ref_audio": "/job/unrelated.wav", "duration": 1.0}
    merged, clones = _carry_srt_voice_metadata(
        [{"id": 0, "start": 10.0, "end": 11.0, "text": "new"}],
        [{"id": "0", "start": 0.0, "end": 1.0, "text": "removed"}],
        {"0": clone},
    )

    assert merged[0]["speaker_id"] == "Speaker 1"
    assert clones == {}


def test_import_srt_rekeys_clone_refs_and_rebuilds_cast(monkeypatch):
    from api.routers import dub_core

    job_id = "srt-cast"
    clone = {
        "ref_audio": "/job/speaker-two.wav",
        "ref_text": "source",
        "duration": 3.0,
    }
    job = {
        "duration": 6.0,
        "segments": [
            {
                "id": "old-1",
                "start": 0.0,
                "end": 2.0,
                "text": "old one",
                "speaker_id": "Speaker 1",
                "profile_id": "auto:speaker_1",
            },
            {
                "id": "old-2",
                "start": 2.0,
                "end": 5.0,
                "text": "old two",
                "speaker_id": "Speaker 2",
                "profile_id": "auto:speaker_2",
            },
        ],
        "segment_clones": {"old-2": clone},
    }
    dub_core._dub_jobs[job_id] = job
    monkeypatch.setattr(dub_core, "_save_job", lambda *_args: None)
    upload = UploadFile(
        filename="replacement.srt",
        file=io.BytesIO(
            b"1\n00:00:00,000 --> 00:00:02,000\nFirst line\n\n"
            b"2\n00:00:02,000 --> 00:00:05,000\nSecond line\n"
        ),
    )
    try:
        result = asyncio.run(dub_core.dub_import_srt(job_id, upload))
    finally:
        dub_core._dub_jobs.pop(job_id, None)

    assert [segment["speaker_id"] for segment in result["segments"]] == [
        "Speaker 1",
        "Speaker 2",
    ]
    assert [segment["profile_id"] for segment in result["segments"]] == [
        "auto:speaker_1",
        "auto:speaker_2",
    ]
    assert job["segment_clones"]["1"] is clone
    assert job["cast_sources"]["Speaker 2"]["kind"] == "segment"


def test_import_srt_clears_stale_clone_and_cast_maps(monkeypatch):
    from api.routers import dub_core

    job_id = "srt-clear-cast"
    job = {
        "duration": 20.0,
        "segments": [{"id": "0", "start": 0.0, "end": 1.0, "text": "old"}],
        "segment_clones": {"0": {"ref_audio": "/job/unrelated.wav"}},
        "speaker_clones": {"Speaker 1": {"ref_audio": "/job/stale-speaker.wav"}},
        "cast_sources": {"Speaker 1": {"kind": "segment", "ref_audio": "/job/unrelated.wav"}},
    }
    dub_core._dub_jobs[job_id] = job
    monkeypatch.setattr(dub_core, "_save_job", lambda *_args: None)
    upload = UploadFile(
        filename="replacement.srt",
        file=io.BytesIO(b"1\n00:00:10,000 --> 00:00:11,000\nNew line\n"),
    )
    try:
        asyncio.run(dub_core.dub_import_srt(job_id, upload))
    finally:
        dub_core._dub_jobs.pop(job_id, None)

    assert job["segment_clones"] == {}
    assert job["speaker_clones"] == {}
    assert "cast_sources" not in job


def test_import_srt_scopes_matched_speaker_clone_to_the_matched_cue(monkeypatch):
    from api.routers import dub_core

    job_id = "srt-scope-speaker-clone"
    speaker_clone = {"ref_audio": "/job/speaker-one.wav", "duration": 2.0}
    job = {
        "duration": 20.0,
        "segments": [
            {
                "id": "old",
                "start": 0.0,
                "end": 2.0,
                "text": "old",
                "speaker_id": "Speaker 1",
                "profile_id": "auto:speaker_1",
            }
        ],
        "speaker_clones": {"Speaker 1": speaker_clone},
        "cast_sources": {"Speaker 1": {"kind": "speaker"}},
    }
    dub_core._dub_jobs[job_id] = job
    monkeypatch.setattr(dub_core, "_save_job", lambda *_args: None)
    upload = UploadFile(
        filename="replacement.srt",
        file=io.BytesIO(
            b"1\n00:00:00,000 --> 00:00:02,000\nMatched\n\n"
            b"2\n00:00:10,000 --> 00:00:11,000\nUnmatched\n"
        ),
    )
    try:
        asyncio.run(dub_core.dub_import_srt(job_id, upload))
    finally:
        dub_core._dub_jobs.pop(job_id, None)

    assert job["speaker_clones"] == {}
    assert job["segment_clones"] == {"0": speaker_clone}
    assert "1" not in job["segment_clones"]
    assert job["cast_sources"]["Speaker 1"]["kind"] == "segment"


def test_import_does_not_restore_job_deleted_during_upload(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from api.routers import dub_core

    job = {'duration': 2, 'segments': []}
    current = [job]
    saved = []
    monkeypatch.setattr(dub_core, '_get_job', lambda _: current[0])
    monkeypatch.setattr(dub_core, '_save_job', lambda *args: saved.append(args))
    class Upload:
        async def read(self):
            current[0] = None
            return b'1\n00:00:00,000 --> 00:00:01,000\nNew text\n'
    with pytest.raises(HTTPException) as error:
        asyncio.run(dub_core.dub_import_srt('deleted', Upload()))
    assert error.value.status_code == 404
    assert job['segments'] == []
    assert saved == []
