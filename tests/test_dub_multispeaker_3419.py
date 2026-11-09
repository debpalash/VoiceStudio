"""Multi-speaker dubbing regressions (#3419).

Synthetic diarization turns + word timestamps only: no model, no download.

* A short reply ("Good, and you?") must not stay merged into the previous
  speaker's line on any transcription path. The SSE path split it since #486;
  the synchronous ``/dub/transcribe`` (CLI, phone companion) only relabelled.
* A diarized speaker who only answers in short turns must still get a clone
  reference, cut clear of the other speaker's audio, instead of the default
  voice.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
import soundfile as sf

from services.segmentation import (
    label_segments_by_diarization,
    resplit_segments_by_turns,
    Word,
)
from services.speaker_clone import (
    ADJACENT_TURN_GUARD_S,
    MIN_SLICE_DURATION_S,
    build_cast_sources,
    extract_segment_refs,
    extract_speaker_clones,
)

SR = 16000

# "Hello, how are you?" (SPEAKER_00) then "Good, and you?" (SPEAKER_01).
WORDS = [
    ("Hello,", 0.0, 0.4), ("how", 0.45, 0.7), ("are", 0.7, 0.9), ("you?", 0.9, 1.3),
    ("Good,", 1.4, 1.8), ("and", 1.85, 2.0), ("you?", 2.0, 2.4),
]


class _Turn:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _FakeDiar:
    def __init__(self, turns):
        self._turns = turns

    def itertracks(self, yield_label=True):
        for start, end, label in self._turns:
            yield _Turn(start, end), None, label


DIAR = _FakeDiar([(0.0, 1.35, "SPEAKER_00"), (1.35, 2.5, "SPEAKER_01")])


def _whisper_result():
    return {
        "language": "en",
        "segments": [{
            "start": 0.0, "end": 2.4,
            "text": " ".join(w for w, _, _ in WORDS),
            "words": [{"word": w, "start": s, "end": e} for w, s, e in WORDS],
        }],
    }


def _speakers(segments):
    return [(s["text"], s["speaker_id"]) for s in segments]


def test_label_segments_by_diarization_splits_a_merged_reply():
    merged = [{"id": "s1", "start": 0.0, "end": 2.4, "speaker_id": "Speaker 1",
               "text": "Hello, how are you? Good, and you?"}]
    words = [Word(start=s, end=e, text=w) for w, s, e in WORDS]
    out = label_segments_by_diarization(merged, words, DIAR)
    assert _speakers(out) == [
        ("Hello, how are you?", "Speaker 1"),
        ("Good, and you?", "Speaker 2"),
    ]


@pytest.mark.usefixtures("asr_model_installed")
def test_sync_transcribe_splits_turns_at_the_speaker_boundary(tmp_path, monkeypatch):
    from api.routers import dub_core as dc

    audio = tmp_path / "audio.wav"
    vocals = tmp_path / "vocals.wav"
    for path in (audio, vocals):
        sf.write(str(path), np.zeros(3 * SR, dtype=np.float32), SR)
    job = {"audio_path": str(audio), "vocals_path": str(vocals), "duration": 3.0,
           "segments": None, "scene_cuts": []}

    class _ASR:
        id = "fake"

        def transcribe(self, path, word_timestamps=True):
            return _whisper_result()

        def unload(self):
            pass

    monkeypatch.setattr("services.asr_backend.load_active_asr_backend",
                        lambda **_kw: _ASR())
    monkeypatch.setattr(dc, "_get_job", lambda _id: job)
    monkeypatch.setattr(dc, "_save_job", lambda *a, **k: None)
    monkeypatch.setattr(dc, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(dc, "_ensure_diarisation_accepted", lambda: None)
    hints = []

    def _pipe(target, **kwargs):
        hints.append(kwargs.get("num_speakers"))
        return DIAR

    monkeypatch.setattr(dc, "get_diarization_pipeline", lambda: _pipe)

    out = asyncio.run(dc.dub_transcribe("job-3419", num_speakers=2))

    assert hints == [2]
    assert _speakers(out["segments"]) == [
        ("Hello, how are you?", "Speaker 1"),
        ("Good, and you?", "Speaker 2"),
    ]


def _voice(duration_s, f0=140.0, amp=0.5):
    """Harmonic-rich voiced tone, a stand-in for a vowel."""
    t = np.arange(int(duration_s * SR)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


def _whisper(duration_s, amp=0.3, seed=3):
    """Band-limited noise: the spectrum of a whispered (unvoiced) reply."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * SR)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spec[(freqs < 500) | (freqs > 3000)] = 0
    x = np.fft.irfft(spec, n)
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


def _voiced_then_whispered():
    """Voiced 0–1.3 s ("Hello, how are you?"), whispered 1.4–2.4 s ("Good, and you?")."""
    return np.concatenate([_voice(1.3), np.zeros(SR // 10, np.float32),
                           _whisper(1.0), np.zeros(int(0.6 * SR), np.float32)])


def _whispered_then_voiced():
    """Whispered 0–1.3 s ("Hello, how are you?"), voiced 1.4–2.4 s ("Good, and you?")."""
    return np.concatenate([_whisper(1.3), np.zeros(SR // 10, np.float32),
                           _voice(1.0), np.zeros(int(0.6 * SR), np.float32)])


def _check_voiced_then_whispered(segments):
    first, reply = segments
    assert first["end"] < 1.6  # the voiced line still ends on its speech
    assert reply["end"] == pytest.approx(2.4)  # whispered: no voiced anchor


def _check_whispered_then_voiced(segments):
    # Snapping the shared segment's start to the first voiced onset (~1.4 s)
    # before the re-split cut the whispered speaker's words out of it.
    first, reply = segments
    assert first["start"] < 0.2
    assert reply["start"] == pytest.approx(1.4, abs=0.1)


SHARED_LINE_CASES = [
    pytest.param(_voiced_then_whispered, _check_voiced_then_whispered, id="whispered-reply"),
    pytest.param(_whispered_then_voiced, _check_whispered_then_voiced, id="whispered-opener"),
]


@pytest.mark.usefixtures("asr_model_installed")
@pytest.mark.parametrize("make_vocals, check", SHARED_LINE_CASES)
def test_sync_transcribe_keeps_a_whispered_speaker_sharing_a_voiced_line(
    tmp_path, monkeypatch, make_vocals, check,
):
    # One ASR segment holding a voiced and a whispered speaker. Snapping its
    # start or trimming its end to voiced speech before the speaker re-split
    # cut the whispered speaker's words out. Both edges are now aligned per
    # speaker line, after the re-split.
    from api.routers import dub_core as dc

    audio = tmp_path / "audio.wav"
    vocals = tmp_path / "vocals.wav"
    sf.write(str(audio), np.zeros(3 * SR, dtype=np.float32), SR)
    sf.write(str(vocals), make_vocals(), SR)
    job = {"audio_path": str(audio), "vocals_path": str(vocals), "duration": 3.0,
           "segments": None, "scene_cuts": []}

    class _ASR:
        id = "fake"

        def transcribe(self, path, word_timestamps=True):
            return _whisper_result()

        def unload(self):
            pass

    monkeypatch.setattr("services.asr_backend.load_active_asr_backend",
                        lambda **_kw: _ASR())
    monkeypatch.setattr(dc, "_get_job", lambda _id: job)
    monkeypatch.setattr(dc, "_save_job", lambda *a, **k: None)
    monkeypatch.setattr(dc, "should_preload_tts_asr", lambda: False)
    monkeypatch.setattr(dc, "_ensure_diarisation_accepted", lambda: None)
    monkeypatch.setattr(dc, "get_diarization_pipeline", lambda: lambda *_a, **_k: DIAR)

    out = asyncio.run(dc.dub_transcribe("job-3419-whisper"))

    assert _speakers(out["segments"]) == [
        ("Hello, how are you?", "Speaker 1"),
        ("Good, and you?", "Speaker 2"),
    ]
    check(out["segments"])


@pytest.mark.usefixtures("asr_model_installed")
@pytest.mark.parametrize("make_vocals, check", SHARED_LINE_CASES)
def test_stream_transcribe_keeps_a_whispered_speaker_sharing_a_voiced_line(
    tmp_path, monkeypatch, make_vocals, check,
):
    # Same shape on the SSE path: the per-chunk pass aligned the shared
    # segment before the final diarization re-split.
    import json
    from unittest.mock import MagicMock

    from api.routers import dub_core as dc

    audio = tmp_path / "audio.wav"
    vocals = tmp_path / "vocals.wav"
    sf.write(str(audio), np.zeros(3 * SR, dtype=np.float32), SR)
    sf.write(str(vocals), make_vocals(), SR)
    dc._dub_jobs["job-3419-sse"] = {"audio_path": str(audio), "vocals_path": str(vocals),
                                    "scene_cuts": []}

    class _ASR:
        id = "fake"

        def ensure_loaded(self):
            pass

        def transcribe(self, path, word_timestamps=True):
            # The streaming path reads word-level chunks.
            return {"language": "en",
                    "chunks": [{"text": w, "timestamp": (a, b)} for w, a, b in WORDS]}

        def unload(self):
            pass

    model = MagicMock()

    async def _model():
        return model

    monkeypatch.setattr(dc, "get_model", _model)
    monkeypatch.setattr("services.asr_backend.get_active_asr_backend", lambda *a, **k: _ASR())
    pipe = lambda *_a, **_k: DIAR  # noqa: E731
    monkeypatch.setattr(dc, "get_diarization_pipeline",
                        lambda return_error=False: (pipe, None) if return_error else pipe)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "restore_tts_after_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_save_job", lambda *a, **k: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda *a, **k: None)

    async def _collect():
        resp = await dc.dub_transcribe_stream("job-3419-sse")
        return "".join([c.decode() if isinstance(c, (bytes, bytearray)) else str(c)
                        async for c in resp.body_iterator])

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop("job-3419-sse", None)
    assert "event: final" in body, body[-1500:]
    final = body.split("event: final", 1)[1]
    segments = json.loads(final.split("data:", 1)[1].split("\n\n", 1)[0])["segments"]
    assert _speakers(segments) == [
        ("Hello, how are you?", "Speaker 1"),
        ("Good, and you?", "Speaker 2"),
    ]
    check(segments)


# ── Every diarized speaker gets a clone ─────────────────────────────────────

A_LEVEL, B_LEVEL = 0.9, -0.4


def _exchange(tmp_path, reply_len=1.3, rounds=4):
    """Speaker 1 talks 4 s, Speaker 2 replies ``reply_len`` s 0.1 s later.

    Each speaker's audio is a distinct constant so a reference can be checked
    for the other speaker's samples."""
    audio = np.zeros(int(rounds * 6.0 * SR) + SR, dtype=np.float32)
    segments = []
    for k in range(rounds):
        t = k * 6.0
        a = (t, t + 4.0)
        b = (t + 4.1, t + 4.1 + reply_len)
        audio[int(a[0] * SR):int(a[1] * SR)] = A_LEVEL
        audio[int(b[0] * SR):int(b[1] * SR)] = B_LEVEL
        segments.append({"id": f"a{k}", "start": a[0], "end": a[1],
                         "speaker_id": "Speaker 1", "text": "long line"})
        segments.append({"id": f"b{k}", "start": b[0], "end": b[1],
                         "speaker_id": "Speaker 2", "text": "Good, and you?"})
    path = tmp_path / "vocals.wav"
    sf.write(str(path), audio, SR)
    return str(path), segments


def test_short_turn_speaker_gets_a_clean_clone(tmp_path):
    vocals, segments = _exchange(tmp_path)
    assert 1.3 < MIN_SLICE_DURATION_S  # premise: every reply is "too short"

    clones = extract_speaker_clones(vocals, segments, str(tmp_path / "refs"),
                                    labels_source="pyannote")

    assert set(clones) == {"Speaker 1", "Speaker 2"}
    ref, _ = sf.read(clones["Speaker 2"]["ref_audio"], dtype="float32")
    assert not np.any(np.isclose(ref, A_LEVEL, atol=1e-3)), "Speaker 1 bled into Speaker 2"
    assert np.isclose(ref, B_LEVEL, atol=1e-3).sum() / SR >= 3.0
    # Each reply loses the guard next to Speaker 1's line.
    assert clones["Speaker 2"]["duration"] == pytest.approx(
        4 * (1.3 - (ADJACENT_TURN_GUARD_S - 0.1)) + 3 * 0.02, abs=0.01)
    sources = build_cast_sources(segments, clones, {})
    assert set(sources) == {"Speaker 1", "Speaker 2"}


def test_speaker_with_too_little_clean_audio_still_falls_back(tmp_path):
    vocals, segments = _exchange(tmp_path, reply_len=1.0, rounds=2)
    clones = extract_speaker_clones(vocals, segments, str(tmp_path / "refs"),
                                    labels_source="pyannote")
    assert set(clones) == {"Speaker 1"}


def test_line_with_another_speaker_inside_is_not_used(tmp_path):
    vocals, segments = _exchange(tmp_path)
    for k in range(4):  # Speaker 1 interjects in the middle of every reply
        t = k * 6.0 + 4.6
        segments.append({"id": f"x{k}", "start": t, "end": t + 0.2,
                         "speaker_id": "Speaker 1", "text": "mm"})
    clones = extract_speaker_clones(vocals, segments, str(tmp_path / "refs"),
                                    labels_source="pyannote")
    assert "Speaker 2" not in clones


def test_heuristic_labels_still_get_no_short_turn_clone(tmp_path):
    vocals, segments = _exchange(tmp_path)
    assert extract_speaker_clones(vocals, segments, str(tmp_path / "refs"),
                                  labels_source="heuristic") == {}


# ── A trimmed reference never names words whose audio was cut ───────────────

def _with_reply_words(segments):
    for seg in segments:
        if seg["speaker_id"] == "Speaker 2":
            t = seg["start"]
            seg["words"] = [  # "Good," sits in the trimmed guard next to Speaker 1
                {"text": "Good,", "start": t, "end": t + 0.15},
                {"text": "and", "start": t + 0.25, "end": t + 0.5},
                {"text": "you?", "start": t + 0.5, "end": t + 1.2},
            ]
    return segments


def test_trimmed_reference_text_keeps_only_words_inside_the_slice(tmp_path):
    vocals, segments = _exchange(tmp_path)
    clones = extract_speaker_clones(vocals, _with_reply_words(segments),
                                    str(tmp_path / "refs"), labels_source="pyannote")
    assert clones["Speaker 2"]["ref_text"] == " ".join(["and you?"] * 4)
    # Untrimmed long lines keep their full text.
    assert clones["Speaker 1"]["ref_text"].startswith("long line")


def test_trimmed_reference_without_word_timestamps_has_empty_text(tmp_path):
    # Unknown transcript -> empty, so the engine transcribes the clip itself
    # instead of trusting text that names cut words.
    vocals, segments = _exchange(tmp_path)
    clones = extract_speaker_clones(vocals, segments, str(tmp_path / "refs"),
                                    labels_source="pyannote")
    assert clones["Speaker 2"]["ref_text"] == ""


# ── Follow-up: one-word replies, uncovered lines, overlapping per-line refs ──

def _turns(*spans):
    return [{"start": s, "end": e, "speaker": spk} for s, e, spk in spans]


REPLY_WORDS = [
    ("How", 0.0, 0.2), ("are", 0.2, 0.4), ("you?", 0.4, 0.8),
    ("Fine.", 1.0, 1.4),
    ("Good", 1.6, 1.8), ("to", 1.8, 1.9), ("hear.", 1.9, 2.6),
]


def _reply_segment(text="How are you? Fine. Good to hear."):
    return {"id": "s1", "start": 0.0, "end": 2.6, "speaker_id": "Speaker 1", "text": text}


def test_one_word_reply_is_split_off_not_smoothed_away():
    # B's whole reply is one word; single-word smoothing folded it into A.
    words = [Word(s, e, t) for t, s, e in REPLY_WORDS]
    turns = _turns((0.0, 0.9, "Speaker 1"), (0.9, 1.5, "Speaker 2"), (1.5, 2.6, "Speaker 1"))
    out = resplit_segments_by_turns([_reply_segment()], words, turns)
    assert _speakers(out) == [
        ("How are you?", "Speaker 1"),
        ("Fine.", "Speaker 2"),
        ("Good to hear.", "Speaker 1"),
    ]
    assert len({s["id"] for s in out}) == 3


def test_mid_sentence_one_word_blip_is_still_smoothed():
    # Same coverage, but the flipped word sits inside a sentence: noise.
    seg = _reply_segment("I think fine things happen.")
    words = [Word(s, e, t) for t, s, e in [
        ("I", 0.0, 0.2), ("think", 0.2, 0.8), ("fine", 1.0, 1.4),
        ("things", 1.6, 2.0), ("happen.", 2.0, 2.6),
    ]]
    turns = _turns((0.0, 0.9, "Speaker 1"), (0.9, 1.5, "Speaker 2"), (1.5, 2.6, "Speaker 1"))
    assert resplit_segments_by_turns([seg], words, turns) == [seg]


def test_one_word_inside_overlapping_speech_is_still_smoothed():
    # Both speakers' turns cover the word: overlapped speech, not a reply.
    words = [Word(s, e, t) for t, s, e in REPLY_WORDS]
    turns = _turns((0.0, 2.6, "Speaker 1"), (0.9, 1.5, "Speaker 2"))
    seg = _reply_segment()
    assert resplit_segments_by_turns([seg], words, turns) == [seg]


def test_reply_word_in_a_diarization_gap_takes_the_nearest_turn():
    # The diarizer left a gap where "Good," sits: filling from the left
    # neighbour handed it to Speaker 1 and split the reply mid-sentence.
    seg = {"id": "s1", "start": 0.0, "end": 2.4, "speaker_id": "Speaker 1",
           "text": "Hello, how are you? Good, and you?"}
    words = [Word(start=s, end=e, text=w) for w, s, e in WORDS]
    turns = _turns((0.0, 1.1, "Speaker 1"), (1.9, 2.5, "Speaker 2"))
    out = resplit_segments_by_turns([seg], words, turns)
    assert _speakers(out) == [
        ("Hello, how are you?", "Speaker 1"),
        ("Good, and you?", "Speaker 2"),
    ]


def test_line_outside_every_turn_drops_its_pre_diarization_label():
    # The streaming pass labels lines by silence gaps first. A short line the
    # diarizer did not cover kept that estimate ("Speaker 3" of a 3-speaker
    # rotation) and showed up as a speaker diarization never found.
    segs = [
        {"id": "a", "start": 0.0, "end": 3.0, "speaker_id": "Speaker 1", "text": "a"},
        {"id": "b", "start": 3.4, "end": 3.8, "speaker_id": "Speaker 3", "text": "Yes."},
        {"id": "c", "start": 4.5, "end": 8.0, "speaker_id": "Speaker 2", "text": "c"},
    ]
    diar = _FakeDiar([(0.0, 3.0, "SPEAKER_00"), (4.4, 8.0, "SPEAKER_01")])
    out = label_segments_by_diarization(segs, [], diar)
    assert [s["speaker_id"] for s in out] == ["Speaker 1", "Speaker 1", "Speaker 2"]


def test_per_line_reference_skips_lines_with_overlapping_speech(tmp_path):
    vocals = tmp_path / "vocals.wav"
    sf.write(vocals, np.zeros(SR * 12, dtype=np.float32), SR)
    segs = [
        {"id": "a", "start": 0.0, "end": 5.0, "speaker_id": "Speaker 1", "text": "a"},
        {"id": "b", "start": 4.0, "end": 8.0, "speaker_id": "Speaker 2", "text": "b"},
        {"id": "c", "start": 8.0, "end": 12.0, "speaker_id": "Speaker 1", "text": "c"},
    ]
    refs = extract_segment_refs(str(vocals), segs, str(tmp_path / "refs"),
                                seg_ids=[s["id"] for s in segs])
    # a and b overlap, so each clip holds two voices; c only touches b's end.
    assert sorted(refs) == ["c"]
