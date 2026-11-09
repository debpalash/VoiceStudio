"""Fitted-timeline subtitle remapping (Wave 3.1 / Spec 1) — pure, no I/O."""

import pytest

from services.fitted_subtitles import fitted_cues, map_time_to_fitted

# A 2-chunk plan: chunk 0 [0,4]→[0,6] (1.5× slow), chunk 1 [4,8]→[6,10] (1× — gap).
PLAN = [
    {"orig_start": 0.0, "orig_end": 4.0, "new_start": 0.0, "new_end": 6.0, "stretch_ratio": 1.5},
    {"orig_start": 4.0, "orig_end": 8.0, "new_start": 6.0, "new_end": 10.0, "stretch_ratio": 1.0},
]


def test_empty_plan_is_identity():
    assert map_time_to_fitted(3.7, []) == 3.7


def test_chunk_start_and_end_map_to_fitted_bounds():
    assert map_time_to_fitted(0.0, PLAN) == pytest.approx(0.0)
    assert map_time_to_fitted(4.0, PLAN) == pytest.approx(6.0)
    assert map_time_to_fitted(8.0, PLAN) == pytest.approx(10.0)


def test_midpoint_interpolates_linearly():
    # halfway through chunk 0 (t=2 of [0,4]) → halfway of [0,6] = 3.0
    assert map_time_to_fitted(2.0, PLAN) == pytest.approx(3.0)


def test_time_past_last_chunk_carries_at_unit_rate():
    assert map_time_to_fitted(9.0, PLAN) == pytest.approx(11.0)  # 10 + (9-8)


def test_fitted_cues_uses_new_timeline():
    segs = [{"start": 0.0, "end": 2.0}, {"start": 4.0, "end": 6.0}]
    cues = fitted_cues(segs, PLAN)
    assert cues[0] == (pytest.approx(0.0), pytest.approx(3.0))
    # seg 2: 4.0→6.0, 6.0→8.0
    assert cues[1] == (pytest.approx(6.0), pytest.approx(8.0))


def test_fitted_cues_are_monotone():
    """Shared timeline cues may overlap while their starts remain ordered."""
    # Overlapping/odd inputs must still produce a non-decreasing cue stream.
    segs = [{"start": 3.9, "end": 4.1}, {"start": 4.0, "end": 4.0}]
    cues = fitted_cues(segs, PLAN)
    for s, e in cues:
        assert e >= s
    assert cues[1][0] >= cues[0][0] - 1e-9
    assert cues[1][0] == pytest.approx(6.0)


def test_legacy_sequential_stretch_plan_keeps_caption_after_previous_voice():
    """Legacy overlapping source chunks serialize voices and their captions."""
    plan = [
        dict(orig_start=0, orig_end=2, new_start=0, new_end=2),
        dict(orig_start=1, orig_end=3, new_start=2, new_end=4),
    ]
    assert fitted_cues([dict(start=0, end=2), dict(start=1, end=3)], plan) == [
        (0, 2), (2, 4),
    ]


def test_empty_plan_cues_match_original():
    segs = [{"start": 1.0, "end": 2.5}]
    assert fitted_cues(segs, []) == [(pytest.approx(1.0), pytest.approx(2.5))]
