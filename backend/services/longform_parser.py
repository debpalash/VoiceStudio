"""Canonical longform marker parser (#27) — the single source of grammar truth.

The longform marker dialect (``# heading``, ``[voice:NAME]``, ``[pause …]``,
``[slow]/[fast]/[emphasis]/[spell]``) was parsed by three independent code
paths that disagreed (client/server/regex-level). This module is the one
canonical Python parser; ``frontend/src/utils/longformParser.js`` is its
mechanically-mirrored JS twin, and ``tests/fixtures/longform_parser_cases.json``
is the shared golden corpus asserted byte-for-byte against both.

Pure text→plan, import-light (no torch). Grammar precedence (outer→inner):

    # chapter  →  [voice:]  →  [pause]  →  SSML-lite  →  [spell]

It reuses the existing pause dialect (``omnivoice.utils.text.parse_pause_markers``)
and SSML-lite (``services.ssml_lite``) verbatim so those modules stay the single
home of their sub-grammars.
"""
from __future__ import annotations

import re
from typing import Optional

from omnivoice.utils.text import parse_pause_markers

# A Markdown H1 (``# Title``) starts a new chapter. Deeper headings (``##``…)
# stay in the body as ordinary text. The title capture starts with ``\S`` (a
# non-space) so the leading ``[ \t]+`` and the title's ``.*`` can't both match
# the same whitespace run — that overlap is what makes ``[ \t]+(.+)``
# polynomial-time on adversarial tabs (ReDoS). Moved verbatim from
# audiobook.py (already CodeQL-cleared). Stripped in code.
_HEADING_RE = re.compile(r"^[ \t]*#[ \t]+(\S.*)$", re.MULTILINE)
# ``[voice:NAME]`` switches the active narrator. The content class excludes BOTH
# brackets (``[^\]\[]``) so nested ``[voice:`` prefixes can't create overlapping
# match attempts across ``finditer`` (the ReDoS source). A voice name never
# contains a bracket; the value is stripped in code. Empty → default voice.
_VOICE_RE = re.compile(r"\[voice:([^\]\[]*)\]")
# A blank line (paragraph break). Linear: one ``\n``, a run of inline
# whitespace, one ``\n`` — no nested quantifiers.
_BLANK_LINE_RE = re.compile(r"\n[ \t\r]*\n")


def _normalize(text: Optional[str]) -> str:
    """Coerce None→'' and normalize CRLF/CR→LF so ``$`` (re.MULTILINE) and span
    text never carry a stray ``\\r`` on Windows-authored scripts — a
    cross-platform default-behaviour divergence the JS twin mirrors exactly."""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _parse_chapter_body(
    body: str,
    *,
    default_voice: Optional[str] = None,
    default_speed: Optional[float] = None,
) -> list[dict]:
    """Voice→pause→SSML layering for ONE chapter body (no chapter split).

    Returns a list of span dicts ``{voice_id, text, pause_ms_after, speed}``.
    A ``#`` inside ``body`` is NOT treated as a heading here — that is the
    caller's (chapter-split) concern. The JS twin (``parseChapterBody``) is what
    ``storyToSpans`` calls per spoken track."""
    spans: list[dict] = []
    cur_voice = default_voice
    runs: list[tuple[Optional[str], str]] = []
    last = 0
    for m in _VOICE_RE.finditer(body):
        if m.start() > last:
            runs.append((cur_voice, body[last:m.start()]))
        cur_voice = (m.group(1).strip() or default_voice)
        last = m.end()
    runs.append((cur_voice, body[last:]))

    from services.ssml_lite import parse_ssml_lite, spell_out

    for voice, run_text in runs:
        for span_text, pause_ms in parse_pause_markers(run_text):
            t = span_text.strip()
            if not t and pause_ms == 0:
                continue  # pure whitespace between markers — nothing to render
            # (text, speed, paragraph_break_before). The whitespace BETWEEN two
            # kept segments is tracked so a blank line that happens to sit on a
            # markup boundary still ends the line instead of being swallowed.
            rendered: list[tuple[str, Optional[float], bool]] = []
            between = ""
            for seg in (parse_ssml_lite(t) if t else []):
                raw = seg["text"]
                st = (spell_out(raw) if seg["spell"] else raw).strip()
                if not st:
                    between += raw
                    continue
                # Inline SSML speed overrides the per-line default; a plain
                # segment inherits default_speed.
                sp = seg["speed"] if seg["speed"] is not None else default_speed
                lead = raw[:len(raw) - len(raw.lstrip())]
                rendered.append((st, sp, bool(_BLANK_LINE_RE.search(between + lead))))
                between = raw[len(raw.rstrip()):]
            if not rendered:
                # Only-markers / empty text but a real pause → carry the silence.
                if pause_ms > 0:
                    spans.append({"voice_id": voice, "text": "",
                                  "pause_ms_after": pause_ms, "speed": None})
                continue
            for j, (st, sp, _brk) in enumerate(rendered):
                span = {
                    "voice_id": voice, "text": st,
                    "pause_ms_after": pause_ms if j == len(rendered) - 1 else 0,
                    "speed": sp,
                }
                if j < len(rendered) - 1:
                    # Inline markup split one run of text. Say how this span
                    # joins the next: straight on, or across a blank line. Key
                    # present only here — plain scripts parse byte-identically.
                    span["join"] = "paragraph" if rendered[j + 1][2] else "continue"
                spans.append(span)
    return spans


def parse_script_to_spans(
    text: Optional[str],
    *,
    default_voice: Optional[str] = None,
    default_speed: Optional[float] = None,
) -> list[dict]:
    """Parse a chapter-delimited script into ``[{"title", "spans": [...]}, …]``.

    span dict == ``{"voice_id": str|None, "text": str, "pause_ms_after": int,
    "speed": float|None}`` (key order matches ``Span.to_dict()``).

    Contract:
      * None / "" / whitespace-only input → ``[]``.
      * CRLF/CR normalized to LF at entry (cross-platform parity).
      * H1 (``# <non-space>…``) opens a chapter; ``##``…``######`` and ``# ``
        (no ``\\S`` title) are body.
      * Each chapter body resets the active voice to ``default_voice``.
      * A span is dropped iff its text is empty AND pause_ms_after == 0.
      * Chapters with no surviving spans are dropped; untitled bodies are
        numbered ``Chapter {kept_so_far + 1}`` (post-drop numbering).
    """
    text = _normalize(text)
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        raw = [(None, text)]
    else:
        raw = []
        intro = text[:matches[0].start()]
        if intro.strip():
            raw.append((None, intro))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            raw.append((m.group(1).strip(), text[m.end():end]))

    chapters: list[dict] = []
    for title, body in raw:
        spans = _parse_chapter_body(body, default_voice=default_voice,
                                    default_speed=default_speed)
        if not spans:
            continue
        chapters.append({"title": title or f"Chapter {len(chapters) + 1}",
                         "spans": spans})
    return chapters
