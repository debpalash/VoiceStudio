"""Map subtitle cues onto the Smart-Fit timeline (Wave 3.1 / Spec 1).

When a dub uses ``stretch_video`` mode, the video is re-timed per segment so
the dubbed audio fits (see fit_planner + the export stretch filter). The
dubbed audio therefore plays at *fitted* positions, not the original
timestamps. A subtitle file exported with the original times would drift
against the dubbed video — so we regenerate the cue timeline from the same
plan the video stretch uses ("subtitles track actual dub placement", the
last piece of Spec 1).

Pure functions — no I/O — so the remapping is unit-testable. The plan is the
persisted ``video_stretch_plan`` list of
``{orig_start, orig_end, new_start, new_end, stretch_ratio}`` chunks.
"""

from __future__ import annotations


def map_time_to_fitted(t: float, plan: list[dict]) -> float:
    """Map a time on the original timeline to its position on the fitted one.

    Finds the plan chunk whose original span contains ``t`` and interpolates
    linearly into that chunk's fitted span (a chunk's stretch is uniform).
    Before the first chunk maps 1:1; after the last chunk the trailing offset
    is carried at 1:1 (the planner runs gaps/tail at rate 1.0). Empty plan ⇒
    identity.
    """
    if not plan:
        return t
    for chunk in plan:
        o0 = float(chunk.get("orig_start", 0.0))
        o1 = float(chunk.get("orig_end", 0.0))
        n0 = float(chunk.get("new_start", o0))
        n1 = float(chunk.get("new_end", o1))
        if t < o0:
            # In a gap before this chunk — carry the offset at 1:1 from the
            # previous chunk's fitted end (or from 0 for the very first).
            return n0 - (o0 - t)
        if o0 <= t <= o1:
            span = o1 - o0
            if span <= 0:
                return n0
            return n0 + (t - o0) / span * (n1 - n0)
    # Past the last chunk: 1:1 tail from its fitted end.
    last = plan[-1]
    return float(last.get("new_end", 0.0)) + (t - float(last.get("orig_end", 0.0)))


def fitted_cues(segments: list[dict], plan: list[dict]) -> list[tuple[float, float]]:
    """Return ``[(start, end), ...]`` for each segment on the fitted timeline.

    Monotonicity guard: a cue's end is never before its start, and successive
    starts never go backwards (rounding across chunk seams can't produce a
    non-monotone SRT). Legacy plans with overlapping source chunks serialize
    those chunks, so their cues retain the previous-end guard.
    """
    out: list[tuple[float, float]] = []
    sequential = False
    covered_until = float("-inf")
    for chunk in plan:
        if float(chunk.get("orig_start", 0.0)) < covered_until:
            sequential = True
            break
        covered_until = max(covered_until, float(chunk.get("orig_end", 0.0)))
    prev_start = 0.0
    prev_end = 0.0
    for seg in segments:
        s = map_time_to_fitted(float(seg.get("start", 0.0)), plan)
        e = map_time_to_fitted(float(seg.get("end", 0.0)), plan)
        # Shared timelines allow simultaneous speakers; legacy sequential
        # plans must still wait for the previous speaker's end.
        s = max(s, prev_end if sequential else prev_start)
        e = max(e, s)
        out.append((s, e))
        prev_start = s
        prev_end = e
    return out
