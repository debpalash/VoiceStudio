"""Smart Fit planner (dub-length fitting v2, Phase A) — unit + golden tests.

The planner is pure (no I/O, no torch), so these tests pin down the exact
numeric behaviour:

  - threshold boundaries (need = 0.9 / 1.0 / 1.2 / 1.21 / 4.0);
  - cap saturation → overflow_trimmed with the residual reported;
  - slack absorption into the silent gap, minus the gap guard;
  - last-segment tail absorption to the end of the video;
  - timeline-cursor monotonicity (no overlap on the fitted timeline);
  - allow_video_retime=False audio-only mode;
  - video_plan shape — fed straight into dub_export's
    `_build_video_stretch_filter_graph`, which must accept it and emit a
    parsable graph (the Phase B export pipeline consumes exactly this);
  - GOLDEN fixtures: canned scenarios → committed JSON; any drift in the
    algorithm is a deliberate diff to the fixture, never a silent change;
  - `fit_fingerprint` canonicalisation (the #281 regression class: int vs
    float, omitted vs default must hash identically).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict

import pytest

from services.fit_planner import FitParams, FitPlan, MAX_AUDIO_RATE_HARD, plan_fit
from services.incremental import fit_fingerprint


def _single_seg_plan(natural: float, *, slot: float = 1.0, params: FitParams | None = None) -> FitPlan:
    """One segment [0, slot] covering the whole video — no slack, no tail."""
    return plan_fit(
        [{"id": "s0", "start": 0.0, "end": slot}],
        [natural],
        slot,
        params or FitParams(),
    )


# ── Threshold boundaries ───────────────────────────────────────────────


def test_need_below_one_is_slowed_toward_the_slot():
    """Underrun fill: a line shorter than its slot is slowed (pitch-preserving)
    so speech covers the on-screen mouth time instead of leaving a hole of
    thin bed residue (measured live: 8.8s of holes across 18.7s of speech)."""
    p = _single_seg_plan(0.9)
    sf = p.segments[0]
    assert sf.status == "audio_slowed"
    assert sf.audio_rate == pytest.approx(0.9)   # exactly fills the slot
    assert sf.video_ratio == 1.0
    assert sf.overflow_s == 0.0


def test_underrun_fill_is_bounded_by_the_floor():
    """A drastically short line only slows to min_audio_rate — 0.6× speech
    would sound wrong; a smaller hole remains, honestly."""
    p = _single_seg_plan(0.6)
    sf = p.segments[0]
    assert sf.status == "audio_slowed"
    assert sf.audio_rate == pytest.approx(FitParams().min_audio_rate)


def test_near_full_slots_are_left_alone():
    """Within UNDERRUN_TOLERANCE the hole is imperceptible — no ffmpeg pass."""
    p = _single_seg_plan(0.97)
    sf = p.segments[0]
    assert sf.status == "fits"
    assert sf.audio_rate == 1.0


def test_underrun_fill_disabled_via_min_audio_rate():
    p = _single_seg_plan(0.6, params=FitParams(min_audio_rate=1.0))
    sf = p.segments[0]
    assert sf.status == "fits"
    assert sf.audio_rate == 1.0


def test_empty_audio_is_not_slowed():
    p = _single_seg_plan(0.0)
    assert p.segments[0].status == "fits"
    assert p.segments[0].audio_rate == 1.0


def test_need_exactly_one_fits():
    p = _single_seg_plan(1.0)
    sf = p.segments[0]
    assert sf.status == "fits"
    assert sf.audio_rate == 1.0
    assert sf.video_ratio == 1.0


def test_need_at_audio_only_boundary_is_audio_only():
    """need = 1.2 (the max_audio_only_rate default) → audio only, no video."""
    p = _single_seg_plan(1.2)
    sf = p.segments[0]
    assert sf.status == "audio_stretched"
    assert sf.audio_rate == pytest.approx(1.2)
    assert sf.video_ratio == 1.0
    assert sf.overflow_s == 0.0


def test_need_just_past_boundary_goes_hybrid():
    """need = 1.21 → geometric split: sqrt(1.21) = 1.1 on each side."""
    p = _single_seg_plan(1.21)
    sf = p.segments[0]
    assert sf.status == "hybrid"
    assert sf.audio_rate == pytest.approx(1.1)
    assert sf.video_ratio == pytest.approx(1.1)
    assert sf.overflow_s == 0.0
    # Timeline grew by the video ratio.
    assert p.total_duration == pytest.approx(1.1, abs=1e-3)


def test_need_four_saturates_both_caps_and_overflows():
    """need = 4.0: sqrt(4)=2 > audio cap 1.5 → audio=1.5; video=min(4/1.5, 2)=2.
    Combined 3.0× < 4.0× → residual overflow trimmed at mix time."""
    p = _single_seg_plan(4.0)
    sf = p.segments[0]
    assert sf.status == "overflow_trimmed"
    assert sf.audio_rate == pytest.approx(1.5)
    assert sf.video_ratio == pytest.approx(2.0)
    # Stretched audio = 4/1.5 ≈ 2.667 s; new video slot = 1×2 = 2 s.
    assert sf.overflow_s == pytest.approx(4.0 / 1.5 - 2.0, abs=1e-3)


# ── Cap saturation / overflow accounting ──────────────────────────────


def test_custom_caps_are_respected():
    params = FitParams(audio_rate_cap=1.3, video_slow_cap=1.5)
    p = _single_seg_plan(4.0, params=params)
    sf = p.segments[0]
    assert sf.audio_rate == pytest.approx(1.3)
    assert sf.video_ratio == pytest.approx(1.5)
    assert sf.status == "overflow_trimmed"
    assert sf.overflow_s == pytest.approx(4.0 / 1.3 - 1.5, abs=1e-3)


def test_unit_caps_preserve_timing_and_report_residual_overflow():
    params = FitParams(max_audio_only_rate=1.0, audio_rate_cap=1.0, video_slow_cap=1.0)
    p = _single_seg_plan(2.0, params=params)
    sf = p.segments[0]
    assert sf.audio_rate == 1.0
    assert sf.video_ratio == 1.0
    assert sf.status == "overflow_trimmed"
    assert sf.overflow_s == pytest.approx(1.0)
    assert p.total_duration == pytest.approx(1.0)
    assert not p.needs_video_retime


def test_caps_above_defaults_remain_supported():
    p = _single_seg_plan(9.0, params=FitParams(audio_rate_cap=10.0, video_slow_cap=10.0))
    sf = p.segments[0]
    assert sf.audio_rate == pytest.approx(3.0)
    assert sf.video_ratio == pytest.approx(3.0)
    assert sf.status == "hybrid"
    assert sf.overflow_s == 0.0


# ── Slack absorption ───────────────────────────────────────────────────


def test_gap_slack_absorbed_minus_gap_guard():
    """A 1.9 s natural dub in a 1 s slot fits because the following 1 s gap
    is absorbed, leaving only the 50 ms guard before the next onset."""
    p = plan_fit(
        [
            {"id": "a", "start": 0.0, "end": 1.0},
            {"id": "b", "start": 2.0, "end": 3.0},
        ],
        [1.9, 0.5],
        4.0,
    )
    a, b = p.segments
    assert a.effective_end == pytest.approx(1.95)  # 2.0 − gap_guard_s
    assert a.status == "fits"                      # 1.9 / 1.95 < 1.0
    # The unretimed guard sliver keeps b anchored at its original start.
    assert b.new_start == pytest.approx(2.0)


@pytest.mark.parametrize("guard, effective_end", [(0.0, 2.0), (60.0, 1.0)])
def test_gap_guard_boundaries_preserve_the_next_onset(guard, effective_end):
    p = plan_fit(
        [
            {"id": "a", "start": 0.0, "end": 1.0},
            {"id": "b", "start": 2.0, "end": 3.0},
        ],
        [1.0, 1.0],
        3.0,
        FitParams(gap_guard_s=guard),
    )
    assert p.segments[0].effective_end == pytest.approx(effective_end)
    assert p.segments[1].new_start == pytest.approx(2.0)
    assert p.total_duration == pytest.approx(3.0)


def test_back_to_back_segments_do_not_shrink_the_slot():
    """Extend-only: when the next segment starts immediately, the slot stays
    the original [start, end] — it never shrinks below it."""
    p = plan_fit(
        [
            {"id": "a", "start": 0.0, "end": 1.0},
            {"id": "b", "start": 1.0, "end": 2.0},
        ],
        [1.0, 1.0],
        2.0,
    )
    assert p.segments[0].effective_end == pytest.approx(1.0)
    assert p.segments[0].status == "fits"


# ── Last-segment tail ──────────────────────────────────────────────────


def test_last_segment_absorbs_tail_to_video_end():
    p = plan_fit(
        [{"id": "a", "start": 0.0, "end": 1.0}],
        [4.5],
        5.0,
    )
    sf = p.segments[0]
    assert sf.effective_end == pytest.approx(5.0)
    assert sf.status == "audio_slowed"  # 4.5 / 5.0 → filled toward the slot
    assert sf.audio_rate == pytest.approx(0.9)
    assert p.total_duration == pytest.approx(5.0)


def test_unknown_total_duration_gives_last_segment_no_tail():
    p = plan_fit([{"id": "a", "start": 0.0, "end": 1.0}], [1.1], 0.0)
    sf = p.segments[0]
    assert sf.effective_end == pytest.approx(1.0)
    assert sf.status == "audio_stretched"


# ── Cursor monotonicity ────────────────────────────────────────────────


def test_cursor_monotonic_no_overlap_on_fitted_timeline():
    segs = [
        {"id": "a", "start": 0.5, "end": 2.0},
        {"id": "b", "start": 2.3, "end": 4.0},
        {"id": "c", "start": 4.1, "end": 6.0},
        {"id": "d", "start": 7.0, "end": 9.0},
    ]
    naturals = [3.0, 1.0, 4.0, 8.0]  # mix of fits / audio-only / hybrid / overflow
    p = plan_fit(segs, naturals, 10.0)
    prev_end = 0.0
    for sf in p.segments:
        assert sf.new_start >= prev_end - 1e-6, f"overlap at seg {sf.index}"
        assert sf.new_end >= sf.new_start
        prev_end = sf.new_end
    # Pre-roll preserved at native rate.
    assert p.segments[0].new_start == pytest.approx(0.5)
    # Fitted timeline can only grow (all ratios ≥ 1.0).
    assert p.total_duration >= 10.0 - 1e-6


# ── allow_video_retime=False ───────────────────────────────────────────


def test_audio_only_mode_caps_at_legacy_hard_limit():
    params = FitParams(allow_video_retime=False)
    p = _single_seg_plan(4.0, params=params)
    sf = p.segments[0]
    assert sf.audio_rate == pytest.approx(MAX_AUDIO_RATE_HARD)
    assert sf.video_ratio == 1.0
    assert sf.status == "overflow_trimmed"
    assert sf.overflow_s == pytest.approx(4.0 / MAX_AUDIO_RATE_HARD - 1.0, abs=1e-3)
    # No video retime → timeline doesn't grow.
    assert p.total_duration == pytest.approx(1.0)
    assert not p.needs_video_retime


def test_audio_only_mode_within_hard_limit_has_no_overflow():
    params = FitParams(allow_video_retime=False)
    p = _single_seg_plan(1.6, params=params)
    sf = p.segments[0]
    assert sf.audio_rate == pytest.approx(1.6)
    assert sf.status == "audio_stretched"
    assert sf.overflow_s == 0.0


# ── video_plan shape — consumed by the export filter-graph builder ─────


def test_video_plan_feeds_the_stretch_filter_graph_builder():
    """Planner chunks satisfy the export router's filter-graph contract."""
    from api.routers.dub_export import _build_video_stretch_filter_graph

    p = plan_fit(
        [
            {"id": "a", "start": 1.0, "end": 3.0},
            {"id": "b", "start": 4.0, "end": 6.0},
        ],
        [2.0, 5.0],
        8.0,
    )
    for entry in p.video_plan:
        assert set(entry) == {"orig_start", "orig_end", "new_start", "new_end", "stretch_ratio"}
    graph, label = _build_video_stretch_filter_graph(p.video_plan, orig_dur=p.orig_duration)
    assert label == "[vstretched]"
    assert "split=" in graph and "concat=n=" in graph
    # Every chunk is a well-formed trim+setpts node; the graph parses as
    # `;`-separated filter chains with bracketed labels.
    for part in graph.split(";"):
        assert part.startswith("[")


def test_empty_segment_list_yields_empty_plan():
    """An empty track retains source duration and needs no video filter."""
    from api.routers.dub_export import _build_video_stretch_filter_graph

    p = plan_fit([], [], 10.0)
    assert p.segments == []
    assert p.video_plan == []
    assert p.total_duration == pytest.approx(10.0)
    graph, label = _build_video_stretch_filter_graph(p.video_plan, orig_dur=10.0)
    assert graph == "" and label == "[0:v]"


def test_mismatched_inputs_raise():
    with pytest.raises(ValueError):
        plan_fit([{"id": "a", "start": 0.0, "end": 1.0}], [1.0, 2.0], 3.0)


# ── GOLDEN fixtures ────────────────────────────────────────────────────
#
# Exact serialized FitPlans committed under tests/fixtures/fit_planner/.
# If the algorithm changes, these fail — regenerate the fixture ON PURPOSE
# (and explain the behaviour change in the PR), never loosen the assert.

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "fit_planner")

_GOLDEN_SCENARIOS = {
    "all_fit": dict(
        segments=[
            {"id": "g0", "start": 0.0, "end": 2.0},
            {"id": "g1", "start": 3.0, "end": 5.0},
        ],
        naturals=[1.5, 2.5],
        total=6.0,
        params=FitParams(),
    ),
    "audio_only_and_hybrid": dict(
        segments=[
            {"id": "g0", "start": 0.0, "end": 1.0},
            {"id": "g1", "start": 1.5, "end": 2.5},
            {"id": "g2", "start": 3.0, "end": 4.0},
        ],
        naturals=[1.6, 2.0, 0.4],
        total=5.0,
        params=FitParams(),
    ),
    "overflow_caps": dict(
        segments=[
            {"id": "g0", "start": 0.5, "end": 1.5},
            {"id": "g1", "start": 2.0, "end": 3.0},
        ],
        naturals=[5.0, 1.0],
        total=3.5,
        params=FitParams(),
    ),
    "no_video_retime": dict(
        segments=[
            {"id": "g0", "start": 0.0, "end": 1.0},
            {"id": "g1", "start": 2.0, "end": 3.0},
        ],
        naturals=[2.5, 1.3],
        total=4.0,
        params=FitParams(allow_video_retime=False),
    ),
}


def _plan_payload(p: FitPlan) -> dict:
    return {
        "segments": [asdict(s) for s in p.segments],
        "video_plan": p.video_plan,
        "total_duration": p.total_duration,
        "orig_duration": p.orig_duration,
    }


@pytest.mark.parametrize("name", sorted(_GOLDEN_SCENARIOS))
def test_golden_fit_plan(name):
    sc = _GOLDEN_SCENARIOS[name]
    plan = plan_fit(sc["segments"], sc["naturals"], sc["total"], sc["params"])
    got = _plan_payload(plan)
    with open(os.path.join(_FIXTURE_DIR, f"{name}.json"), encoding="utf-8") as f:
        expected = json.load(f)
    assert got == expected, (
        f"FitPlan for scenario {name!r} drifted from the committed golden "
        f"fixture. If this is an intentional algorithm change, regenerate "
        f"tests/fixtures/fit_planner/{name}.json and call out the change."
    )


# ── fit_fingerprint canonicalisation (#281 regression class) ───────────


def test_fit_fingerprint_empty_equals_fully_spelled_defaults():
    full = {
        "timing_strategy": "smart_fit",
        "max_audio_only_rate": 1.2,
        "audio_rate_cap": 1.5,
        "video_slow_cap": 2.0,
        "gap_guard_s": 0.05,
        "allow_video_retime": True,
    }
    assert fit_fingerprint({}) == fit_fingerprint(full)
    assert fit_fingerprint(None) == fit_fingerprint(full)


def test_fit_fingerprint_int_vs_float():
    """JS sends `video_slow_cap: 2`, pydantic parses `2.0` — same hash."""
    assert fit_fingerprint({"video_slow_cap": 2}) == fit_fingerprint({"video_slow_cap": 2.0})
    assert fit_fingerprint({"audio_rate_cap": 1}) == fit_fingerprint({"audio_rate_cap": 1.0})


def test_fit_fingerprint_omitted_vs_none_vs_default():
    assert fit_fingerprint({"audio_rate_cap": None}) == fit_fingerprint({})
    assert fit_fingerprint({"audio_rate_cap": 1.5}) == fit_fingerprint({})


def test_fit_fingerprint_real_changes_flip_the_hash():
    base = fit_fingerprint({})
    assert fit_fingerprint({"audio_rate_cap": 1.4}) != base
    assert fit_fingerprint({"allow_video_retime": False}) != base
    assert fit_fingerprint({"timing_strategy": "strict_slot"}) != base
    assert fit_fingerprint({"gap_guard_s": 0.1}) != base


def test_fit_fingerprint_is_stable():
    """Pin the digest of the default config: existing fit_fp values stored in
    omnivoice_data/ jobs must stay valid across releases (same guarantee
    segment hashes have)."""
    import hashlib

    payload = {
        "allow_video_retime": True,
        "audio_rate_cap": 1.5,
        "gap_guard_s": 0.05,
        "max_audio_only_rate": 1.2,
        "timing_strategy": "smart_fit",
        "video_slow_cap": 2.0,
    }
    expected = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    assert fit_fingerprint({}) == expected


def test_fit_fingerprint_min_audio_rate_only_counts_when_not_default():
    # Tracks fitted before the knob existed must not look stale after upgrade.
    assert fit_fingerprint({"min_audio_rate": 0.85}) == fit_fingerprint({})
    assert fit_fingerprint({"min_audio_rate": None}) == fit_fingerprint({})
    assert fit_fingerprint({"min_audio_rate": 0.6}) != fit_fingerprint({})
    assert fit_fingerprint({"min_audio_rate": 0.6}) == fit_fingerprint({"min_audio_rate": 0.60})
