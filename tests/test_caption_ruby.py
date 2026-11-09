"""Caption-to-speech ruby cases shared with Electron; no models required."""
import importlib
import json
from pathlib import Path
import time

import pytest

CASES = json.loads((Path(__file__).parent / "fixtures/caption_ruby_cases.json").read_text(encoding="utf-8"))


def _parser():
    return importlib.import_module("services.srt_parser")


def caption(cue, webvtt):
    """Wrap one cue in its import format."""
    prefix = "WEBVTT\n\n" if webvtt else "1\n"
    return f"{prefix}00:00:01.000 --> 00:00:02.000\n{cue}\n"


@pytest.mark.parametrize("webvtt", [False, True])
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_spoken_ruby_and_source_round_trip(case, webvtt):
    """Speak only the base and preserve the original export provenance."""
    parser = _parser()
    assert parser.spoken_cue_text(case["cue"], webvtt=webvtt) == case["spoken"]
    segments = parser.parse_srt(caption(case["cue"], webvtt)).segments
    assert [segment["text"] for segment in segments] == [case["spoken"]]
    key = "webvtt_source" if webvtt else "srt_source"
    assert parser.source_cue_or(segments[0], case["spoken"], key) == case["cue"]
    assert parser.escape_webvtt_text(case["cue"]) == case["cue"]
    assert parser.srt_cue_to_webvtt(case["cue"]) == case["cue"]


@pytest.mark.parametrize("webvtt", [False, True])
@pytest.mark.parametrize("gap", ["", "\n"])
def test_unclosed_annotation_cannot_consume_next_cue(webvtt, gap):
    """Missing ruby closers cannot hide a later timed cue."""
    parser = _parser()
    first = caption("Please <ruby>read<rt>reed", webvtt)
    second_index = "" if webvtt else "2\n"
    content = first + gap + second_index + "00:00:02.000 --> 00:00:03.000\nNext cue.\n"
    assert [segment["text"] for segment in parser.parse_srt(content).segments] == ["Please read", "Next cue."]


def test_escaped_annotation_is_dialogue():
    """Decode WebVTT entities only after removing real markup."""
    parser = _parser()
    assert parser.spoken_cue_text("&lt;rt&gt;reed&lt;/rt&gt;", webvtt=True) == "<rt>reed</rt>"
    assert parser.spoken_cue_text("<rt 5> is literal in SubRip") == "<rt 5> is literal in SubRip"


def test_adversarial_annotations_remain_bounded():
    """An upload containing many tags must not cause regex backtracking."""
    parser = _parser()
    started = time.perf_counter()
    text = "<ruby>base" + "<rtc>" * 20_000 + "</rp>" * 20_000 + "</rtc>" * 20_000 + "</ruby> end"
    assert parser.spoken_cue_text(text) == "base end"
    assert time.perf_counter() - started < 2.0
