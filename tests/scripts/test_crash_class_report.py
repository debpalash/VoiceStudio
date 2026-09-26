"""Bucketing contract for scripts/crash_class_report.py — the reliability
cycle's recurrence metric.

Pins two things:
- title → sub-class mapping matches the auto-reporter's real title shapes
  (seeded from error messages, so titles are machine-stable), and
- body → build bucket mapping matches the exact `**Build status:**` lines
  the bug reporter stamps (electron/src/shared/utils/bugReport.js) — a drift in
  either side silently zeroes the metric.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "crash_class_report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("crash_class_report", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crash_class_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # The real title shapes from the historical corpus.
        ("[Bug] Can't reach the local VoiceStudio backend. It has not answered", "unreachable"),
        ("[Bug] Can't reach the local OmniVoice backend — it may still be starti", "unreachable"),
        ("[Bug] It was answering 6 minutes ago and then stopped responding", "unreachable"),
        ("[Crash] Backend died (exit code 1)", "backend-died"),
        ("[Crash] Backend ended uncleanly (previous run)", "backend-died"),
        ("[Backend] Backend failed to start", "never-started"),
        # Non-class titles must not count toward the metric.
        ("[Bug] Synthesize produced silence in Design mode", None),
        ("[Feature] Add a voice changer", None),
    ],
)
def test_title_bucketing(report, title, expected):
    sub = next((k for k, rx in report.CRASH_CLASS.items() if rx.search(title)), None)
    assert sub == expected


def test_build_status_lines_match_the_reporter_stamps(report):
    # These literals must stay in lockstep with electron/src/shared/utils/bugReport.js
    # captureContext() — the stamps are the metric's only version signal.
    assert report.OUTDATED.search("**Build status:** OUTDATED — `v0.5.0` was already out when this was filed")
    assert report.CURRENT.search("**Build status:** current at filing time (latest `v0.5.0`)")
    assert not report.OUTDATED.search("**Build status:** current at filing time (latest `v0.5.0`)")
    assert report.VERSION_LINE.search("**Version:** `0.5.1-3`").group(1) == "0.5.1-3"


@pytest.mark.parametrize(
    ("body", "version", "expected"),
    [
        # Stamp-based (no target version).
        ("**Build status:** OUTDATED — `v0.5.0` was already out when this was filed", None, "outdated"),
        ("**Build status:** current at filing time (latest `v0.5.0`)", None, "current"),
        ("no stamp at all", None, "unknown"),
        # With --version the Version line is authoritative: a report stamped
        # "current" during ANOTHER version's window must not count here.
        ("**Version:** `0.4.2`\n**Build status:** current at filing time (latest `v0.4.2`)", "0.5.0", "outdated"),
        ("**Version:** `0.5.0`\n**Build status:** current at filing time (latest `v0.5.0`)", "0.5.0", "current"),
        ("**Version:** `0.5.1-3`", "0.5.1", "current"),  # preview stamps match by base
        ("no version line", "0.5.0", "unknown"),
    ],
)
def test_classify_build(report, body, version, expected):
    assert report.classify_build(body, version) == expected


def test_reporter_source_still_emits_the_stamps(report):
    # Fail here (not in production silence) if bugReport.js rewords the marker.
    src = (SCRIPT_PATH.parents[1] / "electron/src/shared/utils/bugReport.js").read_text(encoding="utf-8")
    assert "**Build status:** OUTDATED" in src
    assert "**Build status:** current at filing time" in src
