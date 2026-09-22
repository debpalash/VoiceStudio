"""Caption markup is styling, not dialogue.

YouTube auto-captions write karaoke timestamps and <c> spans into the cue.
Fansub and player SRTs prefix a line with {\\an8} or {\\i1}. Downloaded
WebVTT already dropped those before seeding a dub; a pasted or picked file
went through parse_srt and kept them, so the editor and the voice read
"c everyone" or "an8 hello".
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")

YOUTUBE_KARAOKE = """WEBVTT

00:00:00.160 --> 00:00:02.310 align:start position:0%
hey<00:00:00.480><c> everyone</c><00:00:00.640><c> welcome</c>
"""

MARKUP_VTT = """WEBVTT

00:00:01.000 --> 00:00:02.000
<i>Hello</i> <v Roger>there</v>
"""

ASS_SRT = """1
00:00:01,000 --> 00:00:02,000
{\\an8}{\\i1}Hello{\\i0}
"""


def test_pasted_youtube_karaoke_is_spoken_words_only():
    from services.srt_parser import parse_srt

    assert [cue["text"] for cue in parse_srt(YOUTUBE_KARAOKE).segments] == [
        "hey everyone welcome",
    ]


def test_downloaded_and_pasted_webvtt_agree_on_karaoke_text(tmp_path):
    from services.dub_pipeline import parse_vtt_segments
    from services.srt_parser import parse_srt

    track = tmp_path / "original.en-orig.vtt"
    track.write_text(YOUTUBE_KARAOKE, encoding="utf-8")
    spoken = [cue["text"] for cue in parse_vtt_segments(str(track))]
    assert spoken == ["hey everyone welcome"]
    assert [cue["text"] for cue in parse_srt(YOUTUBE_KARAOKE).segments] == spoken


def test_paste_endpoint_strips_webvtt_markup():
    from api.routers import dub_core
    from schemas.requests import ParseSubtitleTextRequest

    result = dub_core.dub_parse_subtitle_text(
        ParseSubtitleTextRequest(text=MARKUP_VTT)
    )
    assert [cue["text"] for cue in result["segments"]] == ["Hello there"]


def test_imported_srt_alignment_tags_are_not_dialogue():
    from services.srt_parser import parse_srt

    assert [cue["text"] for cue in parse_srt(ASS_SRT).segments] == ["Hello"]


def test_literal_less_than_in_srt_is_not_a_tag():
    from services.srt_parser import parse_srt

    srt = "1\n00:00:01,000 --> 00:00:02,000\nI <3 you\n"
    assert parse_srt(srt).segments[0]["text"] == "I <3 you"


def test_webvtt_export_keeps_imported_markup_after_stripping_speech():
    from services.dub_pipeline import _dub_jobs
    from services.srt_parser import parse_srt
    from fastapi.testclient import TestClient
    from main import app
    import uuid

    job_id = str(uuid.uuid4())[:8]
    _dub_jobs[job_id] = {
        "video_path": "/nonexistent/original.mp4",
        "duration": 10.0,
        "filename": "clip.mp4",
        "segments": parse_srt(MARKUP_VTT).segments,
    }
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        exported = client.get(f"/dub/vtt/{job_id}")
    finally:
        _dub_jobs.pop(job_id, None)

    assert exported.status_code == 200
    assert "<i>Hello</i> <v Roger>there</v>" in exported.text
    assert parse_srt(MARKUP_VTT).segments[0]["text"] == "Hello there"


def test_escaped_tags_stay_literal_in_spoken_text():
    from services.srt_parser import parse_srt

    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&lt;i&gt;literal&lt;/i&gt; <i>real</i>\n"
    assert parse_srt(vtt).segments[0]["text"] == "<i>literal</i> real"
