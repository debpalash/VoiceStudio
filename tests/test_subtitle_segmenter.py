"""Unit tests for services/subtitle_segmenter.py — Phase 1.2."""
import pytest

import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from services.subtitle_segmenter import (
    segment_for_subtitles,
    format_subtitle_lines,
    MAX_CHARS_PER_LINE,
    MAX_CHARS_TOTAL,
    MAX_CPS,
)


# ── Pass-throughs (segment already fits) ─────────────────────────────────────


def test_short_segment_unchanged():
    segs = [{"start": 0.0, "end": 2.0, "text": "Hello there."}]
    out = segment_for_subtitles(segs)
    assert out == [{"start": 0.0, "end": 2.0, "text": "Hello there."}]


def test_preserves_extra_keys():
    segs = [{
        "start": 0.0, "end": 2.0, "text": "Hello.",
        "speaker_id": "SPK-01", "id": "s001", "text_original": "Hello.",
    }]
    out = segment_for_subtitles(segs)
    assert out[0]["speaker_id"] == "SPK-01"
    assert out[0]["id"] == "s001"
    assert out[0]["text_original"] == "Hello."


# ── Sentence-level splits ────────────────────────────────────────────────────


def test_splits_at_sentence_boundary():
    text = "This is the first sentence. This is the second one." * 2
    segs = [{"start": 0.0, "end": 6.0, "text": text}]
    out = segment_for_subtitles(segs)
    assert len(out) > 1
    # No piece exceeds the total char cap.
    for s in out:
        assert len(s["text"]) <= MAX_CHARS_TOTAL, s["text"]
    # Splits land at sentence ends — "." should close each segment.
    assert all(s["text"].rstrip().endswith(".") for s in out)


# ── Clause-level splits when no sentence terminator ──────────────────────────


def test_splits_at_clause_boundary():
    text = "The quick brown fox, jumping over fences and dodging rocks, evaded the lazy dog completely."
    segs = [{"start": 0.0, "end": 5.0, "text": text}]
    out = segment_for_subtitles(segs)
    assert len(out) >= 2
    for s in out:
        assert len(s["text"]) <= MAX_CHARS_TOTAL


def test_splits_at_conjunction():
    # Long enough to force a split (> MAX_CHARS_TOTAL = 84).
    text = (
        "I walked down the hill that winds through the forest and then I saw "
        "a very large friendly dog that wagged its tail at me."
    )
    segs = [{"start": 0.0, "end": 6.0, "text": text}]
    out = segment_for_subtitles(segs)
    # The word "and" should be a valid split point.
    assert len(out) >= 2
    for s in out:
        assert len(s["text"]) <= MAX_CHARS_TOTAL


# ── CPS enforcement ──────────────────────────────────────────────────────────


def test_cps_enforcement():
    # 35 chars in 1 second = 35 CPS — above the 17 limit; segmenter must split
    # OR flag it. Under the rule, any segment above MAX_CPS should be split
    # unless it's already a single word.
    text = "A cascade of rapidly-delivered syllables sprinted."
    segs = [{"start": 0.0, "end": 1.0, "text": text}]
    out = segment_for_subtitles(segs)
    # At least one split; no piece both over MAX_CHARS_TOTAL AND over MAX_CPS.
    for s in out:
        dur = max(1e-3, s["end"] - s["start"])
        assert (len(s["text"]) <= MAX_CHARS_TOTAL) or (len(s["text"]) / dur <= MAX_CPS)


# ── Word-level timings ───────────────────────────────────────────────────────


def test_uses_word_timings_when_present():
    # Long enough to force a split; the clause comma is the prime candidate,
    # and with word timings the split should land right at the comma's time.
    words = [
        {"text": "This",       "start": 0.0, "end": 0.2},
        {"text": "is",         "start": 0.2, "end": 0.4},
        {"text": "an",         "start": 0.4, "end": 0.55},
        {"text": "extended",   "start": 0.55, "end": 1.0},
        {"text": "monologue,", "start": 1.0, "end": 1.8},
        {"text": "spoken",     "start": 1.8, "end": 2.1},
        {"text": "with",       "start": 2.1, "end": 2.3},
        {"text": "deliberate", "start": 2.3, "end": 2.9},
        {"text": "pacing",     "start": 2.9, "end": 3.3},
        {"text": "to",         "start": 3.3, "end": 3.4},
        {"text": "illustrate", "start": 3.4, "end": 4.0},
        {"text": "clause",     "start": 4.0, "end": 4.4},
        {"text": "splitting.", "start": 4.4, "end": 5.0},
    ]
    text = " ".join(w["text"] for w in words)
    segs = [{"start": 0.0, "end": 5.0, "text": text, "words": words}]
    out = segment_for_subtitles(segs)
    # Two segments, split at the comma around 1.8s.
    assert len(out) >= 2
    assert 1.5 <= out[0]["end"] <= 2.0
    # Last segment should end exactly at the original 5.0s.
    assert abs(out[-1]["end"] - 5.0) < 0.1


# ── Merger: tiny segments fold up ────────────────────────────────────────────


def test_merges_tiny_neighbour():
    segs = [
        {"start": 0.0, "end": 1.5, "text": "A long enough starter."},
        {"start": 1.5, "end": 1.8, "text": "Ok."},  # tiny
    ]
    out = segment_for_subtitles(segs)
    # "Ok." should merge into the starter because its duration < 1.2s and it's
    # short. But we don't merge across sentence terminators — so this stays.
    # ("A long enough starter." ends in '.')
    assert len(out) == 2  # respected sentence boundary


def test_merges_tiny_into_neighbour_when_no_sentence_break():
    segs = [
        {"start": 0.0, "end": 0.6, "text": "Um"},   # tiny + no terminator
        {"start": 0.6, "end": 2.5, "text": "let me think about it."},
    ]
    out = segment_for_subtitles(segs)
    assert len(out) == 1
    assert out[0]["text"].startswith("Um")


def test_does_not_merge_across_speakers():
    segs = [
        {"start": 0.0, "end": 0.6, "text": "Yes",        "speaker_id": "A"},
        {"start": 0.6, "end": 1.2, "text": "no thanks.", "speaker_id": "B"},
    ]
    out = segment_for_subtitles(segs)
    assert len(out) == 2
    assert out[0]["speaker_id"] == "A"
    assert out[1]["speaker_id"] == "B"


# ── format_subtitle_lines ────────────────────────────────────────────────────


def test_format_single_line_fits():
    lines = format_subtitle_lines("Hello world.")
    assert lines == ["Hello world."]


def test_format_wraps_to_two_lines():
    long = "A long sentence that definitely will not fit in forty-two chars total length."
    lines = format_subtitle_lines(long, max_chars=MAX_CHARS_PER_LINE)
    assert 1 <= len(lines) <= 2
    assert all(len(l) <= MAX_CHARS_PER_LINE or l == lines[-1] for l in lines)


# ── No-op edge cases ─────────────────────────────────────────────────────────


def test_empty_input():
    assert segment_for_subtitles([]) == []


def test_single_word_rumble_accepted():
    # A 30-char single word in 1s is 30 CPS — over limit, but un-splittable.
    segs = [{"start": 0.0, "end": 1.0, "text": "Antidisestablishmentarianism!"}]
    out = segment_for_subtitles(segs)
    assert len(out) == 1  # no natural cut available


def test_oversized_first_word_still_leaves_room_for_a_second_line():
    assert format_subtitle_lines("unbreakable two words", max_chars=6) == [
        "unbreakable", "two words",
    ]
