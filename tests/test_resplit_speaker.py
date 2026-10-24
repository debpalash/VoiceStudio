"""Speaker-aware re-split after diarization (#486).

Segmentation groups words by sentence before diarization, so one segment can
span two speakers; assign_speakers_* only relabels it with the majority speaker,
merging the turn. resplit_segments_* splits such a segment at the word boundary.

The load-bearing guarantee is **no single-speaker regression**: a segment whose
words all map to one speaker must come back byte-for-byte unchanged (same dict,
id, text, start, end) so single-speaker dub timing never moves. These tests pin
that, plus the actual split behaviour and diarization-noise robustness.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services.segmentation import (  # noqa: E402
    Word,
    resplit_segments_by_turns,
    resplit_segments_by_diarization,
    _resplit_core,
)


def _w(start, end, text):
    return Word(start=start, end=end, text=text)


def _seg(start, end, text, sid="Speaker 1", _id="seg1"):
    return {"start": start, "end": end, "text": text, "speaker_id": sid, "id": _id,
            "text_original": text}


# ── No-regression: single-speaker segments are untouched ─────────────────────

def test_single_speaker_segment_unchanged():
    seg = _seg(0.0, 4.0, "hello there friends")
    words = [_w(0.0, 1.0, "hello"), _w(1.0, 2.0, "there"), _w(2.0, 3.5, "friends")]
    turns = [{"start": 0.0, "end": 4.0, "speaker": "Speaker 1"}]
    out = resplit_segments_by_turns([dict(seg)], words, turns)
    assert len(out) == 1
    assert out[0] == seg  # byte-for-byte identical (text, id, timing all preserved)


def test_no_turns_is_identity():
    seg = _seg(0.0, 4.0, "all one speaker")
    words = [_w(0.0, 2.0, "all"), _w(2.0, 4.0, "one")]
    assert resplit_segments_by_turns([seg], words, []) == [seg]
    assert resplit_segments_by_turns([seg], words, None) == [seg]


def test_segment_with_fewer_than_two_words_unchanged():
    seg = _seg(0.0, 1.0, "hi")
    words = [_w(0.0, 1.0, "hi")]
    turns = [{"start": 0.0, "end": 0.5, "speaker": "Speaker 1"},
             {"start": 0.5, "end": 1.0, "speaker": "Speaker 2"}]
    assert resplit_segments_by_turns([seg], words, turns) == [seg]



def test_neighbour_boundary_word_inside_rounding_is_not_borrowed():
    # Segment bounds are rounded to 0.01s; word times are not. "I" starts at
    # 5.2273, 2.7ms before the rounded 5.23 end of the previous segment, and
    # must not be split off (and spoken twice) as a one-word Speaker 2 piece.
    first = _seg(0.0, 5.23, "my good friend?", _id="seg1")
    second = _seg(5.23, 7.0, "I am fine.", sid="Speaker 2", _id="seg2")
    words = [_w(0.0, 2.0, "my"), _w(2.0, 4.0, "good"), _w(4.0, 5.2273, "friend?"),
             _w(5.2273, 5.6, "I"), _w(5.6, 6.2, "am"), _w(6.2, 7.0, "fine.")]
    turns = [{"start": 0.0, "end": 5.2273, "speaker": "Speaker 1"},
             {"start": 5.2273, "end": 7.0, "speaker": "Speaker 2"}]
    out = resplit_segments_by_turns([dict(first), dict(second)], words, turns)
    assert out == [first, second]

# ── The fix: a two-speaker segment splits at the boundary ────────────────────

def test_two_speaker_segment_splits():
    # "how are you" (Speaker 1, 0-3s) + "i am fine" (Speaker 2, 3-6s) got merged
    # into one segment; the re-split must separate them.
    seg = _seg(0.0, 6.0, "how are you i am fine")
    words = [
        _w(0.0, 1.0, "how"), _w(1.0, 2.0, "are"), _w(2.0, 3.0, "you"),
        _w(3.0, 4.0, "i"), _w(4.0, 5.0, "am"), _w(5.0, 6.0, "fine"),
    ]
    turns = [
        {"start": 0.0, "end": 3.0, "speaker": "Speaker 1"},
        {"start": 3.0, "end": 6.0, "speaker": "Speaker 2"},
    ]
    out = resplit_segments_by_turns([seg], words, turns)
    assert len(out) == 2
    assert out[0]["text"] == "how are you"
    assert out[0]["speaker_id"] == "Speaker 1"
    assert out[1]["text"] == "i am fine"
    assert out[1]["speaker_id"] == "Speaker 2"
    # Pieces exactly cover the original span; outer edges preserved.
    assert out[0]["start"] == 0.0
    assert out[1]["end"] == 6.0
    # Interior boundary on a word edge; new piece gets a distinct id.
    assert out[0]["end"] == 3.0 and out[1]["start"] == 3.0
    assert out[0]["id"] == "seg1" and out[1]["id"] != "seg1"
    assert out[1]["text_original"] == "i am fine"


def test_three_speaker_runs_split_into_three():
    seg = _seg(0.0, 6.0, "a b c d e f")
    words = [_w(i, i + 1, ch) for i, ch in enumerate(["a", "b", "c", "d", "e", "f"])]
    turns = [
        {"start": 0.0, "end": 2.0, "speaker": "Speaker 1"},
        {"start": 2.0, "end": 4.0, "speaker": "Speaker 2"},
        {"start": 4.0, "end": 6.0, "speaker": "Speaker 1"},
    ]
    out = resplit_segments_by_turns([seg], words, turns)
    assert [s["text"] for s in out] == ["a b", "c d", "e f"]
    assert [s["speaker_id"] for s in out] == ["Speaker 1", "Speaker 2", "Speaker 1"]


# ── Robustness: diarization noise must not over-split ─────────────────────────

def test_single_word_flip_is_smoothed_not_split():
    # One word mis-attributed to Speaker 2 inside Speaker 1's run → no split.
    seg = _seg(0.0, 5.0, "one two three four five")
    words = [_w(i, i + 1, t) for i, t in enumerate(["one", "two", "three", "four", "five"])]
    turns = [
        {"start": 0.0, "end": 2.0, "speaker": "Speaker 1"},
        {"start": 2.0, "end": 3.0, "speaker": "Speaker 2"},   # lone noisy word
        {"start": 3.0, "end": 5.0, "speaker": "Speaker 1"},
    ]
    out = resplit_segments_by_turns([seg], words, turns)
    assert len(out) == 1
    assert out[0] == seg


def test_only_mixed_segment_splits_others_identity():
    a = _seg(0.0, 2.0, "solo one", sid="Speaker 1", _id="a")
    b = _seg(2.0, 8.0, "mixed left mixed right", sid="Speaker 1", _id="b")
    c = _seg(8.0, 10.0, "solo two", sid="Speaker 2", _id="c")
    words = [
        _w(0.0, 1.0, "solo"), _w(1.0, 2.0, "one"),
        _w(2.0, 3.0, "mixed"), _w(3.0, 4.0, "left"),
        _w(5.0, 6.0, "mixed"), _w(6.0, 7.0, "right"),
        _w(8.0, 9.0, "solo"), _w(9.0, 10.0, "two"),
    ]
    turns = [
        {"start": 0.0, "end": 4.0, "speaker": "Speaker 1"},
        {"start": 4.0, "end": 8.0, "speaker": "Speaker 2"},
        {"start": 8.0, "end": 10.0, "speaker": "Speaker 2"},
    ]
    out = resplit_segments_by_turns([a, b, c], words, turns)
    # a and c untouched (identity); b split in two.
    assert out[0] == a
    assert out[-1] == c
    mid = [s for s in out if s["id"].startswith("b")]
    assert len(mid) == 2
    assert [s["speaker_id"] for s in mid] == ["Speaker 1", "Speaker 2"]


# ── pyannote wrapper: label formatting (SPEAKER_00 → Speaker 1) ───────────────

class _FakeTurn:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _FakeDiar:
    def __init__(self, tracks):
        self._tracks = tracks  # list of (start, end, raw_label)

    def itertracks(self, yield_label=True):
        for s, e, lab in self._tracks:
            yield _FakeTurn(s, e), None, lab


def test_diarization_wrapper_formats_labels_and_splits():
    seg = _seg(0.0, 4.0, "left side right side")
    words = [_w(0.0, 1.0, "left"), _w(1.0, 2.0, "side"),
             _w(2.0, 3.0, "right"), _w(3.0, 4.0, "side")]
    diar = _FakeDiar([(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")])
    out = resplit_segments_by_diarization([seg], words, diar)
    assert [s["speaker_id"] for s in out] == ["Speaker 1", "Speaker 2"]
    assert [s["text"] for s in out] == ["left side", "right side"]
