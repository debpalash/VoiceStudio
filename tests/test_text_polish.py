"""
Unit tests for services/text_polish.py — the deterministic polish applied to
every dictation `final` (dictation v2): leading capital (Latin scripts only),
terminal punctuation (incl. CJK forms), whitespace cleanup. Pure function, no
model — so these pin exact strings.
"""
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from services.text_polish import polish_text  # noqa: E402


# ── Latin: capitalization + terminal punctuation ─────────────────────────────


@pytest.mark.parametrize("raw,polished", [
    ("hello world", "Hello world."),
    ("Hello world", "Hello world."),
    ("what time is it?", "What time is it?"),      # already terminated
    ("wow!", "Wow!"),
    ("wait…", "Wait…"),                            # ellipsis is terminal
    ("42 is the answer", "42 is the answer."),     # non-letter start untouched
    ("école ouverte", "École ouverte."),           # Latin-1 accents capitalize
    ("čapek wrote robots", "Čapek wrote robots."),  # Latin Extended
])
def test_latin_basics(raw, polished):
    assert polish_text(raw) == polished


def test_whitespace_collapsed_and_stripped():
    assert polish_text("  hello   world  ") == "Hello world."
    assert polish_text("a \t b") == "A b."         # mixed space/tab run


def test_dangling_separator_swapped_for_stop():
    # ASR often stops mid-breath on a comma — swap it, don't stack ",.".
    assert polish_text("see you tomorrow,") == "See you tomorrow."
    assert polish_text("first; second;") == "First; second."


def test_terminal_behind_closing_quote_respected():
    assert polish_text('he said "stop."') == 'He said "stop."'
    assert polish_text("(done!)") == "(done!)"
    assert polish_text('he said "stop"') == 'He said "stop".'


# ── CJK: passthrough (no capitalization) + fullwidth stop ────────────────────


def test_cjk_already_punctuated_passes_through():
    assert polish_text("你好，世界。") == "你好，世界。"
    assert polish_text("すごい！") == "すごい！"
    assert polish_text("本当ですか？") == "本当ですか？"


def test_cjk_gets_fullwidth_stop():
    assert polish_text("你好世界") == "你好世界。"
    assert polish_text("ありがとう") == "ありがとう。"


def test_cjk_gets_fullwidth_stop_behind_closing_bracket():
    # The closer is not CJK itself; the sentence before it is.
    assert polish_text("他说「你好」") == "他说「你好」。"
    assert polish_text("面白い（笑）") == "面白い（笑）。"
    # Latin behind a closer still gets the Latin stop.
    assert polish_text('he said "stop"') == 'He said "stop".'


def test_cjk_dangling_comma_swapped():
    assert polish_text("你好，") == "你好。"


# ── Non-Latin alphabets: no capitalization, still get a stop ─────────────────


def test_cyrillic_and_greek_not_capitalized():
    assert polish_text("привет мир") == "привет мир."
    assert polish_text("γεια σου") == "γεια σου."


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_empty_and_whitespace_only():
    assert polish_text("") == ""
    assert polish_text("   ") == ""
    assert polish_text("\t \t") == ""
    assert polish_text(",") == ""                   # dangling-only → empty


def test_idempotent():
    for raw in ["hello world", "你好世界", "привет", "What?!", "a,  b,"]:
        once = polish_text(raw)
        assert polish_text(once) == once
