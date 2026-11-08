"""
Unit tests for services/dictation_cleanup.py — the deterministic cleanup that
runs before text_polish on every dictation final: hesitations, stutters,
all-caps streaming output, punctuation spacing, optional spoken punctuation,
and the CJK-aware join of committed utterances. Pure functions, exact strings.
"""
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")



def polish_dictation(*args, **kwargs):
    # Resolved at call time so sys.modules swaps by other tests can't leave a
    # stale module behind.
    from services.dictation_cleanup import polish_dictation as fn

    return fn(*args, **kwargs)


def join_dictation_pieces(pieces):
    from services.dictation_cleanup import join_dictation_pieces as fn

    return fn(pieces)


def _p(text, language=None, spoken=False):
    return polish_dictation(text, language, spoken_punctuation=spoken)


# ── Casing ──────────────────────────────────────────────────────────────────


def test_all_caps_streaming_output_is_sentence_cased():
    # The streaming zipformers emit upper case without punctuation.
    assert _p("HELLO WORLD HOW ARE YOU") == "Hello world how are you."
    assert _p("I THINK I AM READY") == "I think I am ready."


def test_single_acronym_and_mixed_case_kept():
    assert _p("NASA") == "NASA."
    assert _p("Ship it to NASA today", "en") == "Ship it to NASA today."


def test_english_pronoun_i():
    assert _p("i think i'm ready", "en") == "I think I'm ready."
    assert _p("i.e. this works", "en") == "I.e. this works."  # not "I.E."


def test_pronoun_rule_is_english_only():
    # Italian "i" is an article.
    assert _p("ho visto i ragazzi", "it") == "Ho visto i ragazzi."


# ── Hesitations ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,clean", [
    ("um so the meeting is at three", "So the meeting is at three."),
    ("So, um, the meeting is, uh, at three.", "So, the meeting is, at three."),
    ("Hello. Um, so we start", "Hello. So we start."),
    ("thanks, um", "Thanks."),
    ("the end, uh.", "The end."),
    ("Umm I'm not sure erm about that", "I'm not sure about that."),
    ("uh-huh, that works", "Uh-huh, that works."),  # a real answer, kept
])
def test_hesitations_removed(raw, clean):
    assert _p(raw, "en") == clean


def test_um_and_er_are_words_outside_english():
    assert _p("um carro azul", "pt") == "Um carro azul."
    assert _p("Er sagt nein", "de") == "Er sagt nein."
    # ...but "uh" is never a word, so it goes even with no language hint.
    assert _p("uh I mean it") == "I mean it."


# ── Stutters ────────────────────────────────────────────────────────────────


def test_function_word_stutters_collapse():
    assert _p("the the thing is i i think", "en") == "The thing is I think."
    assert _p("I- I think we, we should go", "en") == "I think we should go."


def test_emphasis_and_grammatical_doubles_survive():
    assert _p("I know that that is very very good", "en") == \
        "I know that that is very very good."
    assert _p("what it is is a test", "en") == "What it is is a test."
    assert _p("no no no", "en") == "No no no."


def test_stutter_rule_is_english_only():
    assert _p("nous nous levons", "fr") == "Nous nous levons."


# ── Spacing ─────────────────────────────────────────────────────────────────


def test_space_before_punctuation_removed():
    assert _p("hello , world , how are you ?", "en") == "Hello, world, how are you?"


def test_french_keeps_typographic_space():
    assert _p("Quoi ?", "fr") == "Quoi ?"
    assert _p("Bonjour , madame", "fr") == "Bonjour, madame."


def test_decimal_and_ellipsis_untouched():
    assert _p("it costs .5 dollars", "en") == "It costs .5 dollars."


# ── Spoken punctuation (opt-in) ─────────────────────────────────────────────


@pytest.mark.parametrize("raw,clean", [
    ("hello comma how are you question mark", "Hello, how are you?"),
    # Whisper's own punctuation around the command word is absorbed.
    ("Hello, comma, how are you? Period.", "Hello, how are you."),
    ("first line new line second line", "First line\nSecond line."),
    ("done. New line. next", "Done.\nNext."),
    ("dear Sam new paragraph thanks period", "Dear Sam\n\nThanks."),
    ("wait exclamation point really full stop", "Wait! Really."),
    ("HELLO COMMA WORLD PERIOD THANKS", "Hello, world. Thanks."),
    ("comma", ""),  # punctuation alone types nothing
])
def test_spoken_punctuation(raw, clean):
    assert _p(raw, "en", spoken=True) == clean


def test_spoken_punctuation_off_by_default_keeps_words():
    assert _p("the trial period ended", "en") == "The trial period ended."
    assert _p("hello comma world", "en") == "Hello comma world."


def test_spoken_punctuation_reads_setting(monkeypatch):
    from services import refinement

    monkeypatch.setattr(refinement, "get_refinement_config",
                        lambda: {"spoken_punctuation": True})
    assert polish_dictation("hello comma world", "en") == "Hello, world."
    monkeypatch.setattr(refinement, "get_refinement_config",
                        lambda: {"spoken_punctuation": False})
    assert polish_dictation("hello comma world", "en") == "Hello comma world."


def test_setting_failure_never_costs_the_final(monkeypatch):
    from services import refinement

    def boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(refinement, "get_refinement_config", boom)
    assert polish_dictation("hello comma world", "en") == "Hello comma world."


# ── Invariants ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "HELLO WORLD", "um so the the thing", "Hello , world", "hi comma there new line ok",
    "\u4f60\u597d\u4e16\u754c", "привет мир", "", "   ",
])
@pytest.mark.parametrize("spoken", [False, True])
def test_idempotent(raw, spoken):
    once = _p(raw, "en", spoken)
    assert _p(once, "en", spoken) == once


def test_no_trailing_spaces_on_lines():
    out = _p("one   new line   two  ", "en", spoken=True)
    assert all(line == line.rstrip() for line in out.split("\n"))


# ── Joining committed utterances ────────────────────────────────────────────


def test_join_latin_pieces_with_one_space():
    assert join_dictation_pieces(["Hello world.", " How are you? "]) == \
        "Hello world. How are you?"


def test_join_cjk_pieces_without_space():
    # Before: " ".join put a space between Chinese sentences.
    assert join_dictation_pieces(["\u4f60\u597d\u3002", "\u4e16\u754c\u3002"]) == \
        "\u4f60\u597d\u3002\u4e16\u754c\u3002"


def test_join_skips_empty_pieces():
    assert join_dictation_pieces(["", "Hi.", "  ", "Bye."]) == "Hi. Bye."


# ── Review regressions ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,clean", [
    ("hello comma", "Hello,"),
    ("dear Sam colon", "Dear Sam:"),
    ("first semicolon", "First;"),
])
def test_spoken_separator_at_utterance_end_is_kept(raw, clean):
    # polish_text swaps a dangling ASR comma for a stop; a spoken one stays.
    assert _p(raw, "en", spoken=True) == clean


@pytest.mark.parametrize("raw,clean", [
    ("你好 comma", "你好,"),
    ("他说「你好」 comma", "他说「你好」,"),
])
def test_spoken_separator_after_cjk_drops_the_fullwidth_stop(raw, clean):
    # polish_text ends CJK with 。, which must not sit before the spoken comma.
    assert _p(raw, None, spoken=True) == clean


def test_trailing_asr_comma_still_becomes_a_stop():
    assert _p("see you tomorrow,", "en", spoken=True) == "See you tomorrow."


def test_explicit_language_beats_all_caps_english_guess():
    assert _p("UM CARRO AZUL", "pt") == "Um carro azul."
    assert _p("ER IST HIER", "de") == "Er ist hier."
    # No language (or "auto") from an English streaming model: English rules.
    assert _p("UM I THINK SO", "auto") == "I think so."


def test_spoken_commands_skip_known_non_english_text():
    assert _p("le deux colon", "fr", spoken=True) == "Le deux colon."
    assert _p("hello comma world", None, spoken=True) == "Hello, world."


def test_platform_stream_polisher_keeps_plain_polish():
    from api.routers import capture_ws as cw

    platform = cw._PlatformWebSocket.__new__(cw._PlatformWebSocket)
    polish = cw._final_polisher(platform, "en")
    assert polish("um hello comma world", "en") == "Um hello comma world."


def test_recovery_language_unknown_does_not_inherit_english_hint():
    # Silent-model recovery: an English sherpa model failed and the fallback
    # recognizer (Parakeet) reports no language. Its Portuguese/German text
    # must not be cleaned with English-only filler rules (#3432 review).
    from api.routers import capture_ws as cw

    polish = cw._final_polisher(object(), "en")
    assert polish("um I think so") == "I think so."
    assert polish("um carro azul", None) == "Um carro azul."
    assert polish("er ist hier", None) == "Er ist hier."
