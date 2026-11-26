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


#: Most SSML-lite tags one request may carry. Nesting depth is bounded by this,
#: so tracking the open tags stays cheap whatever the text length.
MAX_TAGS = 500


class _SsmlState:
    """Open SSML-lite tags carried across pause and blank-line pieces.

    The effective prosody is resolved when a tag changes the stack, never per
    piece, so the work is linear in text length plus tags (times depth).
    """

    def __init__(self) -> None:
        from services.ssml_lite import _TAG_RE, _resolve

        self._tag_re, self._resolve_stack = _TAG_RE, _resolve
        self.stack: list[str] = []
        self.props = self._resolve_stack(self.stack)

    def feed(self, text: str) -> list[tuple[str, Optional[float]]]:
        """Spoken ``(text, speed_factor)`` segments of ``text``; tags update the state."""
        from services.ssml_lite import spell_out

        segs: list[dict] = []

        def emit(chunk: str) -> None:
            if not chunk:
                return
            p = self.props
            if segs and all(segs[-1][k] == p[k] for k in ("speed", "spell", "emphasis")):
                segs[-1]["text"] += chunk
            else:
                segs.append({"text": chunk, **p})

        last = 0
        for m in self._tag_re.finditer(text):
            emit(text[last:m.start()])
            last = m.end()
            name = m.group(2).lower()
            if m.group(1) == "/":
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i] == name:
                        del self.stack[i]
                        break
            else:
                self.stack.append(name)
            self.props = self._resolve_stack(self.stack)
        emit(text[last:])
        return [(spell_out(g["text"]) if g["spell"] else g["text"], g["speed"])
                for g in segs]


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
    from services.ssml_lite import _TAG_RE

    if len(_TAG_RE.findall(text)) > MAX_TAGS:
        raise ValueError(f"Too many [slow]/[fast]/[emphasis]/[spell] tags (at most {MAX_TAGS}).")
    state = _SsmlState()
    for pause_text, pause_ms in parse_pause_markers(text):
        explicit_ms += pause_ms
        inner: list[Span] = []
        pieces = _split_at_gaps(pause_text, opts) if opts.gaps_on else [(pause_text, 0)]
        for piece, gap_ms in pieces:
            sub = [Span(t, 0, f) for t, f in state.feed(piece)]
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
