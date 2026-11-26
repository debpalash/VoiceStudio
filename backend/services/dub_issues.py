"""Subtitle timing problems a render will hit, found before any synthesis.

Narration and opted-in SRT-only rescue place speech inside each cue's own
window (services/subtitle_audio.py), so overlapping or out-of-range cues are
reported by number. Every other render keeps its existing timing; there a cue
of 0.05 s or less that holds words cannot be spoken (main refuses to publish
it after the whole render), so it is refused up front, also by number.
"""
from __future__ import annotations

import math
import re

# Matches bounded_audio_layout's tolerances, so a cue it would reject is
# reported here first, by number.
_OVERLAP_EPS = 1e-6
_SOURCE_EPS = 1e-3
#: A fixed window shorter than this holds no playable sample at any engine rate.
_MIN_WINDOW_S = 1e-3


def uses_fixed_windows(req, *, subtitle_only: bool) -> bool:
    """True when this render places speech inside each cue's own window."""
    strategy = (req.timing_strategy or "concise").lower()
    return strategy == "narration" or (subtitle_only and bool(req.auto_fit_overflow))


MIN_SPOKEN_SEGMENT_S = 0.05


def _storage_key(segment_id) -> str:
    # dub_seg_path's filename sanitizer, case-folded for macOS/Windows volumes.
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(segment_id)).lower()


def segment_id_issues(segment_ids, count: int) -> list[dict]:
    """Rows whose ids share one speech file with an earlier row.

    Each row's speech, preview and recovery copy are stored under its id, so
    two rows whose ids collide (equal, or equal once unsafe characters are
    replaced) would read each other's audio.
    """
    seen: dict[str, int] = {}
    issues = []
    for index in range(count):
        sid = segment_ids[index] if index < len(segment_ids) else f"seg_{index}"
        key = _storage_key(sid)
        if key in seen:
            issues.append({"code": "dub_segment_id_collision", "segment": index, "cue": index + 1,
                           "segment_id": str(sid), "collides_with_cue": seen[key] + 1})
        else:
            seen[key] = index
    return issues


def cue_issues(segments, segment_ids, *, fixed_windows: bool, source_duration: float = 0.0,
               rescue: bool = False) -> list[dict]:
    source_duration = float(source_duration or 0.0)
    if not fixed_windows:
        issues = []
        for index, segment in enumerate(segments):
            if not segment.text.strip():
                continue
            row = {"segment": index, "cue": index + 1,
                   "segment_id": str(segment_ids[index] if index < len(segment_ids) else f"seg_{index}"),
                   "start": segment.start, "end": segment.end}
            if segment.end - segment.start <= MIN_SPOKEN_SEGMENT_S:
                issues.append({**row, "code": "dub_cue_too_short"})
            elif rescue and source_duration > 0 and segment.start >= source_duration:
                # Overflow rescue fits speech inside the source; a cue that
                # starts after it has no playable time to fit into.
                issues.append({**row, "code": "dub_cue_beyond_media", "media_duration": source_duration})
        return issues
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
        elif source_duration > 0 and (end > source_duration + _SOURCE_EPS or start >= source_duration):
            issues.append({**row, "code": "dub_cue_beyond_media", "media_duration": source_duration})
        elif end - start < _MIN_WINDOW_S:
            issues.append({**row, "code": "dub_cue_invalid_window"})
        previous = index
    return issues


def describe_issues(issues: list[dict], *, rescue: bool = False, noun: str = "subtitle", limit: int = 5) -> str:
    """One actionable sentence naming the cues (1-based) to fix."""
    parts = []
    for issue in issues[:limit]:
        if issue["code"] == "dub_cue_overlap":
            parts.append(f"{noun} {issue['cue']} starts before {noun} {issue['overlaps_cue']} ends")
        elif issue["code"] == "dub_cue_too_short":
            parts.append(f"{noun} {issue['cue']} holds words but lasts only "
                         f"{max(0.0, issue['end'] - issue['start']):.3f} s "
                         f"({issue['start']:.3f}-{issue['end']:.3f} s)")
        elif issue["code"] == "dub_cue_beyond_media" and issue["start"] >= issue["media_duration"]:
            parts.append(f"{noun} {issue['cue']} starts at {issue['start']:.2f} s, after the source ends "
                         f"({issue['media_duration']:.2f} s)")
        elif issue["code"] == "dub_cue_beyond_media":
            parts.append(f"{noun} {issue['cue']} ends at {issue['end']:.2f} s, past the end of the source "
                         f"({issue['media_duration']:.2f} s)")
        elif issue["code"] == "dub_segment_id_collision":
            parts.append(f"{noun} {issue['cue']} has the same id as {noun} {issue['collides_with_cue']}")
        else:
            parts.append(f"{noun} {issue['cue']} has no valid time window")
    if len(issues) > limit:
        parts.append(f"{len(issues) - limit} more")
    if all(issue["code"] == "dub_cue_too_short" for issue in issues):
        fix = f"Lengthen each one or merge it with a neighbouring {noun}"
    elif all(issue["code"] == "dub_segment_id_collision" for issue in issues):
        fix = f"Give each {noun} its own id (reload the project if it was edited elsewhere)"
    elif rescue:
        fix = f"Adjust or merge those {noun}s, or turn off automatic overflow fitting"
    else:
        fix = f"Adjust or merge those {noun}s"
    return f"These {noun} times cannot be used: " + "; ".join(parts) + f". {fix}, then generate again."
