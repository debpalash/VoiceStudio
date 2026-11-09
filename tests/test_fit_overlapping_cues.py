"""Overlapping speakers share one source-video timeline, not concatenated copies."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest


def _plan(rows, durations, total, **kwargs):
    """Build a plan with underrun fill off to isolate timeline placement."""
    from services.fit_planner import FitParams, plan_fit

    return plan_fit(
        [dict(id=str(i), start=a, end=b) for i, (a, b) in enumerate(rows)],
        durations, total, FitParams(min_audio_rate=1.0, **kwargs),
    )


@pytest.mark.parametrize("retime", [False, True])
def test_overlapping_normal_rate_speech_keeps_source_times(retime):
    """Two overlapping two-second lines occupy three seconds, not four."""
    plan = _plan([(0, 2), (1, 3)], [2, 2], 3, allow_video_retime=retime)
    assert plan.total_duration == 3
    assert [(s.new_start, s.new_end) for s in plan.segments] == [(0, 2), (1, 3)]
    assert not plan.needs_video_retime


def test_nested_cues_and_preroll_keep_the_original_timeline():
    """A later cue inside an earlier speaker's line must not replay that video."""
    rows = [(1, 5), (2, 3), (2.5, 4), (6, 7)]
    plan = _plan(rows, [4, 1, 1.5, 1], 8)
    assert plan.total_duration == 8
    assert [s.new_start for s in plan.segments] == [1, 2, 2.5, 6]


def test_shared_video_uses_largest_requested_slowdown_once():
    """A shared source interval cannot be rendered twice at different rates."""
    from services.fitted_subtitles import map_time_to_fitted
    from services.video_retime import expand_retime_chunks

    plan = _plan([(0, 2), (1, 3)], [4, 2], 3)
    assert plan.total_duration == pytest.approx(2 * 2 ** .5 + 1, abs=1e-4)
    chunks = expand_retime_chunks(plan.video_plan, 3)
    assert sum(b - a for a, b, _ in chunks) == pytest.approx(3)
    assert all(left[1] <= right[0] for left, right in zip(chunks, chunks[1:]))
    for segment in plan.segments:
        assert segment.new_start == pytest.approx(
            map_time_to_fitted(segment.orig_start, plan.video_plan), abs=1e-4,
        )
        assert segment.new_end == pytest.approx(
            map_time_to_fitted(segment.effective_end, plan.video_plan), abs=1e-4,
        )
        assert segment.overflow_s == 0


def test_simultaneous_speakers_use_one_shared_video_chunk():
    """The strongest requested slowdown wins without multiplying the duration."""
    plan = _plan([(0, 2), (0, 2)], [4, 6], 2)
    assert plan.total_duration == 4
    assert [s.new_start for s in plan.segments] == [0, 0]
    assert all(s.video_ratio == 2 for s in plan.segments)
    assert all(s.overflow_s == 0 for s in plan.segments)


def test_overlapping_subtitles_keep_the_speakers_onsets():
    """Chronological subtitle starts do not require nonoverlapping cue ends."""
    from services.fitted_subtitles import fitted_cues

    rows = [(0, 2), (1, 3)]
    plan = _plan(rows, [2, 2], 3)
    assert fitted_cues(
        [dict(start=a, end=b) for a, b in rows], plan.video_plan,
    ) == [(0, 2), (1, 3)]


def test_nested_slowdown_expires_before_the_outer_speaker_ends():
    """A short, demanding speaker slows only the shared part of a longer line."""
    from services.video_retime import expand_retime_chunks

    plan = _plan([(1, 5), (2, 3)], [4, 3], 0)
    assert expand_retime_chunks(plan.video_plan, 5) == [
        (0, 1, 1), (1, 2, 1), (2, 3, 2), (3, 5, 1),
    ]
    assert [(s.new_start, s.new_end) for s in plan.segments] == [(1, 6), (2, 4)]
    assert plan.total_duration == 6


def test_shared_slowdown_retains_unabsorbed_overflow():
    """Sharing video does not hide speech that still exceeds the hard caps."""
    plan = _plan([(0, 2), (0, 2)], [8, 2], 2)
    assert plan.segments[0].status == "overflow_trimmed"
    assert plan.segments[0].overflow_s == pytest.approx(8 / 1.5 - 4, abs=1e-4)
    assert plan.segments[1].overflow_s == 0


def test_subtitle_start_guard_still_handles_out_of_order_input():
    """Allowing overlaps must not reintroduce backwards or negative cues."""
    from services.fitted_subtitles import fitted_cues

    assert fitted_cues([
        dict(start=2, end=4), dict(start=1, end=0),
    ], []) == [(2, 4), (2, 2)]


@pytest.mark.parametrize("durations", [[2, 2], [4, 2]])
def test_overlapping_plan_renders_matching_video_duration(tmp_path, durations):
    """Exercise the export graph with a real synthetic video and ffprobe."""
    from services.video_retime import expand_retime_chunks, build_chunk_filter_graph

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe required")
    plan = _plan([(0, 2), (1, 3)], durations, 3)
    chunks = expand_retime_chunks(plan.video_plan, 3)
    assert sum(b - a for a, b, _ in chunks) == pytest.approx(3)
    graph, label = build_chunk_filter_graph(chunks, "[0:v]", out_fps="30")
    output = tmp_path / "fitted.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=3",
        "-filter_complex", graph, "-map", label, "-an", "-c:v", "libx264",
        str(output),
    ], check=True, capture_output=True, timeout=30)
    result = subprocess.run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(output),
    ], check=True, capture_output=True, text=True, timeout=10)
    measured = float(json.loads(result.stdout)["format"]["duration"])
    assert measured == pytest.approx(plan.total_duration, abs=.08)
