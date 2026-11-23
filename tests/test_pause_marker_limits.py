"""Out-of-range inline pauses should clamp before converting to integer samples."""

import pytest


@pytest.mark.parametrize(("duration", "unit"), [("9" * 400, "ms"), ("9" * 307, "s")], ids=["huge-ms", "huge-seconds"])
def test_pause_overflow_uses_documented_maximum(duration, unit):
    from omnivoice.utils.text import PAUSE_MAX_MS, parse_pause_markers

    assert parse_pause_markers(f"Before[pause {duration}{unit}]After") == [
        ("Before", PAUSE_MAX_MS),
        ("After", 0),
    ]
