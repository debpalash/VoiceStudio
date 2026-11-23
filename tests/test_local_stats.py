"""Local-only usage insights — the privacy-preserving alternative to cloud analytics.

PostHog was proposed and rejected (PR #1110) because a third-party telemetry
endpoint breaks the product's headline promise ("nothing leaves your machine").
This feature answers the same question from data the app has *already* written
locally. These tests pin the two properties that make that true:

  1. it aggregates correctly from the user's own DB, and
  2. it NEVER returns content — no take text, no paths, no identifiers.

(2) is the load-bearing one: it's what stops this from quietly becoming telemetry.
"""
from __future__ import annotations

import os
import sqlite3
import time

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from core.db import _BASE_SCHEMA
from services import local_stats


SECRET_TEXT = "my private script about a confidential merger"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway DB with a couple of takes, wired into local_stats' db_conn."""
    path = tmp_path / "stats.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_BASE_SCHEMA)
    now = time.time()
    conn.executemany(
        "INSERT INTO generation_history "
        "(id, text, mode, language, audio_path, duration_seconds, generation_time, starred, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("t1", SECRET_TEXT, "clone", "en", "/Users/someone/secret.wav", 10.0, 2.0, 1, now),
            ("t2", SECRET_TEXT, "clone", "en", "/Users/someone/secret2.wav", 5.0, 1.0, 0, now),
            ("t3", SECRET_TEXT, "design", "fr", "/Users/someone/secret3.wav", 2.5, 0.5, 0, now),
        ],
    )
    conn.execute(
        "INSERT INTO voice_profiles (id, name, created_at) VALUES ('v1','Narrator',?)", (now,)
    )
    conn.commit()
    conn.close()

    def _connect():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return c

    # local_stats uses core.db.db_conn, which calls core.db.get_db — patch at
    # that seam (same idiom the takes suite uses against the module-purge leak).
    monkeypatch.setitem(local_stats.db_conn.__wrapped__.__globals__, "get_db", _connect)
    return path


def test_aggregates_the_users_own_history(db):
    s = local_stats.usage_summary()

    assert s["takes"] == 3
    assert s["starred"] == 1
    assert s["audio_seconds"] == 17.5           # 10 + 5 + 2.5
    assert s["compute_seconds"] == 3.5          # 2 + 1 + 0.5
    assert s["voices"] == 1
    assert s["active_days"] == 1
    # Distributions, biggest first.
    assert s["by_mode"][0] == {"name": "clone", "count": 2}
    assert {"name": "design", "count": 1} in s["by_mode"]
    assert {"name": "en", "count": 2} in s["by_language"]


def test_never_returns_content_paths_or_identifiers(db):
    """The property that keeps this from becoming telemetry: aggregates only."""
    s = local_stats.usage_summary()
    blob = repr(s)

    assert SECRET_TEXT not in blob          # never the text of a take
    assert "secret.wav" not in blob         # never a file path
    assert "/Users/" not in blob            # never a home dir / username
    assert "t1" not in s.get("by_mode", [])  # never a row id
    # And it says so on the tin, so any future consumer sees the guarantee.
    assert s["local_only"] is True


def test_empty_install_returns_zeros_not_an_error(tmp_path, monkeypatch):
    """A fresh install has no takes — the panel must render, not 500."""
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_BASE_SCHEMA)
    conn.commit()
    conn.close()

    def _connect():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setitem(local_stats.db_conn.__wrapped__.__globals__, "get_db", _connect)

    s = local_stats.usage_summary()
    assert s["takes"] == 0
    assert s["audio_seconds"] == 0.0
    assert s["by_mode"] == []
    assert s["first_at"] is None


def test_a_missing_table_degrades_to_zero_rather_than_raising(tmp_path, monkeypatch):
    """An older DB predating a table must not break the panel."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    # Only generation_history — no voice_profiles/dub_history/etc.
    conn.execute(
        "CREATE TABLE generation_history (id TEXT, text TEXT, mode TEXT, language TEXT, "
        "audio_path TEXT, duration_seconds REAL, generation_time REAL, starred INTEGER, created_at REAL)"
    )
    conn.execute(
        "INSERT INTO generation_history VALUES ('t1','x','clone','en','/p',3.0,1.0,0,?)",
        (time.time(),),
    )
    conn.commit()
    conn.close()

    def _connect():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setitem(local_stats.db_conn.__wrapped__.__globals__, "get_db", _connect)

    s = local_stats.usage_summary()
    assert s["takes"] == 1
    assert s["voices"] == 0   # missing table → 0, not a crash
    assert s["dubs"] == 0


def test_counts_legacy_missing_language_values_in_one_bucket(db):
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE generation_history SET language = NULL WHERE id = 't1'")
        conn.execute("UPDATE generation_history SET language = '  ' WHERE id = 't2'")
        conn.execute("UPDATE generation_history SET language = 'unknown' WHERE id = 't3'")
    assert local_stats.usage_summary()["by_language"] == [{"name": "unknown", "count": 3}]
