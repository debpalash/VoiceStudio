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


def _resolve(open_tags: dict[str, list[int]]) -> dict:
    """Resolve the four-tag vocabulary without walking the nesting depth.

    Each tag keeps its open positions. The latest speed-bearing tag wins;
    spell and emphasis remain active while any matching open survives.
    """
    latest = -1
    speed: Optional[float] = None
    for name in ("slow", "fast", "emphasis"):
        positions = open_tags[name]
        if positions and positions[-1] > latest:
            latest = positions[-1]
            speed = _TAGS[name]["speed"]
    return {"speed": speed, "spell": bool(open_tags["spell"]),
            "emphasis": bool(open_tags["emphasis"])}


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
    open_tags: dict[str, list[int]] = {name: [] for name in _TAGS}
    position = 0
    last = 0

    def emit(chunk: str) -> None:
        if not chunk:
            return
        props = _resolve(open_tags)
        seg = {"text": [chunk], **props}
        # Merge with the previous segment when prosody is identical so plain
        # text never fragments into multiple identical-styled pieces.
        if segments:
            prev = segments[-1]
            if (
                prev["speed"] == seg["speed"]
                and prev["spell"] == seg["spell"]
                and prev["emphasis"] == seg["emphasis"]
            ):
                prev["text"].append(chunk)
                return
        segments.append(seg)

    for m in _TAG_RE.finditer(text):
        emit(text[last:m.start()])
        last = m.end()
        is_close = m.group(1) == "/"
        name = m.group(2).lower()
        if is_close:
            # Remove only the nearest matching open, including non-top closes.
            if open_tags[name]:
                open_tags[name].pop()
        else:
            open_tags[name].append(position)
            position += 1  # unclosed opens remain active to EOL

    emit(text[last:])

    if not segments:
        # Input was only tag markers (e.g. "[slow][/slow]"): nothing to speak.
        return []
    return [{**seg, "text": "".join(seg["text"])} for seg in segments]


def spell_out(word: str) -> str:
    """Space out a run for the ``[spell]`` case: ``"USA"`` → ``"U S A"``.

    Collapses surrounding whitespace, then joins the remaining grapheme
    clusters with single spaces so the engine pronounces each letter discretely. Whitespace
    inside the run is treated as a separator (each token spelled, joined by a
    single space), so ``"go USA"`` → ``"g o U S A"``.
    """
    if not word:
        return ""
    # Segment tokens separately: removing whitespace first could join an
    # Indic virama, combining mark or regional indicator to the next token.
    import regex

    return " ".join(
        cluster for token in word.split() for cluster in regex.findall(r"\X", token)
    )
