"""Clone-purity guards in services.speaker_clone (speaker-hint fix).

A per-speaker auto-clone reference cut from mislabeled or boundary-adjacent
audio mixes two people's voices — the field-reported "made up" clone voices.
These tests pin the three guards:

  * per-slice minimum duration (MIN_SLICE_DURATION_S),
  * non-adjacency scoring preference (ADJACENT_TURN_GUARD_S) — a preference,
    never a hard filter,
  * labels_source="heuristic" skips extraction entirely.

Pure tests over a synthetic vocals wav — no model, no main import.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from queue import Queue
from threading import Semaphore

import numpy as np
import pytest
import soundfile as sf

from services.speaker_clone import (
    ADJACENT_TURN_GUARD_S,
    MIN_REF_DURATION_S,
    MIN_SLICE_DURATION_S,
    _pick_reference_slices,
    build_cast_sources,
    extract_speaker_clones,
    refine_ref_text,
    refine_ref_texts,
)
from services.asr_backend import ASRTimeoutError, run_transcribe_guarded

SR = 16000


@pytest.fixture
def vocals(tmp_path):
    # 60 s of non-silent audio so every segment slice has content.
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.float32(np.sin(np.linspace(0, 18000, 60 * SR))), SR)
    return str(path)


def _seg(start, end, speaker="Speaker 1", text="hello there"):
    return {"start": start, "end": end, "speaker_id": speaker, "text": text}


class TestPickReferenceSlices:
    def test_rejects_slices_below_minimum_duration(self):
        # Fragments under MIN_SLICE_DURATION_S never join a reference while the
        # speaker has enough long slices. (A speaker with ONLY short turns uses
        # the guarded short-turn fallback instead — tests/test_dub_multispeaker_3419.py.)
        long_items = [(10 + i, _seg(30.0 + i * 5.0, 34.0 + i * 5.0)) for i in range(2)]
        short_items = [(i, _seg(i * 3.0, i * 3.0 + 1.0)) for i in range(6)]
        assert MIN_SLICE_DURATION_S > 1.0  # test premise
        assert 2 * 4.0 >= MIN_REF_DURATION_S
        chosen = _pick_reference_slices(short_items + long_items)
        assert [idx for idx, _ in chosen] == [10, 11]

    def test_prefers_slice_not_adjacent_to_other_speaker(self):
        # Two equal-length candidates for Speaker 1; the first is 0.1 s away
        # from a Speaker 2 turn (< ADJACENT_TURN_GUARD_S), the second is far
        # from everyone. The clean one must win the ranking.
        adjacent = _seg(0.0, 8.0, "Speaker 1")
        other = _seg(8.1, 10.0, "Speaker 2")
        clean = _seg(20.0, 28.0, "Speaker 1")
        all_segments = [adjacent, other, clean]
        items = [(0, adjacent), (2, clean)]
        chosen = _pick_reference_slices(
            items, speaker_id="Speaker 1", all_segments=all_segments,
        )
        assert [seg for _, seg in chosen] == [clean]

    def test_adjacency_is_a_preference_not_a_hard_filter(self):
        # Dense dialogue: every Speaker 1 slice borders a Speaker 2 turn.
        # Extraction must still succeed using the adjacent slices.
        s1a = _seg(0.0, 6.0, "Speaker 1")
        s2a = _seg(6.1, 8.0, "Speaker 2")
        s1b = _seg(8.2, 12.0, "Speaker 1")
        all_segments = [s1a, s2a, s1b]
        items = [(0, s1a), (2, s1b)]
        chosen = _pick_reference_slices(
            items, speaker_id="Speaker 1", all_segments=all_segments,
        )
        assert chosen, "dense dialogue must still produce a reference"

    def test_heuristic_labels_source_returns_nothing(self):
        items = [(0, _seg(0.0, 8.0))]
        assert _pick_reference_slices(items, labels_source="heuristic") == []

    def test_legacy_call_without_kwargs_still_picks_long_slice(self):
        # Backward compat: positional-only invocation (the pre-fix signature)
        # keeps working and picks the long slice.
        long_seg = _seg(0.0, 8.0)
        chosen = _pick_reference_slices([(0, long_seg)])
        assert [seg for _, seg in chosen] == [long_seg]

    def test_overlapping_other_speaker_counts_as_adjacent(self):
        # Negative gap (overlap) must also be flagged — that is the worst
        # mixed-audio case of all.
        overlapped = _seg(0.0, 8.0, "Speaker 1")
        other = _seg(4.0, 6.0, "Speaker 2")
        clean = _seg(20.0, 28.0, "Speaker 1")
        chosen = _pick_reference_slices(
            [(0, overlapped), (2, clean)],
            speaker_id="Speaker 1",
            all_segments=[overlapped, other, clean],
        )
        assert [seg for _, seg in chosen] == [clean]


class TestExtractSpeakerClones:
    def test_heuristic_labels_source_skips_extraction(self, tmp_path, vocals):
        segs = [_seg(0.0, 8.0), _seg(10.0, 18.0, "Speaker 2")]
        out = extract_speaker_clones(
            vocals, segs, str(tmp_path), labels_source="heuristic",
        )
        assert out == {}

    @pytest.mark.parametrize("source", [None, "pyannote", "turns"])
    def test_trusted_labels_still_extract(self, tmp_path, vocals, source):
        # None (legacy caller, missing kwarg) and real diarization sources
        # keep the current behavior: clones are produced.
        segs = [_seg(0.0, 8.0), _seg(10.0, 18.0, "Speaker 2")]
        kwargs = {} if source is None else {"labels_source": source}
        out = extract_speaker_clones(vocals, segs, str(tmp_path), **kwargs)
        assert set(out) == {"Speaker 1", "Speaker 2"}
        for info in out.values():
            assert info["duration"] >= MIN_REF_DURATION_S

    def test_adjacency_guard_constant_sane(self):
        # The guard must stay tighter than the heuristic's own gap threshold,
        # or every real turn boundary would be flagged.
        from services.segmentation import SPEAKER_GAP
        assert 0 < ADJACENT_TURN_GUARD_S < SPEAKER_GAP


class TestBuildCastSources:
    def test_exposes_segment_reference_when_no_pooled_clone_exists(self):
        segments = [
            {"id": "a", "speaker_id": "Speaker 1"},
            {"id": "b", "speaker_id": "Speaker 1"},
        ]
        sources = build_cast_sources(
            segments,
            {},
            {
                "a": {"ref_audio": "/private/a.wav", "duration": 3.1},
                "b": {"ref_audio": "/private/b.wav", "duration": 6.4},
            },
        )
        assert sources == {
            "Speaker 1": {"duration": 6.4, "source_count": 1, "kind": "segment"}
        }
        assert "/private" not in repr(sources)

    def test_pooled_clone_wins_and_private_fields_do_not_cross_api(self):
        sources = build_cast_sources(
            [{"id": "a", "speaker_id": "Speaker 1"}],
            {
                "Speaker 1": {
                    "ref_audio": "/private/speaker.wav",
                    "ref_text": "secret transcript",
                    "duration": 8.2,
                    "source_count": 2,
                }
            },
            {"a": {"ref_audio": "/private/a.wav", "duration": 12.0}},
        )
        assert sources == {
            "Speaker 1": {"duration": 8.2, "source_count": 2, "kind": "speaker"}
        }


class _FakeASR:
    """Stands in for the active ASR backend's .transcribe() — no model, no
    network. `chunks_by_path` maps a ref_audio path to the canned chunk list
    that path's re-transcription should return."""
    def __init__(self, chunks_by_path=None, raises_for=()):
        self.chunks_by_path = chunks_by_path or {}
        self.raises_for = set(raises_for)
        self.calls = []

    def transcribe(self, path, *, word_timestamps=True):
        self.calls.append(path)
        if path in self.raises_for:
            raise RuntimeError("simulated ASR failure")
        return {"chunks": self.chunks_by_path.get(path, []), "language": "es"}


class TestRefineRefText:
    # Issue #1004: the ASR segment's `text` field and its `[start, end]`
    # timestamps routinely drift (a trailing word audible in the slice but
    # missing from the text, or vice versa) — pairing a mismatched (ref_audio,
    # ref_text) breaks zero-shot TTS prompt priming badly enough that the
    # clone can speak the reference text verbatim instead of the target text.
    # Re-transcribing the actual written clip guarantees the pair matches.

    def test_replaces_mismatched_text_with_the_actual_clip_transcript(self):
        asr = _FakeASR(chunks_by_path={
            "/tmp/ref.wav": [{"text": "hola"}, {"text": "que tal"}],
        })
        out = refine_ref_text("/tmp/ref.wav", asr, fallback_text="mismatched source text")
        assert out == "hola que tal"
        assert asr.calls == ["/tmp/ref.wav"]

    def test_falls_back_to_original_text_on_asr_failure(self):
        asr = _FakeASR(raises_for={"/tmp/ref.wav"})
        out = refine_ref_text("/tmp/ref.wav", asr, fallback_text="original text")
        assert out == "original text"

    def test_falls_back_to_original_text_on_empty_transcript(self):
        # A clip ASR can't get any text out of (e.g. near-silent) shouldn't
        # wipe out a usable original — empty is worse than stale.
        asr = _FakeASR(chunks_by_path={"/tmp/ref.wav": []})
        out = refine_ref_text("/tmp/ref.wav", asr, fallback_text="original text")
        assert out == "original text"

    def test_no_asr_backend_is_a_strict_no_op(self):
        # Preflight ASR load failure, or any other reason the caller has no
        # backend to hand in — never a crash, never blocks the original path.
        out = refine_ref_text("/tmp/ref.wav", None, fallback_text="original text")
        assert out == "original text"


class TestRefineRefTexts:
    def test_refines_every_entry_in_place_and_returns_the_dict(self):
        asr = _FakeASR(chunks_by_path={
            "/tmp/spk1.wav": [{"text": "hola amigo"}],
            "/tmp/spk2.wav": [{"text": "buenos dias"}],
        })
        clones = {
            "Speaker 1": {"ref_audio": "/tmp/spk1.wav", "ref_text": "stale 1"},
            "Speaker 2": {"ref_audio": "/tmp/spk2.wav", "ref_text": "stale 2"},
        }
        out = refine_ref_texts(clones, asr)
        assert out is clones  # mutated in place, returned for call-and-reassign
        assert clones["Speaker 1"]["ref_text"] == "hola amigo"
        assert clones["Speaker 2"]["ref_text"] == "buenos dias"

    def test_a_failing_entry_does_not_affect_the_others(self):
        asr = _FakeASR(
            chunks_by_path={"/tmp/spk2.wav": [{"text": "buenos dias"}]},
            raises_for={"/tmp/spk1.wav"},
        )
        clones = {
            "Speaker 1": {"ref_audio": "/tmp/spk1.wav", "ref_text": "kept on failure"},
            "Speaker 2": {"ref_audio": "/tmp/spk2.wav", "ref_text": "stale 2"},
        }
        refine_ref_texts(clones, asr)
        assert clones["Speaker 1"]["ref_text"] == "kept on failure"
        assert clones["Speaker 2"]["ref_text"] == "buenos dias"


class _PausedRecognizer:
    def __init__(self):
        self.arrivals = Queue(maxsize=1)
        self.permission = Semaphore(0)

    def transcribe(self, path, *, word_timestamps=True):
        self.arrivals.put_nowait(path)
        self.permission.acquire()
        return {"chunks": [{"text": "replacement text"}], "language": "en"}

    def wait_until_called(self):
        return self.arrivals.get(timeout=1)

    def resume(self):
        self.permission.release()


def test_refine_ref_texts_has_no_internal_wall_clock_timeout():
    recognizer = _PausedRecognizer()
    originals = {"Speaker 1": {"ref_audio": "/tmp/pause.wav", "ref_text": "source"}}
    worker = ThreadPoolExecutor(max_workers=1)
    pending = worker.submit(refine_ref_texts, originals, recognizer)
    assert recognizer.wait_until_called() == "/tmp/pause.wav"
    try:
        with pytest.raises(FutureTimeoutError):
            pending.result(timeout=0.02)
    finally:
        recognizer.resume()
        worker.shutdown(wait=False)


def test_guarded_refine_times_out_without_changing_original_text():
    recognizer = _PausedRecognizer()
    originals = {"Speaker 1": {"ref_audio": "/tmp/guard.wav", "ref_text": "unchanged"}}
    worker = ThreadPoolExecutor(max_workers=1)

    async def guarded_call():
        return await run_transcribe_guarded(
            worker,
            lambda: refine_ref_texts(originals, recognizer),
            what="blocked reference-text check",
            timeout=0.02,
        )

    try:
        with pytest.raises(ASRTimeoutError):
            asyncio.run(guarded_call())
        assert recognizer.wait_until_called() == "/tmp/guard.wav"
        assert originals["Speaker 1"]["ref_text"] == "unchanged"
    finally:
        recognizer.resume()
        worker.shutdown(wait=False)
