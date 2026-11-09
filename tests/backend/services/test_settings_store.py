"""Tests for backend/services/settings_store.py — AUTH-02 round-trip encryption.

Behaviors covered:
- set_hf_token / get_hf_token round-trips the exact same string
- Raw bytes in the SQLite `settings.value` column do NOT contain the plaintext
  token (nor a long chunk of it) and round-trip back via get_hf_token —
  encrypted at rest, not plaintext
- clear_hf_token followed by get_hf_token returns None
- First set generates and stores a per-install salt row
- Alembic upgrade head on a v0.2.7 fixture DB succeeds and adds settings table
  without disturbing existing tables
- Alembic downgrade -1 drops only the settings table
- Concurrent reads from two threads return consistent values
"""
import os
import logging
import sqlite3
import sys
import threading
import time
from contextlib import nullcontext

import pytest


SAMPLE_TOKEN = "hf_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Point core.config.DB_PATH at a tmp DB and force a fresh load of the
    settings_store + _secret_key modules so module-level caches don't leak
    between tests.

    NOTE: we purge the entire `core` and `services` package namespaces from
    sys.modules — popping just `core.config` is not enough because the
    parent `core` package keeps an attribute pointing at the old submodule,
    so `from core.config import DB_PATH` would resolve to the stale value.
    """
    # Make sure core.config picks up the override before any consumer imports it.
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))

    # Drop any cached modules that captured the old DB_PATH.
    for mod in list(sys.modules):
        if mod == "core" or mod.startswith("core."):
            del sys.modules[mod]
        elif mod == "services" or mod.startswith("services."):
            del sys.modules[mod]

    from core import db as _db
    _db.init_db()  # Apply _BASE_SCHEMA (now includes settings table)
    yield tmp_path / "omnivoice.db"


def test_round_trip_token(isolated_db):
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)
    assert settings_store.get_hf_token() == SAMPLE_TOKEN


def test_text_state_preserves_null_as_present_but_empty(isolated_db, monkeypatch):
    from services import settings_store
    from core import db

    class NullRow:
        def execute(self, *_args):
            return self

        @staticmethod
        def fetchone():
            return (None,)

    monkeypatch.setattr(db, "db_conn", lambda: nullcontext(NullRow()))

    assert settings_store.get_text_state("worker.mode") == (True, "")


def test_stored_value_is_encrypted_not_plaintext(isolated_db):
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)

    with sqlite3.connect(str(isolated_db)) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'hf_token'"
        ).fetchone()
    assert row is not None
    raw = row[0]
    # The stored value must not be the plaintext token. Check the full token and
    # a long leading chunk — but NOT a 3-char prefix like "hf_": the value is
    # Fernet urlsafe-base64 (alphabet includes '_'), so a random ciphertext
    # occasionally contains "hf_" by chance, which made that assertion flaky.
    assert SAMPLE_TOKEN not in raw
    assert SAMPLE_TOKEN[:16] not in raw  # not even a leading chunk leaks
    # And it must round-trip — proving it's genuinely encrypted, not just absent.
    assert settings_store.get_hf_token() == SAMPLE_TOKEN


def test_clear_removes_token(isolated_db):
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)
    assert settings_store.get_hf_token() == SAMPLE_TOKEN
    settings_store.clear_hf_token()
    assert settings_store.get_hf_token() is None


def test_first_write_persists_salt(isolated_db):
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)
    with sqlite3.connect(str(isolated_db)) as conn:
        rows = {
            k: v
            for (k, v) in conn.execute("SELECT key, value FROM settings").fetchall()
        }
    assert "hf_token" in rows
    assert "_secret_key_salt" in rows
    assert rows["_secret_key_salt"]  # non-empty


def test_clear_preserves_salt(isolated_db):
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)
    settings_store.clear_hf_token()
    with sqlite3.connect(str(isolated_db)) as conn:
        rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    # Token row is gone, but the per-install salt persists so a future set_hf_token
    # produces ciphertext that the same machine-id derivation can still decrypt.
    assert "hf_token" not in rows
    assert "_secret_key_salt" in rows


def test_get_returns_none_when_unset(isolated_db):
    from services import settings_store
    assert settings_store.get_hf_token() is None


def test_invalid_token_returns_none_with_warning(isolated_db, caplog):
    """If the encrypted blob can't be decrypted (e.g. machine-id changed
    because the user migrated omnivoice_data/ across machines), get_hf_token
    must return None and the resolver falls through to env / HF-CLI naturally."""
    from services import settings_store

    # Hand-inject garbage ciphertext so Fernet raises InvalidToken on decrypt.
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("hf_token", "not-a-valid-fernet-blob", time.time()),
        )
        conn.commit()

    caplog.clear()
    result = settings_store.get_hf_token()
    assert result is None


def test_secret_failure_logs_omit_secret_identifier(isolated_db, caplog):
    from services import settings_store

    private_name = "llm_key.private_provider"
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            (f"secret.{private_name}", "not-a-valid-fernet-blob", time.time()),
        )
        conn.commit()

    caplog.clear()
    assert settings_store.get_secret(private_name) is None
    assert private_name not in caplog.text
    assert "private_provider" not in caplog.text
    assert "Stored encrypted setting failed to decrypt" in caplog.text


def test_get_secret_propagates_unexpected_decryption_failure(isolated_db, monkeypatch):
    from services import settings_store

    private_name = "llm_key.private_provider"
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            (f"secret.{private_name}", "ciphertext", time.time()),
        )
        conn.commit()

    def fail_decryption():
        raise RuntimeError("unexpected decryption failure")

    monkeypatch.setattr(settings_store, "_fernet", fail_decryption)
    with pytest.raises(RuntimeError, match="unexpected decryption failure"):
        settings_store.get_secret(private_name)


def test_get_secret_sqlite_failure_uses_fixed_shape_fallback(
    isolated_db, monkeypatch, caplog
):
    from core import db
    from services import settings_store

    def fail_db_conn():
        raise sqlite3.OperationalError("private database detail")

    monkeypatch.setattr(db, "db_conn", fail_db_conn)
    caplog.clear()
    assert settings_store.get_secret("llm_key.private_provider") is None
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "settings_store.get_secret: SQLite read failed"
    ]
    assert len(records) == 1
    assert records[0].exc_info is None
    assert records[0].exc_text is None
    assert "private database detail" not in caplog.text
    assert "private_provider" not in caplog.text


def test_concurrent_reads_consistent(isolated_db):
    """Two threads reading at the same time get the same value (sqlite WAL on)."""
    from services import settings_store
    settings_store.set_hf_token(SAMPLE_TOKEN)
    results: list[str | None] = []

    def _reader():
        for _ in range(20):
            results.append(settings_store.get_hf_token())

    threads = [threading.Thread(target=_reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == SAMPLE_TOKEN for r in results)
    assert len(results) == 60


# ── Alembic migration tests ──────────────────────────────────────────────

V027_SCHEMA = """
    CREATE TABLE voice_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ref_audio_path TEXT,
        ref_text TEXT DEFAULT '',
        instruct TEXT DEFAULT '',
        language TEXT DEFAULT 'Auto',
        locked_audio_path TEXT DEFAULT '',
        seed INTEGER DEFAULT NULL,
        is_locked INTEGER DEFAULT 0,
        personality TEXT DEFAULT '',
        created_at REAL
    );
    CREATE TABLE generation_history (
        id TEXT PRIMARY KEY,
        text TEXT,
        mode TEXT,
        language TEXT,
        instruct TEXT,
        profile_id TEXT,
        audio_path TEXT,
        duration_seconds REAL,
        generation_time REAL,
        seed INTEGER DEFAULT NULL,
        created_at REAL
    );
    CREATE TABLE dub_history (
        id TEXT PRIMARY KEY,
        filename TEXT,
        duration REAL,
        segments_count INTEGER,
        language TEXT,
        language_code TEXT,
        tracks TEXT DEFAULT '[]',
        job_data TEXT,
        content_hash TEXT DEFAULT '',
        created_at REAL
    );
    CREATE TABLE studio_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        video_path TEXT,
        audio_path TEXT,
        duration REAL,
        state_json TEXT,
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE export_history (
        id TEXT PRIMARY KEY,
        filename TEXT,
        destination_path TEXT,
        mode TEXT,
        created_at REAL
    );
    CREATE TABLE glossary_terms (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        note TEXT DEFAULT '',
        auto INTEGER DEFAULT 0,
        created_at REAL
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        project_id TEXT,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        finished_at REAL,
        error TEXT,
        meta_json TEXT DEFAULT '{}'
    );
    CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        created_at REAL NOT NULL,
        payload TEXT NOT NULL
    );
"""


def _run_alembic(direction: str, db_path: str, target: str = "head"):
    """Programmatic alembic upgrade/downgrade against a specific DB path."""
    from alembic import command
    from alembic.config import Config

    # Find the worktree root by walking up to the directory containing alembic.ini.
    here = os.path.abspath(os.path.dirname(__file__))
    root = here
    while root and root != "/" and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    assert os.path.isfile(os.path.join(root, "alembic.ini")), "alembic.ini not found"
    cfg = Config(os.path.join(root, "alembic.ini"))
    # Override the URL so the migration runs against the fixture DB, not
    # the developer's actual omnivoice_data/.
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    if direction == "upgrade":
        command.upgrade(cfg, target)
    elif direction == "downgrade":
        command.downgrade(cfg, target)
    else:
        raise ValueError(direction)


def test_alembic_upgrade_on_v027_db_preserves_existing_tables(tmp_path, monkeypatch):
    """A user upgrading from v0.2.7 must keep their voice_profiles / dub_history
    intact and gain a new settings table."""
    db = tmp_path / "v027.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(V027_SCHEMA)
        # Seed a row in each pre-Phase-1 table so we can verify no data loss.
        conn.execute(
            "INSERT INTO voice_profiles(id, name) VALUES (?, ?)",
            ("vp-1", "Alice"),
        )
        conn.execute(
            "INSERT INTO dub_history(id, filename, content_hash) "
            "VALUES (?, ?, ?)",
            ("dh-1", "movie.mp4", "abc123"),
        )
        conn.commit()

    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    # Re-import core.config so DB_PATH picks up the new env var, then run
    # the alembic upgrade.
    for mod in ["core.config", "core.db"]:
        if mod in sys.modules:
            del sys.modules[mod]

    _run_alembic("upgrade", str(db))

    with sqlite3.connect(str(db)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "settings" in tables
        # All v0.2.7 tables still there.
        for t in (
            "voice_profiles",
            "generation_history",
            "dub_history",
            "studio_projects",
            "export_history",
            "glossary_terms",
            "jobs",
            "job_events",
        ):
            assert t in tables, f"v0.2.7 table {t!r} lost during migration"
        # Seeded rows survived.
        assert conn.execute(
            "SELECT name FROM voice_profiles WHERE id='vp-1'"
        ).fetchone()[0] == "Alice"
        assert conn.execute(
            "SELECT content_hash FROM dub_history WHERE id='dh-1'"
        ).fetchone()[0] == "abc123"


def test_alembic_downgrade_drops_settings_only(tmp_path, monkeypatch):
    db = tmp_path / "down.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(V027_SCHEMA)
        conn.commit()

    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    for mod in ["core.config", "core.db"]:
        if mod in sys.modules:
            del sys.modules[mod]

    _run_alembic("upgrade", str(db))
    _run_alembic("downgrade", str(db), target="base")

    with sqlite3.connect(str(db)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # settings table gone, everything else still present.
        assert "settings" not in tables
        assert "voice_profiles" in tables
        assert "jobs" in tables


# ── Phase 4 Plan 04-01 (GGUF-04): quant override round-trip + allow-list ──


def test_quant_override_round_trip(isolated_db):
    """A user picks a quant in Settings; after restart (modelled by a
    fresh `get_quant_override()` against the same SQLite path) the
    value still resolves."""
    from services import settings_store

    # Round-trips an allow-listed quant filename.
    settings_store.set_quant_override("omnivoice-base-F32.gguf")
    assert settings_store.get_quant_override() == "omnivoice-base-F32.gguf"


def test_quant_override_clear_with_none(isolated_db):
    from services import settings_store

    settings_store.set_quant_override("omnivoice-base-F32.gguf")
    settings_store.set_quant_override(None)
    assert settings_store.get_quant_override() is None


def test_quant_override_clear_with_auto_sentinel(isolated_db):
    from services import settings_store

    settings_store.set_quant_override("omnivoice-base-Q8_0.gguf")
    settings_store.set_quant_override("auto")
    # "auto" is the explicit clear sentinel; get_quant_override returns
    # None for both "row absent" and "auto" so the caller's downstream
    # auto-select code path runs.
    assert settings_store.get_quant_override() is None


def test_quant_override_rejects_freeform_path(isolated_db):
    """T-04-05 — UI input cannot load an attacker-controlled GGUF path."""
    from services import settings_store

    with pytest.raises(ValueError):
        settings_store.set_quant_override("../etc/passwd")


def test_quant_override_rejects_unknown_filename(isolated_db):
    """Anything not in the quant_map.json allow-list is refused."""
    from services import settings_store

    with pytest.raises(ValueError):
        settings_store.set_quant_override("malicious-base-Q4.gguf")


def test_quant_override_rejects_non_string(isolated_db):
    from services import settings_store

    with pytest.raises(ValueError):
        settings_store.set_quant_override(42)  # type: ignore[arg-type]


def test_reads_before_the_store_exists_are_unset_not_errors(monkeypatch, tmp_path, caplog):
    """Startup restores prefs before init_db; a fresh install has no table yet."""
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod in ("core", "services") or mod.startswith(("core.", "services.")):
            del sys.modules[mod]
    from core import prefs
    from services import settings_store

    caplog.set_level(logging.DEBUG)
    caplog.clear()
    assert settings_store.get_hf_token() is None
    assert settings_store.get_secret("llm_key.openai") is None
    assert settings_store.list_secret_names() == []
    assert settings_store.get_text("perf.example", default="d") == "d"
    assert settings_store.get_license_accepted("supertonic3") is False
    prefs.restore_env({})
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_missing_other_table_is_still_reported(isolated_db, monkeypatch, caplog):
    from core import db
    from services import settings_store

    def fail_db_conn():
        raise sqlite3.OperationalError("no such table: voices")

    monkeypatch.setattr(db, "db_conn", fail_db_conn)
    caplog.clear()
    assert settings_store.get_secret("llm_key.openai") is None
    assert "settings_store.get_secret: SQLite read failed" in caplog.text
