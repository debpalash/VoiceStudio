"""Inline ``[[…]]`` pronunciation overrides survive dictionaries and chapters.

An explicit ``[[route|route]]`` was rewritten by a saved ``route -> root``
entry before its brackets resolved, and the longform chapter path applied
only the lexicon, so the engine read ``root [[root|root]]`` aloud. Chapters
and segments rendered that way are re-keyed; everything else keeps its key.
"""
from __future__ import annotations

import pytest

ROWS = [{"term": "route", "replacement": "root", "type": "respelling",
         "language": "*", "enabled": 1}]
LEXICON = {"route": "root"}
SR = 24000


@pytest.mark.parametrize(("text", "expected"), [
    ("route [[route|route]]", "root route"),
    ("route [[route]]", "root route"),
    ("[[Route|ROUTE]] then route.", "ROUTE then root."),
    ("no override, route", "no override, root"),
])
def test_dictionary_never_rewrites_inside_an_inline_override(text, expected):
    from services.pronunciation import apply_lexicon_and_overrides, apply_pronunciation

    assert apply_pronunciation(text, ROWS, "en") == expected
    assert apply_lexicon_and_overrides(text, LEXICON) == expected


def test_chapter_spans_resolve_inline_overrides_after_the_lexicon():
    torch = pytest.importorskip("torch")
    from services.audiobook import parse_audiobook_script, synthesize_chapter

    seen = []

    def synth(text, voice_id, speed=None):
        seen.append(text)
        return torch.ones(100, dtype=torch.float32)

    plan = parse_audiobook_script("Take the route, not [[route|route]].", default_voice="v")
    synthesize_chapter(plan.chapters[0].spans, synth, 16000, lexicon=LEXICON)
    assert " ".join(seen) == "Take the root, not route."

    seen.clear()
    plan = parse_audiobook_script("Say [[Nuh-VAD-uh]] twice.", default_voice="v")
    synthesize_chapter(plan.chapters[0].spans, synth, 16000)
    assert " ".join(seen) == "Say Nuh-VAD-uh twice."


def test_only_spans_with_an_inline_override_are_rekeyed(tmp_path, monkeypatch):
    from api.routers import audiobook as router
    from services import pronunciation
    from services.audiobook import Chapter, Span

    plain = Span(voice_id=None, text="Take the route.", pause_ms_after=0)
    inline = Span(voice_id=None, text="Not [[route|route]].", pause_ms_after=0)
    resolve = lambda vid: {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}  # noqa: E731

    def keys(*spans):
        spans, wav_path, *_rest, cache = router._chapter_cache_inputs(
            Chapter(title="C", spans=list(spans)), SR, "eng", resolve, str(tmp_path), LEXICON)
        return wav_path, [cache._path(span) for span in spans]

    current = keys(plain), keys(plain, inline)
    # The released derivation: nothing knew about inline overrides.
    monkeypatch.setattr(pronunciation, "has_inline_override", lambda text: False)
    released = keys(plain), keys(plain, inline)

    assert current[0] == released[0]  # a plain chapter keeps its cache
    (chapter, (plain_seg, inline_seg)) = current[1]
    (old_chapter, (old_plain_seg, old_inline_seg)) = released[1]
    assert plain_seg == old_plain_seg  # an unaffected segment keeps hitting
    assert inline_seg != old_inline_seg  # the wrongly rendered one re-renders
    assert chapter != old_chapter
