"""Unit tests for services.segmentation — the dub-grade segmentation pipeline."""

import pytest

from services.segmentation import (
    MIN_DUR,
    MIN_CHARS,
    MAX_DUR,
    MAX_CHARS,
    MERGE_GAP,
    IDEAL_DUR,
    Segment,
    Word,
    _best_boundary,
    _words_from_whisper,
    _build_segments_from_words,
    _merge_short,
    _apply_scene_cuts,
    segment_transcript,
    assign_speakers_heuristic,
    assign_speakers_from_diarization,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunks(*pairs):
    """Build a whisper-style result from (text, start, end) tuples."""
    return {"chunks": [{"text": t, "timestamp": (s, e)} for t, s, e in pairs]}


def _words(result):
    """Run _words_from_whisper convenience wrapper."""
    return _words_from_whisper(result)


# ---------------------------------------------------------------------------
# _best_boundary
# ---------------------------------------------------------------------------

class TestBestBoundary:
    def test_prefers_sentence_over_clause(self):
        text = "First thought. Second, clause here finishes the line."
        pos = _best_boundary(text, ideal_pos=len(text) // 2)
        # Expect cut right after the period + space
        assert text[pos - 1] == "." or text[pos - 1] == " "
        assert "First thought" in text[:pos]

    def test_prefers_clause_when_no_sentence(self):
        text = "one two three, four five six seven eight nine"
        pos = _best_boundary(text, ideal_pos=len(text) // 2)
        left = text[:pos].rstrip()
        assert left.endswith(",")

    def test_falls_back_to_word_boundary(self):
        text = "alpha beta gamma delta epsilon zeta eta theta"
        pos = _best_boundary(text, ideal_pos=len(text) // 2)
        # Split must land ON or AFTER a space; neither side may be mid-word.
        assert pos == len(text) or text[pos] == " " or text[pos - 1] == " "
        left = text[:pos].rstrip()
        right = text[pos:].lstrip()
        # Both sides begin/end on complete words.
        assert not left or left[-1].isalnum() or left[-1] in ".,!?;:"
        assert right[:1].isalpha() or right == ""

    def test_no_whitespace_returns_full_length(self):
        text = "unsplittableword"
        pos = _best_boundary(text, ideal_pos=len(text) // 2)
        assert pos == len(text)


# ---------------------------------------------------------------------------
# _words_from_whisper
# ---------------------------------------------------------------------------

class TestWordsFromWhisper:
    def test_prefers_word_level_when_available(self):
        result = {
            "segments": [
                {
                    "start": 0.0, "end": 2.0,
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 0.5},
                        {"word": "world", "start": 0.5, "end": 1.1},
                        {"word": "foo", "start": 1.1, "end": 1.8},
                    ],
                },
            ],
            "chunks": [{"text": "hello world foo", "timestamp": (0.0, 2.0)}],
        }
        words = _words(result)
        assert [w.text for w in words] == ["hello", "world", "foo"]
        assert words[0].start == 0.0

    def test_falls_back_to_chunks(self):
        result = _chunks(("one two three", 0.0, 3.0))
        words = _words(result)
        assert len(words) == 3
        # Time evenly distributed
        assert words[0].start == 0.0
        assert pytest.approx(words[1].start, abs=0.01) == 1.0
        assert pytest.approx(words[2].end, abs=0.01) == 3.0

    def test_empty_result_returns_empty(self):
        assert _words({}) == []
        assert _words({"chunks": []}) == []


# ---------------------------------------------------------------------------
# Core pipeline (segment_transcript)
# ---------------------------------------------------------------------------

class TestSegmentTranscript:
    def test_no_mid_word_splits_on_fragmented_whisper(self):
        """The screenshot bug — 18 mid-word fragments should collapse to clean segs."""
        result = _chunks(
            ("Most hiring team", 0.0, 1.2),
            ("much time on the", 1.2, 2.1),
            ("Same screening", 2.1, 3.0),
            ("same shortlisting", 3.0, 4.4),
            ("and again.", 4.4, 5.2),
            ("So we built Acme", 5.2, 6.6),
            ("You can create in", 6.6, 7.9),
            ("templates yourse", 7.9, 9.0),
            ("Then you", 9.0, 9.7),
            ("schedule intervie", 9.7, 11.0),
            ("The AI", 11.0, 11.7),
            ("then runs the", 11.7, 12.6),
            ("interview while it", 12.6, 13.9),
            ("After that, you ge", 13.9, 15.1),
            ("stru", 15.1, 15.3),
            ("c", 15.3, 15.4),
            ("tured report with", 15.4, 16.7),
            ("Try Acme if", 16.7, 17.9),
        )
        segs = segment_transcript(result, duration=18.0)

        assert 1 < len(segs) < 8, f"expected consolidation, got {len(segs)}"
        for s in segs:
            dur = s["end"] - s["start"]
            # No fragment should slip past the floor.
            assert dur >= MIN_DUR or s["end"] == segs[-1]["end"], (
                f"fragment {s!r} below MIN_DUR={MIN_DUR}"
            )
            assert len(s["text"]) >= MIN_CHARS or s["end"] == segs[-1]["end"], (
                f"fragment {s!r} below MIN_CHARS={MIN_CHARS}"
            )

    def test_no_word_duplicated_across_boundary(self):
        """The mid-buffer split must use word boundaries, not char ratios."""
        result = _chunks(
            ("The cat sat on the mat and slept quietly for hours", 0.0, 15.0),
        )
        segs = segment_transcript(result, duration=15.0)
        if len(segs) < 2:
            pytest.skip("single segment — split path not exercised")
        joined = " ".join(s["text"] for s in segs)
        # No word should appear twice unless present twice in input
        for tok in ("cat", "mat", "quietly", "slept"):
            assert joined.count(tok) <= 1, f"word duplicated across boundary: {tok}"

    def test_respects_sentence_boundaries(self):
        result = _chunks(
            ("Hello, my name is Alice.", 0.0, 2.5),
            ("I work at a company in Boston.", 2.5, 5.5),
            ("We build software for hospitals.", 5.5, 8.5),
            ("It is complex but rewarding work.", 8.5, 11.5),
            ("Thanks for listening today.", 11.5, 14.0),
        )
        segs = segment_transcript(result, duration=14.0)
        # Every segment should end on a sentence terminator.
        for s in segs:
            assert s["text"].rstrip().endswith((".", "!", "?"))

    def test_enforces_max_dur(self):
        # Synthesize a single long chunk; should get split.
        long_text = " ".join(f"word{i}" for i in range(60))
        result = _chunks((long_text, 0.0, 20.0))
        segs = segment_transcript(result, duration=20.0)
        assert len(segs) >= 2
        for s in segs:
            dur = s["end"] - s["start"]
            # Allow small margin — best_boundary may land slightly past IDEAL.
            assert dur <= MAX_DUR + 1.0, f"segment {s!r} exceeds MAX_DUR"

    def test_single_short_input_returns_single_segment(self):
        result = _chunks(("Hello there.", 0.0, 1.5))
        segs = segment_transcript(result, duration=1.5)
        assert len(segs) == 1
        assert segs[0]["text"] == "Hello there."

    def test_empty_result_returns_empty(self):
        assert segment_transcript({}, duration=0.0) == []
        assert segment_transcript({"chunks": []}, duration=5.0) == []

    def test_missing_chunks_uses_flat_text(self):
        segs = segment_transcript({"text": "Short fallback."}, duration=2.0)
        assert len(segs) == 1
        assert segs[0]["text"] == "Short fallback."

    def test_ids_are_unique(self):
        result = _chunks(
            ("One sentence ends here.", 0.0, 3.0),
            ("Second one follows now.", 3.0, 6.0),
            ("A third rounds it out.", 6.0, 9.0),
        )
        segs = segment_transcript(result, duration=9.0)
        ids = [s["id"] for s in segs]
        assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# _merge_short
# ---------------------------------------------------------------------------

class TestMergeShort:
    def test_folds_fragment_into_previous(self):
        segs = [
            Segment(0.0, 3.0, "First segment is long enough.", "S1"),
            Segment(3.0, 3.4, "ok", "S1"),  # fragment
        ]
        merged = _merge_short(list(segs))
        assert len(merged) == 1
        assert merged[0].end == 3.4
        assert "ok" in merged[0].text

    def test_does_not_cross_speaker_boundary_when_gap_too_large(self):
        segs = [
            Segment(0.0, 3.0, "First segment is long enough.", "S1"),
            Segment(9.0, 9.4, "ok", "S2"),  # different speaker, far away
            Segment(10.0, 13.0, "Another good length segment ok.", "S2"),
        ]
        merged = _merge_short(list(segs))
        speakers = [m.speaker_id for m in merged]
        # The fragment should fold into S2 (same speaker) not S1.
        assert "S1" in speakers and "S2" in speakers
        # S1 segment text unchanged
        s1 = next(m for m in merged if m.speaker_id == "S1")
        assert s1.text == "First segment is long enough."

    def test_stranded_fragment_survives_when_no_neighbor_matches(self):
        segs = [Segment(0.0, 0.3, "hi", "S1")]
        merged = _merge_short(list(segs))
        # Orphan — nothing to merge with.
        assert len(merged) == 1


# ---------------------------------------------------------------------------
# _apply_scene_cuts
# ---------------------------------------------------------------------------

class TestApplySceneCuts:
    def test_splits_at_safe_cut(self):
        segs = [
            Segment(0.0, 6.0, "We are going to the store. The store sells groceries.", "S1"),
        ]
        out = _apply_scene_cuts(list(segs), [3.0])
        assert len(out) == 2
        assert out[0].end == 3.0
        assert out[1].start == 3.0

    def test_rejects_cut_producing_tiny_left(self):
        segs = [Segment(0.0, 6.0, "Hello world this sentence runs long enough for a cut.", "S1")]
        # Cut at 0.3 would leave < MIN_DUR on the left
        out = _apply_scene_cuts(list(segs), [0.3])
        assert len(out) == 1
        assert out[0].start == 0.0

    def test_rejects_cut_producing_tiny_right(self):
        segs = [Segment(0.0, 6.0, "Hello world this sentence runs long enough for a cut.", "S1")]
        # Cut at 5.9 would leave < MIN_DUR on the right
        out = _apply_scene_cuts(list(segs), [5.9])
        assert len(out) == 1

    def test_no_cuts_returns_input_untouched(self):
        segs = [Segment(0.0, 3.0, "Hello world test.", "S1")]
        out = _apply_scene_cuts(list(segs), [])
        assert out == segs


# ---------------------------------------------------------------------------
# Speaker assignment
# ---------------------------------------------------------------------------

class TestSpeakerAssignment:
    def test_heuristic_alternates_on_gap(self):
        segs = [
            {"start": 0.0, "end": 2.0, "text": "a", "id": "1", "speaker_id": "?"},
            {"start": 2.1, "end": 4.0, "text": "b", "id": "2", "speaker_id": "?"},  # small gap
            {"start": 6.0, "end": 8.0, "text": "c", "id": "3", "speaker_id": "?"},  # big gap → switch
        ]
        out = assign_speakers_heuristic(segs)
        assert out[0]["speaker_id"] == out[1]["speaker_id"]
        assert out[2]["speaker_id"] != out[1]["speaker_id"]

    @staticmethod
    def _gapped_segs(n, dur=2.0, gap=2.0):
        """n segments, each separated by a > SPEAKER_GAP silence."""
        segs = []
        t = 0.0
        for i in range(n):
            segs.append({"start": t, "end": t + dur, "text": f"s{i}", "id": str(i)})
            t += dur + gap
        return segs

    def test_heuristic_hint_three_speakers_cycles_three_labels(self):
        # Speaker-hint fix: num_speakers=3 must yield 3 distinct labels on
        # alternating-gap audio. Pre-fix the heuristic hardcoded 2 speakers
        # and silently ignored the hint.
        out = assign_speakers_heuristic(self._gapped_segs(6), num_speakers=3)
        labels = [s["speaker_id"] for s in out]
        assert labels == [
            "Speaker 1", "Speaker 2", "Speaker 3",
            "Speaker 1", "Speaker 2", "Speaker 3",
        ]
        assert len(set(labels)) == 3

    def test_heuristic_hint_one_speaker_single_label(self):
        out = assign_speakers_heuristic(self._gapped_segs(4), num_speakers=1)
        assert {s["speaker_id"] for s in out} == {"Speaker 1"}

    def test_heuristic_none_hint_preserves_legacy_two_speaker_alternation(self):
        out = assign_speakers_heuristic(self._gapped_segs(4), num_speakers=None)
        labels = [s["speaker_id"] for s in out]
        assert labels == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]

    @pytest.mark.parametrize("bad", [0, -3, "not-a-number"])
    def test_heuristic_invalid_hint_falls_back_to_legacy(self, bad):
        out = assign_speakers_heuristic(self._gapped_segs(4), num_speakers=bad)
        labels = [s["speaker_id"] for s in out]
        assert labels == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]

    def test_heuristic_hint_no_gaps_keeps_one_speaker(self):
        # N is an upper bound, not a quota: back-to-back speech with no
        # > SPEAKER_GAP pause stays one speaker even with a hint of 3.
        segs = [
            {"start": 0.0, "end": 2.0, "text": "a", "id": "1"},
            {"start": 2.1, "end": 4.0, "text": "b", "id": "2"},
            {"start": 4.2, "end": 6.0, "text": "c", "id": "3"},
        ]
        out = assign_speakers_heuristic(segs, num_speakers=3)
        assert {s["speaker_id"] for s in out} == {"Speaker 1"}

    def test_diarization_uses_overlap_weighted_assignment(self):
        # Build a fake diarization with two overlapping turns for the same seg;
        # the one with more overlap should win, not the one at midpoint.
        class FakeTurn:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class FakeDiar:
            def itertracks(self, yield_label=True):
                # SPEAKER_00 covers 0.0–1.0 (1.0s overlap with seg 0–2)
                # SPEAKER_01 covers 1.0–1.3 (0.3s overlap) — midpoint 1.0 → SPEAKER_01
                yield FakeTurn(0.0, 1.0), None, "SPEAKER_00"
                yield FakeTurn(1.0, 1.3), None, "SPEAKER_01"
                yield FakeTurn(1.3, 2.0), None, "SPEAKER_00"

        segs = [{"start": 0.0, "end": 2.0, "text": "x", "id": "1", "speaker_id": "?"}]
        out = assign_speakers_from_diarization(segs, FakeDiar())
        assert out[0]["speaker_id"] == "Speaker 1"  # SPEAKER_00 + 1

    def test_diarization_falls_back_to_midpoint_when_no_overlap(self):
        class FakeTurn:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class FakeDiar:
            def itertracks(self, yield_label=True):
                yield FakeTurn(10.0, 20.0), None, "SPEAKER_03"  # no overlap with 0–2

        segs = [{"start": 0.0, "end": 2.0, "text": "x", "id": "1", "speaker_id": "?"}]
        out = assign_speakers_from_diarization(segs, FakeDiar())
        # No overlap, no midpoint match — speaker_id stays "?"
        assert out[0]["speaker_id"] == "?"


# ---------------------------------------------------------------------------
# End-to-end contract
# ---------------------------------------------------------------------------

class TestSegmentDictContract:
    def test_returned_dicts_have_required_keys(self):
        result = _chunks(("Hello there friends, this is enough text.", 0.0, 3.0))
        segs = segment_transcript(result, duration=3.0)
        for s in segs:
            assert {"id", "start", "end", "text", "speaker_id"} <= set(s.keys())
            assert isinstance(s["start"], float)
            assert isinstance(s["end"], float)
            assert isinstance(s["text"], str)
            assert s["end"] > s["start"]


# ── #2572: segments without word timings keep their speech ──────────────────

@pytest.mark.parametrize("unaligned_first", [True, False])
def test_mixed_aligned_and_unaligned_segments_keep_all_speech(unaligned_first):
    aligned = {
        "start": 2.0, "end": 3.0, "text": "exact words",
        "words": [
            {"word": "exact", "start": 2.05, "end": 2.4},
            {"word": "words", "start": 2.5, "end": 2.95},
        ],
    }
    unaligned = {"start": 0.0, "end": 1.5, "text": " kept speech here ", "words": []}
    if not unaligned_first:
        unaligned = {"start": 4.0, "end": 5.5, "text": "kept speech here"}
    segs = [unaligned, aligned] if unaligned_first else [aligned, unaligned]

    words = _words_from_whisper({"segments": segs, "chunks": []})

    texts = [w.text for w in words]
    expected = ["kept", "speech", "here", "exact", "words"]
    assert texts == (expected if unaligned_first else expected[3:] + expected[:3])
    exact = [(w.start, w.end) for w in words if w.text in ("exact", "words")]
    assert exact == [(2.05, 2.4), (2.5, 2.95)]
    spread = [w for w in words if w.text in ("kept", "speech", "here")]
    base = 0.0 if unaligned_first else 4.0
    assert spread[0].start == pytest.approx(base)
    assert spread[-1].end == pytest.approx(base + 1.5)
    assert all(w.end > w.start for w in spread)


def test_unaligned_segment_without_a_usable_span_is_skipped():
    segs = [
        {"start": 0.0, "end": 1.0, "words": [{"word": "hi", "start": 0.1, "end": 0.4}]},
        {"start": None, "end": None, "text": "untimed"},
        {"start": 2.0, "end": 2.0, "text": "zero span"},
    ]
    assert [w.text for w in _words_from_whisper({"segments": segs})] == ["hi"]
