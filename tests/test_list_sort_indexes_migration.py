"""0019 creates the list sort indexes through alembic, guarded by column existence,
and stays in lockstep with ``core.db._ensure_perf_indexes`` (the no-alembic path)."""
import importlib.util
import os
import sqlite3

from core import db as core_db


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root != os.path.dirname(root) and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    return root


def _upgrade(db_path, target="head"):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _index_names(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _migration():
    path = os.path.join(_repo_root(), "backend", "migrations", "versions", "0019_list_sort_indexes.py")
    spec = importlib.util.spec_from_file_location("_m0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_index_list_matches_the_init_db_helper():
    assert tuple(_migration().INDEXES) == tuple(core_db._PERF_INDEXES)


def test_upgrade_creates_the_indexes_on_a_populated_database_and_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(core_db._BASE_SCHEMA)
        for _name, table, *_ in core_db._PERF_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {_name}")
        conn.execute("INSERT INTO generation_history(id, text, created_at) VALUES ('g', 'hi', 1.0)")
    _upgrade(db, "0018_pronunciation_profile_scope")
    assert not {n for n, *_ in core_db._PERF_INDEXES} & _index_names(db)

    _upgrade(db)
    _upgrade(db)
    assert {n for n, *_ in core_db._PERF_INDEXES} <= _index_names(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT id FROM generation_history").fetchall() == [("g",)]


def test_upgrade_tolerates_a_database_missing_most_tables(tmp_path):
    db = tmp_path / "bare.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE generation_history (id TEXT PRIMARY KEY, created_at REAL)")
        # Earlier revisions in the chain need voice_profiles to exist.
        conn.execute(
            "CREATE TABLE voice_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "ref_audio_path TEXT, ref_text TEXT DEFAULT '', instruct TEXT DEFAULT '', "
            "language TEXT DEFAULT 'Auto', created_at REAL)"
        )
    _upgrade(db)  # most list tables absent at 0019's start: must not fail
    assert "idx_generation_history_created" in _index_names(db)
