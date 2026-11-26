"""Subtitle timing problems a render will hit, found before any synthesis.

Narration and opted-in SRT-only rescue place speech inside each cue's own
window (services/subtitle_audio.py), so overlapping or out-of-range cues are
reported by number. Every other render keeps its existing timing; there a cue
of 0.05 s or less that holds words cannot be spoken, which the read-only plan
reports so the user can fix it before a long render.
"""
from __future__ import annotations

import math

# Matches bounded_audio_layout's tolerances, so a cue it would reject is
# reported here first, by number.
_OVERLAP_EPS = 1e-6
_SOURCE_EPS = 1e-3


def uses_fixed_windows(req, *, subtitle_only: bool) -> bool:
    """True when this render places speech inside each cue's own window."""
    strategy = (req.timing_strategy or "concise").lower()
    return strategy == "narration" or (subtitle_only and bool(req.auto_fit_overflow))


MIN_SPOKEN_SEGMENT_S = 0.05


def cue_issues(segments, segment_ids, *, fixed_windows: bool, source_duration: float = 0.0) -> list[dict]:
    if not fixed_windows:
        return [{"code": "dub_cue_too_short", "segment": index, "cue": index + 1,
                 "segment_id": str(segment_ids[index] if index < len(segment_ids) else f"seg_{index}"),
                 "start": segment.start, "end": segment.end}
                for index, segment in enumerate(segments)
                if segment.text.strip() and segment.end - segment.start <= MIN_SPOKEN_SEGMENT_S]
    source_duration = float(source_duration or 0.0)
    issues = []
    previous = None
    for index, segment in enumerate(segments):
        start, end = float(segment.start), float(segment.end)
        row = {"segment": index, "cue": index + 1,
               "segment_id": str(segment_ids[index] if index < len(segment_ids) else f"seg_{index}"),
               "start": start, "end": end}
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            issues.append({**row, "code": "dub_cue_invalid_window"})
        elif previous is not None and start < float(segments[previous].end) - _OVERLAP_EPS:
            issues.append({**row, "code": "dub_cue_overlap", "overlaps_cue": previous + 1})
        elif source_duration > 0 and end > source_duration + _SOURCE_EPS:
            issues.append({**row, "code": "dub_cue_beyond_media", "media_duration": source_duration})
        previous = index
    return issues


def describe_issues(issues: list[dict], *, rescue: bool = False, noun: str = "subtitle", limit: int = 5) -> str:
    """One actionable sentence naming the cues (1-based) to fix."""
    parts = []
    for issue in issues[:limit]:
        if issue["code"] == "dub_cue_overlap":
            parts.append(f"{noun} {issue['cue']} starts before {noun} {issue['overlaps_cue']} ends")
        elif issue["code"] == "dub_cue_beyond_media":
            parts.append(f"{noun} {issue['cue']} ends at {issue['end']:.2f} s, past the end of the source "
                         f"({issue['media_duration']:.2f} s)")
        else:
            parts.append(f"{noun} {issue['cue']} has no valid time window")
    if len(issues) > limit:
        parts.append(f"{len(issues) - limit} more")
    fix = (f"Adjust or merge those {noun}s, or turn off automatic overflow fitting"
           if rescue else f"Adjust or merge those {noun}s")
    return f"These {noun} times cannot be fitted: " + "; ".join(parts) + f". {fix}, then generate again."
