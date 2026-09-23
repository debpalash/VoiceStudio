import re
import sqlite3
import logging
from contextlib import contextmanager
from core.config import DB_PATH
from core import db_backup
from core.version import APP_VERSION

logger = logging.getLogger("omnivoice.db")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TYPE_RE = re.compile(r"^[A-Za-z0-9_ '\"\(\)\-\.]+$")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_conn():
    """Context-managed SQLite connection that commits on clean exit and always closes."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


_BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS voice_profiles (
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
        verified_own_voice INTEGER DEFAULT 0,
        consent_text TEXT DEFAULT '',
        consent_audio_path TEXT DEFAULT '',
        consent_recorded_at REAL DEFAULT NULL,
        kind TEXT DEFAULT 'clone',
        vd_states TEXT DEFAULT NULL,
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS generation_history (
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
        starred INTEGER DEFAULT 0,
        created_at REAL,
        FOREIGN KEY (profile_id) REFERENCES voice_profiles(id)
    );
    CREATE TABLE IF NOT EXISTS dub_history (
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
    CREATE TABLE IF NOT EXISTS studio_projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        video_path TEXT,
        audio_path TEXT,
        duration REAL,
        state_json TEXT,
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS export_history (
        id TEXT PRIMARY KEY,
        filename TEXT,
        destination_path TEXT,
        mode TEXT,
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS glossary_terms (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        note TEXT DEFAULT '',
        auto INTEGER DEFAULT 0,
        created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_glossary_project ON glossary_terms(project_id);

    CREATE TABLE IF NOT EXISTS jobs (
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
    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

    CREATE TABLE IF NOT EXISTS job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        created_at REAL NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_job_events_job_seq ON job_events(job_id, seq);

    -- Phase 1 AUTH-02: encrypted per-install key/value store. Used today
    -- for the HF token row + the per-install Fernet salt. Both fresh
    -- installs (this CREATE) and v0.2.7 upgrades (alembic
    -- 0001_phase1_settings) converge on the same schema.
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at REAL NOT NULL
    );

    -- Wave 2.2: per-agent MCP voice bindings. An MCP client (Claude Code,
    -- Cursor, …) identified by the X-OmniVoice-Client-Id header it sends is
    -- bound to a default voice profile / engine. Fresh installs create it
    -- here; v0.3.x upgrades get it via alembic 0004.
    CREATE TABLE IF NOT EXISTS mcp_client_bindings (
        client_id TEXT PRIMARY KEY,
        label TEXT NOT NULL DEFAULT '',
        profile_id TEXT,
        default_engine TEXT,
        last_seen_at REAL,
        created_at REAL
    );

    -- Expressive-TTS Spec 01 Phase 1: user pronunciation dictionary. A
    -- per-language word→respelling map applied as pure text substitution
    -- before synthesis (Settings → Pronunciation). Fresh installs create it
    -- here; existing DBs get it via alembic 0008_pronunciation_dictionary.
    -- Both paths converge on this identical schema (dual-path discipline).
    CREATE TABLE IF NOT EXISTS pronunciation_entries (
        id TEXT PRIMARY KEY,
        term TEXT NOT NULL,
        replacement TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT 'respelling',
        language TEXT NOT NULL DEFAULT '*',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_pron_lang ON pronunciation_entries(language);

    -- Phone call agent (docs/integrations/calls.md): one row per call placed
    -- or answered by the agent. Full numbers stay in this local DB; the API
    -- returns only remote_masked. Existing DBs get it via alembic
    -- 0012_call_sessions (dual-path discipline).
    CREATE TABLE IF NOT EXISTS call_sessions (
        id TEXT NOT NULL PRIMARY KEY,
        direction TEXT NOT NULL DEFAULT 'outbound',
        remote_number TEXT NOT NULL DEFAULT '',
        remote_masked TEXT NOT NULL DEFAULT '',
        from_number TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT 'twilio',
        provider_call_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        brief TEXT NOT NULL DEFAULT '',
        profile_id TEXT NOT NULL DEFAULT '',
        engine TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT '',
        disclosure TEXT NOT NULL DEFAULT '',
        max_minutes INTEGER NOT NULL DEFAULT 10,
        outcome TEXT,
        summary TEXT NOT NULL DEFAULT '',
        transcript_json TEXT NOT NULL DEFAULT '[]',
        timeline_json TEXT NOT NULL DEFAULT '[]',
        recording_path TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at REAL,
        started_at REAL,
        ended_at REAL,
        duration_s REAL
    );
    CREATE INDEX IF NOT EXISTS idx_call_sessions_created ON call_sessions(created_at);

    -- Remote GPU workers (docs/remote-workers.md). Opt-in: an install with no
    -- remote workers never writes a row here and behaves exactly as before.
    --
    -- `public_key` is the worker's identity — a server-assigned id is a name,
    -- not proof, so every reconnect is verified against this key. Revocation
    -- is a persisted fact (not in-memory state) precisely so a restart of the
    -- control plane cannot silently readmit a worker the user removed.
    CREATE TABLE IF NOT EXISTS remote_workers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        key_id TEXT NOT NULL,
        public_key BLOB NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        revoked INTEGER NOT NULL DEFAULT 0,
        revoked_at REAL,
        priority INTEGER NOT NULL DEFAULT 50,
        endpoint TEXT NOT NULL DEFAULT '',
        host_json TEXT NOT NULL DEFAULT '{}',
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        max_concurrent_tasks INTEGER NOT NULL DEFAULT 1,
        -- Bumped on every successful (re)connect. Messages stamped with an
        -- older epoch are from a session we have already replaced.
        session_epoch INTEGER NOT NULL DEFAULT 0,
        consent_granted_at REAL,
        created_at REAL NOT NULL,
        last_seen_at REAL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_workers_key ON remote_workers(key_id);

    -- Single-use join tokens. Only the hash is stored: the plaintext exists
    -- once, in the dialog that shows it.
    CREATE TABLE IF NOT EXISTS remote_worker_enrollments (
        token_id TEXT PRIMARY KEY,
        secret_hash TEXT NOT NULL,
        endpoint TEXT NOT NULL DEFAULT '',
        cert_fingerprint TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        used_at REAL,
        used_by_worker TEXT
    );

    -- Tasks dispatched to remote workers. Unlike the local `jobs` table (whose
    -- startup sweep marks anything in-flight as failed), these must SURVIVE a
    -- control-plane restart: the desktop app quits while a remote GPU keeps
    -- rendering, and the worker is the source of truth for what is still
    -- running. Reconciliation on reconnect rebuilds live state from here.
    CREATE TABLE IF NOT EXISTS remote_tasks (
        id TEXT PRIMARY KEY,
        -- Client-supplied; deduplicates client retries before the worker
        -- protocol is involved at all.
        idempotency_key TEXT,
        operation TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT '',
        model_id TEXT NOT NULL DEFAULT '',
        params_json TEXT NOT NULL DEFAULT '{}',
        priority INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'queued',
        max_attempts INTEGER NOT NULL DEFAULT 3,
        excluded_json TEXT NOT NULL DEFAULT '[]',
        error_json TEXT,
        -- Written BEFORE RESULT_ACK is sent. If the server dies between
        -- receiving a result and acknowledging it, the worker redelivers and
        -- this row is what makes the second delivery a no-op instead of a
        -- silently lost multi-minute render.
        result_ref TEXT,
        result_json TEXT,
        project_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        deadline_at REAL,
        -- Deliberate additive-reconcile exception to the alembic rule: remote
        -- task recovery must work in bundled installs where alembic may be
        -- unavailable, and this nullable affinity column is additive-only.
        pinned_worker_id TEXT,
        finished_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_remote_tasks_state ON remote_tasks(state, priority, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_tasks_idem ON remote_tasks(idempotency_key)
        WHERE idempotency_key IS NOT NULL;

    CREATE TABLE IF NOT EXISTS remote_task_attempts (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        session_epoch INTEGER NOT NULL DEFAULT 0,
        attempt_number INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL DEFAULT 'assigned',
        progress REAL NOT NULL DEFAULT 0,
        stage TEXT NOT NULL DEFAULT '',
        error_json TEXT,
        created_at REAL NOT NULL,
        accepted_at REAL,
        started_at REAL,
        finished_at REAL,
        lease_expires_at REAL,
        grace_expires_at REAL,
        deadlines_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_remote_attempts_task ON remote_task_attempts(task_id);
    CREATE INDEX IF NOT EXISTS idx_remote_attempts_worker ON remote_task_attempts(worker_id, state);
"""

# Only tables/columns this module is allowed to ALTER. Prevents SQL injection via
# the f-string ALTER below if these helpers ever get exposed to user input.
_ALLOWED_MIGRATIONS = {
    ("voice_profiles", "locked_audio_path"),
    ("voice_profiles", "seed"),
    ("voice_profiles", "is_locked"),
    ("voice_profiles", "personality"),
    ("generation_history", "seed"),
    ("dub_history", "content_hash"),
}


def _add_column_if_missing(conn, table: str, column: str, typedef: str):
    if (table, column) not in _ALLOWED_MIGRATIONS:
        raise ValueError(f"Migration not allowed: {table}.{column}")
    if not _IDENT_RE.match(table) or not _IDENT_RE.match(column):
        raise ValueError(f"Invalid identifier: {table}.{column}")
    if not _TYPE_RE.match(typedef):
        raise ValueError(f"Invalid typedef: {typedef!r}")
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            logger.warning("ALTER %s.%s failed: %s", table, column, e)


def _migrate(conn, current: int) -> int:
    """Apply migrations sequentially. Return new version."""
    if current < 1:
        _add_column_if_missing(conn, "voice_profiles", "locked_audio_path", "TEXT DEFAULT ''")
        _add_column_if_missing(conn, "voice_profiles", "seed", "INTEGER DEFAULT NULL")
        _add_column_if_missing(conn, "voice_profiles", "is_locked", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "generation_history", "seed", "INTEGER DEFAULT NULL")
        current = 1
    if current < 2:
        _add_column_if_missing(conn, "dub_history", "content_hash", "TEXT DEFAULT ''")
        current = 2
    # v3: glossary_terms table lives in _BASE_SCHEMA (IF NOT EXISTS), so an old
    # DB simply picks it up on the next init — no ALTER needed.
    if current < 3:
        current = 3
    if current < 4:
        _add_column_if_missing(conn, "voice_profiles", "personality", "TEXT DEFAULT ''")
        current = 4
    return current


def _reconcile_additive_columns(conn) -> None:
    """Make the live schema converge to ``_BASE_SCHEMA`` by ADDing any column the
    canonical schema declares but an existing table is missing — the belt for
    when alembic can't run on an upgraded DB.

    ``CREATE TABLE IF NOT EXISTS`` (init_db) never adds columns to a table that
    already exists, the legacy ``_migrate`` only knows pre-0.3 columns, and
    ``_run_alembic_upgrade`` swallows failures. So a DB whose ``alembic_version``
    is stamped at a removed revision (e.g. after running a preview build), or
    where alembic isn't importable in the bundled interpreter, would otherwise
    lose every alembic-era additive column forever — the ``no such column:
    consent_audio_path`` 500 (#552/#547), and the same class for
    ``kind``/``vd_states``/``is_demo``/.... Additive only: never drops or retypes
    a column, so it is safe and backward-compatible with existing user data. The
    canonical names/types/defaults come solely from ``_BASE_SCHEMA`` (developer
    controlled), so the ALTER is injection-safe.
    """
    canon = sqlite3.connect(":memory:")
    try:
        canon.executescript(_BASE_SCHEMA)
        _tables_sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        live_tables = {r[0] for r in conn.execute(_tables_sql)}
        for table in (r[0] for r in canon.execute(_tables_sql)):
            if table not in live_tables:
                continue  # whole table missing → init_db's CREATE already made it
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            # (cid, name, type, notnull, dflt_value, pk)
            for _cid, name, ctype, notnull, dflt, _pk in canon.execute(f"PRAGMA table_info({table})"):
                if name in have or not _IDENT_RE.match(name):
                    continue
                ddl = f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ctype or "TEXT"}'
                if dflt is not None:
                    ddl += f" DEFAULT {dflt}"
                elif notnull:
                    ddl += " DEFAULT ''"  # SQLite requires a default to ADD a NOT NULL column
                try:
                    conn.execute(ddl)
                    logger.info("schema reconcile: added missing column %s.%s", table, name)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        logger.warning("schema reconcile ALTER %s.%s failed: %s", table, name, exc)
        conn.commit()
    finally:
        canon.close()


def ensure_schema() -> None:
    """Idempotently ensure the base tables + additive columns exist.

    A runtime self-heal for a DB that somehow missed init — e.g. a write hitting
    ``no such table: generation_history`` (#710) because ``init_db()``'s
    ``executescript`` never took on that DB. Safe to call anytime: it's just
    ``CREATE ... IF NOT EXISTS`` plus the additive-only column reconcile, so it
    never drops or retypes anything and is backward-compatible with user data.
    Cheaper than ``init_db()`` (skips the legacy ``_migrate`` + alembic), so a
    write path can call it on a schema error and retry without a 500.
    """
    conn = get_db()
    try:
        conn.executescript(_BASE_SCHEMA)
        _reconcile_additive_columns(conn)
        conn.commit()
    finally:
        conn.close()


def init_db():
    conn = get_db()
    try:
        conn.executescript(_BASE_SCHEMA)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        new_version = _migrate(conn, version)
        if new_version != version:
            conn.execute(f"PRAGMA user_version = {new_version}")
        # Converge any alembic-era additive columns that CREATE TABLE IF NOT
        # EXISTS + the legacy _migrate don't add to a pre-existing table
        # (consent_audio_path, kind, ...). Runs regardless of whether alembic
        # below succeeds, so an unrunnable alembic can't leave a 500-ing schema.
        _reconcile_additive_columns(conn)
        conn.commit()
    finally:
        conn.close()
    # Phase 1: also run any pending alembic migrations. Fresh installs land
    # the schema via _BASE_SCHEMA above; v0.2.7 → v0.3.0 upgrades pick up
    # the same end-state via the alembic versions/ chain. Both paths
    # converge because every migration uses `CREATE TABLE IF NOT EXISTS`
    # or explicit existence checks.
    _run_alembic_upgrade()


class MigrationError(RuntimeError):
    """A schema migration failed *while executing*. Startup must NOT continue
    on a possibly half-migrated database — the caller lets this propagate so
    the process stops with an actionable message naming the pre-migration
    backup (see ``core.db_backup``). Restore is deliberately manual: silently
    auto-restoring the snapshot could itself discard user data."""


def _reconcile_after_alembic_skip() -> None:
    """Converge the schema directly when alembic can't run at all (not
    importable, or stamped at a removed revision — #552/#547) so additive
    columns still land instead of 500-ing on `no such column`. Only for the
    "nothing was applied" classes; a mid-migration failure must NOT reach
    here (see MigrationError)."""
    try:
        conn = get_db()
        try:
            _reconcile_additive_columns(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema reconcile after alembic skip also failed: %s", exc)


def _stamped_revisions(db_path: str) -> set | None:
    """Revisions recorded in ``alembic_version`` (empty set = never stamped),
    or None when the DB can't be read."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            try:
                return {r[0] for r in conn.execute("SELECT version_num FROM alembic_version")}
            except sqlite3.OperationalError:
                return set()  # table absent — nothing ever stamped
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def _plan_alembic(cfg) -> str:
    """Decide what an ``upgrade head`` run would actually do:

    - ``up_to_date``       — stamped at head; upgrade is a no-op.
    - ``pending``          — migrations WILL execute (snapshot the DB first).
    - ``unknown_revision`` — stamped at a revision this build doesn't ship
      (preview→stable downgrade, #552/#547); upgrade would fail before
      applying anything, so skip it and reconcile additively instead.
    - ``indeterminate``    — can't tell; treat like pending (snapshot, run).
    """
    try:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(cfg)
        known = {rev.revision for rev in script.walk_revisions()}
        heads = set(script.get_heads())
        stamped = _stamped_revisions(DB_PATH)
        if stamped is None:
            return "indeterminate"
        if stamped and not stamped <= known:
            return "unknown_revision"
        if stamped == heads:
            return "up_to_date"
        return "pending"
    except Exception:  # noqa: BLE001
        return "indeterminate"


def _run_alembic_upgrade() -> None:
    """`alembic upgrade head` on startup, wrapped in the data-safety net.

    Failure classes are handled differently on purpose:

    - alembic unavailable / stamped at an unknown revision → **non-fatal**
      (nothing was applied; warn + `_reconcile_additive_columns` keeps the
      schema converged, exactly the pre-existing #552/#547 behavior).
    - migrations actually pending → the DB is snapshotted first
      (``omnivoice.db.backup-<version>-<n>``, newest 3 kept), then upgraded.
    - a migration fails **while executing** → raise :class:`MigrationError`:
      startup stops with a message naming the backup, instead of silently
      running the app on a half-migrated DB.
    """
    try:
        import os
        from alembic import command
        from alembic.config import Config

        # Walk up from backend/core/db.py to find the alembic.ini at the
        # project root.
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))
        ini = os.path.join(root, "alembic.ini")
        if not os.path.isfile(ini):
            logger.debug("alembic.ini not found at %s; skipping migrations", ini)
            return
        cfg = Config(ini)
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")
        # In-app run: alembic.ini's logging section must not touch the live
        # app's logging. env.py's fileConfig() — even with
        # disable_existing_loggers=False — replaces the root logger's handlers
        # and applies [logger_root] level=WARN, so every boot that actually
        # migrated (first run, upgrades) lost the omnivoice.log file handler
        # and all INFO logging for the rest of the process — including the
        # graceful-shutdown trace, making a SIGTERM'd clean quit look like a
        # silent crash (#1174). env.py checks this attribute; the standalone
        # `alembic` CLI (which doesn't set it) keeps its logging config.
        cfg.attributes["configure_logger"] = False
    except Exception as exc:  # noqa: BLE001 — alembic not importable / bad ini
        logger.warning("alembic upgrade head skipped: %s", exc)
        _reconcile_after_alembic_skip()
        return

    plan = _plan_alembic(cfg)
    if plan == "up_to_date":
        return
    if plan == "unknown_revision":
        logger.warning(
            "alembic_version is stamped at a revision this build doesn't ship "
            "(preview/newer build ran on this DB) — skipping alembic and "
            "reconciling the schema additively (#552/#547)"
        )
        _reconcile_after_alembic_skip()
        return

    # Migrations may actually execute: snapshot the DB first so a failed or
    # interrupted migration can never cost user data. A backup problem alone
    # must not brick startup (the >500 MB skip is by design), so log and go on.
    # ``db_backup``/``APP_VERSION`` are module-level imports (top of file), not
    # re-imported here: a test that patches ``core.db_backup.MAX_BACKUP_DB_BYTES``
    # on the object it imported at collection must see the same object this
    # function uses. A lazy ``from core import db_backup`` would re-resolve
    # through the (possibly re-imported) ``core`` package and silently miss the
    # patch after another suite purged ``core.*`` from ``sys.modules``.
    backup_path = None
    try:
        backup_path = db_backup.snapshot_before_migration(DB_PATH, APP_VERSION)
    except Exception:  # noqa: BLE001
        logger.exception("Pre-migration DB backup failed — continuing without one")

    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        if "Can't locate revision" in str(exc):
            # Belt for an unknown-revision case _plan_alembic missed: alembic
            # bails before applying anything, so the old non-fatal path is safe.
            logger.warning("alembic upgrade head skipped: %s", exc)
            _reconcile_after_alembic_skip()
            return
        backup_note = (
            f"A backup of your data from just before the migration is at: {backup_path}"
            if backup_path
            else "No pre-migration backup was written this run (see the log above)"
        )
        msg = (
            f"Database migration failed while running: {exc}. "
            f"VoiceStudio stopped instead of running on a partially migrated database, "
            f"and nothing was auto-restored (your database at {DB_PATH} was left "
            f"exactly as the failed migration left it). "
            f"{backup_note}. "
            "What to do: relaunch to retry; if it keeps failing, report it at "
            "https://github.com/debpalash/VoiceStudio/issues (keep the backup file). "
            "To roll back manually: quit the app, replace omnivoice.db with the backup "
            "file, and reinstall the previous version."
        )
        logger.error(msg)
        raise MigrationError(msg) from exc
