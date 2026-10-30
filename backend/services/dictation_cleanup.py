"""
Deterministic cleanup for dictation finals — runs before
:func:`services.text_polish.polish_text` on every text dictation types.

What it fixes (rule-based, no model, no network; idempotent except that a
spoken trailing ", ; :" is kept only on the first pass, where it was spoken):

  * ALL-CAPS output from the streaming sherpa zipformers ("HELLO WORLD") is
    put into sentence case, so it no longer types as shouting.
  * Hesitations are removed. "uh", "uhm", "umm" and "erm" go in every
    language; "um" and "er" only when the text is English, because "um" is
    a word in Portuguese and "er" is one in German.
  * English stutters on short function words ("the the", "I, I") collapse.
    Emphasis on content words ("very very", "no no") and grammatical
    doubles ("that that", "had had") stay.
  * English "i" / "i'm" become "I" / "I'm".
  * A space before , . ; : ! ? is removed ("hello , world"). French keeps its
    typographic space before ? ! ; : ("Quoi ?").
  * Optional spoken punctuation (off by default, English): "comma",
    "period", "full stop", "question mark", "exclamation mark/point",
    "colon", "semicolon", "new line", "new paragraph".
  * Trailing spaces on each line go; doubled spaces collapse.

The LLM refinement in services.refinement stays a separate, opt-in step on
top of this.

CJK codepoints are ``\\u``-escaped (functional punctuation handling), which
keeps this file outside the literal-CJK scan in tests/test_no_hardcoded_cjk.py.
"""
from __future__ import annotations

import logging
import re

from services.text_polish import polish_text

logger = logging.getLogger("omnivoice.dictation_cleanup")

# Private-use markers: "capitalize the next letter" (a sentence starts here),
# and "this separator was spoken on purpose" (keep it at the very end).
_CAP = "\ue000"
_SPOKEN = "\ue001"

_LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏ]{2,}")
_LATIN_LOWER = re.compile(r"[a-zß-öø-ÿā-ɏ]")

# Hesitations that are not words in any language dictation supports.
_FILLERS_ANY = r"u+h+|u+h+m+|u+m{2,}|e+r+m+"
# English-only: "um" is Portuguese for "one", "er" is German for "he".
_FILLERS_EN = r"um|er+"

# Short function words people stutter on. Content words are left alone so
# emphasis ("very very", "no no no") survives; "that that", "had had", "is is"
# ("what it is is") and "so so" are real English and excluded on purpose.
_STUTTER_WORDS = (
    "a", "an", "and", "at", "but", "for", "he", "i", "in", "it", "my", "of",
    "on", "or", "she", "the", "they", "to", "we", "with", "you",
)
_STUTTER = re.compile(
    r"(?<![\w'])(" + "|".join(_STUTTER_WORDS) + r")(?:[,\-]?\s+\1)+(?![\w'])",
    re.IGNORECASE,
)
_PRONOUN_I = re.compile(r"(?<![\w'.])i(?=(?:'(?:m|d|ll|ve|s))?(?![\w']|\.\w))")

# Spoken punctuation (English). Longer phrases first.
_COMMANDS: tuple[tuple[str, str], ...] = (
    (r"new\s+paragraph", "\n\n"),
    (r"new\s*line", "\n"),
    (r"question\s+mark", "?"),
    (r"exclamation\s+(?:mark|point)", "!"),
    (r"full\s+stop", "."),
    (r"semi\s*colon", ";"),
    (r"period", "."),
    (r"comma", ","),
    (r"colon", ":"),
)
_COMMAND = re.compile(
    r"[ \t]*(?P<lead>[,;:.!?])?[ \t]*(?<![\w'])(?P<cmd>" + "|".join(p for p, _ in _COMMANDS)
    + r")(?![\w'])[,;:.!?]*",
    re.IGNORECASE,
)

_SPACE_BEFORE = re.compile(r"[ \t]+([,.;:!?])(?=\s|$|[\"')\]”’])")
_SPACE_BEFORE_FR = re.compile(r"[ \t]+([,.])(?=\s|$|[\"')\]”’])")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_LINE_EDGES = re.compile(r"[ \t]*\n[ \t]*")
_CAP_NEXT = re.compile(_CAP + r"[ \t" + _CAP + r"]*(\w?)")

# CJK (kana, ideographs, compatibility forms, fullwidth punctuation): no space
# between pieces that meet on one of these.
_CJK_EDGE = re.compile(
    "[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def _is_english(language: str | None) -> bool:
    return bool(language) and language.lower().replace("_", "-").split("-")[0] in {
        "en", "english",
    }


def _decase_shouting(text: str) -> tuple[str, bool]:
    """Sentence-case all-caps Latin text with at least two words."""
    if len(_LATIN_WORD.findall(text)) < 2 or _LATIN_LOWER.search(text):
        return text, False
    return text.lower(), True


def _at_sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \t" + _CAP)
    return not before or before[-1] in ".!?\n…"


def _remove_fillers(text: str, english: bool) -> str:
    words = _FILLERS_ANY + ("|" + _FILLERS_EN if english else "")
    pattern = re.compile(
        r"(?P<pre>,[ \t]*)?(?<![\w'\-])(?:" + words + r")(?![\w'\-])"
        r"(?P<post>[ \t]*(?:\.\.\.|…|[,;:.!?]))?",
        re.IGNORECASE,
    )

    def repl(m: re.Match) -> str:
        post = (m.group("post") or "").strip()
        if _at_sentence_start(text, m.start()):
            return _CAP
        if post in {".", "!", "?"}:
            return post  # "the end, um." -> "the end."
        return m.group("pre") or " "

    return pattern.sub(repl, text)


def _apply_commands(text: str) -> str:
    symbols = {i: s for i, (_, s) in enumerate(_COMMANDS)}
    compiled = [re.compile(p + r"$", re.IGNORECASE) for p, _ in _COMMANDS]

    def repl(m: re.Match) -> str:
        word = m.group("cmd")
        symbol = next(symbols[i] for i, c in enumerate(compiled) if c.match(word))
        if symbol.startswith("\n"):
            # Keep a stop the speaker already had: "done. New line." -> "done.\n"
            lead = m.group("lead") if m.group("lead") in {".", "!", "?"} else ""
            return lead + symbol + _CAP
        if symbol in ".?!":
            return symbol + " " + _CAP
        return symbol + _SPOKEN + " "

    return _COMMAND.sub(repl, text)


def _language_given(language: str | None) -> bool:
    return bool(language) and language.lower() not in {"auto", "unknown", "none"}


def clean_dictation_text(
    text: str, language: str | None = None, *, spoken_punctuation: bool = False,
) -> str:
    """Apply the cleanup rules above. Does not add terminal punctuation or the
    leading capital — :func:`polish_dictation` adds those via polish_text."""
    return _clean(text, language, spoken_punctuation)[0]


def _clean(text: str, language: str | None, spoken_punctuation: bool) -> tuple[str, bool]:
    """Cleaned text, plus whether it ends with a spoken , ; or : to keep."""
    if not text or not text.strip():
        return "", False
    out = text.replace(_CAP, "").replace(_SPOKEN, "")
    out, decased = _decase_shouting(out)
    # All-caps output comes from the English streaming models, but an explicit
    # language always wins: "UM CARRO" in Portuguese keeps its "um".
    english = _is_english(language) or (decased and not _language_given(language))
    # The command words are English: never rewrite text known to be another
    # language ("colon" in French dictation stays a word).
    if spoken_punctuation and (english or not _language_given(language)):
        out = _apply_commands(out)
    out = _remove_fillers(out, english)
    if english:
        out = _STUTTER.sub(r"\1", out)
        out = _PRONOUN_I.sub("I", out)
    french = (language or "").lower().startswith("fr")
    out = (_SPACE_BEFORE_FR if french else _SPACE_BEFORE).sub(r"\1", out)
    out = _MULTISPACE.sub(" ", out)
    out = _LINE_EDGES.sub("\n", out)
    out = _CAP_NEXT.sub(lambda m: m.group(1).upper(), out)
    # Capitalize the first letter after a stop when we lowered the casing.
    if decased:
        out = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    out = out.strip()
    spoken_end = out.endswith(_SPOKEN)
    out = out.replace(_SPOKEN, "")
    # Punctuation alone (a lone "comma" utterance) types nothing.
    if not any(ch.isalnum() for ch in out):
        return "", False
    return out, spoken_end


def _spoken_punctuation_enabled() -> bool:
    try:
        from services.refinement import get_refinement_config

        return bool(get_refinement_config().get("spoken_punctuation", False))
    except Exception:  # noqa: BLE001 — a settings read must never cost the final
        logger.debug("spoken punctuation setting unavailable", exc_info=True)
        return False


def polish_dictation(
    text: str, language: str | None = None, *, spoken_punctuation: bool | None = None,
) -> str:
    """Cleanup + polish for one dictation final. ``spoken_punctuation=None``
    reads the saved setting (Settings → Models → Dictation)."""
    if spoken_punctuation is None:
        spoken_punctuation = _spoken_punctuation_enabled()
    out, spoken_end = _clean(text, language, spoken_punctuation)
    if spoken_end:
        # "hello comma" keeps the comma the speaker asked for; polish_text
        # would swap a dangling separator for a stop.
        body = polish_text(out[:-1])
        # The stop polish_text added is "." or, after CJK, the fullwidth "\u3002".
        if body.endswith((".", "\u3002")) and not body.endswith(("..", "\u3002\u3002")):
            body = body[:-1]
        return body + out[-1]
    return polish_text(out)


def join_dictation_pieces(pieces: list[str]) -> str:
    """Join committed utterances the way they were typed: one space between
    pieces, none where either side is CJK or a piece ends with a newline.
    Mirrors ``joinDictationText`` in electron/src/shared/utils/captureProtocol.js."""
    out = ""
    for piece in pieces:
        piece = (piece or "").strip(" \t")
        if not piece:
            continue
        out += dictation_separator(out, piece) + piece
    return out.strip()


def dictation_separator(previous: str, nxt: str) -> str:
    if not previous or not nxt:
        return ""
    if previous.endswith("\n") or nxt.startswith("\n"):
        return ""
    if _CJK_EDGE.match(previous[-1]) or _CJK_EDGE.match(nxt[0]):
        return ""
    return " "
