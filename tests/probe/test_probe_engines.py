"""Engine matrix — TTS/ASR registry verified against the real running backend
(shared boot), plus offline judge unit tests."""

from __future__ import annotations

import os

import pytest

from . import spec as probe_spec
from .judges import engine as E

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "engines.probe.yaml")


def _judge_engine_matrix(capture):
    """Evaluate a capture without recording synthetic cases in the live report."""
    spec = probe_spec.load_spec(_SPEC)
    ctx = {"engines_tts": capture["engines_tts"], "engines_asr": capture["engines_asr"]}
    return spec, probe_spec.run_judges(spec, ctx)


def test_engine_matrix(probe_report, boot_capture):
    spec, results = _judge_engine_matrix(boot_capture)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    # Real-world sanity: the shipped defaults are available out of the box.
    assert E.active_engine_available(boot_capture["engines_tts"]).passed is True
    assert E.active_engine_available(boot_capture["engines_asr"]).passed is True


@pytest.mark.parametrize("active,available", [
    ("whisperx", True),
    ("pytorch-whisper", True),
    ("pytorch-whisper", False),
    ("missing", False),
])
def test_engine_matrix_respects_selected_asr(active, available):
    capture = {
        "engines_tts": {
            "active": "tts",
            "backends": [{"id": "tts", "available": True}],
        },
        "engines_asr": {
            "active": active,
            "backends": [
                {"id": "whisperx", "available": active == "whisperx", "reason": "gated"},
                {"id": "pytorch-whisper", "available": available, "reason": "unavailable"},
            ],
        },
    }
    _, results = _judge_engine_matrix(capture)
    failures = probe_spec.blocking_failures(results)
    if available:
        assert failures == [], "\n".join(str(r) for r in results)
    else:
        assert len(failures) == 1
        assert failures[0].name == "active_engine_available"
        assert failures[0].measured == active


def test_unavailable_engines_explained_synthetic():
    payload = {"active": "a", "backends": [
        {"id": "a", "available": True},
        {"id": "b", "available": False, "reason": "pip install b"},
    ]}
    assert E.unavailable_engines_explained(payload).passed is True
    silent = {"backends": [{"id": "c", "available": False, "reason": ""}]}
    assert E.unavailable_engines_explained(silent).passed is False


def test_active_engine_unavailable_fails():
    payload = {"active": "ghost", "backends": [{"id": "real", "available": True}]}
    assert E.active_engine_available(payload).passed is False
