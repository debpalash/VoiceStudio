"""Smart Fit overrides must be valid before a dub can enter synthesis."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.requests import DubRequest, FitOptions


_UNBOUNDED_FIELDS = (
    "max_audio_only_rate", "audio_rate_cap", "video_slow_cap", "gap_guard_s",
)
# min_audio_rate is the slowest-speed floor: bounded to 0.5-1.0 by the schema.
_NUMERIC_FIELDS = (*_UNBOUNDED_FIELDS, "min_audio_rate")


@pytest.mark.parametrize("field", _NUMERIC_FIELDS)
@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-Infinity",
])
def test_fit_options_reject_non_finite_values(field, value):
    with pytest.raises(ValidationError) as exc:
        FitOptions(**{field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", _NUMERIC_FIELDS[:3])
@pytest.mark.parametrize("value", [-1.0, 0.0, 0.99])
def test_fit_options_reject_rates_below_one(field, value):
    with pytest.raises(ValidationError):
        FitOptions(**{field: value})


def test_fit_options_reject_negative_gap_guard():
    with pytest.raises(ValidationError):
        FitOptions(gap_guard_s=-0.01)


@pytest.mark.parametrize("options", [{}, dict.fromkeys((*_NUMERIC_FIELDS, "allow_video_retime"))])
def test_fit_options_leave_default_resolution_to_the_planner(options):
    assert FitOptions(**options).model_dump() == dict.fromkeys(
        (*_NUMERIC_FIELDS, "allow_video_retime"),
    )


@pytest.mark.parametrize("extra", [{}, {"fit_options": None}])
def test_dub_request_preserves_absent_fit_options(extra):
    request = DubRequest(segments=[{"start": 0.0, "end": 1.0, "text": "test"}], **extra)
    assert request.fit_options is None


def test_fit_options_preserve_boundary_values_and_false():
    values = {
        "max_audio_only_rate": 1.0,
        "audio_rate_cap": 1.0,
        "video_slow_cap": 1.0,
        "gap_guard_s": 0.0,
        "allow_video_retime": False,
    }
    assert FitOptions(**values).model_dump() == {**values, "min_audio_rate": None}
    assert FitOptions(min_audio_rate=0.5).min_audio_rate == 0.5
    assert FitOptions(min_audio_rate=1.0).min_audio_rate == 1.0


@pytest.mark.parametrize("value", [0.0, 0.49, 1.01, 2.0])
def test_fit_options_reject_slowest_speed_floor_out_of_range(value):
    with pytest.raises(ValidationError):
        FitOptions(min_audio_rate=value)


@pytest.mark.parametrize("field", _UNBOUNDED_FIELDS)
def test_fit_options_do_not_impose_an_arbitrary_upper_bound(field):
    assert getattr(FitOptions(**{field: 1e100}), field) == 1e100
