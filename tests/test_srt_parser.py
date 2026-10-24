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


@pytest.mark.parametrize("separator", [",", "."])
def test_short_millisecond_fields_are_milliseconds(separator):
    # Leniently accepted 1- and 2-digit fields are millisecond counts,
    # not decimal fractions of a second.
    subtitle = f"1\n00:00:01{separator}5 --> 00:00:02{separator}50\nHello\n"
    segment = parse_srt(subtitle).segments[0]
    assert segment["start"] == pytest.approx(1.005)
    assert segment["end"] == pytest.approx(2.050)


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

def test_keeps_a_final_cue_that_is_only_a_number():
    # Regression: cue bodies are sliced up to the next timing line, which
    # swallows that cue's index, and the parser used to claw it back by
    # popping *every* trailing digit-only line. The last cue has no next
    # index to pop, so a closing "1999" was mistaken for one — the cue lost
    # its only line and was dropped as empty. Numeric-only dialogue is
    # everywhere in subtitles (a year, a score, a street number).
    srt = """1
00:00:01,000 --> 00:00:02,000
The year was

2
00:00:03,000 --> 00:00:04,000
1999
"""
    result = parse_srt(srt)
    assert result.skipped_cues == 0
    assert [s["text"] for s in result.segments] == ["The year was", "1999"]


def test_keeps_numeric_dialogue_in_an_index_less_file():
    # An index-less export has no index lines to strip at all, so a cue
    # ending in a number kept its text silently truncated — no skip counted,
    # so the import reported itself as lossless while dropping a line.
    srt = """00:00:01,000 --> 00:00:02,000
The answer is
42

00:00:03,000 --> 00:00:04,000
Next.
"""
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["The answer is\n42", "Next."]


def test_keeps_numeric_text_when_the_blank_separator_is_missing():
    # Off-spec file with no blank line between cue text and the next index:
    # exactly one trailing index line may be reclaimed, never two.
    srt = """1
00:00:01,000 --> 00:00:02,000
100
2
00:00:03,000 --> 00:00:04,000
Hi
"""
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["100", "Hi"]


def test_keeps_a_multi_line_numeric_countdown():
    # The old `while` loop popped digit lines until it hit a non-digit, so a
    # "3 / 2 / 1" countdown cue was consumed line by line and then dropped.
    srt = """1
00:00:01,000 --> 00:00:02,000
Ready?

2
00:00:03,000 --> 00:00:04,000
3
2
1
"""
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["Ready?", "3\n2\n1"]


def test_non_ascii_numerals_are_dialogue_not_cue_indices():
    # `str.isdigit()` is True for Arabic-Indic and Devanagari numerals, which
    # SubRip never uses for indices but a 646-language dubbing app sees as
    # dialogue constantly.
    srt = """1
00:00:01,000 --> 00:00:02,000
١٩٩٩

2
00:00:03,000 --> 00:00:04,000
२०२६
"""
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["١٩٩٩", "२०२६"]


def test_still_strips_the_index_line_swallowed_from_the_next_cue():
    # The guard against over-correcting: a numeric-bodied cue followed by
    # another must keep its own text and still not leak the next index.
    srt = """1
00:00:01,000 --> 00:00:02,000
1999

2
00:00:03,000 --> 00:00:04,000
Next.
"""
    result = parse_srt(srt)
    assert [s["text"] for s in result.segments] == ["1999", "Next."]


@pytest.mark.parametrize("first_index", ["", "1\n"])
def test_mixed_indexed_and_unindexed_cues_preserve_numbers(first_index):
    text = first_index + "00:00:01,000 --> 00:00:02,000\n42\n\n2\n00:00:03,000 --> 00:00:04,000\n1999\n\n00:00:05,000 --> 00:00:06,000\n3\n2\n1\n"
    assert [x["text"] for x in parse_srt(text).segments] == ["42", "1999", "3\n2\n1"]


def test_webvtt_numeric_dialogue_is_not_a_cue_identifier():
    text = "WEBVTT\n\n00:01.000 --> 00:02.000\n1999\n\nnext-id\n00:03.000 --> 00:04.000\n42\n"
    assert [cue["text"] for cue in parse_srt(text).segments] == ["1999", "42"]


def test_indexless_numeric_dialogue_after_blank_line_is_preserved():
    text = '00:00:01,000 --> 00:00:02,000\nFirst\n\n42\n00:00:03,000 --> 00:00:04,000\nNext'
    segments = parse_srt(text).segments
    assert segments[0]['text'] == 'First\n42'


@pytest.mark.parametrize("gap", ["", "\n"])
def test_initial_index_does_not_remove_later_numeric_dialogue(gap):
    text = "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n2\n00:00:03,000 --> 00:00:04,000\nAnswer\n" + gap + "42\n00:00:05,000 --> 00:00:06,000\nLast"
    assert [cue["text"] for cue in parse_srt(text).segments] == ["First", "Answer\n42", "Last"]


def test_nonsequential_ambiguous_number_is_retained_as_dialogue():
    text = "00:00:01,000 --> 00:00:02,000\nFirst\n\n3\n00:00:03,000 --> 00:00:04,000\nNext"
    assert parse_srt(text).segments[0]["text"] == "First\n3"



def test_skipped_cue_still_advances_numbering_state():
    text = "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n2\n00:00:04,000 --> 00:00:03,000\nInvalid\n\n3\n00:00:05,000 --> 00:00:06,000\nThird\n\n4\n00:00:07,000 --> 00:00:08,000\nFourth"
    result = parse_srt(text)
    assert result.skipped_cues == 1
    assert [cue["text"] for cue in result.segments] == ["First", "Third", "Fourth"]



def test_numeric_only_cue_after_invalid_cue_is_not_discarded():
    text = "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n2\n00:00:04,000 --> 00:00:03,000\nInvalid\n\n3\n00:00:05,000 --> 00:00:06,000\n4\n00:00:07,000 --> 00:00:08,000\nLast"
    assert [cue["text"] for cue in parse_srt(text).segments] == ["First", "4", "Last"]


@pytest.mark.parametrize('header', ['NOTE', 'NOTE translator notes', 'NOTE\ttranslator notes', 'STYLE', 'REGION'])
@pytest.mark.parametrize('separator', ['\n\n', '\n\n \n', '\n \n\n'])
def test_webvtt_metadata_timestamps_never_become_dialogue(header, separator):
    text = f'WEBVTT{separator}{header}\nMetadata content\n00:00.000 --> 00:02.000\nMetadata only\n\ncue\n00:03.000 --> 00:04.000\nReal dialogue\n'
    result = parse_srt(text)
    assert [cue['text'] for cue in result.segments] == ['Real dialogue']
    assert result.segments[0]['start'] == 3


def test_webvtt_note_words_inside_dialogue_are_retained():
    text = 'WEBVTT\n\n00:01.000 --> 00:02.000\nNOTE this is spoken\nSTYLE\nREGION\n'
    assert parse_srt(text).segments[0]['text'] == 'NOTE this is spoken\nSTYLE\nREGION'


@pytest.mark.parametrize('identifier', ['STYLE', 'REGION'])
def test_webvtt_metadata_words_can_identify_a_cue(identifier):
    text = f'WEBVTT\n\n{identifier}\n00:01.000 --> 00:02.000\nSpoken text\n'
    assert [cue['text'] for cue in parse_srt(text).segments] == ['Spoken text']


@pytest.mark.parametrize('identifier', ['NOTE', 'NOTE identifier'])
def test_webvtt_note_block_is_never_a_cue_even_with_a_timing_line(identifier):
    text = f'WEBVTT\n\n{identifier}\n00:01.000 --> 00:02.000\nprivate note\n\n00:03.000 --> 00:04.000\nSpoken text\n'
    assert [cue['text'] for cue in parse_srt(text).segments] == ['Spoken text']


@pytest.mark.parametrize('gap', ['\n', ' \n', '\n\n'])
def test_empty_webvtt_cue_does_not_capture_following_identifier(gap):
    text = f'WEBVTT\n\n00:01.000 --> 00:02.000\n{gap}next-id\n00:03.000 --> 00:04.000\nSpoken text\n'
    result = parse_srt(text)
    assert result.skipped_cues == 1
    assert [cue['text'] for cue in result.segments] == ['Spoken text']


@pytest.mark.parametrize("hours", [100, 1234])
@pytest.mark.parametrize("webvtt", [False, True])
def test_exported_long_hour_timestamps_can_be_imported(hours, webvtt):
    from services.srt_parser import format_cue_timestamp

    start = hours * 3600 + 1.25
    separator = "." if webvtt else ","
    prefix = "WEBVTT\n\n" if webvtt else "1\n"
    content = (prefix + format_cue_timestamp(start, separator) + " --> "
               + format_cue_timestamp(start + 2, separator) + "\nSpoken text\n")
    result = parse_srt(content)
    assert len(result.segments) == 1
    assert result.segments[0]["start"] == start
    assert result.segments[0]["end"] == start + 2


def test_unrepresentable_hour_timestamp_is_skipped():
    hours = "9" * 1000
    result = parse_srt(f"{hours}:00:00.000 --> {hours}:00:01.000\nToo large\n")
    assert result.segments == []
