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

from omnivoice.utils.text import parse_pause_markers

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


def _scan_tags(text: str, stack: list[str]) -> None:
    """Update the open-tag ``stack`` with the SSML-lite tags in ``text``."""
    from services.ssml_lite import _TAG_RE

    for m in _TAG_RE.finditer(text):
        name = m.group(2).lower()
        if m.group(1) == "/":
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == name:
                    del stack[i]
                    break
        else:
            stack.append(name)


def _ssml_pieces(text: str, stack: list[str]) -> list[tuple[str, Optional[float]]]:
    """SSML-lite segments of ``text`` as ``(spoken_text, speed_factor)``.

    ``stack`` holds the tags still open from earlier text (a ``[slow]`` that
    spans a ``[pause]`` or a blank line); it is updated for the text after this one.
    """
    from services.ssml_lite import _TAG_RE, parse_ssml_lite, spell_out

    if not stack and not _TAG_RE.search(text):
        return [(text, None)]
    reopened = "".join(f"[{name}]" for name in stack)
    _scan_tags(text, stack)
    out: list[tuple[str, Optional[float]]] = []
    for seg in parse_ssml_lite(reopened + text):
        spoken = spell_out(seg["text"]) if seg["spell"] else seg["text"]
        out.append((spoken, seg["speed"]))
    return out


def _merge_unspoken(spans: list[Span]) -> list[Span]:
    """Fold spans with nothing to say into the span before them.

    Whitespace-only pieces carry their silence back so nothing is lost, and a
    punctuation-only piece (``[slow]world[/slow].``) joins the text before it
    instead of reaching the engine alone, where some engines read it aloud or
    render a click. A leading empty span (text that starts with ``[pause]``)
    stays, as leading silence. Silences are summed, not clamped: each
    ``[pause]`` already honours its own limit.
    """
    out: list[Span] = []
    for span in spans:
        if out and not _has_spoken_text(span.text):
            prev = out[-1]
            extra = span.text.strip()
            out[-1] = Span(prev.text.rstrip() + extra if extra else prev.text,
                           prev.pause_ms + span.pause_ms,
                           prev.speed_factor)
            continue
        out.append(span)
    return out


def plan_spans(text: str, opts: Optional[JoinOptions] = None) -> list[Span]:
    """Plan the engine calls for one Generate request.

    Order: ``[pause]`` → paragraph gaps → SSML-lite. A tag stays open across a
    pause or a blank line. An explicit ``[pause]`` keeps its exact length and
    replaces any gap at the same place; the end of the text never gets a gap.
    Raises ``ValueError`` when the gaps would add more than
    :data:`MAX_JOIN_SILENCE_MS` of silence.
    """
    opts = opts or JoinOptions()
    planned: list[Span] = []
    explicit_ms = 0
    stack: list[str] = []
    for pause_text, pause_ms in parse_pause_markers(text):
        explicit_ms += pause_ms
        inner: list[Span] = []
        pieces = _split_at_gaps(pause_text, opts) if opts.gaps_on else [(pause_text, 0)]
        for piece, gap_ms in pieces:
            sub = [Span(t, 0, f) for t, f in _ssml_pieces(piece, stack)]
            if sub:
                sub[-1] = Span(sub[-1].text, gap_ms, sub[-1].speed_factor)
                inner.extend(sub)
            elif gap_ms and inner:
                inner[-1] = Span(inner[-1].text, inner[-1].pause_ms + gap_ms,
                                 inner[-1].speed_factor)
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


def needs_span_render(spans: list[Span], opts: Optional[JoinOptions] = None,
                      text: Optional[str] = None) -> bool:
    """True when the request must take the span renderer, not one-shot chunking.

    ``text`` is the request text: a single plain span whose planned text differs
    from it had tags stripped or spelled out, so the engine must get the plan.
    """
    if opts is not None and opts.trim_engine_silence:
        return True
    if len(spans) > 1:
        return True
    if not spans:
        return False
    if spans[0].pause_ms > 0 or spans[0].speed_factor is not None:
        return True
    return text is not None and spans[0].text != text
