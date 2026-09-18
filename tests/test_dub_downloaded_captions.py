"""Seeding a Dub job from the caption track downloaded with a video.

When a video has no manual captions in its language, ingest downloads the
platform's automatic ``-orig`` track. On YouTube that track *rolls*: every cue
repeats the line before it, a 10 ms cue holding only that line joins the two,
and each cue starts exactly where the previous one ended. The seeded
transcript must still read every spoken line once.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")

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
