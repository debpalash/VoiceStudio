"""Regression tests for the dub timing-strategy feature.

Covers:
  - DubRequest schema defaults — `timing_strategy="concise"` is the new
    safe default; back-compat `slot_fit="time_stretch"` is still accepted.
  - `_build_video_stretch_filter_graph` — builds a deterministic ffmpeg
    filter_complex graph that splits the source video into per-segment
    chunks, setpts each, and concats. Includes gap and pre/tail handling.
  - `_video_stretch_plan_for` — only returns a plan when the job's
    `timing_strategy == "stretch_video"` AND a plan was persisted for
    that lang_code, otherwise None.

Why these helpers and not the full mix loop: the mix loop lives inside
the `dub_generate` async generator, which needs a loaded TTS model to
exercise end-to-end. The helpers tested here are the new pure logic the
mode introduced — if they hold, the integration is essentially correct
modulo the actual ffmpeg call (which is exercised by the existing dub
smoke tests).
"""
from __future__ import annotations

import pytest

from schemas.requests import DubRequest, DubSegment
from api.routers.dub_export import (
    _build_video_stretch_filter_graph,
    _video_stretch_plan_for,
)


# ── DubRequest schema defaults ────────────────────────────────────────


def _minimal_segs():
    return [DubSegment(start=0.0, end=1.0, text="hi")]


def test_dubrequest_defaults_to_concise():
    req = DubRequest(segments=_minimal_segs())
    assert req.timing_strategy == "concise"
    assert req.overflow_budget_s == 0.0
    # slot_fit default retained for legacy callers that still send it.
    assert req.slot_fit == "time_stretch"


@pytest.mark.parametrize("strategy", ["concise", "stretch_video", "strict_slot"])
def test_dubrequest_accepts_all_three_strategies(strategy):
    req = DubRequest(segments=_minimal_segs(), timing_strategy=strategy)
    assert req.timing_strategy == strategy


def test_dubrequest_rejects_unknown_strategy():
    with pytest.raises(Exception):
        DubRequest(segments=_minimal_segs(), timing_strategy="warp_speed")


# ── _build_video_stretch_filter_graph ─────────────────────────────────


def test_filter_graph_empty_plan_returns_empty_string_and_passthrough_label():
    graph, label = _build_video_stretch_filter_graph(plan=[], orig_dur=10.0)
    assert graph == ""
    assert label == "[0:v]"


def test_filter_graph_single_segment_no_pre_or_tail():
    """If the seg covers the whole video, no extra chunks are emitted."""
    plan = [{
        "orig_start": 0.0, "orig_end": 5.0,
        "new_start": 0.0, "new_end": 6.0,
        "stretch_ratio": 1.2,
    }]
    graph, label = _build_video_stretch_filter_graph(plan, orig_dur=5.0)
    assert label == "[vstretched]"
    # One split node, one trim+setpts node, one concat — concat n=1.
    assert "split=1" in graph
    assert "trim=start=0.0000:end=5.0000" in graph
    assert "setpts=1.200000*PTS" in graph
    assert ("concat=n=1:v=1:a=0[vcat];[vcat]"
            "pad=ceil(iw/2)*2:ceil(ih/2)*2[vstretched]") in graph


def test_filter_graph_pre_roll_gap_and_tail_all_at_native_rate():
    """Pre-roll, inter-segment gap, and tail should each be a 1.0x chunk."""
    plan = [
        {"orig_start": 1.0, "orig_end": 3.0, "new_start": 1.0, "new_end": 3.5, "stretch_ratio": 1.25},
        {"orig_start": 4.0, "orig_end": 6.0, "new_start": 4.5, "new_end": 6.7, "stretch_ratio": 1.10},
    ]
    graph, label = _build_video_stretch_filter_graph(plan, orig_dur=8.0)
    assert label == "[vstretched]"
    # Chunks: [0,1]@1.0 (pre-roll), [1,3]@1.25, [3,4]@1.0 (gap), [4,6]@1.10, [6,8]@1.0 (tail)
    assert "split=5" in graph
    assert ("concat=n=5:v=1:a=0[vcat];[vcat]"
            "pad=ceil(iw/2)*2:ceil(ih/2)*2[vstretched]") in graph
    # Native-rate chunks are emitted with setpts=1.000000 so the graph stays uniform.
    assert graph.count("setpts=1.000000*PTS") == 3
    assert "setpts=1.250000*PTS" in graph
    assert "setpts=1.100000*PTS" in graph


def test_filter_graph_chains_after_subtitle_filter():
    """When `in_label` is supplied, the graph reads from that label instead of
    the raw source — used when subtitles burn into [vsub] first."""
    plan = [{
        "orig_start": 0.0, "orig_end": 2.0,
        "new_start": 0.0, "new_end": 2.5,
        "stretch_ratio": 1.25,
    }]
    graph, label = _build_video_stretch_filter_graph(
        plan, orig_dur=2.0, in_label="[vsub]",
    )
    assert label == "[vstretched]"
    assert graph.startswith("[vsub]split=1")


# ── _video_stretch_plan_for ───────────────────────────────────────────


def test_plan_for_returns_none_when_strategy_is_concise():
    job = {
        "timing_strategy": "concise",
        "video_stretch_plans": {"bn": {"plan": [{"orig_start": 0.0, "orig_end": 1.0,
                                                  "new_start": 0.0, "new_end": 1.0,
                                                  "stretch_ratio": 1.0}]}},
    }
    assert _video_stretch_plan_for(job, "bn") is None


def test_plan_for_returns_none_when_plan_missing_for_lang():
    job = {
        "timing_strategy": "stretch_video",
        "video_stretch_plans": {"de": {"plan": [{"orig_start": 0.0, "orig_end": 1.0,
                                                  "new_start": 0.0, "new_end": 1.0,
                                                  "stretch_ratio": 1.0}]}},
    }
    assert _video_stretch_plan_for(job, "bn") is None


def test_plan_for_returns_entry_when_strategy_matches_and_plan_exists():
    entry = {
        "plan": [{"orig_start": 0.0, "orig_end": 1.0,
                  "new_start": 0.0, "new_end": 1.2,
                  "stretch_ratio": 1.2}],
        "total_duration": 1.2,
        "orig_duration": 1.0,
    }
    job = {"timing_strategy": "stretch_video", "video_stretch_plans": {"bn": entry}}
    got = _video_stretch_plan_for(job, "bn")
    assert got is entry


def test_plan_for_returns_none_when_video_stretch_plans_absent():
    """A job that ran strict_slot then was upgraded won't carry the plans
    dict; the helper must tolerate that and return None."""
    job = {"timing_strategy": "stretch_video"}
    assert _video_stretch_plan_for(job, "bn") is None
