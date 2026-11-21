"""The runtime schema must self-heal additive columns even when `alembic
upgrade head` can't run — the "no such column: consent_audio_path" 500
(#552/#547) and its whole class (kind/vd_states/is_demo/...).

A DB whose alembic_version is stamped at a revision no longer in versions/
(common after running a preview/main build) makes alembic raise; the failure is
swallowed, and CREATE TABLE IF NOT EXISTS never adds columns to a pre-existing
table — so without reconciliation the new columns never land.
"""
import sqlite3

import pytest

from core.db import _BASE_SCHEMA, _reconcile_additive_columns


def _cols(db_path, table="voice_profiles"):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _base_schema_cols(table="voice_profiles"):
    canon = sqlite3.connect(":memory:")
    try:
        canon.executescript(_BASE_SCHEMA)
        return {r[1] for r in canon.execute(f"PRAGMA table_info({table})")}
    finally:
        canon.close()


# A pre-consent / pre-unification voice_profiles — missing every alembic-era
# additive column.
_LEGACY_PROFILES = """
    CREATE TABLE voice_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ref_audio_path TEXT,
        ref_text TEXT DEFAULT '',
        instruct TEXT DEFAULT '',
        language TEXT DEFAULT 'Auto',
        created_at REAL
    );
"""


def test_init_db_self_heals_missing_columns_when_alembic_fails(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_LEGACY_PROFILES)
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('vp-1', 'Alice')")
        # alembic stamped at a revision that no longer exists → command.upgrade
        # raises 'Can't locate revision', exercising the swallowed-failure path.
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('0003_preview_removed_rev')")
        conn.commit()

    monkeypatch.setattr("core.db.DB_PATH", str(db))
    from core.db import init_db

    init_db()  # must NOT raise, and must converge the schema

    cols = _cols(db)
    for col in ("verified_own_voice", "consent_text", "consent_audio_path",
                "consent_recorded_at", "kind", "vd_states", "is_demo"):
        assert col in cols, f"schema reconcile did not add {col} (the #552 symptom)"
    # the existing row survives
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT name FROM voice_profiles WHERE id='vp-1'").fetchone()[0] == "Alice"


def test_reconcile_converges_voice_profiles_to_base_schema(tmp_path):
    db = tmp_path / "stripped.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_LEGACY_PROFILES)
        conn.commit()
        _reconcile_additive_columns(conn)
    assert _cols(db) == _base_schema_cols(), "reconcile must match the canonical column set"


def test_reconcile_is_idempotent_and_additive_only(tmp_path):
    db = tmp_path / "twice.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_LEGACY_PROFILES)
        conn.commit()
        _reconcile_additive_columns(conn)
        after_first = _cols(db)
        _reconcile_additive_columns(conn)  # second run must be a clean no-op
        after_second = _cols(db)
    assert after_first == after_second
    # additive only — the original legacy columns are never dropped
    assert {"id", "name", "instruct", "language"} <= after_second


def _indexes(db, table="voice_profiles"):
    with sqlite3.connect(str(db)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}


def test_schema_only_path_creates_the_voice_sync_id_index(tmp_path, monkeypatch):
    """Both schema paths must enforce one cloud-sync identity per voice.

    Alembic 0015 creates `idx_voice_profiles_sync_id`; a bundled install whose
    interpreter cannot run alembic only gets `_BASE_SCHEMA` plus the reconciler.
    The index cannot live in `_BASE_SCHEMA` (an index over a column that a
    legacy table has not been given yet aborts the whole `executescript`), so
    the schema-only path has to create it after the additive reconcile — without
    this, duplicate sync ids are accepted locally and only surface later as a
    cloud-sync conflict.
    """
    db = tmp_path / "sync-index.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_LEGACY_PROFILES)
        # alembic stamped at a revision that no longer exists → upgrade raises
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('0003_preview_removed_rev')")
        conn.commit()

    monkeypatch.setattr("core.db.DB_PATH", str(db))
    from core.db import init_db

    init_db()

    assert "idx_voice_profiles_sync_id" in _indexes(db)
    with sqlite3.connect(str(db)) as conn:
        # partial index: unassigned identities (NULL) may repeat…
        conn.execute("INSERT INTO voice_profiles(id, name, sync_id) VALUES ('a', 'A', NULL)")
        conn.execute("INSERT INTO voice_profiles(id, name, sync_id) VALUES ('b', 'B', NULL)")
        # …but a claimed one may not
        conn.execute("INSERT INTO voice_profiles(id, name, sync_id) VALUES ('c', 'C', 'sync-1')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO voice_profiles(id, name, sync_id) VALUES ('d', 'D', 'sync-1')")


def test_init_db_upgrades_legacy_history_table_and_adds_perf_indexes(tmp_path, monkeypatch):
    """A generation_history predating ``starred`` must not abort init_db:
    sort indexes are created after the additive reconcile, not in _BASE_SCHEMA."""
    db = tmp_path / "legacy_history.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(
            "CREATE TABLE generation_history (id TEXT PRIMARY KEY, text TEXT, created_at REAL);"
        )
        conn.execute("INSERT INTO generation_history(id, text) VALUES ('g1', 'hi')")
        conn.commit()

    monkeypatch.setattr("core.db.DB_PATH", str(db))
    from core.db import init_db

    init_db()  # must NOT raise "no such column: starred"

    assert "starred" in _cols(db, "generation_history")
    with sqlite3.connect(str(db)) as conn:
        names = {r[1] for r in conn.execute("PRAGMA index_list(generation_history)")}
        assert conn.execute("SELECT id FROM generation_history").fetchall() == [("g1",)]
    assert {"idx_generation_history_starred", "idx_generation_history_created"} <= names
