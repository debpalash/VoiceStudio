"""Generate-mode span planning: pauses, paragraph gaps, SSML-lite.

``/generate`` (and the MCP ``generate_speech`` tool that calls it) used to
honour only inline ``[pause Nms]`` markers. Long narration came back with
topic changes run together, and ``[emphasis]``/``[slow]`` tags
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

#: Upper bound of a single paragraph gap, matching Audiobook's API.
GAP_MAX_MS = 5000
#: Most silence the gaps may add to one take, matching Audiobook's chapter cap.
MAX_JOIN_SILENCE_MS = 15 * 60 * 1000

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

    paragraph_gap_ms: int = 0
    trim_engine_silence: bool = False

    @property
    def gaps_on(self) -> bool:
        return self.paragraph_gap_ms > 0


def _has_spoken_text(text: str) -> bool:
    return any(unicodedata.category(ch)[0] in "LN" for ch in text)


def _split_at_gaps(text: str, opts: JoinOptions) -> list[tuple[str, int]]:
    """Split ``text`` at each blank line.

    Returns ``(piece, gap_ms_after)`` pairs; the last piece carries no gap.
    """
    pieces: list[tuple[str, int]] = []
    last = 0
    for m in _BLANK_LINE_RE.finditer(text):
        start = m.start()
        while start > last and text[start - 1] in " \t\r":
            start -= 1
        if start <= last or start >= len(text):
            continue
        pieces.append((text[last:start], opts.paragraph_gap_ms))
        last = start
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
                f"The paragraph gaps add more than {minutes} minutes of "
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
