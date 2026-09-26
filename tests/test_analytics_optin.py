"""Opt-in analytics — the three rules, pinned.

OmniVoice is local-first, so an analytics SDK gets held to a higher bar. These
tests exist so the guarantees can't quietly rot:

  1. OFF unless the user says yes (default False; silence is not consent).
  2. NO exception autocapture — the SDK's own default would ship raw tracebacks
     carrying home paths and, in this codebase, Hugging Face tokens out of
     exception messages, bypassing core.failure.sanitize() entirely.
  3. Metadata ONLY, enforced by allowlist — so no future caller can leak the text
     of a take, a file path, or a voice name by adding a property.
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import analytics


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Fresh prefs + isolated DATA_DIR + no token + no kill switch, per test.

    Also pre-installs an inert fake `posthog` module: set_opted_in(True) now
    fires the one-shot install ping (lifecycle events), which builds the client
    eagerly — tests must never construct the real SDK (background queue threads
    + network attempts). Tests that inspect client construction install their
    own fake BEFORE opting in."""
    import sys
    import types

    from core import config, prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("OMNIVOICE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("OMNIVOICE_INSTALL_CHANNEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)

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


# ── Rule 1: off unless the user says yes ────────────────────────────────────


def test_off_by_default_even_when_the_build_ships_a_token(monkeypatch):
    """The whole promise: a default install transmits nothing."""
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    assert analytics.user_opted_in() is False
    assert analytics.enabled() is False


def test_source_builds_have_a_destination_via_the_in_repo_default(monkeypatch):
    """#1193: with no env/baked token, the committed publishable key is the
    fallback — so source builds get the SAME consent flow as installers. Consent
    is still the gate: available ≠ enabled."""
    assert analytics.token_configured() is True  # no env token set by _isolate
    assert analytics.enabled() is False          # …but silence is still not consent
    analytics.set_opted_in(True)
    assert analytics.enabled() is True


def test_the_env_token_beats_the_in_repo_default(monkeypatch):
    """Release builds bake a token through the shell env; developers set their
    own. Either must override the committed default."""
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "  phc_env_wins  ")
    assert analytics._resolved_token() == "phc_env_wins"
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN")
    assert analytics._resolved_token() == analytics._PUBLIC_PROJECT_TOKEN


def test_opting_in_without_any_token_still_cannot_transmit(monkeypatch):
    """A build with no destination at all (env absent AND in-repo default
    blanked) — the toggle must not pretend otherwise."""
    monkeypatch.setattr(analytics, "_PUBLIC_PROJECT_TOKEN", "")
    analytics.set_opted_in(True)
    assert analytics.user_opted_in() is True
    assert analytics.token_configured() is False
    assert analytics.enabled() is False


def test_enabled_only_when_BOTH_gates_are_true(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.set_opted_in(True)
    assert analytics.enabled() is True


def test_kill_switch_outranks_everything(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.set_opted_in(True)
    monkeypatch.setenv("OMNIVOICE_ANALYTICS_DISABLED", "1")
    assert analytics.enabled() is False


def test_withdrawing_consent_takes_effect_immediately(monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
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
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
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


def test_client_is_built_with_exception_autocapture_OFF(monkeypatch):
    """The SDK's own default ships raw tracebacks — home paths, and in this
    codebase HF tokens out of exception messages — bypassing the redaction in
    core.failure.sanitize(). It must be explicitly disabled."""
    captured_kwargs = {}

    class FakePosthog:
        def __init__(self, token, **kwargs):
            captured_kwargs.update(kwargs)

        def capture(self, *a, **k):
            pass

        def shutdown(self):
            pass

    import sys
    import types

    fake_mod = types.ModuleType("posthog")
    fake_mod.Posthog = FakePosthog
    # Installed BEFORE opting in: set_opted_in(True) builds the client eagerly
    # (one-shot install ping), and that construction is what's under test.
    monkeypatch.setitem(sys.modules, "posthog", fake_mod)

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.set_opted_in(True)
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})

    assert captured_kwargs.get("enable_exception_autocapture") is False


def test_capture_never_raises_even_if_the_sdk_explodes(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("network on fire")

    import sys
    import types

    fake_mod = types.ModuleType("posthog")
    fake_mod.Posthog = Boom
    monkeypatch.setitem(sys.modules, "posthog", fake_mod)

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.set_opted_in(True)  # builds the client eagerly — must not raise
    analytics.capture("speech_generated", {"engine_id": "omnivoice"})  # must not raise


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
