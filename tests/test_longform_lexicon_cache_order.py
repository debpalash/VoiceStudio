"""Pronunciation precedence must remain part of chapter cache identity."""
import os

import torch


def _render(directory, lexicon, calls):
    # Resolved at run time so a replaced sys.modules entry is never stale.
    from api.routers.audiobook import _render_chapter_cached
    from services.audiobook import Chapter, Span

    def synth(text, voice_id, speed=None):
        calls.append(text)
        return torch.full((2400,), 0.1)

    chapter = Chapter(title="C", spans=[Span(voice_id=None, text="GIF", pause_ms_after=0)])
    resolve = lambda _vid: {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}
    return _render_chapter_cached(chapter, synth, 24000, "fixture", resolve, str(directory), lexicon)


def test_reordering_case_variants_changes_chapter_audio_inputs(tmp_path):
    calls = []
    first = {"GIF": "first", "gif": "last"}
    second = {"gif": "last", "GIF": "first"}
    first_path, _, cached, _ = _render(tmp_path, first, calls)
    assert not cached and calls == ["last"]
    second_path, _, cached, _ = _render(tmp_path, second, calls)
    assert not cached and calls == ["last", "first"]
    assert first_path != second_path
    assert _render(tmp_path, second, calls)[2]
    assert calls == ["last", "first"]


def test_unchanged_lexicon_reuses_chapter_and_segments(tmp_path):
    calls = []
    lexicon = {"GIF": "jiff"}
    path, _, cached, _ = _render(tmp_path, lexicon, calls)
    assert not cached and calls == ["jiff"]
    assert _render(tmp_path, lexicon, calls)[2]
    os.remove(path)
    assert not _render(tmp_path, lexicon, calls)[2]
    assert calls == ["jiff"]

