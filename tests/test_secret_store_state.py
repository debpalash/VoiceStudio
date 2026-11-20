"""An unreadable credential store must not read as "no credential configured".

`list_secret_names` deliberately collapses "no store yet" and "the store could not
be read" into ``[]``, which is right for a caller that only renders a list. The
Twilio enable gate is not such a caller: on a locked, corrupt or unreadable SQLite
file it refused to enable the integration and told the user to add an Auth Token
that was already stored correctly -- and the outbound path said the same.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from services import settings_store  # noqa: E402
from services.telephony import config as telephony_config  # noqa: E402


def _broken_store(monkeypatch):
    """Make every settings-store read fail the way a locked DB does."""

    class _Conn:
        def __enter__(self):
            raise sqlite3.OperationalError("database is locked")

        def __exit__(self, *_exc):
            return False

    import core.db as core_db

    monkeypatch.setattr(core_db, "db_conn", lambda: _Conn())


def test_an_unreadable_store_reports_not_readable(monkeypatch):
    _broken_store(monkeypatch)
    readable, names = settings_store.secret_names_state()
    assert readable is False
    assert names == []


def test_the_lenient_helper_still_returns_an_empty_list(monkeypatch):
    # Display callers keep their degrade, so a settings page still renders.
    _broken_store(monkeypatch)
    assert settings_store.list_secret_names() == []


def test_a_missing_table_counts_as_readable_and_empty(monkeypatch):
    class _Conn:
        def __enter__(self):
            raise sqlite3.OperationalError("no such table: settings")

        def __exit__(self, *_exc):
            return False

    import core.db as core_db

    monkeypatch.setattr(core_db, "db_conn", lambda: _Conn())
    readable, names = settings_store.secret_names_state()
    assert (readable, names) == (True, [])


@pytest.mark.parametrize("state", ["configured", "absent", "unreadable"])
def test_auth_token_state_reflects_the_store(monkeypatch, state):
    names = {"integrations.twilio.auth_token": 1}
    readable = state != "unreadable"
    found = list(names) if state == "configured" else []
    # Both seams, as a real caller sees them.
    monkeypatch.setattr(settings_store, "list_secret_names", lambda: found)
    monkeypatch.setattr(
        settings_store, "secret_names_state", lambda: (readable, found)
    )
    assert telephony_config.auth_token_state() == state
    assert telephony_config.has_auth_token() is (state == "configured")


def test_a_present_token_never_needs_the_strict_read(monkeypatch):
    """The lenient list is enough when the credential is there -- one read, as before."""
    monkeypatch.setattr(
        settings_store, "list_secret_names", lambda: ["integrations.twilio.auth_token"]
    )

    def boom():
        raise AssertionError("the strict read must not run when the token is listed")

    monkeypatch.setattr(settings_store, "secret_names_state", boom)
    assert telephony_config.auth_token_state() == "configured"


def test_the_enable_gate_names_the_unreadable_case(monkeypatch):
    cfg = telephony_config.TwilioConfig(account_sid="AC123", public_base_url="https://x.test")
    monkeypatch.setattr(telephony_config, "load_call_settings", lambda: telephony_config.CallSettings())

    readable_missing = telephony_config.missing_for_enable(cfg, False, token_state="absent")
    assert "auth_token" in readable_missing

    unreadable = telephony_config.missing_for_enable(cfg, False, token_state="unreadable")
    assert "auth_token_unreadable" in unreadable
    assert "auth_token" not in unreadable, (
        "an unreadable store must not be reported as a missing token"
    )
