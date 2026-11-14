"""Finite duration-based ASR budgets shared by the backend and MCP client."""
import math
import os

DEFAULT_TRANSCRIBE_S = 300.0
DEFAULT_REALTIME_FACTOR = 2.0


def _setting(name: str, default: float, *, allow_zero: bool = False) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        return default
    return value


def transcribe_budget_s(duration_s: float | None = None, *, floor_s: float | None = None) -> float:
    """Allow twice the recording's duration, with a configurable fixed floor.

    Zero realtime factor disables scaling. Invalid settings or unavailable
    duration retain the finite floor; no progress event renews this budget.
    """
    floor = _setting("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", DEFAULT_TRANSCRIBE_S)
    if floor_s is not None and math.isfinite(floor_s) and floor_s > 0:
        floor = floor_s
    factor = _setting("OMNIVOICE_ASR_TIMEOUT_REALTIME_FACTOR", DEFAULT_REALTIME_FACTOR, allow_zero=True)
    try:
        scaled = float(duration_s) * factor
    except (TypeError, ValueError, OverflowError):
        return floor
    return max(floor, scaled) if math.isfinite(scaled) and scaled > 0 else floor
