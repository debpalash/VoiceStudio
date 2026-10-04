"""Engine matrix — TTS/ASR registry verified against the real running backend
(shared boot), plus offline judge unit tests."""

from __future__ import annotations

import os

from . import spec as probe_spec
from .judges import engine as E

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "engines.probe.yaml")


def test_engine_matrix(probe_report, boot_capture):
    spec = probe_spec.load_spec(_SPEC)
    ctx = {"engines_tts": boot_capture["engines_tts"], "engines_asr": boot_capture["engines_asr"]}
    results = probe_spec.run_judges(spec, ctx)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    # Real-world sanity: the shipped defaults are available out of the box.
    assert E.active_engine_available(boot_capture["engines_tts"]).passed is True
    assert E.active_engine_available(boot_capture["engines_asr"]).passed is True


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
