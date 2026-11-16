"""Source issues that must be corrected before rendering spoken media cues."""
from __future__ import annotations


def cue_issues(segments, segment_ids, *, subtitle_only: bool = False) -> list[dict]:
    if subtitle_only:
        return []
    issues = []
    for index, segment in enumerate(segments):
        if segment.text.strip() and segment.end - segment.start <= 0.05:
            issues.append({"code": "dub_cue_too_short", "segment": index,
                           "segment_id": str(segment_ids[index] if index < len(segment_ids) else f"seg_{index}"),
                           "start": segment.start, "end": segment.end})
    return issues
