"""Voices get a stable sync_id and consent columns (S2 prep, guide §4)."""
import os
import sqlite3

import pytest

from core import db as core_db
from services.cloud_sync.record_ids import ensure_sync_ids


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    return root


def _cfg(path):
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return cfg


NEW = ("sync_id", "consent_basis", "consent_attested_at")


def _old_db(path):
    """voice_profiles as 0014 left it: without the S2 columns."""
    from alembic import command

    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(voice_profiles)") if r[1] not in NEW]
        conn.execute(f"CREATE TABLE vp_old AS SELECT {', '.join(cols)} FROM voice_profiles")
        conn.execute("DROP TABLE voice_profiles")
        conn.execute("ALTER TABLE vp_old RENAME TO voice_profiles")
        conn.execute("INSERT INTO voice_profiles (id, name, created_at) VALUES ('ab12cd34', 'Mine', 1)")
    command.stamp(_cfg(path), "0014_cloud_sync")


def _columns(path):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(voice_profiles)")}


def test_migration_adds_the_columns_and_keeps_existing_voices(tmp_path):
    from alembic import command

    path = str(tmp_path / "old.db")
    _old_db(path)
    command.upgrade(_cfg(path), "head")
    assert set(NEW) <= _columns(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM voice_profiles WHERE id='ab12cd34'").fetchone()[0] == "Mine"
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(voice_profiles)")}
    assert "idx_voice_profiles_sync_id" in indexes


def test_reconcile_adds_the_columns_when_alembic_cannot_run(tmp_path):
    path = str(tmp_path / "old.db")
    _old_db(path)
    with sqlite3.connect(path) as conn:
        core_db._reconcile_additive_columns(conn)
    assert set(NEW) <= _columns(path)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "omnivoice.db")
    monkeypatch.setattr(core_db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
        for n in range(3):
            conn.execute("INSERT INTO voice_profiles (id, name, created_at) VALUES (?, ?, 1)", (f"id{n}", f"v{n}"))
    return path


def test_ids_are_filled_once_and_stay_stable(db):
    assert ensure_sync_ids() == 3
    with sqlite3.connect(db) as conn:
        first = dict(conn.execute("SELECT id, sync_id FROM voice_profiles"))
    assert all(len(v) == 16 and v.isalnum() for v in first.values())
    assert len(set(first.values())) == 3
    assert ensure_sync_ids() == 0
    with sqlite3.connect(db) as conn:
        assert dict(conn.execute("SELECT id, sync_id FROM voice_profiles")) == first
