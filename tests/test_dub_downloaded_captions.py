"""Seeding a Dub job from the caption track downloaded with a video.

When a video has no manual captions in its language, ingest downloads the
platform's automatic ``-orig`` track. On YouTube that track *rolls*: every cue
repeats the line before it, a 10 ms cue holding only that line joins the two,
and each cue starts exactly where the previous one ended. The seeded
transcript must still read every spoken line once.
"""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")


@pytest.mark.parametrize("header", ["NOTE", "NOTE translator explanation", "NOTE\ttranslator explanation"])
def test_downloaded_vtt_comment_blocks_preserve_spoken_metadata_words(tmp_path, header):
    from services.dub_pipeline import parse_vtt_segments

    blocks = [
        "WEBVTT",
        header + "\n00:01.000 --> 00:02.000\nComment example, not dialogue.",
        "00:03.000 --> 00:04.000\nNOTE this is spoken.\nNOTEBOOK is spoken too.\nWEBVTT is a spoken format name.",
        "STYLE\n00:05.000 --> 00:06.000\nOrdinary dialogue.",
        "REGION\n00:07.000 --> 00:08.000\nAnother spoken line.",
    ]
    track = tmp_path / "original.en.vtt"
    track.write_text("\n\n".join(blocks), encoding="utf-8")
    assert parse_vtt_segments(str(track)) == [
        {"start": 3.0, "end": 4.0, "text": "NOTE this is spoken. NOTEBOOK is spoken too. WEBVTT is a spoken format name."},
        {"start": 5.0, "end": 6.0, "text": "Ordinary dialogue."},
        {"start": 7.0, "end": 8.0, "text": "Another spoken line."},
    ]


def test_downloaded_vtt_keeps_rolling_caption_timings(tmp_path):
    from services.dub_pipeline import parse_vtt_segments

    track = tmp_path / "original.en-orig.vtt"
    track.write_text(ROLLING_VTT, encoding="utf-8")
    segments = parse_vtt_segments(str(track))
    assert len(segments) == 5
    assert segments[1] == {"start": 2.31, "end": 2.32, "text": "hey everyone welcome back"}
    assert segments[2] == {"start": 2.32, "end": 4.79, "text": "hey everyone welcome back today we are baking bread"}


def test_downloaded_vtt_keeps_cue_immediately_after_header(tmp_path):
    from services.dub_pipeline import parse_vtt_segments

    track = tmp_path / "original.en.vtt"
    track.write_text("WEBVTT\n00:01.000 --> 00:02.000\nOrdinary dialogue.\n", encoding="utf-8")
    assert parse_vtt_segments(str(track)) == [{"start": 1.0, "end": 2.0, "text": "Ordinary dialogue."}]


def test_downloaded_vtt_ingest_persists_only_dialogue(monkeypatch):
    import asyncio
    import shutil
    import uuid
    import wave
    from pathlib import Path
    from core.config import DUB_DIR
    from core.db import db_conn, init_db
    from services import dub_pipeline as pipeline

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("native caption ingest needs ffmpeg and ffprobe")
    init_db()
    job_id = uuid.uuid4().hex
    job_dir = Path(DUB_DIR) / job_id
    job_dir.mkdir(parents=True)
    media = job_dir / "original.wav"
    with wave.open(str(media), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 48000)
    track = job_dir / "original.en.vtt"
    track.write_text("WEBVTT\n\nNOTE explanation\n00:00.000 --> 00:01.000\nComment example.\n\n00:01.000 --> 00:02.000\nNOTE real dialogue.\n", encoding="utf-8")
    # Only the external download transport is replaced; parsing, native
    # extraction, hashing, job admission and SQLite persistence are real.
    monkeypatch.setattr(pipeline, "yt_download_sync", lambda *args, **kwargs: (str(media), "Fixture", [str(track)]))

    async def prepare():
        stream = pipeline.ingest_pipeline(job_id, str(job_dir), {"kind": "url", "url": "https://example.invalid/fixture", "input_type": "audio", "fetch_subs": True})
        try:
            async for event in stream:
                if '"extract_done"' in event:
                    return  # Preparation persisted; do not enter ML stages.
            pytest.fail("ingest did not finish audio preparation")
        finally:
            await stream.aclose()

    try:
        asyncio.run(prepare())
        pipeline._dub_jobs.pop(job_id, None)
        job = pipeline.get_job(job_id)
        assert job["youtube_subs"]["en"] == [{"start": 1.0, "end": 2.0, "text": "NOTE real dialogue."}]
    finally:
        pipeline._dub_jobs.pop(job_id, None)
        with db_conn() as connection:
            connection.execute("DELETE FROM dub_history WHERE id=?", (job_id,))
        shutil.rmtree(job_dir)

ROLLING_VTT = "\n".join([
    "WEBVTT",
    "Kind: captions",
    "Language: en",
    "",
    "00:00:00.160 --> 00:00:02.310 align:start position:0%",
    " ",
    "hey<00:00:00.480><c> everyone</c><00:00:00.640><c> welcome</c><00:00:01.200><c> back</c>",
    "",
    "00:00:02.310 --> 00:00:02.320 align:start position:0%",
    "hey everyone welcome back",
    " ",
    "",
    "00:00:02.320 --> 00:00:04.790 align:start position:0%",
    "hey everyone welcome back",
    "today<00:00:02.560><c> we</c><00:00:02.720><c> are</c><00:00:03.100><c> baking</c><00:00:03.500><c> bread</c>",
    "",
    "00:00:04.790 --> 00:00:04.800 align:start position:0%",
    "today we are baking bread",
    " ",
    "",
    "00:00:04.800 --> 00:00:07.000 align:start position:0%",
    "today we are baking bread",
    "from<00:00:05.200><c> scratch</c>",
    "",
])


def _seed_from(vtt_text, tmp_path, monkeypatch):
    from api.routers import dub_core
    from services.dub_pipeline import parse_vtt_segments

    track = tmp_path / "original.en-orig.vtt"
    track.write_text(vtt_text, encoding="utf-8")
    job = {"youtube_subs": {"en-orig": parse_vtt_segments(str(track))}, "duration": 10.0}
    monkeypatch.setattr(dub_core, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dub_core, "_save_job", lambda job_id, saved: None)
    return dub_core.dub_use_downloaded_captions("job"), job


def test_rolling_automatic_captions_seed_each_line_once(tmp_path, monkeypatch):
    result, job = _seed_from(ROLLING_VTT, tmp_path, monkeypatch)

    assert job["full_transcript"] == (
        "hey everyone welcome back today we are baking bread from scratch"
    )
    segments = result["segments"]
    assert segments[0]["start"] == 0.16
    assert segments[-1]["end"] == 7.0
    assert all(a["end"] <= b["start"] for a, b in zip(segments, segments[1:]))


def test_touching_cues_keep_a_word_that_recurs_across_the_boundary(tmp_path, monkeypatch):
    manual = "\n".join([
        "WEBVTT",
        "",
        "00:00:01.000 --> 00:00:03.500",
        "I told you we should go",
        "",
        "00:00:03.500 --> 00:00:06.000",
        "go home before the last train leaves.",
        "",
    ])
    _, job = _seed_from(manual, tmp_path, monkeypatch)

    assert job["full_transcript"] == (
        "I told you we should go go home before the last train leaves."
    )


def test_overlapping_cue_still_drops_the_words_it_repeats(tmp_path, monkeypatch):
    manual = "\n".join([
        "WEBVTT",
        "",
        "00:00:01.000 --> 00:00:03.500",
        "I told you we should go",
        "",
        "00:00:03.000 --> 00:00:06.000",
        "should go home before the last train leaves.",
        "",
    ])
    _, job = _seed_from(manual, tmp_path, monkeypatch)

    assert job["full_transcript"] == (
        "I told you we should go home before the last train leaves."
    )


def test_touching_cues_keep_intentional_repetition_of_a_whole_phrase(tmp_path, monkeypatch):
    manual = '\n'.join([
        'WEBVTT', '',
        '00:00:01.000 --> 00:00:03.000', 'Never give up.', '',
        '00:00:03.000 --> 00:00:05.000', 'Never give up.', '',
        '00:00:05.000 --> 00:00:07.000', 'Never give up. Keep going.', '',
    ])
    _, job = _seed_from(manual, tmp_path, monkeypatch)
    assert job['full_transcript'] == 'Never give up. Never give up. Never give up. Keep going.'


def test_rollup_detection_does_not_remove_later_spoken_repetitions(tmp_path, monkeypatch):
    vtt = ROLLING_VTT + '\n'.join([
        '', '00:00:07.000 --> 00:00:09.000', 'from scratch', '',
    ])
    _, job = _seed_from(vtt, tmp_path, monkeypatch)
    assert job['full_transcript'] == (
        'hey everyone welcome back today we are baking bread from scratch from scratch'
    )


NOTE_VTT = "\n".join([
    "WEBVTT",
    "",
    "NOTE translator explanation",
    "00:01.000 --> 00:02.000",
    "Comment example, not dialogue.",
    "",
    "00:03.000 --> 00:04.000",
    "NOTE this is spoken.",
    "NOTEBOOK is spoken too.",
    "WEBVTT is a spoken format name.",
    "",
])


def test_downloaded_note_blocks_are_not_speech_but_dialogue_words_are(tmp_path):
    """#2510: metadata is block-scoped, not line-scoped, and line endings
    (CRLF or classic CR) must not change the answer."""
    from services.dub_pipeline import parse_vtt_segments

    for newline in ("\n", "\r\n", "\r"):
        track = tmp_path / "t.vtt"
        track.write_bytes(NOTE_VTT.replace("\n", newline).encode("utf-8"))
        assert parse_vtt_segments(str(track)) == [{
            "start": 3.0,
            "end": 4.0,
            "text": "NOTE this is spoken. NOTEBOOK is spoken too. WEBVTT is a spoken format name.",
        }]


def test_downloaded_captions_and_uploaded_parser_agree_on_note_handling(tmp_path):
    from services.dub_pipeline import parse_vtt_segments
    from services.srt_parser import parse_srt

    track = tmp_path / "t.vtt"
    track.write_text(NOTE_VTT, encoding="utf-8")
    downloaded = [s["text"] for s in parse_vtt_segments(str(track))]
    uploaded = [" ".join(s["text"].split()) for s in parse_srt(NOTE_VTT).segments]
    assert downloaded == uploaded
