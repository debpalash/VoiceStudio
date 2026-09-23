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


def test_srt_dialogue_with_angle_brackets_is_kept():
    from services.srt_parser import parse_srt

    for line in ("2 < 3 and 4 > 1", "<laughter> okay", "if a<b and c>d", "x <= y >= z"):
        srt = f"1\n00:00:01,000 --> 00:00:02,000\n{line}\n"
        assert parse_srt(srt).segments[0]["text"] == line


def test_srt_player_tags_are_not_dialogue():
    from services.srt_parser import parse_srt

    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n"
        '<font color="#ffff00"><b>Hi</b></font> <u>there</u> <I>you</I>\n'
    )
    assert parse_srt(srt).segments[0]["text"] == "Hi there you"


def test_line_break_tags_separate_words():
    from services.srt_parser import parse_srt

    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello<br>big<BR />world\n"
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello<br/>world\n"
    assert parse_srt(srt).segments[0]["text"] == "Hello big world"
    assert parse_srt(vtt).segments[0]["text"] == "Hello world"


def test_webvtt_escaped_math_reads_as_written():
    from services.srt_parser import parse_srt

    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>x &lt; y</i> &amp;&amp; y &gt; z\n"
    assert parse_srt(vtt).segments[0]["text"] == "x < y && y > z"


def test_cjk_and_rtl_text_survive_markup_stripping():
    from services.srt_parser import parse_srt

    words = "\u4f60\u597d \u05e9\u05dc\u05d5\u05dd \u0645\u0631\u062d\u0628\u0627"
    srt = f"1\n00:00:01,000 --> 00:00:02,000\n{{\\an8}}<i>{words}</i>\n"
    vtt = f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v A>{words}</v>\n"
    assert parse_srt(srt).segments[0]["text"] == words
    assert parse_srt(vtt).segments[0]["text"] == words


def test_malformed_markup_prefixes_parse_in_linear_time():
    import time

    from services.srt_parser import spoken_cue_text

    for junk in ("<" * 50_000, "{\\" * 50_000, "<i" * 50_000, "{{\\" * 50_000, "<br " * 50_000):
        for webvtt in (False, True):
            started = time.perf_counter()
            spoken_cue_text(junk, webvtt=webvtt)
            assert time.perf_counter() - started < 1.0, (junk[:4], webvtt)


def _export(kind, segments):
    from services.dub_pipeline import _dub_jobs
    from fastapi.testclient import TestClient
    from main import app
    import uuid

    job_id = str(uuid.uuid4())[:8]
    _dub_jobs[job_id] = {
        "video_path": "/nonexistent/original.mp4",
        "duration": 10.0,
        "filename": "clip.mp4",
        "segments": segments,
    }
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        response = client.get(f"/dub/{kind}/{job_id}")
    finally:
        _dub_jobs.pop(job_id, None)
    assert response.status_code == 200
    return response.text


def test_unchanged_srt_export_keeps_alignment_and_italics():
    from services.srt_parser import parse_srt

    segments = parse_srt(ASS_SRT).segments
    assert segments[0]["text"] == "Hello"
    assert "{\\an8}{\\i1}Hello{\\i0}" in _export("srt", segments)
    # WebVTT has no `{\an8}`; SubRip's `<i>` is still markup there.
    italic = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n{\\an8}<i>Hello</i>\n").segments
    vtt = _export("vtt", italic)
    assert "<i>Hello</i>" in vtt and "an8" not in vtt
    broken = parse_srt("1\n00:00:01,000 --> 00:00:02,000\nHello<br>world\n").segments
    assert "Hello<br>world" in _export("srt", broken)
    assert "Hello\nworld" in _export("vtt", broken)
    # A blank line would end the WebVTT cue early and drop the rest.
    edged = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n<br>Hello<br><br>big<br>\nworld<br>\n").segments
    vtt = _export("vtt", edged)
    assert "00:00:02.000\nHello\nbig\nworld\n" in vtt
    assert [s["text"] for s in parse_srt(vtt).segments] == ["Hello\nbig\nworld"]


def test_srt_dialogue_brackets_survive_webvtt_export_and_reimport():
    from services.srt_parser import parse_srt

    srt = "1\n00:00:01,000 --> 00:00:02,000\nif a<b and c>d, <i>Q&A</i> &amp; 2 < 3\n"
    segments = parse_srt(srt).segments
    spoken = segments[0]["text"]
    assert spoken == "if a<b and c>d, Q&A &amp; 2 < 3"
    vtt = _export("vtt", segments)
    assert "if a&lt;b and c>d, <i>Q&amp;A</i> &amp;amp; 2 &lt; 3" in vtt
    assert parse_srt(vtt).segments[0]["text"] == spoken
    # Edited SubRip text keeps SubRip rules too.
    segments[0]["text"] = "x <b and y> z"
    assert parse_srt(_export("vtt", segments)).segments[0]["text"] == "x <b and y> z"


def test_reimported_caption_file_exports_its_own_markup(monkeypatch):
    """`/dub/import-srt` onto a job: the new cue's source wins, never the prior one."""
    import asyncio
    import io

    from api.routers import dub_core
    from fastapi import UploadFile
    from services.dub_pipeline import _dub_jobs
    from services.srt_parser import parse_srt

    job_id = "caption-reimport"
    prior = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n{\\an8}<i>Hello</i>\n").segments
    job = {"duration": 10.0, "filename": "clip.mp4", "video_path": "/nonexistent/clip.mp4",
           "segments": [{**prior[0], "profile_id": "auto:speaker_1"}]}
    monkeypatch.setattr(dub_core, "_save_job", lambda *_args: None)
    _dub_jobs[job_id] = job
    try:
        for body, srt_line, vtt_line in (
            (b"1\n00:00:01,000 --> 00:00:02,000\n<b>Hello</b>\n", "<b>Hello</b>", "<b>Hello</b>"),
            (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<u>Hello</u>\n", "Hello", "<u>Hello</u>"),
        ):
            upload = UploadFile(filename="captions.srt", file=io.BytesIO(body))
            asyncio.run(dub_core.dub_import_srt(job_id, upload))
            segment = job["segments"][0]
            assert segment["text"] == "Hello"
            assert segment["profile_id"] == "auto:speaker_1"
            vtt = _export("vtt", job["segments"])
            assert f"\n{vtt_line}\n" in vtt and "<i>" not in vtt
            assert f"\n{srt_line}\n" in _export("srt", job["segments"])
    finally:
        _dub_jobs.pop(job_id, None)


def _generate(job, texts, lang, cue_ids=None):
    from api.routers.dub_generate import _sync_job_segments
    from schemas.requests import DubRequest, DubSegment

    ids = [str(s["id"]) for s in job["segments"]]
    segs = [
        DubSegment(start=s["start"], end=s["end"], text=t,
                   cue_source_id=(cue_ids or [None] * len(texts))[i])
        for i, (s, t) in enumerate(zip(job["segments"], texts))
    ]
    _sync_job_segments(job, DubRequest(segments=segs, segment_ids=ids, language_code=lang))


def test_imported_cue_needs_the_import_id_not_equal_text():
    """A paste or edit that lands on the imported words is not the import (#2295)."""
    from services.srt_parser import parse_srt

    srt = "1\n00:00:01,000 --> 00:00:02,000\n<i>Hello</i>\n"
    for cue_id, expected in ((None, "\nHello\n"), ("someone-else:0", "\nHello\n"), ("own", "\n<i>Hello</i>\n")):
        segments = parse_srt(srt).segments
        own = segments[0]["srt_source"]["id"]
        job = {"segments": segments}
        _generate(job, ["Hello"], "en", [own if cue_id == "own" else cue_id])
        assert expected in _export("srt", job["segments"]), cue_id
    # Ids are unique per import, so another file's cue never vouches for this one.
    assert parse_srt(srt).segments[0]["srt_source"]["id"] != parse_srt(srt).segments[0]["srt_source"]["id"]


def test_per_language_export_reuses_markup_only_for_the_vouched_track():
    from services.srt_parser import parse_srt
    from services.dub_pipeline import _dub_jobs
    from fastapi.testclient import TestClient
    from main import app

    segments = parse_srt("1\n00:00:01,000 --> 00:00:02,000\n<i>Hello</i>\n").segments
    own = segments[0]["srt_source"]["id"]
    job = {"video_path": "/nonexistent/clip.mp4", "duration": 10.0, "filename": "clip.mp4",
           "segments": segments}
    _generate(job, ["Hello"], "fr")             # pasted, equal words: no id
    _generate(job, ["Hello"], "en", [own])      # the import's own text
    job_id = "cue-per-lang"
    _dub_jobs[job_id] = job
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        en = client.get(f"/dub/srt/{job_id}?lang=en").text
        fr = client.get(f"/dub/srt/{job_id}?lang=fr").text
    finally:
        _dub_jobs.pop(job_id, None)
    assert "<i>Hello</i>" in en
    assert "<i>" not in fr and "\nHello\n" in fr


def test_edited_srt_text_exports_as_edited():
    from services.srt_parser import parse_srt

    segments = parse_srt(ASS_SRT).segments
    segments[0]["text"] = "Goodbye"
    exported = _export("srt", segments)
    assert "Goodbye" in exported and "an8" not in exported
