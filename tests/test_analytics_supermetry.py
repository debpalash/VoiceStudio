"""Supermetry receives what PostHog receives, under the same three rules.

Consent gates it, the allowlist filters it, and a failure never reaches the
feature. It is posted from the backend so no remote script runs in the app window.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import analytics


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    import sys
    import types

    from core import config, prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OMNIVOICE_ANALYTICS_DISABLED", raising=False)

    class _InertPosthog:
        def __init__(self, *a, **k):
            pass

        def capture(self, *a, **k):
            pass

        def shutdown(self):
            pass

    fake = types.ModuleType("posthog")
    fake.Posthog = _InertPosthog
    monkeypatch.setitem(sys.modules, "posthog", fake)
    analytics.shutdown()
    yield
    analytics.shutdown()


@pytest.fixture
def sent(monkeypatch):
    payloads: list[dict] = []
    monkeypatch.setattr(analytics, "_send_supermetry", payloads.append)
    return payloads


def test_nothing_is_sent_without_consent(sent):
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})
    assert sent == []


def test_the_kill_switch_outranks_consent(sent, monkeypatch):
    analytics.set_opted_in(True)
    sent.clear()
    monkeypatch.setenv("OMNIVOICE_ANALYTICS_DISABLED", "1")
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})
    assert sent == []


def test_withdrawing_consent_stops_sending(sent):
    analytics.set_opted_in(True)
    analytics.set_opted_in(False)
    sent.clear()
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})
    assert sent == []


def test_an_event_names_the_site_and_carries_only_allowlisted_props(sent):
    analytics.set_opted_in(True)
    sent.clear()
    analytics.capture(
        "speech_generated",
        {
            "engine_id": "omnivoice",
            "language": None,
            "text": "my private script about a confidential merger",
            "audio_path": "/Users/someone/voice.wav",
        },
    )
    assert len(sent) == 1
    event = sent[0]
    assert event["site"] == "b6dmmlkehv7t"
    assert event["name"] == "speech_generated"
    assert event["url"] == "https://app.voicestudio.sh/"
    assert event["v"] == 1
    assert event["profile_id"] == analytics.installation_id()
    assert event["props"] == {"engine_id": "omnivoice"}
    blob = json.dumps(event)
    assert "confidential" not in blob
    assert "/Users/" not in blob


def test_posthog_still_gets_the_event_if_supermetry_fails(monkeypatch):
    received = []

    class _Client:
        def capture(self, event, **kwargs):
            received.append(event)

        def shutdown(self):
            pass

    def explode(payload):
        raise RuntimeError("cannot start thread")

    analytics.set_opted_in(True)
    monkeypatch.setattr(analytics, "_get_client", lambda: _Client())
    monkeypatch.setattr(analytics, "_send_supermetry", explode)
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})  # must not raise
    assert received == ["speech_generated"]


def test_the_post_uses_the_event_endpoint_and_a_named_agent(monkeypatch):
    calls = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append((request, timeout))
        return _Response()

    monkeypatch.setattr(analytics.urllib.request, "urlopen", fake_urlopen)
    analytics._post_supermetry({"name": "speech_generated"})

    request, timeout = calls[0]
    assert request.full_url == "https://app.supermetry.com/api/public/v1/events"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"name": "speech_generated"}
    assert request.get_header("User-agent").startswith("VoiceStudio/")
    assert timeout == analytics._SUPERMETRY_TIMEOUT_SECONDS


def test_a_failed_post_never_raises(monkeypatch):
    def fail(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(analytics.urllib.request, "urlopen", fail)
    analytics._post_supermetry({"name": "speech_generated"})


def test_no_remote_analytics_script_can_load_in_the_app_window():
    """The window can reach the Tauri IPC and its DOM holds the user's content."""
    root = Path(__file__).resolve().parents[1] / "frontend"
    conf = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert "supermetry" not in conf["app"]["security"]["csp"]
    assert "supermetry" not in (root / "package.json").read_text(encoding="utf-8")
