"""Startup migration safety net (feat/safe-updates).

`core.db._run_alembic_upgrade` must:

- snapshot the DB *before* pending migrations run (and name the snapshot
  ``omnivoice.db.backup-<version>-<n>``),
- NOT snapshot when the DB is already at head (no churn on every launch),
- keep the pre-existing non-fatal behavior for the "nothing was applied"
  classes (#552/#547: stamped at a removed revision → warn + reconcile),
- and on a migration that fails WHILE executing: stop startup with
  ``MigrationError`` naming the backup path — never auto-restore, never
  continue on a half-migrated DB.
"""
import sqlite3
from pathlib import Path

import pytest

import core.db as db_module
from core import db_backup
from core.db import _BASE_SCHEMA, _run_alembic_upgrade, init_db

# Patch DB_PATH on the SAME module object these symbols were imported from
# (``db_module``), not via the dotted string "core.db.DB_PATH". An earlier
# suite (tests/backend/**) purges ``core.*`` from ``sys.modules`` and never
# restores it, so the string re-resolves to a *re-imported* ``core.db`` while
# ``_run_alembic_upgrade``/``init_db`` — bound here at collection — keep
# reading the ORIGINAL module's globals. Patching the imported object is the
# correct, self-contained seam and is immune to that leak (#909 full-suite).


def _seed_user_db(path, stamp=None):
    """A user DB with real data (and optionally a stamped alembic revision).

    Seeds the full base schema — in the real startup order ``init_db`` lays
    ``_BASE_SCHEMA`` + reconcile *before* alembic runs, so this is what a
    pending-migration DB actually looks like."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_BASE_SCHEMA)
        conn.execute("INSERT INTO voice_profiles(id, name) VALUES ('vp-1', 'Alice')")
        if stamp is not None:
            conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            conn.execute("INSERT INTO alembic_version VALUES (?)", (stamp,))
        conn.commit()
    finally:
        conn.close()


def _profile_names(path):
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT name FROM voice_profiles ORDER BY id")]
    finally:
        conn.close()


def test_pending_migrations_snapshot_first_then_upgrade(tmp_path, monkeypatch):
    """A DB behind head (here: never stamped) gets a backup, then migrates."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    _run_alembic_upgrade()

    backups = db_backup.list_backups(str(db))
    assert backups, "a pre-migration backup must exist"
    assert ".backup-" in backups[0]
    # The backup holds the PRE-migration data.
    assert _profile_names(backups[0]) == ["Alice"]
    # And the live DB migrated to head (alembic_version now stamped).
    conn = sqlite3.connect(str(db))
    try:
        stamped = [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")]
    finally:
        conn.close()
    assert stamped, "upgrade must have stamped the DB at head"


def test_pending_migrations_apply_from_any_cwd(tmp_path, monkeypatch):
    """The desktop app may launch the backend outside the repository root, not the
    repo root. alembic resolves a bare relative script_location against the
    CWD, so alembic.ini must use %(here)s — otherwise the first pending
    migration kills startup with "Path doesn't exist: backend/migrations"."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))
    monkeypatch.chdir(tmp_path)  # anywhere but the repo root

    _run_alembic_upgrade()  # must not raise MigrationError

    conn = sqlite3.connect(str(db))
    try:
        stamped = [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")]
    finally:
        conn.close()
    assert stamped, "upgrade must have stamped the DB at head"


def test_alembic_ini_paths_survive_spaces_and_drive_letters(tmp_path, monkeypatch):
    """The other half of the %(here)s fix: path_separator=os. Without it,
    alembic legacy-splits prepend_sys_path on spaces, commas AND colons —
    shredding "C:\\..." into ["C", "\\..."] on Windows and any POSIX path
    containing a space. The cwd-only test above can't see that (core.config
    is already imported when it runs), so exercise the ini's own resolution
    from a directory with a space in its name and assert exactly one, intact
    sys.path entry gets prepended."""
    import shutil
    import sys

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    repo = Path(__file__).resolve().parents[1]
    here = tmp_path / "a b"  # the space is the point
    (here / "backend").mkdir(parents=True)
    shutil.copytree(repo / "backend" / "migrations", here / "backend" / "migrations")
    shutil.copy(repo / "alembic.ini", here / "alembic.ini")

    monkeypatch.chdir(tmp_path)
    before = list(sys.path)
    try:
        script = ScriptDirectory.from_config(Config(str(here / "alembic.ini")))
        added = [p for p in sys.path if p not in before]
    finally:
        sys.path[:] = before

    assert Path(script.dir).resolve() == (here / "backend" / "migrations").resolve()
    assert len(added) == 1 and Path(added[0]).resolve() == (here / "backend").resolve(), (
        f"prepend_sys_path was shredded by legacy splitting: {added}"
    )


def test_up_to_date_db_is_not_resnapshotted(tmp_path, monkeypatch):
    """Once at head, later launches must not churn new backups."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    _run_alembic_upgrade()
    first = db_backup.list_backups(str(db))
    _run_alembic_upgrade()  # second launch: already at head
    second = db_backup.list_backups(str(db))

    assert first == second, "no new backup when there is nothing to migrate"


def test_unknown_revision_stays_nonfatal_and_makes_no_backup(tmp_path, monkeypatch):
    """#552/#547 class: stamped at a revision this build doesn't ship
    (preview→stable). Nothing would be applied → keep the old warn+continue
    behavior, and don't churn a pointless backup every launch."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db, stamp="9999_from_a_newer_build")
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    init_db()  # must NOT raise (same contract as test_db_schema_reconcile)

    assert db_backup.list_backups(str(db)) == []
    assert _profile_names(db) == ["Alice"]


def test_midflight_failure_stops_startup_and_names_backup(tmp_path, monkeypatch):
    """The data-loss case this PR closes: a migration that fails while
    executing must raise MigrationError (startup stops), leave the original
    data reachable, and point the user at the pre-migration backup."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    import alembic.command

    def _boom(cfg, rev):
        raise RuntimeError("simulated failure inside migration 0007")

    # core.db does `from alembic import command` at call time, so patching the
    # module attribute is what its `command.upgrade(...)` call resolves.
    monkeypatch.setattr(alembic.command, "upgrade", _boom)

    with pytest.raises(db_module.MigrationError) as excinfo:
        _run_alembic_upgrade()

    msg = str(excinfo.value)
    backups = db_backup.list_backups(str(db))
    assert backups, "the pre-migration backup must exist on failure"
    assert backups[0] in msg, "the error must name the backup path"
    assert str(db) in msg, "the error must name the live DB path"
    assert "github.com/debpalash/VoiceStudio/issues" in msg
    # Original data still present in BOTH the live DB and the backup —
    # and nothing was auto-restored (the backup file is separate).
    assert _profile_names(db) == ["Alice"]
    assert _profile_names(backups[0]) == ["Alice"]


def test_midflight_failure_without_backup_says_so(tmp_path, monkeypatch):
    """If the snapshot was skipped (oversized DB), the failure message must
    say a backup wasn't written instead of naming a phantom path."""
    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))
    monkeypatch.setattr(db_backup, "MAX_BACKUP_DB_BYTES", 1)

    import alembic.command

    monkeypatch.setattr(
        alembic.command, "upgrade",
        lambda cfg, rev: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(db_module.MigrationError) as excinfo:
        _run_alembic_upgrade()

    assert "No pre-migration backup was written" in str(excinfo.value)
    assert db_backup.list_backups(str(db)) == []
    assert _profile_names(db) == ["Alice"]


def test_startup_migration_leaves_app_logging_intact(tmp_path, monkeypatch):
    """The in-app `alembic upgrade head` must NOT reconfigure the app's
    logging (#1174): env.py's fileConfig — even with
    disable_existing_loggers=False — used to replace the root logger's
    handlers with alembic.ini's console handler and apply its
    `[logger_root] level=WARN`. Every boot that actually migrated (every
    FIRST RUN, every upgrade) then lost the omnivoice.log file handler and
    all INFO logging for the rest of the process — including the entire
    graceful-shutdown trace, so a SIGTERM'd clean quit read as a silent
    crash. Fail-before/pass-after."""
    import logging

    db = tmp_path / "omnivoice.db"
    _seed_user_db(db)
    monkeypatch.setattr(db_module, "DB_PATH", str(db))

    root = logging.getLogger()
    marker = logging.NullHandler()
    marker.set_name("app-file-log-marker")
    root.addHandler(marker)
    prev_level = root.level
    root.setLevel(logging.INFO)
    try:
        _run_alembic_upgrade()  # migrations actually execute (fresh stamp)
        assert marker in root.handlers, (
            "alembic's fileConfig stripped the app's root handlers"
        )
        assert root.level == logging.INFO, (
            "alembic's [logger_root] level leaked into the live app"
        )
    finally:
        root.removeHandler(marker)
        root.setLevel(prev_level)
