"""Pronunciation lexicon core (longform render, PR 8).

Pure tests for the whole-word, case-insensitive respelling matcher plus the
JSON round-trip. No torch, no engine, no GPU.
"""
from __future__ import annotations

import json

import pytest
from services.pronunciation import (
    apply_lexicon,
    load_lexicon,
    normalize_lexicon,
    save_lexicon,
)


# ── apply_lexicon: whole-word replace ────────────────────────────────────────

def test_whole_word_replace():
    assert apply_lexicon("I love GIF files", {"GIF": "jiff"}) == "I love jiff files"


def test_case_insensitive_match():
    lex = {"OmniVoice": "Omni Voice"}
    assert apply_lexicon("omnivoice rocks", lex) == "Omni Voice rocks"
    assert apply_lexicon("OMNIVOICE rocks", lex) == "Omni Voice rocks"
    assert apply_lexicon("OmniVoice rocks", lex) == "Omni Voice rocks"


def test_no_partial_word_replacement():
    # "cat" must not touch "category" / "scatter".
    assert apply_lexicon("category scatter cat", {"cat": "feline"}) == \
        "category scatter feline"


def test_punctuation_adjacency_trailing():
    # "smith," — trailing comma preserved, word still matched.
    assert apply_lexicon("Hello smith, hi", {"Smith": "Smyth"}) == "Hello Smyth, hi"


def test_punctuation_adjacency_period_key():
    # Key with an internal/edge period: "Dr." should match before a space.
    out = apply_lexicon("See Dr. Jones", {"Dr.": "Doctor"})
    assert out == "See Doctor Jones"


def test_longest_match_first():
    lex = {"Dr": "Doctor", "Dr. Smith": "Doctor Smith the third"}
    # The longer key must win over the shorter overlapping one.
    assert apply_lexicon("Call Dr. Smith now", lex) == "Call Doctor Smith the third now"


def test_multiple_replacements_in_one_pass():
    lex = {"GIF": "jiff", "SQL": "sequel"}
    assert apply_lexicon("GIF and SQL", lex) == "jiff and sequel"


def test_empty_text_and_empty_lexicon():
    assert apply_lexicon("", {"a": "b"}) == ""
    assert apply_lexicon(None, {"a": "b"}) == ""
    assert apply_lexicon("unchanged text", {}) == "unchanged text"
    assert apply_lexicon("unchanged text", None) == "unchanged text"


def test_idempotence_when_no_key_in_output():
    lex = {"GIF": "jiff"}
    once = apply_lexicon("a GIF here", lex)
    twice = apply_lexicon(once, lex)
    assert once == twice == "a jiff here"


def test_respelling_not_rescanned_in_single_pass():
    # If a value contains another key, a single pass must NOT re-expand it.
    lex = {"NY": "New York", "York": "Yorkshire"}
    # "NY" -> "New York"; the produced "York" is part of the substituted
    # text and must not be re-matched within the same pass.
    assert apply_lexicon("from NY today", lex) == "from New York today"


def test_unicode_word_boundary():
    lex = {"café": "kaffey"}
    assert apply_lexicon("a café here", lex) == "a kaffey here"
    # Must not partial-match inside a longer token.
    assert apply_lexicon("cafés plural", lex) == "cafés plural"


def test_no_space_scripts_match_inside_a_sentence():
    # Japanese, Chinese and Thai write words without spaces, so a key sits between
    # more word characters and a \b boundary never matched it mid-sentence: the
    # entry only ever applied to a line that was the key alone.
    assert apply_lexicon("東京に行きます", {"東京": "とうきょう"}) == "とうきょうに行きます"
    assert apply_lexicon("我在北京工作", {"北京": "Beijing"}) == "我在Beijing工作"
    assert apply_lexicon("ฉันไปกรุงเทพวันนี้", {"กรุงเทพ": "Krung Thep"}) == \
        "ฉันไปKrung Thepวันนี้"
    # The ideographic iteration mark repeats the kanji before it and is part of
    # the word: a key ending in it matches inside a sentence too.
    assert apply_lexicon("色々試す", {"色々": "いろいろ"}) == "いろいろ試す"
    # Spaced scripts keep their boundaries.
    assert apply_lexicon("category of cat", {"cat": "kat"}) == "category of kat"


def test_value_may_be_empty_to_delete_word():
    assert apply_lexicon("the [marker] gone", {"[marker]": ""}) == "the  gone"


# ── normalize_lexicon ────────────────────────────────────────────────────────

def test_normalize_drops_empty_keys():
    lex = {"": "x", "  ": "y", "ok": "v", None: "z"}
    assert normalize_lexicon(lex) == {"ok": "v"}


def test_normalize_strips_keys_and_coerces_values():
    assert normalize_lexicon({"  Dr  ": "Doctor", "n": None}) == {"Dr": "Doctor", "n": ""}


def test_normalize_non_dict():
    assert normalize_lexicon(None) == {}
    assert normalize_lexicon(["a", "b"]) == {}


# ── load / save round-trip ───────────────────────────────────────────────────

def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "sub" / "lexicon.json"
    written = save_lexicon(str(path), {"  GIF ": "jiff", "": "skip"})
    assert written == {"GIF": "jiff"}
    assert path.is_file()
    assert load_lexicon(str(path)) == {"GIF": "jiff"}


def test_load_missing_or_empty(tmp_path):
    assert load_lexicon(str(tmp_path / "nope.json")) == {}
    empty = tmp_path / "empty.json"
    empty.write_text("   ", encoding="utf-8")
    assert load_lexicon(str(empty)) == {}


def test_load_non_object_json(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_lexicon(str(p)) == {}


def test_load_normalizes(tmp_path):
    p = tmp_path / "lex.json"
    p.write_text(json.dumps({"  Dr  ": "Doctor", "": "x"}), encoding="utf-8")
    assert load_lexicon(str(p)) == {"Dr": "Doctor"}


# ── DB dictionary layer: per-language + inline overrides (Spec 01) ────────────

from services.pronunciation import (  # noqa: E402
    apply_inline_overrides,
    apply_pronunciation,
    entries_for_language,
)


def _row(term, replacement, type="respelling", language="*", enabled=1, id="x"):
    return {
        "id": id, "term": term, "replacement": replacement, "type": type,
        "language": language, "enabled": enabled, "created_at": 1.0,
    }


_ROWS = [
    _row("GIF", "jiff", language="*", id="1"),
    _row("Nevada", "Nuh-VAD-uh", language="en", id="2"),
    _row("Berlin", "Bear-LEEN", language="de", id="3"),
    _row("parked", "NOPE", language="*", enabled=0, id="4"),
    _row("cafe", "kaˈfeː", type="ipa", language="*", id="5"),
]


def test_global_row_always_applies():
    assert apply_pronunciation("I love GIF", _ROWS, "en") == "I love jiff"
    assert apply_pronunciation("I love GIF", _ROWS, "de") == "I love jiff"
    assert apply_pronunciation("I love GIF", _ROWS, "Auto") == "I love jiff"


def test_language_row_only_on_matching_language():
    assert apply_pronunciation("Nevada", _ROWS, "en-US") == "Nuh-VAD-uh"
    assert apply_pronunciation("Nevada", _ROWS, "de") == "Nevada"
    assert apply_pronunciation("Berlin", _ROWS, "de") == "Bear-LEEN"
    assert apply_pronunciation("Berlin", _ROWS, "en") == "Berlin"


def test_auto_language_applies_only_global_rows():
    assert apply_pronunciation("GIF Nevada", _ROWS, "Auto") == "jiff Nevada"
    assert apply_pronunciation("GIF Nevada", _ROWS, None) == "jiff Nevada"


def test_disabled_row_is_a_no_op():
    assert apply_pronunciation("a parked car", _ROWS, "en") == "a parked car"


def test_ipa_row_never_enters_the_grapheme_stream():
    # A phoneme row with no respelling must NOT substitute raw IPA into text.
    assert apply_pronunciation("a cafe", _ROWS, "en") == "a cafe"


def test_language_row_overrides_global_on_same_term():
    rows = [
        _row("color", "kuh-ler", language="*", id="g"),
        _row("color", "KOL-or", language="en", id="l"),
    ]
    assert apply_pronunciation("a color", rows, "en") == "a KOL-or"
    assert apply_pronunciation("a color", rows, "de") == "a kuh-ler"


def test_db_longest_term_first():
    rows = [
        _row("Dr", "Doctor", id="a"),
        _row("Dr Smith", "Doctor Smith", id="b"),
    ]
    assert apply_pronunciation("Dr Smith here", rows, "en") == "Doctor Smith here"


def test_db_idempotent():
    once = apply_pronunciation("GIF", _ROWS, "en")
    assert apply_pronunciation(once, _ROWS, "en") == once


def test_inline_pipe_form():
    assert apply_inline_overrides("the [[gif|jiff]] file") == "the jiff file"


def test_inline_bare_form_strips_brackets():
    assert apply_inline_overrides("say [[Nuh-VAD-uh]] now") == "say Nuh-VAD-uh now"


def test_inline_empty_collapses():
    assert apply_inline_overrides("a [[]] b") == "a  b"


def test_inline_does_not_touch_single_bracket_tags():
    s = "[voice:Sam] [pause 200ms] [excited]"
    assert apply_inline_overrides(s) == s


def test_inline_override_wins_over_dictionary():
    assert apply_pronunciation("GIF and [[GIF|GROO]]", _ROWS, "en") == "jiff and GROO"


def test_inline_applied_even_with_empty_dictionary():
    assert apply_pronunciation("x [[a|b]] y", [], "en") == "x b y"


def test_plain_text_passthrough_byte_identical():
    s = "Hello, world. Nothing to see here."
    assert apply_pronunciation(s, _ROWS, "en") == s


def test_empty_text_passthrough_db():
    assert apply_pronunciation("", _ROWS, "en") == ""
    assert apply_pronunciation(None, _ROWS, "en") == ""


def test_no_entries_no_inline_is_noop():
    assert apply_pronunciation("hello world", [], "en") == "hello world"


def test_entries_for_language_collapses_to_map():
    assert entries_for_language(_ROWS, "en") == {"GIF": "jiff", "Nevada": "Nuh-VAD-uh"}


def test_project_lexicon_overlays_db():
    out = apply_pronunciation("GIF", _ROWS, "en", lexicon={"GIF": "PROJECT"})
    assert out == "PROJECT"


def test_inline_redos_safe():
    import time
    s = "[[" * 5000 + "a" + "]]" * 5000
    t0 = time.perf_counter()
    apply_inline_overrides(s)
    assert time.perf_counter() - t0 < 0.5


# ── #2542: picker names and ISO ids resolve to the same language scope ───────

def test_language_scope_key_resolves_names_ids_and_regions():
    from services.pronunciation import language_scope_key

    assert language_scope_key("Spanish") == "es"
    assert language_scope_key("Estonian") == "et"
    assert language_scope_key("es-MX") == "es"
    assert language_scope_key("zh_Hans") == "zh"
    assert language_scope_key("kbt") == "kbt"
    assert language_scope_key("Abadi") == "kbt"
    assert language_scope_key("Mandarin") == "zh"
    assert language_scope_key(" * ") == "*"
    for pinless in (None, "", "  ", "Auto", "AUTO"):
        assert language_scope_key(pinless) is None


def test_spanish_entry_matches_picker_name_and_not_estonian():
    rows = [_row("GIF", "jiff", language="es", id="es")]
    assert apply_pronunciation("GIF", rows, "es") == "jiff"
    assert apply_pronunciation("GIF", rows, "Spanish") == "jiff"
    assert apply_pronunciation("GIF", rows, "es-MX") == "jiff"
    assert apply_pronunciation("GIF", rows, "Estonian") == "GIF"
    assert apply_pronunciation("GIF", rows, "et") == "GIF"


def test_scope_saved_as_picker_name_or_three_letter_id_matches():
    rows = [
        _row("GIF", "jiff", language="Spanish", id="a"),
        _row("Nevada", "Nuh-VAD-uh", language="kbt", id="b"),
    ]
    assert apply_pronunciation("GIF", rows, "es") == "jiff"
    assert apply_pronunciation("Nevada", rows, "Abadi") == "Nuh-VAD-uh"
    assert apply_pronunciation("Nevada", rows, "kb") == "Nevada"


def test_legacy_truncated_name_prefix_keeps_matching_its_language():
    # The old normalizer saved "Spanish" as "sp"; that row keeps working for
    # Spanish requests, while a real id ("es") never gains a prefix meaning.
    rows = [_row("GIF", "jiff", language="sp", id="legacy")]
    assert apply_pronunciation("GIF", rows, "Spanish") == "jiff"
    assert apply_pronunciation("GIF", rows, "Estonian") == "GIF"
    assert apply_pronunciation("GIF", rows, "Auto") == "GIF"


def test_inert_entries_use_the_same_language_resolution():
    from services.pronunciation import inert_entries_for_language

    rows = [_row("cafe", "kaˈfeː", type="ipa", language="es", id="i")]
    assert inert_entries_for_language(rows, "Spanish") == [{"term": "cafe", "type": "ipa"}]
    assert inert_entries_for_language(rows, "Estonian") == []
