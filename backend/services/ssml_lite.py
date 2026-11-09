"""SSML-LITE — inline prosody/spell markup for longform lines (PR 8).

A *single line* of narration may carry inline tags that nudge the engine's
delivery without reaching for full SSML:

  * ``[slow]…[/slow]``         — speak slower   (``speed ≈ 0.85``)
  * ``[fast]…[/fast]``         — speak faster   (``speed ≈ 1.15``)
  * ``[emphasis]…[/emphasis]`` — mild emphasis: a gentle slow-down
                                 (``speed ≈ 0.92``) plus an ``emphasis`` flag
                                 the caller may use for future markup
  * ``[spell]…[/spell]``       — spell the run out letter-by-letter
                                 (``spell=True``; the caller spaces the chars)

:func:`parse_ssml_lite` splits one line into ordered segments::

    [{"text": str, "speed": float | None, "spell": bool, "emphasis": bool}, …]

Semantics (kept deliberately small and predictable):

  * Plain text → exactly one segment ``{text, speed=None, spell=False,
    emphasis=False}``.
  * Tags nest; the **innermost** tag wins for any property it sets. ``speed``
    from an inner ``[fast]`` overrides an outer ``[slow]``; ``[spell]`` inside
    ``[slow]`` keeps the slow speed *and* turns spelling on.
  * An **unclosed** tag applies to the end of the line.
  * A stray close tag with no matching open is ignored (treated as literal
    nothing — the markers are always stripped from the emitted ``text``).
  * Adjacent segments that share identical (speed, spell, emphasis) are merged
    so plain runs stay single segments.

This module is pure (no torch, no I/O) so it is cheap to import and unit-test.
The regex is ReDoS-safe: it is a fixed alternation of literal tag tokens with
no quantifier overlap, so matching is linear in the input length.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Speed multipliers. ``None`` means "engine default" (no override emitted).
SLOW_SPEED = 0.85
FAST_SPEED = 1.15
# Emphasis maps to a *mild* slow-down (between default and [slow]) plus a flag.
EMPHASIS_SPEED = 0.92

# Recognised tag names → the (speed_delta, spell, emphasis) they impose while
# open. ``speed`` of ``None`` for a tag means "this tag does not touch speed".
_TAGS: dict[str, dict] = {
    "slow": {"speed": SLOW_SPEED, "spell": None, "emphasis": None},
    "fast": {"speed": FAST_SPEED, "spell": None, "emphasis": None},
    "emphasis": {"speed": EMPHASIS_SPEED, "spell": None, "emphasis": True},
    "spell": {"speed": None, "spell": True, "emphasis": None},
}

# One regex that matches any open/close tag for the known names. It is a plain
# alternation of fixed literals — ``\[/?(?:slow|fast|emphasis|spell)\]`` — with
# no nested quantifiers and no overlapping ``*``/``+`` runs, so it cannot
# backtrack polynomially (ReDoS-safe). ``finditer`` walks it left-to-right.
_TAG_RE = re.compile(
    r"\[(/?)(" + "|".join(re.escape(name) for name in _TAGS) + r")\]",
    re.IGNORECASE,
)


def _resolve(stack: list[str]) -> dict:
    """Collapse an open-tag stack into the effective segment properties.

    Outer→inner walk: a later (more-deeply-nested) tag overrides any property
    it sets, leaving untouched properties from outer tags intact. So
    ``[slow][spell]`` yields ``speed=SLOW_SPEED, spell=True``.
    """
    speed: Optional[float] = None
    spell = False
    emphasis = False
    for name in stack:
        spec = _TAGS[name]
        if spec["speed"] is not None:
            speed = spec["speed"]
        if spec["spell"] is not None:
            spell = bool(spec["spell"])
        if spec["emphasis"] is not None:
            emphasis = bool(spec["emphasis"])
    return {"speed": speed, "spell": spell, "emphasis": emphasis}


def parse_ssml_lite(text: str) -> list[dict]:
    """Split one line of SSML-LITE markup into ordered prosody segments.

    See the module docstring for the full contract. Always returns at least one
    segment for non-empty input; returns ``[]`` for ``None``/empty input.
    """
    if not text:
        return []
    if "[" not in text:
        return [{"text": text, "speed": None, "spell": False, "emphasis": False}]

    segments: list[dict] = []
    stack: list[str] = []
    last = 0

    def emit(chunk: str) -> None:
        if not chunk:
            return
        props = _resolve(stack)
        seg = {"text": chunk, **props}
        # Merge with the previous segment when prosody is identical so plain
        # text never fragments into multiple identical-styled pieces.
        if segments:
            prev = segments[-1]
            if (
                prev["speed"] == seg["speed"]
                and prev["spell"] == seg["spell"]
                and prev["emphasis"] == seg["emphasis"]
            ):
                prev["text"] += chunk
                return
        segments.append(seg)

    for m in _TAG_RE.finditer(text):
        emit(text[last:m.start()])
        last = m.end()
        is_close = m.group(1) == "/"
        name = m.group(2).lower()
        if is_close:
            # Close the nearest matching open tag; ignore an unmatched close.
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == name:
                    del stack[i]
                    break
        else:
            stack.append(name)  # unclosed opens stay on the stack to EOL

    emit(text[last:])

    if not segments:
        # Input was only tag markers (e.g. "[slow][/slow]"): nothing to speak.
        return []
    return segments


def _grapheme_units(text: str) -> list[str]:
    """Split text into user-perceived characters (grapheme clusters).

    Iterating a Python ``str`` yields Unicode code points, not grapheme
    clusters: ``é`` in NFD (the letter ``e`` plus the combining acute
    U+0301) is two code points, and a regional-indicator pair like
    ``🇺🇸`` is two code points whose visible meaning only exists as a pair.
    Naive code-point iteration would space those clusters apart and let the
    engine speak a stray combining mark or a half-flag.

    A full grapheme-cluster segmentation belongs in a dedicated library
    (``grapheme`` / ``regex`` ``\\X``); the deps file does not pull one in
    directly. This stdlib-only helper covers the cases that actually reach
    the longform spelling path today:

    1. NFC-normalize first so common decomposed sequences (Latin diacritics,
       precomposed vs. conjoining-jamo Hangul) collapse to a single code
       point.
    2. Walk the NFC text; any code point whose Unicode general category is
       ``Mn`` (non-spacing mark), ``Mc`` (spacing combining mark — used by
       Indic scripts such as Devanagari vowel signs), or ``Me`` (enclosing
       mark) is appended to the previous unit instead of starting a new
       one. ``unicodedata.combining`` alone misses ``Mc`` (it reports 0
       for spacing combining marks), so checking the category is the
       portable signal.

    The unit buffer is a list of single-character strings, not a single
    string that we keep growing with ``+=``: ``str += str`` copies the
    whole left-hand side each time, so a long run of trailing combining
    marks (pathological but reachable through user input or imports)
    would be O(n²). Joining once at the end is O(n).
    """
    if not text:
        return []
    normalized = unicodedata.normalize("NFC", text)
    units: list[list[str]] = []
    for ch in normalized:
        if units and unicodedata.category(ch) in ("Mn", "Mc", "Me"):
            units[-1].append(ch)
        else:
            units.append([ch])
    return ["".join(unit) for unit in units]


def spell_out(word: str) -> str:
    """Space out a run for the ``[spell]`` case: ``"USA"`` → ``"U S A"``.

    Collapses surrounding whitespace, then joins the remaining *grapheme
    units* with single spaces so the engine pronounces each visible
    character discretely. Whitespace inside the run is treated as a
    separator (each token spelled, joined by a single space), so
    ``"go USA"`` → ``"g o U S A"``. See :func:`_grapheme_units` for why
    ``str`` code-point iteration alone is not enough.
    """
    if not word:
        return ""
    # Drop all existing whitespace, then interleave the visible characters
    # with spaces. ``split()`` + ``"".join`` removes runs of whitespace
    # first.
    # Group each whitespace-separated token on its own so separate jamo or
    # letters never fuse into one syllable across a space.
    return " ".join(unit for token in word.split() for unit in _grapheme_units(token))
