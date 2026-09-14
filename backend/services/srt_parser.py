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

import re
from dataclasses import dataclass


# Captures: HH MM SS sep(`,` or `.`) ms (1-3 digits)
_TS = r"(\d{1,2}):([0-5]?\d):([0-5]?\d)[,.](\d{1,3})"
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
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms_padded) / 1000.0


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

    raw: list[dict] = []
    skipped = 0
    # Find every timing line, slice the cue text from there to the next
    # timing line (or end of file). This is robust to missing index
    # numbers and to spec deviations in the blank-line separator.
    matches = list(_TIMING_RE.finditer(text))
    for i, m in enumerate(matches):
        try:
            start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
            end = _ts_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        except (ValueError, IndexError):
            skipped += 1
            continue
        if end <= start:
            skipped += 1
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip("\n")
        # Drop the trailing index number of the NEXT cue (which got eaten
        # into our body) by trimming trailing digit-only lines.
        lines = body.split("\n")
        while lines and lines[-1].strip().isdigit():
            lines.pop()
        cue_text = "\n".join(line.strip() for line in lines if line.strip())
        if not cue_text:
            skipped += 1
            continue
        raw.append({"start": start, "end": end, "text": cue_text})

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
        out.append({"start": s, "end": e, "text": r["text"]})
        last_end = e

    segments = [
        {
            "id": i,
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"],
            "text_original": seg["text"],
            "speaker_id": "Speaker 1",
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
