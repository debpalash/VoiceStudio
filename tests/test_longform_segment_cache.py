"""Segment-level render cache under the longform chapter cache.

Covers the two-layer contract: segment key stability across every input
dimension, a one-sentence edit re-rendering exactly one segment, an
interrupted chapter resuming from its already-finished segments, missing /
corrupt / foreign-rate segment files degrading to a clean cache miss, the
byte-cap eviction walking both layers, and the fully-unchanged chapter
short-circuiting at the chapter key without ever touching segment files.
Drives the real `_render_chapter_cached` with a stub synth (no model/GPU).
"""
from __future__ import annotations

import os
import time
import wave

import pytest
import torch

from api.routers.audiobook import _render_chapter_cached
from services.audiobook import Chapter, Span
from services.longform_render import (
    SEGMENT_SUBDIR,
    chapter_cache_key,
    prune_cache_dir,
    segment_cache_key,
)

_SR = 24000
_SIG = "None|None|None|None"  # resolved signature of the no-profile voice


def _resolve(_voice_id):
    return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}


def _chapter(*texts, title="C1"):
    return Chapter(title=title, spans=[
        Span(voice_id=None, text=t, pause_ms_after=100) for t in texts
    ])


def _counting_synth(calls):
    def synth(text, voice_id, speed=None):
        calls.append(text)
        return torch.full((2400,), 0.1)  # 0.1 s @ 24k
    return synth


def _seg_path(tmp_path, text, **kw):
    key = segment_cache_key(text, sample_rate=_SR, engine_id="eng",
                            voice_id=None, voice_sig=_SIG, **kw)
    return tmp_path / SEGMENT_SUBDIR / f"{key}.wav"


# ── segment key ─────────────────────────────────────────────────────────────

def test_segment_key_deterministic():
    kw = dict(sample_rate=_SR, engine_id="eng", voice_id="v",
              voice_sig="a|b|c|1", speed=None, extra_sig="")
    a = segment_cache_key("Hello there.", **kw)
    assert a == segment_cache_key("Hello there.", **kw)
    assert len(a) == 20


@pytest.mark.parametrize("mutation", [
    {"text": "Different."},
    {"sample_rate": 44100},
    {"engine_id": "kokoro"},
    {"voice_id": "other"},
    {"voice_sig": "x|y|z|2"},
    {"speed": 0.8},
    {"extra_sig": '{"Dr": "Doctor"}'},
])
def test_segment_key_changes_on_any_dimension(mutation):
    kw = dict(text="Hello there.", sample_rate=_SR, engine_id="eng",
              voice_id="v", voice_sig="a|b|c|1", speed=None, extra_sig="")
    base = segment_cache_key(kw["text"], **{k: v for k, v in kw.items() if k != "text"})
    kw.update(mutation)
    mutated = segment_cache_key(kw.pop("text"), **kw)
    assert mutated != base


def test_chapter_key_golden_unchanged_by_segment_layer():
    # Locks the on-disk chapter key derivation: chapter caches written by
    # released versions must keep hitting after the segment layer landed.
    key = chapter_cache_key([(None, "hi", 0, None)], sample_rate=_SR,
                            engine_id="eng", voice_sig={"": _SIG})
    assert key == "ce2accacf51a70d04da0"


@pytest.mark.parametrize("keep_chapter", [True, False])
@pytest.mark.parametrize("languages", [("English", "French"), (None, "English")])
def test_language_change_invalidates_chapter_and_segment_audio(tmp_path, keep_chapter, languages):
    """Identical normalized words can be synthesized in different languages."""
    import soundfile as sf

    chapter = _chapter("Hello.")
    calls = []

    def render(language, amplitude):
        def synth(text, voice_id, speed=None):
            calls.append((language, text))
            return torch.full((2400,), amplitude)
        return _render_chapter_cached(chapter, synth, _SR, "eng", _resolve,
                                      str(tmp_path), language=language)

    first_language, second_language = languages
    first, *_ = render(first_language, 0.1)
    if not keep_chapter:
        os.remove(first)  # Force the second render to consult its segment layer.
    second, _dur, cached, stats = render(second_language, 0.2)
    assert cached is False
    assert stats == {"total": 1, "cached": 0}
    assert calls == [(first_language, "Hello."), (second_language, "Hello.")]
    assert first != second
    audio, _ = sf.read(second)
    # Compare the middle of the 2400-sample take: resampling can overshoot
    # its edges, and the chapter may append silence after it.
    assert audio[600:1800].mean() == pytest.approx(0.2, abs=0.001)
    _path, _dur, cached, stats = render(second_language, 0.3)
    assert cached is True
    assert stats is None
    assert len(calls) == 2


# ── one-sentence edit → one segment re-renders ──────────────────────────────

def test_one_sentence_edit_rerenders_exactly_one_segment(tmp_path):
    calls: list[str] = []
    _render_chapter_cached(_chapter("First sentence.", "Second sentence.", "Third sentence."),
                           _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert len(calls) == 3  # cold cache — everything synthesized

    calls.clear()
    edited = _chapter("First sentence.", "Second EDITED sentence.", "Third sentence.")
    wav_path, dur, cached, seg_stats = _render_chapter_cached(
        edited, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert cached is False  # chapter content changed → chapter-level miss
    assert calls == ["Second EDITED sentence."]  # only the edit synthesized
    assert seg_stats == {"total": 3, "cached": 2}
    assert os.path.isfile(wav_path) and dur > 0


# ── interrupted chapter resumes from finished segments ──────────────────────

def test_interrupted_chapter_resumes_from_cached_segments(tmp_path):
    calls: list[str] = []

    def failing(text, voice_id, speed=None):
        calls.append(text)
        if "THIRD" in text:
            raise RuntimeError("interrupted mid-chapter")
        return torch.full((2400,), 0.1)

    ch = _chapter("First sentence.", "Second sentence.", "THIRD sentence fails.")
    with pytest.raises(RuntimeError):
        _render_chapter_cached(ch, failing, _SR, "eng", _resolve, str(tmp_path))
    # The two spans that finished were persisted before the crash.
    assert len(list((tmp_path / SEGMENT_SUBDIR).glob("*.wav"))) == 2

    calls.clear()
    wav_path, _dur, cached, seg_stats = _render_chapter_cached(
        ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert cached is False
    assert calls == ["THIRD sentence fails."]  # only the lost span re-renders
    assert seg_stats == {"total": 3, "cached": 2}
    assert os.path.isfile(wav_path)


def test_resume_with_missing_segment_file(tmp_path):
    calls: list[str] = []
    ch = _chapter("First sentence.", "Second sentence.", "Third sentence.")
    wav_path, *_ = _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng",
                                          _resolve, str(tmp_path))
    # Evicted/deleted mid-way: chapter WAV and one segment gone.
    os.remove(wav_path)
    os.remove(_seg_path(tmp_path, "Second sentence."))

    calls.clear()
    _wav, _dur, cached, seg_stats = _render_chapter_cached(
        ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert cached is False
    assert calls == ["Second sentence."]
    assert seg_stats == {"total": 3, "cached": 2}


# ── corrupt / foreign segment files degrade to a clean miss ─────────────────

def test_corrupt_segment_file_is_clean_miss(tmp_path):
    calls: list[str] = []
    ch = _chapter("First sentence.", "Second sentence.")
    wav_path, *_ = _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng",
                                          _resolve, str(tmp_path))
    os.remove(wav_path)
    _seg_path(tmp_path, "First sentence.").write_bytes(b"not a wav at all")

    calls.clear()
    _wav, _dur, _cached, seg_stats = _render_chapter_cached(
        ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert calls == ["First sentence."]  # corrupt entry re-rendered, no crash
    assert seg_stats == {"total": 2, "cached": 1}


def test_wrong_sample_rate_segment_is_clean_miss(tmp_path):
    calls: list[str] = []
    ch = _chapter("First sentence.")
    wav_path, *_ = _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng",
                                          _resolve, str(tmp_path))
    os.remove(wav_path)
    # Overwrite the cached segment with a valid WAV at a foreign rate.
    p = _seg_path(tmp_path, "First sentence.")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)

    calls.clear()
    _wav, _dur, _cached, seg_stats = _render_chapter_cached(
        ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert calls == ["First sentence."]
    assert seg_stats == {"total": 1, "cached": 0}


# ── unchanged chapter short-circuits at the chapter layer ───────────────────

def test_unchanged_chapter_never_touches_segment_files(tmp_path):
    calls: list[str] = []
    ch = _chapter("First sentence.", "Second sentence.")
    _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))

    # Remove the whole segment layer: a chapter-level hit must not need it.
    for p in (tmp_path / SEGMENT_SUBDIR).glob("*.wav"):
        os.remove(p)
    (tmp_path / SEGMENT_SUBDIR).rmdir()

    def boom(*_a, **_k):
        raise AssertionError("synth must not be called on a chapter cache hit")

    _wav, dur, cached, seg_stats = _render_chapter_cached(
        ch, boom, _SR, "eng", _resolve, str(tmp_path))
    assert cached is True and dur > 0
    assert seg_stats is None
    assert not (tmp_path / SEGMENT_SUBDIR).exists()  # layer never recreated


# ── LRU byte cap covers both layers ─────────────────────────────────────────

def _seed(path, size, age_s):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def test_prune_walks_segment_subdir(tmp_path):
    chap = _seed(tmp_path / "chapter.wav", 600, age_s=5)
    old_seg = _seed(tmp_path / SEGMENT_SUBDIR / "old.wav", 600, age_s=100)
    new_seg = _seed(tmp_path / SEGMENT_SUBDIR / "new.wav", 600, age_s=1)

    remaining, removed = prune_cache_dir(str(tmp_path), max_bytes=1300)
    assert removed == 1
    assert not old_seg.exists()               # oldest evicted — inside segments/
    assert chap.exists() and new_seg.exists()
    assert remaining == 1200                  # both layers counted in the budget


def test_prune_evicts_stale_chapter_before_fresh_segment(tmp_path):
    old_chap = _seed(tmp_path / "stale_chapter.wav", 600, age_s=100)
    seg = _seed(tmp_path / SEGMENT_SUBDIR / "fresh.wav", 600, age_s=1)
    remaining, removed = prune_cache_dir(str(tmp_path), max_bytes=700)
    assert removed == 1
    assert not old_chap.exists() and seg.exists()
    assert remaining == 600


def test_segment_hit_refreshes_mtime_for_lru(tmp_path):
    calls: list[str] = []
    ch = _chapter("First sentence.")
    wav_path, *_ = _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng",
                                          _resolve, str(tmp_path))
    os.remove(wav_path)  # force the segment layer on the next run
    seg = _seg_path(tmp_path, "First sentence.")
    stale = time.time() - 10_000
    os.utime(seg, (stale, stale))

    _render_chapter_cached(ch, _counting_synth(calls), _SR, "eng", _resolve, str(tmp_path))
    assert os.path.getmtime(seg) > stale + 1_000  # hit bumped it — LRU-fresh
