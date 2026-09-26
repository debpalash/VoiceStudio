"""Lifecycle analytics events (owner-sanctioned 2026-07-16) — the guarantees, pinned.

install / update / crash / error events ride the SAME rails as everything else
in core.analytics: the dual gate (build token AND explicit consent), the
property allowlist, and never-raises. These tests pin the event-specific
guarantees on top:

  - each event fires ONCE under consent, and never without it;
  - a later opt-in never replays pre-consent history (updates are marker-gated);
  - `app_crashed` has exactly one authoritative source (the backend run
    sentinel) and ships a BUCKETED uptime, never raw seconds;
  - `error_occurred` carries error_class + coarse stage only, deduped by
    fingerprint and hard-capped per session;
  - a smuggled property (message, path, log tail) is dropped by the allowlist;
  - the uninstall-ping info file exists ⇔ analytics is enabled.
"""
from __future__ import annotations

import json
import os
import sys
import types

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core import analytics


@pytest.fixture()
def sent(monkeypatch, tmp_path):
    """Isolated prefs + DATA_DIR + a fake posthog module recording captures."""
    from core import config, prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("POSTHOG_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("OMNIVOICE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("OMNIVOICE_INSTALL_CHANNEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    analytics.shutdown()
    analytics._reset_error_events_for_tests()

    events: list[tuple[str, dict]] = []

    class FakePosthog:
        def __init__(self, token, **kwargs):
            pass

        def capture(self, event, distinct_id=None, properties=None):
            events.append((event, dict(properties or {})))

        def shutdown(self):
            pass

    fake = types.ModuleType("posthog")
    fake.Posthog = FakePosthog
    monkeypatch.setitem(sys.modules, "posthog", fake)
    yield events
    analytics.shutdown()
    analytics._reset_error_events_for_tests()


def _consent(monkeypatch, *, install_already_recorded=True):
    """Turn the dual gate on. By default pre-marks the install event as sent so
    tests about OTHER events aren't polluted by the opt-in's install ping."""
    from core import prefs

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    if install_already_recorded:
        prefs.set_("analytics_install_recorded", True)
    analytics.set_opted_in(True)


def _names(sent):
    return [e for e, _ in sent]


# ── app_installed ────────────────────────────────────────────────────────────


def test_installed_fires_once_and_only_once(sent, monkeypatch):
    _consent(monkeypatch, install_already_recorded=False)
    # set_opted_in itself fires the one-shot install ping…
    assert _names(sent).count("app_installed") == 1
    # …and every later startup is a no-op for it.
    analytics.record_startup_lifecycle(None)
    analytics.record_startup_lifecycle(None)
    assert _names(sent).count("app_installed") == 1
    props = dict(sent[0][1])
    assert set(props) <= {"app_version", "platform", "install_channel"}
    assert props["install_channel"] == "source"  # no shell/docker marker in tests


def test_installed_never_fires_without_consent_but_is_not_swallowed(sent, monkeypatch):
    """The wizard grants consent AFTER the first boot — a pre-consent startup
    must not send anything, and must not burn the install marker either."""
    from core import prefs

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.record_startup_lifecycle(None)
    assert sent == []
    assert not prefs.get("analytics_install_recorded")
    # Consent arrives → the install ping fires now, once.
    analytics.set_opted_in(True)
    assert _names(sent).count("app_installed") == 1


def test_installed_never_fires_without_any_token(sent, monkeypatch):
    # No destination at all: env absent AND the in-repo default (#1193) blanked.
    monkeypatch.setattr(analytics, "_PUBLIC_PROJECT_TOKEN", "")
    analytics.set_opted_in(True)  # consent but no destination
    analytics.record_startup_lifecycle(None)
    assert sent == []


# ── app_updated ──────────────────────────────────────────────────────────────


def test_updated_fires_once_with_from_and_to(sent, monkeypatch):
    from core import prefs

    _consent(monkeypatch)
    prefs.set_("analytics_last_version", "0.0.1-test")
    analytics.record_startup_lifecycle(None)
    updates = [(e, p) for e, p in sent if e == "app_updated"]
    assert len(updates) == 1
    props = updates[0][1]
    assert props["from_version"] == "0.0.1-test"
    assert props["to_version"] == analytics._app_version()
    # Marker advanced → the next startup does not refire.
    analytics.record_startup_lifecycle(None)
    assert _names(sent).count("app_updated") == 1


def test_an_unconsented_update_is_never_replayed_after_opt_in(sent, monkeypatch):
    """Version bookkeeping runs while un-consented so a later yes only ever
    reports what happens AFTER consent — no retroactive history."""
    from core import prefs

    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    prefs.set_("analytics_last_version", "0.0.1-test")
    analytics.record_startup_lifecycle(None)  # not opted in: sends nothing…
    assert sent == []
    assert prefs.get("analytics_last_version") == analytics._app_version()  # …but advances

    prefs.set_("analytics_install_recorded", True)
    analytics.set_opted_in(True)
    analytics.record_startup_lifecycle(None)
    assert "app_updated" not in _names(sent)


def test_install_run_does_not_also_report_an_update(sent, monkeypatch):
    """First ever consented startup: app_installed only — a fresh install has
    nothing to have updated FROM."""
    from core import prefs

    _consent(monkeypatch, install_already_recorded=False)
    sent.clear()
    prefs.delete("analytics_install_recorded")
    prefs.set_("analytics_last_version", "0.0.1-test")
    analytics.record_startup_lifecycle(None)
    assert _names(sent) == ["app_installed"]


# ── app_crashed ──────────────────────────────────────────────────────────────


CRASH_RECORD = {
    "uptime_hint_s": 42.0,
    "version": "0.0.1-test",
    "last_activity": {"kind": "generate", "detail": "engine load"},
    "log_tail": ["Traceback: /Users/someone/secret.wav exploded"],
}


def test_crash_event_fires_once_from_the_sentinel_record(sent, monkeypatch):
    _consent(monkeypatch)
    analytics.record_startup_lifecycle(dict(CRASH_RECORD))
    crashes = [(e, p) for e, p in sent if e == "app_crashed"]
    assert len(crashes) == 1
    props = crashes[0][1]
    assert props["exit_kind"] == "unclean_exit"
    assert props["stage"] == "generate"
    assert props["uptime_bucket"] == "lt_1m"  # bucketed…
    blob = repr(props)
    assert "42" not in blob  # …never the raw seconds
    assert "secret" not in blob and "/Users/" not in blob  # log tail can't ride in


def test_no_crash_record_means_no_crash_event(sent, monkeypatch):
    _consent(monkeypatch)
    analytics.record_startup_lifecycle(None)
    assert "app_crashed" not in _names(sent)


def test_crash_event_never_fires_without_consent(sent, monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.record_startup_lifecycle(dict(CRASH_RECORD))
    assert sent == []


def test_crash_source_is_single_the_frontend_never_emits_one():
    """No-double-count by construction: the backend run sentinel is the ONE
    crash source. The frontend SDK wrapper must never grow its own crash
    event (the shell's marker describes the same death the sentinel reports)."""
    ts = os.path.join(
        os.path.dirname(__file__), "..", "electron", "src", "shared", "utils", "analytics.ts"
    )
    with open(ts, encoding="utf-8") as f:
        src = f.read()
    assert "app_crashed" not in src
    crash_ts = os.path.join(
        os.path.dirname(__file__), "..", "electron", "src", "shared", "utils", "backendCrash.ts"
    )
    with open(crash_ts, encoding="utf-8") as f:
        assert "app_crashed" not in f.read()


def test_uptime_buckets_cover_the_line():
    b = analytics.uptime_bucket
    assert b(None) == "unknown"
    assert b("nope") == "unknown"
    assert b(3) == "lt_10s"
    assert b(59) == "lt_1m"
    assert b(599) == "lt_10m"
    assert b(3599) == "lt_1h"
    assert b(86399) == "lt_1d"
    assert b(1e6) == "ge_1d"


# ── error_occurred ───────────────────────────────────────────────────────────


def test_error_event_dedupes_by_fingerprint(sent, monkeypatch):
    _consent(monkeypatch)
    analytics.record_error_event("GPU_OOM", "fp1", stage="generate")
    analytics.record_error_event("GPU_OOM", "fp1", stage="generate")
    errors = [(e, p) for e, p in sent if e == "error_occurred"]
    assert len(errors) == 1
    assert errors[0][1]["error_class"] == "GPU_OOM"
    assert errors[0][1]["stage"] == "generate"


def test_error_events_hard_cap_per_session(sent, monkeypatch):
    _consent(monkeypatch)
    for i in range(25):
        analytics.record_error_event("UNKNOWN", f"fp{i}", stage="dub")
    assert _names(sent).count("error_occurred") == analytics._ERROR_EVENT_CAP == 10


def test_error_event_never_fires_without_consent(sent, monkeypatch):
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.record_error_event("GPU_OOM", "fp1", stage="generate")
    assert sent == []


def test_error_journal_hook_sends_class_and_stage_only(sent, monkeypatch, tmp_path):
    from core import error_journal

    monkeypatch.setattr(error_journal, "JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    error_journal.clear()
    _consent(monkeypatch)

    error_journal.record(
        RuntimeError("CUDA out of memory while loading /Users/someone/model.bin"),
        route="/api/generate/stream",
        trace="Traceback ... /Users/someone/model.bin",
    )
    errors = [(e, p) for e, p in sent if e == "error_occurred"]
    assert len(errors) == 1
    props = errors[0][1]
    assert props["error_class"] == "GPU_OOM"
    assert props["stage"] == "generate"  # the route HEAD, not the full path
    blob = repr(props)
    assert "model.bin" not in blob and "/Users/" not in blob and "memory" not in blob
    # A repeat of the same failure dedupes (same journal fingerprint).
    error_journal.record(
        RuntimeError("CUDA out of memory while loading /Users/someone/model.bin"),
        route="/api/generate/stream",
    )
    assert _names(sent).count("error_occurred") == 1
    error_journal.clear()


def test_coarse_stage_strips_params_and_ids():
    from core.error_journal import _coarse_stage

    assert _coarse_stage("/api/generate/stream") == "generate"
    assert _coarse_stage("/api/projects/My%20Secret%20Client/rename") == "projects"
    assert _coarse_stage("/health?probe=1") == "health"
    assert _coarse_stage("") == ""
    assert _coarse_stage(None) == ""


# ── allowlist: a smuggled prop cannot ride along ─────────────────────────────


def test_a_smuggled_prop_is_dropped_at_the_capture_boundary(sent, monkeypatch):
    _consent(monkeypatch)
    analytics.capture(
        "app_crashed",
        {
            "exit_kind": "unclean_exit",
            "uptime_bucket": "lt_1m",
            "log_tail": "Traceback with /Users/someone/take.txt",   # NOT allowlisted
            "message": "secret exception text",                     # NOT allowlisted
            "uptime_hint_s": 42.0,                                  # raw uptime: NOT allowlisted
        },
    )
    (event, props), = [x for x in sent if x[0] == "app_crashed"]
    assert set(props) == {"exit_kind", "uptime_bucket"}
    assert "secret" not in repr(props)


# ── the uninstall-ping info file: present ⇔ enabled ─────────────────────────


def test_uninstall_ping_info_written_when_enabled_and_removed_when_not(
    sent, monkeypatch, tmp_path
):
    info = tmp_path / analytics.UNINSTALL_PING_INFO_BASENAME
    # Not consented → no file (and startup keeps it that way).
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc_test")
    analytics.record_startup_lifecycle(None)
    assert not info.exists()

    analytics.set_opted_in(True)
    assert info.exists()
    payload = json.loads(info.read_text())
    assert payload["token"] == "phc_test"
    assert payload["host"] == "https://us.i.posthog.com"
    assert payload["distinct_id"] == analytics.installation_id()
    assert payload["app_version"] == analytics._app_version()
    # Consent withdrawn → the file goes with it.
    analytics.set_opted_in(False)
    assert not info.exists()


def test_uninstall_ping_info_written_for_a_source_build_with_the_default_token(
    sent, monkeypatch, tmp_path
):
    """#1193: a consented source build has a destination (the in-repo default),
    so the uninstall ping must work there too."""
    analytics.set_opted_in(True)  # no env token → in-repo default
    info = tmp_path / analytics.UNINSTALL_PING_INFO_BASENAME
    assert info.exists()
    payload = json.loads(info.read_text())
    assert payload["token"] == analytics._PUBLIC_PROJECT_TOKEN
    assert payload["host"] == "https://us.i.posthog.com"


def test_uninstall_ping_info_never_written_without_any_token(sent, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics, "_PUBLIC_PROJECT_TOKEN", "")  # destination-less build
    analytics.set_opted_in(True)
    assert not (tmp_path / analytics.UNINSTALL_PING_INFO_BASENAME).exists()
