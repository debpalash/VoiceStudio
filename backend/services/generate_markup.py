"""Generate-mode span planning: pauses, sentence/paragraph gaps, SSML-lite.

``/generate`` (and the MCP ``generate_speech`` tool that calls it) used to
honour only inline ``[pause Nms]`` markers. Long narration came back with
sentences and topic changes run together, and ``[emphasis]``/``[slow]`` tags
reached the engine as text. Audiobook and Stories already had join gaps and
SSML-lite; this module brings the same grammar to one-shot generation.

:func:`plan_spans` turns request text into ``Span`` tuples
``(text, pause_ms_after, speed_factor)``. With default :class:`JoinOptions` and
no SSML-lite tags it returns exactly what ``parse_pause_markers`` returns
(speed factor ``None``), so existing requests keep their render path.

Grammar precedence (outer→inner), as in the longform parser:
``[pause]`` → SSML-lite (``[slow]/[fast]/[emphasis]/[spell]``) → gaps.

Pure and import-light (no torch) so it is cheap to unit-test.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple, Optional

from omnivoice.utils.text import PAUSE_MAX_MS, parse_pause_markers

#: Upper bound of a single sentence or paragraph gap, matching Audiobook's API.
GAP_MAX_MS = 5000
#: Most silence the gaps may add to one take, matching Audiobook's chapter cap.
MAX_JOIN_SILENCE_MS = 15 * 60 * 1000

# Sentence ends. Full-width/CJK marks end a sentence wherever they are; ASCII
# ``.!?`` only before whitespace or the end, so "3.14", "e.g.x" and URLs stay
# whole. Any closing quotes/brackets right after the mark stay with the sentence.
# One character class per part and no nested quantifiers, so matching is linear.
_CLOSERS = "\"'”’」』）)】》〉］\\]"
_SENTENCE_END_RE = re.compile(
    "(?:[。！？．｡‼⁇⁈⁉]+[" + _CLOSERS + "]*"
    "|[.!?]+[" + _CLOSERS + "]*(?=\\s|$))"
)
# A blank line: two newlines with only spaces/tabs/CR between (same as longform).
_BLANK_LINE_RE = re.compile(r"\n[ \t\r]*\n")


class Span(NamedTuple):
    """One engine call's text, the silence after it and its speed multiplier."""

    text: str
    pause_ms: int
    speed_factor: Optional[float] = None


@dataclass(frozen=True)
class JoinOptions:
    """Generate's join settings. All off by default: the render is unchanged."""

    sentence_gap_ms: int = 0
    paragraph_gap_ms: int = 0
    trim_engine_silence: bool = False

    @property
    def gaps_on(self) -> bool:
        return self.sentence_gap_ms > 0 or self.paragraph_gap_ms > 0


def _has_spoken_text(text: str) -> bool:
    return any(unicodedata.category(ch)[0] in "LN" for ch in text)


def _split_at_gaps(text: str, opts: JoinOptions) -> list[tuple[str, int]]:
    """Split ``text`` after each sentence end and blank line.

    Returns ``(piece, gap_ms_after)`` pairs; the last piece carries no gap. A
    blank line uses ``paragraph_gap_ms`` (or the sentence gap when that is 0).
    """
    para_ms = opts.paragraph_gap_ms or opts.sentence_gap_ms
    cuts: dict[int, int] = {}
    if opts.sentence_gap_ms > 0:
        for m in _SENTENCE_END_RE.finditer(text):
            cuts[m.end()] = opts.sentence_gap_ms
    if para_ms > 0:
        for m in _BLANK_LINE_RE.finditer(text):
            # The paragraph gap replaces a sentence gap at the same boundary:
            # drop any sentence cut that falls inside the blank-line whitespace.
            start = m.start()
            while start > 0 and text[start - 1] in " \t\r":
                start -= 1
            for pos in [p for p in cuts if start <= p <= m.end()]:
                del cuts[pos]
            cuts[start] = para_ms
    pieces: list[tuple[str, int]] = []
    last = 0
    for pos in sorted(cuts):
        if pos <= last or pos >= len(text):
            continue
        pieces.append((text[last:pos], cuts[pos]))
        last = pos
    pieces.append((text[last:], 0))
    return pieces


def _ssml_pieces(text: str) -> list[tuple[str, Optional[float]]]:
    """SSML-lite segments of ``text`` as ``(spoken_text, speed_factor)``."""
    from services.ssml_lite import _TAG_RE, parse_ssml_lite, spell_out

    if not _TAG_RE.search(text):
        return [(text, None)]
    out: list[tuple[str, Optional[float]]] = []
    for seg in parse_ssml_lite(text):
        spoken = spell_out(seg["text"]) if seg["spell"] else seg["text"]
        out.append((spoken, seg["speed"]))
    return out


def _merge_unspoken(spans: list[Span]) -> list[Span]:
    """Fold spans with nothing to say into the span before them.

    Whitespace-only pieces carry their silence back so nothing is lost, and a
    punctuation-only piece (``[slow]world[/slow].``) joins the text before it
    instead of reaching the engine alone, where some engines read it aloud or
    render a click. A leading empty span (text that starts with ``[pause]``)
    stays, as leading silence.
    """
    out: list[Span] = []
    for span in spans:
        if out and not _has_spoken_text(span.text):
            prev = out[-1]
            extra = span.text.strip()
            out[-1] = Span(prev.text.rstrip() + extra if extra else prev.text,
                           min(prev.pause_ms + span.pause_ms, PAUSE_MAX_MS),
                           prev.speed_factor)
            continue
        out.append(span)
    return out


def plan_spans(text: str, opts: Optional[JoinOptions] = None) -> list[Span]:
    """Plan the engine calls for one Generate request.

    An explicit ``[pause]`` keeps its exact length and replaces any gap at the
    same place; the end of the text never gets a gap. Raises ``ValueError``
    when the gaps would add more than :data:`MAX_JOIN_SILENCE_MS` of silence.
    """
    opts = opts or JoinOptions()
    planned: list[Span] = []
    explicit_ms = 0
    for pause_text, pause_ms in parse_pause_markers(text):
        explicit_ms += pause_ms
        inner: list[Span] = []
        for ssml_text, factor in _ssml_pieces(pause_text):
            pieces = _split_at_gaps(ssml_text, opts) if opts.gaps_on else [(ssml_text, 0)]
            inner.extend(Span(piece, gap_ms, factor) for piece, gap_ms in pieces)
        inner = _merge_unspoken(inner) or [Span("", 0)]
        last = inner[-1]
        inner[-1] = Span(last.text, pause_ms, last.speed_factor)
        planned.extend(inner)
    planned = _merge_unspoken(planned)
    if opts.gaps_on:
        added = sum(s.pause_ms for s in planned) - explicit_ms
        if added > MAX_JOIN_SILENCE_MS:
            minutes = MAX_JOIN_SILENCE_MS // 60000
            raise ValueError(
                f"The sentence and paragraph gaps add more than {minutes} minutes of "
                f"silence. Lower the gaps or split the text."
            )
    return planned


def needs_span_render(spans: list[Span], opts: Optional[JoinOptions] = None) -> bool:
    """True when the request must take the span renderer, not one-shot chunking."""
    if opts is not None and opts.trim_engine_silence:
        return True
    if len(spans) > 1:
        return True
    return bool(spans) and (spans[0].pause_ms > 0 or spans[0].speed_factor is not None)
