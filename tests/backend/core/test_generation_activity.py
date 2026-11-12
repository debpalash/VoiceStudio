"""In-flight /generate stages for the status poll."""
from __future__ import annotations

from core import generation_activity as activity


def test_snapshot_empty_until_begin():
    activity.end()
    assert activity.snapshot() is None


def test_stages_and_elapsed_are_reported():
    activity.begin()
    try:
        snap = activity.snapshot()
        assert snap is not None
        assert snap["stage"] == "preparing"
        assert snap["elapsed_s"] >= 0
        activity.set_stage("transcribing_reference")
        assert activity.snapshot()["stage"] == "transcribing_reference"
        activity.set_stage("ranking_reference")
        assert activity.snapshot()["stage"] == "ranking_reference"
        activity.set_stage("synthesizing")
        assert activity.snapshot()["stage"] == "synthesizing"
    finally:
        activity.end()
    assert activity.snapshot() is None


def test_unknown_stage_is_ignored():
    activity.begin()
    try:
        activity.set_stage("not-a-real-stage")
        assert activity.snapshot()["stage"] == "preparing"
    finally:
        activity.end()
