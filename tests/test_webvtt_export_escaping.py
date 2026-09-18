"""Exported WebVTT keeps every cue's text on screen.

In WebVTT cue text a `<` that does not open a tag still starts one, so a
player drops the rest of the cue (Chromium shows "I <3 you" as "I "), and a
line containing `-->` ends the cue, emptying it. Both WebVTT writers, the dub
subtitle download and the OpenAI-compatible transcription's ``vtt`` format,
wrote segment text verbatim. Cue markup (the dual layout's ``<i>``, tags
imported with a subtitle file) must stay markup.
"""
from __future__ import annotations

import asyncio
import html
import io
import os
import re
import uuid

import pytest
from fastapi import UploadFile

os.environ.setdefault("OMNIVOICE_MODEL", "test")

_TEXT = "I <3 you & a < b --> c"
_ESCAPED = "I &lt;3 you &amp; a &lt; b --&gt; c"
_MARKUP = '<i>Hi</i> <c.yellow>there</c> <v Roger>you</v> <font color="red">x</font> <00:00:01.500>y'


def _vtt_job(segments):
    from services.dub_pipeline import _dub_jobs

    job_id = str(uuid.uuid4())[:8]
    _dub_jobs[job_id] = {
        "video_path": "/nonexistent/original.mp4",
        "duration": 10.0,
        "filename": "clip.mp4",
        "segments": segments,
    }
    return job_id


@pytest.fixture()
def dub_job():
    from services.dub_pipeline import _dub_jobs

    job_id = _vtt_job([
        {"id": 0, "start": 1.0, "end": 3.0, "text": _TEXT, "text_original": "Tom & Jerry <3"},
        {"id": 1, "start": 3.0, "end": 5.0, "text": _MARKUP},
    ])
    yield job_id
    _dub_jobs.pop(job_id, None)


def _cues(vtt: str) -> list[str]:
    """Each cue's text lines, joined with newlines."""
    cues = []
    for block in vtt.strip().split("\n\n"):
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines) if line[:2].isdigit() and " --> " in line), None)
        if timing is not None:
            cues.append("\n".join(lines[timing + 1:]))
    return cues


def _shown(cue: str) -> str:
    """What a player displays: markup removed, references decoded."""
    text = re.sub(r"<[^<>\n]*>", "", cue)
    assert "<" not in text and "-->" not in text, cue
    return html.unescape(text)


def _client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_dub_vtt_download_escapes_text_and_keeps_markup(dub_job):
    plain = _client().get(f"/dub/vtt/{dub_job}")
    assert plain.status_code == 200
    assert _cues(plain.text) == [_ESCAPED, _MARKUP]

    dual = _client().get(f"/dub/vtt/{dub_job}", params={"dual": "true"})
    assert dual.status_code == 200
    assert _cues(dual.text)[0] == f"{_ESCAPED}\n<i>Tom &amp; Jerry &lt;3</i>"


def test_imported_webvtt_exports_what_it_showed():
    from services.dub_pipeline import _dub_jobs
    from services.srt_parser import parse_srt

    imported = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\nQ&amp;A with Tom &amp; Jerry\n\n"
        "00:00:03.000 --> 00:00:04.000\nI &lt;3 you &gt;&gt; ok\n\n"
        "00:00:05.000 --> 00:00:06.000\n<i>Hi</i> there\n"
    )
    job_id = _vtt_job(parse_srt(imported).segments)
    try:
        exported = _client().get(f"/dub/vtt/{job_id}")
    finally:
        _dub_jobs.pop(job_id, None)

    assert exported.status_code == 200
    cues = _cues(exported.text)
    assert [_shown(cue) for cue in cues] == ["Q&A with Tom & Jerry", "I <3 you >> ok", "Hi there"]
    assert cues[2] == "<i>Hi</i> there"


def test_dub_srt_download_keeps_text_as_written(dub_job):
    srt = _client().get(f"/dub/srt/{dub_job}")
    assert srt.status_code == 200
    assert _TEXT in srt.text.splitlines()


def test_openai_compat_vtt_transcription_escapes_cue_text(monkeypatch):
    from api.routers import openai_compat
    from services import asr_backend

    async def fake_guarded(executor, fn, **kwargs):
        return {"segments": [{"start": 0.0, "end": 2.0, "text": _TEXT}], "language": "en"}

    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", fake_guarded)

    upload = UploadFile(file=io.BytesIO(b"RIFF"), filename="clip.wav")
    response = asyncio.run(openai_compat.create_transcription(
        file=upload, model="whisper-1", language=None, prompt=None,
        response_format="vtt", temperature=None,
    ))

    assert _cues(response.body.decode("utf-8")) == [_ESCAPED]
