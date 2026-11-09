"""Opt-in analytics — the three rules, pinned.

OmniVoice is local-first, so an analytics SDK gets held to a higher bar. These
tests exist so the guarantees can't quietly rot:

  1. OFF unless the user says yes (default False; silence is not consent).
  2. NO exception autocapture — raw tracebacks carry home paths and, in this
     codebase, Hugging Face tokens, bypassing core.failure.sanitize(). Only
     coarse classes and stages are ever sent.
  3. Metadata ONLY, enforced by allowlist — so no future caller can leak the text
     of a take, a file path, or a voice name by adding a property.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import analytics


SENT: list[dict] = []


class _FakeTransport:
    """Records what would be sent; the real transport opens a thread and a socket."""

    def __init__(self, destination, should_send):
        self.destination = destination
        self.should_send = should_send

    def capture(self, event, properties, profile_id=None, traits=None, path="/backend"):
        SENT.append({"event": event, "props": properties, "profile_id": profile_id, "path": path})
        return True

    def shutdown(self):
        pass


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Fresh prefs + isolated DATA_DIR + no override + no kill switch, per test,
    with a fake transport so no test opens a socket."""
    from core import config, prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SUPERMETRY_SITE_ID", raising=False)
    monkeypatch.delenv("SUPERMETRY_HOST", raising=False)
    monkeypatch.delenv("OMNIVOICE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("OMNIVOICE_INSTALL_CHANNEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.setattr(analytics, "SupermetryTransport", _FakeTransport)
    SENT.clear()

    analytics.shutdown()
    yield
    analytics.shutdown()


# ── Rule 1: off unless the user says yes ────────────────────────────────────


def test_off_by_default_even_when_the_build_ships_a_destination(monkeypatch):
    """The whole promise: a default install transmits nothing."""
    assert analytics.user_opted_in() is False
    assert analytics.enabled() is False


def test_source_builds_have_a_destination_via_the_in_repo_default(monkeypatch):
    """With no override, the committed public site id is the destination, so
    source builds get the SAME consent flow as installers. Consent is still the
    gate: available ≠ enabled."""
    assert analytics.token_configured() is True  # no override set by _isolate
    assert analytics.enabled() is False          # …but silence is still not consent
    analytics.set_opted_in(True)
    assert analytics.enabled() is True


def test_the_env_site_beats_the_shared_config(monkeypatch):
    """A developer's own site overrides the committed one."""
    assert analytics._destination()["site"] == "oxxxnwyfwhx4"
    monkeypatch.setenv("SUPERMETRY_SITE_ID", "devsite12345")
    assert analytics._destination()["site"] == "devsite12345"


def test_opting_in_without_any_token_still_cannot_transmit(monkeypatch):
    """A build with no destination (the site blanked) — the toggle must not
    pretend otherwise."""
    monkeypatch.setenv("SUPERMETRY_SITE_ID", "")
    analytics.set_opted_in(True)
    assert analytics.user_opted_in() is True
    assert analytics.token_configured() is False
    assert analytics.enabled() is False


def test_enabled_only_when_BOTH_gates_are_true(monkeypatch):
    analytics.set_opted_in(True)
    assert analytics.enabled() is True


def test_kill_switch_outranks_everything(monkeypatch):
    analytics.set_opted_in(True)
    monkeypatch.setenv("OMNIVOICE_ANALYTICS_DISABLED", "1")
    assert analytics.enabled() is False


def test_withdrawing_consent_takes_effect_immediately(monkeypatch):
    analytics.set_opted_in(True)
    assert analytics.enabled() is True
    analytics.set_opted_in(False)
    assert analytics.enabled() is False
    # capture() after opting out must be a no-op, not a queued event.
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})


def test_a_broken_prefs_file_does_not_enable_tracking(monkeypatch):
    from core import prefs

    def boom(*a, **k):
        raise RuntimeError("prefs corrupt")

    monkeypatch.setattr(prefs, "get", boom)
    assert analytics.user_opted_in() is False  # fails CLOSED


# ── First-run consent prompt: asked exactly once, never defaulted ────────────


def test_not_prompted_by_default():
    """A fresh install has never been asked — the UI may show the ask, but
    analytics itself stays OFF (silence is not consent)."""
    assert analytics.user_prompted() is False
    assert analytics.user_opted_in() is False


def test_any_explicit_choice_marks_prompted():
    """Saying YES marks prompted; saying NO marks prompted too — the question
    is asked exactly once, whatever the answer."""
    analytics.set_opted_in(False)
    assert analytics.user_prompted() is True
    assert analytics.user_opted_in() is False  # "no" really means no

    analytics.set_opted_in(True)
    assert analytics.user_prompted() is True
    assert analytics.user_opted_in() is True


def test_prompted_never_enables_anything(monkeypatch):
    """`prompted` is bookkeeping for the ask, not a consent bit."""
    from core import prefs

    prefs.set_("analytics_prompted", True)
    assert analytics.user_opted_in() is False
    assert analytics.enabled() is False


def test_a_broken_prefs_file_reads_as_not_prompted(monkeypatch):
    """Fails OPEN for the question (it may be re-asked) but never for consent."""
    from core import prefs

    def boom(*a, **k):
        raise RuntimeError("prefs corrupt")

    monkeypatch.setattr(prefs, "get", boom)
    assert analytics.user_prompted() is False
    assert analytics.user_opted_in() is False


# ── Rule 3: metadata only, enforced by allowlist ────────────────────────────


def test_allowlist_drops_anything_that_could_carry_user_content():
    dirty = {
        # The things that must NEVER leave:
        "text": "my private script about a confidential merger",
        "audio_path": "/Users/someone/voice.wav",
        "voice_name": "Grandma's voice",
        "profile_name": "Client - Acme Corp",
        "email": "a@b.com",
        "prompt": "secret",
        # The things that may:
        "engine_id": "omnivoice",
        "language": "en",
        "text_length": 120,
        "has_profile": True,
        "duration_seconds": 3.4,
        "error_type": "RuntimeError",
    }
    clean = analytics.sanitize_properties(dirty)

    assert clean == {
        "engine_id": "omnivoice",
        "language": "en",
        "text_length": 120,
        "has_profile": True,
        "duration_seconds": 3.4,
        "error_type": "RuntimeError",
    }
    blob = repr(clean)
    assert "confidential merger" not in blob
    assert "/Users/" not in blob
    assert "Grandma" not in blob


def test_a_long_string_is_refused_even_on_an_allowlisted_key():
    """Belt and braces: free text must not ride in on a legitimate key."""
    clean = analytics.sanitize_properties({"language": "x" * 500, "engine_id": "omnivoice"})
    assert "language" not in clean
    assert clean == {"engine_id": "omnivoice"}


def test_sanitize_handles_none_and_empty():
    assert analytics.sanitize_properties(None) == {}
    assert analytics.sanitize_properties({}) == {}


def test_frontend_allowlist_mirrors_backend():
    """LOCKED: the frontend ALLOWED_PROPS (utils/analytics.ts) and the backend
    _ALLOWED_PROPS must be the SAME set — otherwise one side can send what the
    other promised to drop. Extending either list means extending both, and
    re-answering "could this ever hold something the user typed, recorded, or
    named?" for the new key."""
    import re

    ts_path = os.path.join(
        os.path.dirname(__file__), "..", "electron", "src", "shared", "utils", "analytics.ts"
    )
    with open(ts_path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"ALLOWED_PROPS\s*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    assert m, "ALLOWED_PROPS Set not found in electron/src/shared/utils/analytics.ts"
    frontend = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert frontend == set(analytics._ALLOWED_PROPS)


# ── Rule 2: no exception autocapture ────────────────────────────────────────


def test_events_carry_an_opaque_install_profile_and_only_allowlisted_props():
    """The destination sees a random install_ profile and metadata only: never
    a traceback, a path, or the take's text."""
    analytics.set_opted_in(True)
    SENT.clear()
    analytics.capture("speech_generated", {"engine_id": "omnivoice", "text": "private script"})
    assert len(SENT) == 1
    sent = SENT[0]
    assert sent["props"] == {"engine_id": "omnivoice"}
    assert sent["profile_id"].startswith("install_") and len(sent["profile_id"]) == len("install_") + 32
    assert "private" not in repr(sent)


def test_nothing_is_sent_without_consent():
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})
    assert SENT == []


def test_capture_never_raises_even_if_the_transport_explodes(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network on fire")

    monkeypatch.setattr(analytics, "SupermetryTransport", Boom)
    analytics.set_opted_in(True)  # builds the client eagerly — must not raise
    assert analytics.capture("speech_generated", {"engine_id": "omnivoice"}) is False


def test_ui_events_are_gated_and_pageviews_use_fixed_paths():
    assert analytics.capture_from_ui("pageview", {}, "view:home") is False  # not opted in
    analytics.set_opted_in(True)
    SENT.clear()
    assert analytics.capture_from_ui("pageview", {}, "view:home") is True
    assert SENT[-1]["path"] == "/home" and SENT[-1]["event"] == "pageview"
    for bad in ("view:../../etc", "view:clone/123", "home", "view:settings/secret"):
        assert analytics.capture_from_ui("pageview", {}, bad) is False
    assert analytics.capture_from_ui("identify", {}) is False
    assert analytics.capture_from_ui("bad name!", {}) is False


def test_engine_id_must_look_like_an_engine_identifier():
    """engine_id is for our engine keys only; a path or a typed name is dropped."""
    for bad in ("My Cool Voice", "/home/me/model.bin", "C:\\Users\\me", "a b", "", "x" * 41):
        assert "engine_id" not in analytics.sanitize_properties({"engine_id": bad})
    for good in ("omnivoice", "indextts2", "cosyvoice-3", "faster_whisper", "qwen3.tts"):
        assert analytics.sanitize_properties({"engine_id": good}) == {"engine_id": good}


def test_installation_id_is_random_not_derived_from_the_machine():
    iid = analytics.installation_id()
    assert analytics.installation_id() == iid  # stable across calls
    import socket

    assert socket.gethostname() not in iid
    assert os.environ.get("USER", "nope") not in iid


# ── install_channel: closed set, driven by env markers (#1193) ───────────────


def test_install_channel_resolves_installer_docker_then_source(monkeypatch):
    assert analytics.install_channel() == "source"  # bare source run
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")  # the Docker image's marker
    assert analytics.install_channel() == "docker"
    monkeypatch.setenv("OMNIVOICE_INSTALL_CHANNEL", "installer")  # desktop shell marker
    assert analytics.install_channel() == "installer"
    # A value outside the closed set falls through to the other markers.
    monkeypatch.setenv("OMNIVOICE_INSTALL_CHANNEL", "franken-build")
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE")
    assert analytics.install_channel() == "source"


def test_install_channel_rides_wherever_app_version_does(monkeypatch):
    """Attached via _common_props (the same place app_version is), and
    allowlisted so the sanitizer doesn't strip it."""
    props = analytics._common_props()
    assert props["install_channel"] == "source"
    assert analytics.sanitize_properties(props)["install_channel"] == "source"
