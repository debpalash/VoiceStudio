"""Unit tests for the SRT parser used by /dub/import-srt."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.srt_parser import parse_srt  # noqa: E402


def test_parses_a_well_formed_srt_file():
    srt = """1
00:00:01,000 --> 00:00:04,500
Hello world.

2
00:00:05,250 --> 00:00:08,000
Second line here.
"""
    result = parse_srt(srt)
    assert result.skipped_cues == 0
    assert result.dropped_overlaps == 0
    assert len(result.segments) == 2
    assert result.segments[0]["start"] == 1.0
    assert result.segments[0]["end"] == 4.5
    assert result.segments[0]["text"] == "Hello world."
    assert result.segments[1]["start"] == 5.25
    assert result.segments[1]["text"] == "Second line here."


def test_joins_multi_line_cues_with_newline():
    srt = """1
00:00:01,000 --> 00:00:04,000
Line one
Line two
"""
    result = parse_srt(srt)
    assert result.segments[0]["text"] == "Line one\nLine two"


def test_accepts_dot_as_milliseconds_separator():
    # WebVTT-style timestamps inside an SRT file are common in the wild.
    srt = """1
00:00:01.500 --> 00:00:03.250
Dotty.
"""
    result = parse_srt(srt)
    assert result.segments[0]["start"] == 1.5
    assert result.segments[0]["end"] == 3.25


def test_handles_utf8_bom_at_start_of_file():
    srt = "﻿1\n00:00:01,000 --> 00:00:02,000\nBOM cue.\n"
    result = parse_srt(srt)
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "BOM cue."


def test_handles_crlf_line_endings():
    srt = "1\r\n00:00:01,000 --> 00:00:02,000\r\nWindows.\r\n\r\n"
    result = parse_srt(srt)
    assert len(result.segments) == 1


def test_skips_cue_with_non_positive_duration():
    srt = """1
00:00:05,000 --> 00:00:05,000
Zero duration.

2
00:00:06,000 --> 00:00:05,500
End before start.

3
00:00:07,000 --> 00:00:08,000
Good cue.
"""
    result = parse_srt(srt)
    assert result.skipped_cues == 2
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Good cue."


def test_skips_cue_with_only_whitespace_body():
    srt = """1
00:00:01,000 --> 00:00:02,000


2
00:00:03,000 --> 00:00:04,000
Real text.
"""
    result = parse_srt(srt)
    assert result.skipped_cues == 1
    assert len(result.segments) == 1


def test_shifts_overlapping_cue_to_keep_both():
    # Cue 2 starts inside cue 1; we expect cue 2's start to be pushed
    # forward to cue 1's end so both still play, in order.
    srt = """1
00:00:01,000 --> 00:00:05,000
First.

2
00:00:03,000 --> 00:00:07,000
Second.
"""
    result = parse_srt(srt)
    assert len(result.segments) == 2
    assert result.segments[1]["start"] == 5.0
    assert result.segments[1]["end"] == 7.0
    assert result.dropped_overlaps == 0


def test_drops_overlap_that_would_have_negative_duration():
    srt = """1
00:00:01,000 --> 00:00:10,000
First.

2
00:00:03,000 --> 00:00:08,000
Second entirely inside first.
"""
    result = parse_srt(srt)
    assert len(result.segments) == 1
    assert result.dropped_overlaps == 1


def test_parses_missing_index_numbers():
    # No "1", "2" lines — just timings + text. This happens with some
    # subtitle editors that strip indices.
    srt = """00:00:01,000 --> 00:00:02,000
First.

00:00:03,000 --> 00:00:04,000
Second.
"""
    result = parse_srt(srt)
    assert len(result.segments) == 2


def test_returns_empty_result_for_empty_input():
    assert parse_srt("").segments == []
    assert parse_srt("").skipped_cues == 0


def test_blank_line_flood_parses_in_linear_time():
    # Regression: the timing-line regex used `^\s*` under re.MULTILINE, so at
    # every one of N line starts the engine consumed all remaining blank
    # lines before failing — quadratic. 20k blank lines already took ~1.7s
    # and a 2 MB blank-line file never returned, pinning the request thread
    # (reachable from /dub/import-srt with a mis-saved export, and from the
    # pasted-text endpoint). Horizontal-whitespace-only classes make it
    # linear: this input parses in milliseconds.
    import time

    srt = "1\n00:00:01,000 --> 00:00:02,000\nOnly cue.\n" + "\n" * 400_000
    started = time.monotonic()
    result = parse_srt(srt)
    elapsed = time.monotonic() - started
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Only cue."
    # Pre-fix this was minutes; the bound is loose enough for a slow CI box
    # and still ~3 orders of magnitude under the quadratic behaviour.
    assert elapsed < 5.0, f"parse_srt took {elapsed:.1f}s — quadratic scan is back"


def test_timing_line_tolerates_leading_and_inner_spaces():
    # The linearity fix narrowed `\s*` to horizontal whitespace; indented
    # cues and extra spaces around the arrow must still parse.
    srt = "  \t00:00:01,000  -->  \t00:00:02,000\nIndented.\n"
    result = parse_srt(srt)
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Indented."


def test_segments_get_sequential_ids_and_required_fields():
    srt = """1
00:00:01,000 --> 00:00:02,000
A

2
00:00:03,000 --> 00:00:04,000
B
"""
    result = parse_srt(srt)
    for i, seg in enumerate(result.segments):
        assert seg["id"] == i
        assert seg["text"] == seg["text_original"]
        assert seg["speaker_id"] == "Speaker 1"


# -- WebVTT through the same parser (Dub -> Paste translation -> Load file) ---


def test_webvtt_cues_without_an_hours_field_are_parsed():
    # WebVTT allows `mm:ss.ttt`; the paste dialog accepts .vtt files.
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.500\nHola\n\n01:03.000 --> 01:04.000\nQue tal\n"
    result = parse_srt(vtt)
    assert [(s["start"], s["end"], s["text"]) for s in result.segments] == [
        (1.0, 2.5, "Hola"),
        (63.0, 64.0, "Que tal"),
    ]


def test_webvtt_identifiers_and_note_blocks_stay_out_of_cue_text():
    vtt = (
        "WEBVTT\n\nNOTE made by a translator\n\n"
        "intro\n00:00:01.000 --> 00:00:02.500 align:start\nHola\n\n"
        "NOTE check this line\n\n"
        "cue-2\n00:00:03.000 --> 00:00:04.000\nQue tal\n"
    )
    result = parse_srt(vtt)
    assert [s["text"] for s in result.segments] == ["Hola", "Que tal"]


def test_srt_text_after_a_blank_line_inside_a_cue_is_still_kept():
    # SRT keeps its lenient blank-line handling; only WebVTT has identifiers.
    srt = "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\nstill first\n2\n00:00:03,000 --> 00:00:04,000\nSecond\n"
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["First\nstill first", "Second"]


def test_paste_endpoint_returns_webvtt_cues():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app, client=("127.0.0.1", 50000))
    res = client.post(
        "/dub/parse-subtitle-text",
        json={"text": "WEBVTT\n\nintro\n00:01.000 --> 00:02.000\nHola\n\nNOTE x\n\n00:03.000 --> 00:04.000\nAdios\n"},
    )
    assert res.status_code == 200, res.text
    assert [(c["start"], c["text"]) for c in res.json()["segments"]] == [(1.0, "Hola"), (3.0, "Adios")]