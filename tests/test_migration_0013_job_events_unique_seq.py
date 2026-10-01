"""Migration 0013 makes job_events(job_id, seq) unique.

`append_event` used to assign seq with SELECT MAX + a separate INSERT, so two
concurrent callers could write the same seq; the index was not UNIQUE, so the
duplicates persisted and SSE replay (`?after_seq=N`) doubled one event and
skipped another. The write path is atomic now — this covers the schema half:
existing duplicates are renumbered (never deleted, both rows are real events)
and the pair becomes UNIQUE.

Drives the real alembic chain on a temp SQLite DB, mirroring
tests/test_migration_0007_instruct_rebuild.py, and separately covers
`core/db.py::_ensure_job_events_seq_unique` — the belt that converges installs
where alembic cannot run — including that the two paths agree.
"""
import importlib.util
import os
import sqlite3

import pytest

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_OLD_INDEX = "idx_job_events_job_seq"
_UNIQUE_INDEX = "idx_job_events_job_seq_unique"

# job_events as it looked before this revision: the pair indexed, not constrained.
_PRE_0013_JOB_EVENTS = f"""
    CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        created_at REAL NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX {_OLD_INDEX} ON job_events(job_id, seq);
"""

# (job_id, seq, payload) in insertion order.
#   raced  — the bug: two callers collided on seq 2, a third on seq 3
#   clean  — untouched, already distinct
#   capped — untouched, and its seq base is above 1 because the per-job cap
#            trimmed its oldest rows; a blanket renumber would silently rebase it
_SEED = [
    ("raced", 1, "a"), ("raced", 2, "b"), ("raced", 2, "c"), ("raced", 3, "d"), ("raced", 3, "e"),
    ("clean", 1, "p"), ("clean", 2, "q"),
    ("capped", 10, "x"), ("capped", 11, "y"), ("capped", 12, "z"),
]


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root and root != "/" and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    assert os.path.isfile(os.path.join(root, "alembic.ini")), "alembic.ini not found"
    return root


def _load_migration_module():
    path = os.path.join(
        _repo_root(), "backend", "migrations", "versions",
        "0013_job_events_unique_seq.py",
    )
    spec = importlib.util.spec_from_file_location("_mig0013", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path: str) -> None:
    """A pre-0013 database holding the duplicates the old write path produced.

    Seeded with the canonical `_BASE_SCHEMA` so the earlier revisions in the
    chain see the tables they expect, then job_events is replaced with its
    pre-0013 shape — the same order init_db uses on a real upgrade.
    """
    from core.db import _BASE_SCHEMA

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_BASE_SCHEMA)
        conn.execute("DROP TABLE IF EXISTS job_events")
        conn.executescript(_PRE_0013_JOB_EVENTS)
        conn.executemany(
            "INSERT INTO job_events (job_id, seq, created_at, payload) VALUES (?, ?, 0.0, ?)",
            _SEED,
        )
        conn.commit()
    finally:
        conn.close()


def _rows(db_path: str, job_id: str) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            (r[0], r[1]) for r in conn.execute(
                "SELECT seq, payload FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
            )
        ]
    finally:
        conn.close()


def _indexes(db_path: str) -> dict[str, bool]:
    """Index name -> is_unique, for every index on job_events."""
    conn = sqlite3.connect(db_path)
    try:
        return {r[1]: bool(r[2]) for r in conn.execute("PRAGMA index_list(job_events)")}
    finally:
        conn.close()


def _assert_converged(db_path: str) -> None:
    idx = _indexes(db_path)
    assert idx.get(_UNIQUE_INDEX) is True, f"unique index missing: {idx}"
    assert _OLD_INDEX not in idx, f"superseded non-unique index still present: {idx}"

    # The repair renumbers, it never drops an event.
    assert _rows(db_path, "raced") == [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")]
    # Jobs that were already distinct are left exactly as they were — including
    # one whose base is above 1 because the per-job cap trimmed its oldest rows.
    assert _rows(db_path, "clean") == [(1, "p"), (2, "q")]
    assert _rows(db_path, "capped") == [(10, "x"), (11, "y"), (12, "z")]

    # And the invariant is now enforced, not merely upheld.
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO job_events (job_id, seq, created_at, payload) VALUES (?, ?, 0.0, ?)",
                ("raced", 5, "duplicate"),
            )
    finally:
        conn.close()


def test_migration_0013_renumbers_duplicates_and_enforces_uniqueness(tmp_path):
    """The alembic path: upgrade head over a database carrying duplicates."""
    from alembic import command
    from alembic.config import Config

    db = tmp_path / "raced.db"
    _seed(str(db))

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    _assert_converged(str(db))


def test_migration_0013_is_idempotent(tmp_path):
    """Running the repair twice must not renumber an already-clean database."""
    mod = _load_migration_module()
    db = tmp_path / "twice.db"
    _seed(str(db))

    conn = sqlite3.connect(str(db))
    try:
        for _ in range(2):
            conn.execute(mod.RENUMBER_DUPLICATE_SEQS)
        conn.commit()
    finally:
        conn.close()

    assert _rows(str(db), "raced") == [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")]
    assert _rows(str(db), "capped") == [(10, "x"), (11, "y"), (12, "z")]


def test_core_db_converges_the_same_way_without_alembic(tmp_path):
    """The belt for bundled installs where alembic cannot run.

    `_reconcile_additive_columns` exists for the same reason; this is its
    counterpart for the one index that has to be UNIQUE. It must reach the same
    end state as the migration, or the two install paths diverge.
    """
    from core.db import _ensure_job_events_seq_unique

    db = tmp_path / "no-alembic.db"
    _seed(str(db))

    conn = sqlite3.connect(str(db))
    try:
        _ensure_job_events_seq_unique(conn)
    finally:
        conn.close()

    _assert_converged(str(db))


def test_base_schema_never_creates_the_unique_index_directly(tmp_path):
    """_BASE_SCHEMA must not carry a bare CREATE UNIQUE INDEX for this pair.

    init_db runs `executescript(_BASE_SCHEMA)` BEFORE any repair. On a database
    that still holds duplicates, a bare CREATE UNIQUE INDEX there would raise
    inside executescript and stop the app from starting — for exactly the users
    the repair exists to serve. Regression guard against someone later "tidying"
    the index back into the schema block.
    """
    from core.db import _BASE_SCHEMA

    db = tmp_path / "boot.db"
    _seed(str(db))

    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_BASE_SCHEMA)  # must not raise over the duplicates
        conn.commit()
    finally:
        conn.close()

    assert _UNIQUE_INDEX not in _indexes(str(db))
