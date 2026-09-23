"""SRT (SubRip subtitle) parser.

Lenient by design — many "SRT" files in the wild are slightly off-spec
(missing index numbers, blank-line variants, BOM, `.` instead of `,` in
the milliseconds separator). We accept what we can, drop what we can't,
and report counts so the caller can warn the user.

Returns a list of segments compatible with the dub-pipeline shape used
elsewhere in the backend:

    {
        "id": int,
        "start": float,             # seconds
        "end": float,               # seconds
        "text": str,
        "text_original": str,       # same as `text` on import; mutable later
        "speaker_id": "Speaker 1",  # filler — no diarization on raw .srt
    }
"""
from __future__ import annotations

import html
import re
import secrets
from dataclasses import dataclass


# Captures: HH MM SS sep(`,` or `.`) ms (1-3 digits)
_TS = r"(?:(\d{1,2}):)?([0-5]?\d):([0-5]?\d)[,.](\d{1,3})"
# Horizontal whitespace only — NEVER plain `\s`, which matches newlines.
# A timing line lives on ONE line, so `\s*` bought nothing but catastrophic
# backtracking: under re.MULTILINE the engine restarts at every line start,
# and `^\s*` there happily consumes every remaining blank line before
# failing on the first digit, making the scan quadratic in the input size.
# A .srt of blank lines (a mis-saved export, a paste gone wrong) pinned the
# parse for hours — 20k blank lines already took 1.7s, 2 MB never returned.
_H = r"[^\S\n]*"
# Whole timing line: `00:00:01,000 --> 00:00:04,500` plus optional trailing
# cue style hints (X1: Y1: ... ) we just throw away.
_TIMING_RE = re.compile(rf"^{_H}{_TS}{_H}-->{_H}{_TS}.*$", re.MULTILINE)


def _ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    # Pad ms to 3 digits so "5" -> 0.005, "50" -> 0.050.
    ms_padded = (ms + "000")[:3]
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms_padded) / 1000.0


def _is_index_line(line: str) -> bool:
    """True when `line` is a bare SubRip cue number.

    Stricter than `str.isdigit()` on purpose: that also accepts non-ASCII
    numerals (Arabic-Indic "١٩٩٩", Devanagari "२०२६", and the full-width
    forms), which in a 646-language dubbing app are dialogue, never the
    ASCII cue indices SubRip actually writes.
    """
    stripped = line.strip()
    return stripped.isascii() and stripped.isdigit()


@dataclass
class SrtParseResult:
    segments: list[dict]
    skipped_cues: int            # malformed cues we couldn't recover
    dropped_overlaps: int        # cues that overlapped a kept one


def parse_srt(content: str) -> SrtParseResult:
    """Parse SRT text and return cleaned, non-overlapping segments.

    - Skips cues with non-positive duration or unparseable timestamps.
    - When two cues overlap, keeps the earlier one and shifts the later
      one's `start` forward to the earlier's `end` (rather than dropping
      it outright — overlapping is common in captions and the user's
      intent is usually "both lines should play, in order"). If the
      adjustment leaves the later cue with zero/negative duration it
      gets dropped and `dropped_overlaps` increments.
    """
    if not content:
        return SrtParseResult([], 0, 0)

    # Strip BOM and normalise line endings; many editors save SRTs as CRLF.
    text = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")

    is_webvtt = bool(re.match(r"WEBVTT(?:[ \t]|\n|$)", text.lstrip()))
    if is_webvtt:
        # Metadata is block-scoped. Filter it BEFORE scanning timings so an
        # example timestamp inside a NOTE/STYLE/REGION cannot become speech.
        blocks = []
        for block in re.split(r"\n[^\S\n]*\n", text):
            lines = block.strip().split("\n")
            first = lines[0].strip()
            # WebVTT's block parser gives a timing line in position two
            # precedence over the identifier (including STYLE/REGION/NOTE).
            # https://www.w3.org/TR/webvtt1/#file-parsing
            identifies_cue = len(lines) > 1 and _TIMING_RE.match(lines[1])
            metadata = first in {"STYLE", "REGION"} or re.match(r"NOTE(?:[ \t]|$)", first)
            if metadata and not identifies_cue:
                continue
            blocks.append(block)
        text = "\n\n".join(blocks)
    raw: list[dict] = []
    skipped = 0
    # Each cue source gets an id unique to this import. A later generate keeps
    # the source only when the client echoes that id (see CUE_SOURCE_FIELDS).
    import_id = secrets.token_hex(6)
    # Find every timing line, slice the cue text from there to the next
    # timing line (or end of file). This is robust to missing index
    # numbers and to spec deviations in the blank-line separator.
    matches = list(_TIMING_RE.finditer(text))
    # A mixed file can stop numbering at any cue. Track each boundary;
    # never treat an initial index as permission to discard later numbers.
    head = text[:matches[0].start()].strip() if matches else ""
    first_marker = head.split("\n")[-1].strip() if head else ""
    cue_index = int(first_marker) if _is_index_line(first_marker) and len(first_marker) <= 12 else None
    for i, m in enumerate(matches):
        body_start = m.end()
        has_next = i + 1 < len(matches)
        body_end = matches[i + 1].start() if has_next else len(text)
        body = text[body_start:body_end]
        if is_webvtt:
            # The blank separator ends WebVTT dialogue; following identifiers,
            # NOTE/STYLE blocks belong outside the cue, even when numeric.
            body = re.split(r"\n[^\S\n]*\n", body, maxsplit=1)[0]
        # An index must directly precede the next timing line. A blank line
        # AFTER a number instead marks that number as preceding dialogue.
        next_index = None
        if has_next and not is_webvtt:
            # Inspect lines rather than a backtracking regex on uploaded text.
            # One newline terminates the marker; a second means it is dialogue.
            marker_lines = body.split("\n")
            if marker_lines and not marker_lines[-1].strip(" \t"):
                marker_lines.pop()
            marker = marker_lines[-1].strip(" \t") if marker_lines else ""
            numeric = bool(marker) and marker.isascii() and marker.isdecimal()
            separated = len(marker_lines) > 2 and not marker_lines[-2].strip()
            expected_index = cue_index + 1 if cue_index is not None else i + 2
            expected = marker.lstrip("0") == str(expected_index)
            has_dialogue = any(line.strip() for line in marker_lines[:-1])
            if numeric and expected and (has_dialogue or separated) and (cue_index is not None or separated):
                body = "\n".join(marker_lines[:-1])
                next_index = expected_index
        cue_index = next_index
        try:
            start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
            end = _ts_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        except (ValueError, IndexError):
            skipped += 1
            continue
        if end <= start:
            skipped += 1
            continue
        lines = body.strip("\n").split("\n")
        source_cue = "\n".join(line.strip() for line in lines if line.strip())
        # Markup is not speech. Strip it before WebVTT unescape so a real
        # `<i>` tag drops and a written `&lt;i&gt;` still reads as `<i>`.
        cue_text = spoken_cue_text(source_cue, webvtt=is_webvtt)
        if not cue_text:
            skipped += 1
            continue
        # Keep the cue as written so an unchanged export restores its markup,
        # and so exporters know which syntax the text follows.
        source_key = "webvtt_source" if is_webvtt else "srt_source"
        raw.append({"start": start, "end": end, "text": cue_text,
                    source_key: {"id": f"{import_id}:{i}", "text": cue_text, "cue": source_cue}})

    raw.sort(key=lambda r: r["start"])

    # De-overlap pass.
    out: list[dict] = []
    dropped = 0
    last_end = 0.0
    for r in raw:
        s, e = r["start"], r["end"]
        if s < last_end:
            s = last_end
        if e <= s:
            dropped += 1
            continue
        out.append({**r, "start": s, "end": e})
        last_end = e

    segments = [
        {
            "id": i,
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"],
            "text_original": seg["text"],
            "speaker_id": "Speaker 1",
            **{k: seg[k] for k in CUE_SOURCE_FIELDS if k in seg},
            # The text is this import's own words.
            **{CUE_SOURCE_ID: seg[k]["id"] for k in CUE_SOURCE_FIELDS if k in seg},
        }
        for i, seg in enumerate(out)
    ]
    return SrtParseResult(segments=segments, skipped_cues=skipped, dropped_overlaps=dropped)


def format_cue_timestamp(seconds: float, ms_separator: str) -> str:
    """`HH:MM:SS<sep>mmm` for `seconds`, rounded to the millisecond.

    Rounds the whole value once, then splits it, so a time that is not exact
    in binary (2.3 is 2.29999...) stays 2.300 instead of truncating to 2.299,
    which moved every such cue a millisecond early on export, and 59.9996
    carries to the next second instead of printing `,1000`. SRT separates
    the milliseconds with `,`; WebVTT with `.`.
    """
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{ms_separator}{ms:03d}"


# Cue markup a segment may carry: WebVTT's tags (`<i>`, `<c.yellow>`,
# `<v Roger>`), SubRip's `<font>` (players skip it) and timestamp tags.
_CUE_MARKUP_RE = re.compile(
    r"</?(?:[biu]|c|v|lang|ruby|rt|font)(?=[\s.>])[^<>\n]*>|<(?:\d+:)?\d{2}:\d{2}\.\d{3}>",
    re.IGNORECASE,
)
# In WebVTT an unescaped `<` always opens a tag and players drop unknown
# ones, so every tag is markup (`<c.colorE5E5E5>`, `<00:00:01.200>`). The
# body excludes `<` so a run of unclosed `<` cannot make the scan quadratic.
_WEBVTT_TAG_RE = re.compile(r"<[^<>\n]*>")
# SubRip has no escaping, so `a<b and c>d` is dialogue. Only exact tag
# shapes are markup: `<i>`/`<b.x>`/`</u>`, `<c.colorE5E5E5>`, `<v Roger>`,
# `<font color=...>` and karaoke timestamps. Mirrored in
# frontend/src/utils/importStory.js (CUE_MARKUP); keep the two in step.
_SRT_MARKUP_RE = re.compile(
    r"</?(?:[biu]|c|ruby|rt)(?:\.[^\s.<>]+)*>"
    r"|<(?:v|lang)(?:\.[^\s.<>]+)*[ \t][^<>\n]*>|</(?:v|lang)>"
    r"|<font[ \t][^<>\n]*>|</?font>"
    r"|<(?:\d+:)?\d{2}:\d{2}\.\d{3}>",
    re.IGNORECASE,
)
# SubRip/ASS overrides (`{\an8}`, `{\i1}`). A `{` in dialogue has no backslash.
# The body excludes `{` for the same linear-time reason.
_ASS_OVERRIDE_RE = re.compile(r"\{\\[^{}\n]*\}")
# `<br>` is a rendered line break in SubRip (and a stray one in WebVTT), so
# it separates words instead of vanishing.
_LINE_BREAK_RE = re.compile(r"<br[ \t]*/?>", re.IGNORECASE)
# An `&` that does not already start a character reference.
_BARE_AMPERSAND_RE = re.compile(r"&(?!#\d+;|#[xX][0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)")


def spoken_cue_text(text: str, *, webvtt: bool = False) -> str:
    """Return the words a cue should speak, without player markup.

    Tags and alignment overrides are dropped first so a WebVTT entity that
    decodes to `<i>` stays literal (`&lt;i&gt;` is speech; `<i>Hi</i>` is not).
    SubRip has no escaping, so a `<` there is often dialogue ("2 < 3",
    "<laughter>"): only the tags players render (`<i>`, `<font>`, karaoke
    timestamps) and ASS overrides are removed.
    """
    out = _LINE_BREAK_RE.sub(" ", _ASS_OVERRIDE_RE.sub("", text))
    out = (_WEBVTT_TAG_RE if webvtt else _SRT_MARKUP_RE).sub("", out)
    if webvtt:
        out = html.unescape(out)
    return "\n".join(line.strip() for line in out.split("\n") if line.strip())


# Segment fields holding the cue as imported. They are provenance, not text:
# a cue is reused only while the segment's CUE_SOURCE_ID names it, meaning its
# `text` is still that import's words, never because later text is equal.
# Imports set CUE_SOURCE_ID; clients echo it on /dub/generate while the text
# is untouched and drop it on any paste, edit or translation. The source
# record itself stays, so returning to the original language can restore it.
CUE_SOURCE_FIELDS = ("webvtt_source", "srt_source")
CUE_SOURCE_ID = "cue_source_id"


def vouch_cue_source(row: dict, text: str, cue_source_id: str | None) -> str | None:
    """Set ``row``'s CUE_SOURCE_ID when ``cue_source_id`` names its cue for ``text``."""
    vouched = None
    for key in CUE_SOURCE_FIELDS:
        source = row.get(key)
        if (isinstance(source, dict) and cue_source_id and source.get("id") == cue_source_id
                and source.get("text") == text):
            vouched = cue_source_id
    if vouched:
        row[CUE_SOURCE_ID] = vouched
    else:
        row.pop(CUE_SOURCE_ID, None)
    return vouched


def srt_cue_to_webvtt(cue: str) -> str:
    """A SubRip cue as escaped WebVTT cue text.

    SubRip has no escaping, so only its player tags stay markup; every other
    `<` and `&` is dialogue and is escaped (`a<b and c>d` stays readable).
    `{\\an8}` overrides have no WebVTT meaning and `<br>` becomes a newline.
    """
    lines = _LINE_BREAK_RE.sub("\n", _ASS_OVERRIDE_RE.sub("", cue)).split("\n")
    # A blank line ends a WebVTT cue, so doubled or edge breaks must not leave one.
    cue = "\n".join(line.strip() for line in lines if line.strip())
    parts = []
    last = 0
    for markup in _SRT_MARKUP_RE.finditer(cue):
        parts.append(_escape_plain_span(cue[last:markup.start()]))
        parts.append(markup.group(0))
        last = markup.end()
    parts.append(_escape_plain_span(cue[last:]))
    return "".join(parts)


def _escape_plain_span(span: str) -> str:
    return span.replace("&", "&amp;").replace("<", "&lt;").replace("-->", "--&gt;")


def source_cue_or(seg: dict, text: str, key: str) -> str:
    """The imported cue syntax for ``text`` while it is unchanged, else ``text``.

    Imports strip markup from the spoken text; an export of the same text
    writes the original cue back so italics and `{\\an8}` survive a
    round trip. Edited text and older projects fall back to ``text``.
    """
    source = seg.get(key)
    if not (isinstance(source, dict) and source.get("text") == text
            and isinstance(source.get("cue"), str)):
        return text
    # Cues from before #2295 carry no id and keep the equal-text rule.
    if source.get("id") and seg.get(CUE_SOURCE_ID) != source["id"]:
        return text
    return source["cue"]


def _escape_cue_span(span: str) -> str:
    return _BARE_AMPERSAND_RE.sub("&amp;", span).replace("<", "&lt;").replace("-->", "--&gt;")


def escape_webvtt_text(text: str, *, preserve_markup: bool = True) -> str:
    """Make cue text safe for a WebVTT file without touching its markup.

    Any other `<` opens a tag, so a player drops the rest of the cue ("I <3
    you" shows as "I "), and a line containing `-->` ends the cue, emptying
    it. A bare `&` becomes `&amp;`; an existing reference is not escaped
    twice. SubRip has no escaping, so SRT text is written as-is.

    Set ``preserve_markup=False`` for known plain text, such as fresh ASR
    output. Literal tags and references then remain visible as spoken text.
    """
    if not preserve_markup:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace("-->", "--&gt;")
    parts = []
    last = 0
    for markup in _CUE_MARKUP_RE.finditer(text):
        parts.append(_escape_cue_span(text[last:markup.start()]))
        parts.append(markup.group(0))
        last = markup.end()
    parts.append(_escape_cue_span(text[last:]))
    return "".join(parts)
