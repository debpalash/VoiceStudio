"""job_events (job_id, seq) is unique, and old duplicates are repaired.

Databases written by the old non-atomic ``append_event`` can hold two events of
one job with the same seq. Both the alembic upgrade (0016) and the runtime
repair in ``core/db.py`` (for installs where alembic cannot run) must renumber
them in arrival order without losing an event, leave other jobs alone, and then
enforce uniqueness.
"""
import os
import sqlite3

import pytest

_OLD_JOB_EVENTS = """
    CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        created_at REAL NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX idx_job_events_job_seq ON job_events(job_id, seq);
"""

# Pre-0003 voice_profiles shape the alembic chain upgrades (as in the 0007 test).
_OLD_PROFILES = """
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
        description TEXT DEFAULT '',
        is_demo INTEGER DEFAULT 0,
        created_at REAL
    );
"""

# (job_id, seq, payload) in insertion (= arrival) order.
_ROWS = [
    ("dup", 1, "a"),
    ("dup", 2, "b"),
    ("dup", 2, "c"),  # collided with "b"
    ("dup", 3, "d"),
    ("trimmed", 41, "x"),  # oldest events were trimmed: base stays 41
    ("trimmed", 41, "y"),
    ("clean", 7, "p"),  # no duplicate: untouched
    ("clean", 8, "q"),
]

_EXPECTED = {
    "dup": [(1, "a"), (2, "b"), (3, "c"), (4, "d")],
    "trimmed": [(41, "x"), (42, "y")],
    "clean": [(7, "p"), (8, "q")],
}


def _seed(conn):
    conn.executemany(
        "INSERT INTO job_events (job_id, seq, created_at, payload) VALUES (?, ?, 0, ?)", _ROWS
    )
    conn.commit()


def _by_job(conn):
    out = {}
    for job_id, seq, payload in conn.execute(
        "SELECT job_id, seq, payload FROM job_events ORDER BY job_id, seq"
    ):
        out.setdefault(job_id, []).append((seq, payload))
    return out


def _assert_unique_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_events (job_id, seq, created_at, payload) VALUES ('dup', 1, 0, 'z')"
        )


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root != os.path.dirname(root) and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    return root


def _alembic_upgrade(db_path: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def test_alembic_upgrade_repairs_duplicates_and_adds_unique_index(tmp_path):
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as conn:
        conn.executescript(_OLD_PROFILES + _OLD_JOB_EVENTS)
    _alembic_upgrade(db, "0015_voice_sync_prep")
    with sqlite3.connect(db) as conn:
        _seed(conn)

    _alembic_upgrade(db, "head")

    with sqlite3.connect(db) as conn:
        assert _by_job(conn) == _EXPECTED
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_job_events_job_seq_unique" in names
        assert "idx_job_events_job_seq" not in names
        _assert_unique_enforced(conn)


def test_runtime_repair_matches_migration_and_is_idempotent():
    from core import db

    conn = sqlite3.connect(":memory:")
    conn.executescript(_OLD_JOB_EVENTS)
    _seed(conn)
    db._ensure_job_events_unique_seq(conn)
    db._ensure_job_events_unique_seq(conn)
    assert _by_job(conn) == _EXPECTED
    _assert_unique_enforced(conn)


def test_base_schema_still_starts_on_a_db_with_duplicates():
    """The unique index must not live in _BASE_SCHEMA: init_db runs it before
    any repair, and a failing CREATE UNIQUE INDEX would stop startup."""
    from core import db

    conn = sqlite3.connect(":memory:")
    conn.executescript(_OLD_JOB_EVENTS)
    _seed(conn)
    conn.executescript(db._BASE_SCHEMA)
    db._ensure_job_events_unique_seq(conn)
    assert _by_job(conn) == _EXPECTED


def test_fresh_schema_enforces_uniqueness():
    from core import db

    conn = sqlite3.connect(":memory:")
    conn.executescript(db._BASE_SCHEMA)
    db._ensure_job_events_unique_seq(conn)
    conn.execute("INSERT INTO job_events (job_id, seq, created_at, payload) VALUES ('dup', 1, 0, 'a')")
    _assert_unique_enforced(conn)
