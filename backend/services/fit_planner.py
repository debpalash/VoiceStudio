"""Smart Fit planner — dub-length fitting v2, Phase A.

Pure planning functions for the ``smart_fit`` timing strategy: given the
original segment timeline and the *natural-rate* duration of each dubbed
segment's TTS audio, decide per segment how to reconcile the two by
splitting the burden between a mild pitch-preserving audio speed-up and a
mild per-segment video slow-down.

Clean-room note: this is a reimplementation from a *published description*
of the audio-speedup + video-slowdown fitting approach (see
docs/competitive-analysis.md, "Dub-length fitting"). No GPL source was
consulted.

Algorithm per segment (defaults in :class:`FitParams`):

1. **Slack absorption** — the usable slot extends past the segment's
   original end into the silent gap before the next segment, keeping a
   small ``gap_guard_s`` clear of the next onset (the last segment may run
   to the end of the video). ``need = natural_dur / slot``.
2. ``need <= 1.0`` — fits as-is; nothing to do.
3. ``1.0 < need <= max_audio_only_rate`` — audio-only speed-up at exactly
   ``need`` (imperceptible up to ~1.2×).
4. ``need > max_audio_only_rate`` — geometric 50/50 split:
   ``audio_rate = min(sqrt(need), audio_rate_cap)`` and
   ``video_ratio = min(need / audio_rate, video_slow_cap)``. Whatever the
   caps can't absorb becomes ``overflow_s`` (trimmed at mix time).
5. ``allow_video_retime=False`` — audio-only mode: rate capped at the
   legacy ``MAX_AUDIO_RATE_HARD`` (1.8, matching dub_generate's
   MAX_STRETCH_RATIO guard rail), residual overflows.
6. **Timeline cursor** — mirrors the existing ``stretch_video`` layout
   loop: pre-roll and inter-segment gaps pass through at 1.0×; each
   segment's video chunk ``[start, effective_end]`` occupies
   ``slot * video_ratio`` on the new timeline. Overlapping slots share a
   piecewise timeline using the largest requested slowdown at each source
   interval; their audio onsets retain the mapped overlap.

This module is deliberately I/O-free and torch-free so it can be unit- and
golden-tested without a model, ffmpeg, or an event loop.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

# Hard ceiling for audio-only compression when video retiming is disabled.
# Matches dub_generate.MAX_STRETCH_RATIO — above ~1.8× speech becomes a
# garbled stream no DSP can rescue.
MAX_AUDIO_RATE_HARD = 1.8

# Underrun fill: a dubbed line that finishes well before its slot leaves a
# hole — on screen the mouth keeps moving while the dub has gone quiet, and
# what the listener hears in the hole is the thin under-speech residue of the
# separated background (measured at ~37% of the original's energy), which
# reads as dead air. Translations routinely run shorter than the source
# delivery (measured live: 8.8s of holes across 18.7s of speech), so this is
# the common case, not a corner. Slots filled to within this fraction are
# left alone — a <5% hole is imperceptible and not worth an ffmpeg pass.
UNDERRUN_TOLERANCE = 0.95

_EPS = 1e-9


@dataclass(frozen=True)
class FitParams:
    """Tunable knobs for the Smart Fit planner.

    All defaults are deliberately conservative: ≤1.2× audio-only is
    imperceptible to most listeners; 1.5× audio is the intelligibility
    cap; 2.0× video slow-down is the limit before motion looks syrupy.
    """
    max_audio_only_rate: float = 1.2
    audio_rate_cap: float = 1.5
    video_slow_cap: float = 2.0
    gap_guard_s: float = 0.05
    allow_video_retime: bool = True
    # Underrun fill floor: a segment shorter than its slot is slowed toward it
    # (pitch-preserving), never below this rate — 0.85× stays comfortably
    # natural-sounding. 1.0 disables the fill entirely.
    min_audio_rate: float = 0.85


@dataclass
class SegmentFit:
    """Planner verdict for one segment."""
    index: int
    seg_id: str
    audio_rate: float       # pitch-preserving rate: >1 speeds up (fit), <1 slows down (fill)
    video_ratio: float      # ≥ 1.0 — average video slowdown across this slot
    new_start: float        # placement on the fitted (possibly longer) timeline
    new_end: float          # end of the video chunk on the fitted timeline
    orig_start: float
    orig_end: float
    effective_end: float    # orig_end + absorbed slack (≤ next start − gap guard)
    status: str             # "fits" | "audio_stretched" | "hybrid" | "overflow_trimmed"
    overflow_s: float       # seconds of (stretched) audio that still don't fit


@dataclass
class FitPlan:
    """Full plan for one dub track."""
    segments: list[SegmentFit] = field(default_factory=list)
    # EXACT dict shape consumed by dub_export._build_video_stretch_filter_graph.
    video_plan: list[dict] = field(default_factory=list)
    total_duration: float = 0.0
    orig_duration: float = 0.0
    params: FitParams = field(default_factory=FitParams)

    @property
    def needs_video_retime(self) -> bool:
        return any(s.video_ratio > 1.0 + 1e-6 for s in self.segments)


def _fit_one(need: float, params: FitParams) -> tuple[float, float, str]:
    """Resolve one segment's need ratio into (audio_rate, video_ratio, status)."""
    if need <= 1.0 + _EPS:
        # Underrun fill: slow the audio toward the slot so the dub keeps
        # speaking while the on-screen mouth does. Bounded by min_audio_rate;
        # near-full slots (within UNDERRUN_TOLERANCE) and degenerate needs
        # (empty audio) stay untouched.
        if need > _EPS and need < UNDERRUN_TOLERANCE and params.min_audio_rate < 1.0 - _EPS:
            return max(need, params.min_audio_rate), 1.0, "audio_slowed"
        return 1.0, 1.0, "fits"
    if need <= params.max_audio_only_rate + _EPS:
        return need, 1.0, "audio_stretched"
    if not params.allow_video_retime:
        audio_rate = min(need, MAX_AUDIO_RATE_HARD)
        status = "audio_stretched" if audio_rate >= need - _EPS else "overflow_trimmed"
        return audio_rate, 1.0, status
    # Geometric 50/50 split: equal perceptual burden on audio and video.
    audio_rate = min(math.sqrt(need), params.audio_rate_cap)
    video_ratio = min(need / audio_rate, params.video_slow_cap)
    if audio_rate * video_ratio >= need - _EPS:
        return audio_rate, video_ratio, "hybrid"
    return audio_rate, video_ratio, "overflow_trimmed"


def _shared_timeline(plan: FitPlan, natural_durs_s: list[float]) -> FitPlan:
    """Resolve overlapping slots onto one source-video timeline in O(n log n).

    Each source interval uses the largest slowdown requested by its active
    speakers. This satisfies each slot's video budget without replaying the
    overlap. Audio rates remain unchanged; audio placement and subtitle mapping
    use the same piecewise timeline as video and background-bed export.
    """
    starts: dict[float, list[tuple[float, float, int]]] = {}
    boundaries = set()
    for i, segment in enumerate(plan.segments):
        a, b = segment.orig_start, segment.effective_end
        boundaries.update((a, b))
        if b > a:
            starts.setdefault(a, []).append((-segment.video_ratio, b, i))
    points = sorted(boundaries)
    active: list[tuple[float, float, int]] = []
    cursor = points[0]  # unchanged pre-roll
    mapped = {points[0]: cursor}
    chunks: list[dict] = []
    for a, b in zip(points, points[1:]):
        for entry in starts.get(a, ()):
            heapq.heappush(active, entry)
        while active and active[0][1] <= a:
            heapq.heappop(active)
        ratio = -active[0][0] if active else 1.0
        end = cursor + (b - a) * ratio
        # Merge equal-rate neighbours; subtitle boundaries still retain their
        # mapped positions even when they fall inside one export chunk.
        if chunks and chunks[-1]["stretch_ratio"] == ratio:
            chunks[-1]["orig_end"] = b
            chunks[-1]["new_end"] = round(end, 4)
        else:
            chunks.append(dict(
                orig_start=a, orig_end=b, new_start=round(cursor, 4),
                new_end=round(end, 4), stretch_ratio=ratio,
            ))
        mapped[b] = end
        cursor = end

    for segment, natural in zip(plan.segments, natural_durs_s):
        start = mapped[segment.orig_start]
        end = mapped[segment.effective_end]
        slot = segment.effective_end - segment.orig_start
        segment.new_start = round(start, 4)
        segment.new_end = round(end, 4)
        segment.video_ratio = round((end - start) / slot, 6) if slot > 0 else 1.0
        overflow = max(0.0, max(0.0, float(natural)) / segment.audio_rate - (end - start))
        segment.overflow_s = round(overflow, 4)
        if overflow > 1e-6:
            segment.status = "overflow_trimmed"
        elif segment.video_ratio > 1.0 + 1e-6:
            segment.status = "hybrid"
        elif segment.audio_rate > 1.0 + 1e-6:
            segment.status = "audio_stretched"
        elif segment.audio_rate < 1.0 - 1e-6:
            segment.status = "audio_slowed"
        else:
            segment.status = "fits"
    plan.video_plan = chunks
    plan.total_duration = round(cursor + max(0.0, plan.orig_duration - points[-1]), 4)
    return plan


def plan_fit(
    segments: list[dict],
    natural_durs_s: list[float],
    total_dur_s: float,
    params: FitParams | None = None,
) -> FitPlan:
    """Plan the Smart Fit layout for a dub track.

    ``segments``: original-timeline segments in chronological order, each a
    dict with ``id``, ``start``, ``end`` (seconds). ``natural_durs_s``: the
    natural-rate TTS audio duration for each segment (parallel list).
    ``total_dur_s``: original video duration (0/unknown tolerated — the last
    segment then gets no tail slack).

    Pure function: no I/O, no torch, deterministic.
    """
    params = params or FitParams()
    n = len(segments)
    if len(natural_durs_s) != n:
        raise ValueError(
            f"segments ({n}) and natural_durs_s ({len(natural_durs_s)}) must be parallel"
        )

    plan = FitPlan(params=params, orig_duration=round(float(total_dur_s), 4))
    if n == 0:
        plan.total_duration = round(max(0.0, float(total_dur_s)), 4)
        return plan

    cursor = 0.0
    for i, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])
        natural = max(0.0, float(natural_durs_s[i]))

        # (a) Slack absorption. Extend-only: the slot never shrinks below
        # the original [start, end] even when segments are back-to-back.
        if i + 1 < n:
            next_start = float(segments[i + 1]["start"])
            effective_end = max(end, next_start - params.gap_guard_s)
            # Never bleed past the next segment's onset (overlapping or
            # near-touching source segments).
            effective_end = min(max(effective_end, start), max(next_start, end))
        else:
            effective_end = max(end, float(total_dur_s)) if total_dur_s > 0 else end
        slot = max(effective_end - start, 1e-3)

        need = natural / slot if natural > 0 else 0.0
        audio_rate, video_ratio, status = _fit_one(need, params)

        # (f) Timeline cursor — mirror the stretch_video layout loop:
        # pre-roll and gaps at 1.0×, the segment's video chunk
        # [start, effective_end] occupies slot × video_ratio.
        if i == 0:
            cursor = start  # pre-roll preserved at native rate
        new_start = cursor
        new_end = new_start + slot * video_ratio
        cursor = new_end
        if i + 1 < n:
            # Unretimed sliver between this chunk and the next chunk's
            # start (the gap guard, or more if extend-only clamped).
            cursor += max(0.0, float(segments[i + 1]["start"]) - effective_end)

        # Residual overflow after both knobs: stretched audio length vs the
        # segment's new video slot.
        stretched = natural / audio_rate if audio_rate > 0 else natural
        overflow_s = max(0.0, stretched - slot * video_ratio)
        if overflow_s <= 1e-6:
            overflow_s = 0.0
        elif status != "overflow_trimmed":
            status = "overflow_trimmed"

        plan.segments.append(SegmentFit(
            index=i,
            seg_id=str(seg.get("id", f"seg_{i}")),
            audio_rate=round(audio_rate, 6),
            video_ratio=round(video_ratio, 6),
            new_start=round(new_start, 4),
            new_end=round(new_end, 4),
            orig_start=round(start, 4),
            orig_end=round(end, 4),
            effective_end=round(effective_end, 4),
            status=status,
            overflow_s=round(overflow_s, 4),
        ))
        plan.video_plan.append({
            "orig_start": round(start, 4),
            "orig_end": round(effective_end, 4),
            "new_start": round(new_start, 4),
            "new_end": round(new_end, 4),
            "stretch_ratio": round(video_ratio, 4),
        })

    # Tail (anything after the last segment's effective end) at 1.0×.
    last_eff = float(plan.segments[-1].effective_end)
    cursor += max(0.0, float(total_dur_s) - last_eff)
    plan.total_duration = round(max(cursor, float(total_dur_s)), 4)
    # Non-overlapping projects keep their established layout byte-for-byte.
    # Overlapping speakers must share the same video, not concatenate copies
    # of the source interval and move later speech past its original onset.
    covered_until = float("-inf")
    for segment in plan.segments:
        if segment.orig_start < covered_until - _EPS:
            return _shared_timeline(plan, natural_durs_s)
        covered_until = max(covered_until, segment.effective_end)
    return plan
