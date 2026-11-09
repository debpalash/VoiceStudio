"""#2728: lexicon entry order decides output, so it must key the chapter cache.

The matcher lets the later of two equal-length case variants win
(``{"GIF": "first", "gif": "last"}`` speaks "last"), but the longform cache
serialized the lexicon with ``sort_keys=True``, so swapping the two replayed
the old audio. Drives the real ``_render_chapter_cached`` with a stub synth.
"""
from __future__ import annotations

import importlib
import json

import pytest
import torch

_SR = 24000


def _mod(name: str):
    return importlib.import_module(name)


@pytest.fixture
def render(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod("services.watermark"), "will_mark", lambda: False)
    monkeypatch.setattr(_mod("core.config"), "VOICES_DIR", str(tmp_path / "voices"))
    cache = tmp_path / "cache"
    cache.mkdir()
    ab = _mod("services.audiobook")

    def run(text, lexicon):
        calls: list[str] = []

        def synth(t, voice_id, speed=None):
            calls.append(t)
            return torch.full((2400,), 0.1)

        chapter = ab.Chapter(title="T", spans=[ab.Span(voice_id="v1", text=text, pause_ms_after=100)])
        resolve = lambda _v: {"ref_audio": None, "ref_text": "hi", "instruct": None, "seed": None}  # noqa: E731
        _wav, _dur, cached, _stats = _mod("api.routers.audiobook")._render_chapter_cached(
            chapter, synth, _SR, "eng", resolve, str(cache), lexicon)
        return cached, calls

    return run


def test_swapping_case_variant_precedence_rerenders(render):
    cached, calls = render("GIF", {"GIF": "first", "gif": "last"})
    assert (cached, calls) == (False, ["last"])

    cached, calls = render("GIF", {"gif": "last", "GIF": "first"})
    assert cached is False
    assert calls == ["first"]


def test_reordering_unrelated_entries_keeps_the_cache(render):
    render("GIF and Dr", {"GIF": "jiff", "Dr": "Doctor"})
    cached, calls = render("GIF and Dr", {"Dr": "Doctor", "GIF": "jiff"})
    assert (cached, calls) == (True, [])


def test_signature_unchanged_for_lexicons_without_competing_variants():
    """Caches written before #2728 keep hitting when order cannot matter."""
    from services.pronunciation import lexicon_signature, normalize_lexicon

    for lex in ({"GIF": "jiff", "Dr": "Doctor"}, {" OmniVoice ": "Omni Voice"}, {"": "x"}, {"gif": "a", "gift": "b"}):
        assert lexicon_signature(lex) == json.dumps(normalize_lexicon(lex), sort_keys=True)


def test_signature_follows_the_matchers_case_rules():
    from services.pronunciation import apply_lexicon, lexicon_signature

    # re.IGNORECASE treats i and dotless ı as equal; casefold does not.
    a, b = {"ki": "one", "kı": "two"}, {"kı": "two", "ki": "one"}
    assert apply_lexicon("ki", a) != apply_lexicon("ki", b)
    assert lexicon_signature(a) != lexicon_signature(b)


def test_moving_independent_variant_groups_keeps_the_signature():
    from services.pronunciation import lexicon_signature

    a = {"GIF": "jiff", "gif": "gif", "SQL": "sequel", "sql": "s q l"}
    b = {"SQL": "sequel", "sql": "s q l", "GIF": "jiff", "gif": "gif"}
    assert lexicon_signature(a) == lexicon_signature(b)
    # Order inside a group still decides the winner, so it still counts.
    c = {"gif": "gif", "GIF": "jiff", "SQL": "sequel", "sql": "s q l"}
    assert lexicon_signature(a) != lexicon_signature(c)


def test_save_and_load_keep_entry_order(tmp_path):
    from services.pronunciation import apply_lexicon, load_lexicon, save_lexicon

    lex = {"gif": "last", "GIF": "first"}
    path = tmp_path / "lex.json"
    save_lexicon(str(path), lex)
    loaded = load_lexicon(str(path))
    assert list(loaded) == list(lex)
    assert apply_lexicon("GIF", loaded) == apply_lexicon("GIF", lex) == "first"
