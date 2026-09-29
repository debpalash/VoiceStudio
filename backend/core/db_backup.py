"""Pre-migration SQLite safety net (data-safe updates).

Before ``alembic upgrade head`` applies *pending* migrations at startup —
which is exactly the first launch of a new app version that changed the
schema — the live database is snapshotted next to itself as
``omnivoice.db.backup-<version>-<n>`` so a failed or interrupted migration
can never cost user data (voices, projects, history, settings).

Design rules (owner intent: "never corrupt/erase user data on update"):

- Snapshots use the SQLite online-backup API (``sqlite3.Connection.backup``),
  not a file copy — the live DB runs in WAL mode, so a plain copy could miss
  everything still sitting in ``omnivoice.db-wal``.
- Only the most recent ``KEEP_BACKUPS`` snapshots are kept; older ones are
  pruned so backups can't grow without bound.
- DBs larger than ``MAX_BACKUP_DB_BYTES`` are skipped with a log line (a
  multi-hundred-MB copy on every schema upgrade is worse than the risk it
  hedges on those installs).
- Restore is NEVER automatic. On migration failure the caller
  (``core.db._run_alembic_upgrade``) stops startup and names the backup path
  so the user (or a support thread) decides — a silent auto-restore could
  itself discard data written after the snapshot.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time

logger = logging.getLogger("omnivoice.db.backup")

#: Keep this many snapshots; older ones are pruned after each new snapshot.
KEEP_BACKUPS = 3

#: Skip the snapshot (with a log line) when the DB exceeds this size.
MAX_BACKUP_DB_BYTES = 500 * 1024 * 1024

#: ``<db name>.backup-<version>-<n>`` — ``<version>`` may itself contain
#: dashes (preview builds stamp ``0.3.9-41``), so the counter is the final
#: ``-<digits>`` group. ``<version>`` is limited to the characters
#: ``_sanitize_version`` emits, and the counter must be the whole tail:
#: an in-progress snapshot (``…backup-<version>-<n>.part-<pid>``) left
#: behind by a crash mid-copy must never be listed as a backup.
_BACKUP_SUFFIX_RE = re.compile(r"\.backup-(?P<version>[A-Za-z0-9._-]+)-(?P<n>\d+)$")

#: Temp name a snapshot is copied into before ``os.replace`` to its final
#: name (see ``snapshot_before_migration``).
_PARTIAL_SUFFIX_RE = re.compile(r"\.part-\d+$")


def _sanitize_version(version: str) -> str:
    """Version string → filesystem-safe fragment (defense in depth; real
    versions are semver and already safe)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(version).strip()) or "unknown"
    return safe[:64]


def list_backups(db_path: str) -> list[str]:
    """All backup files for ``db_path``, newest first (mtime desc)."""
    directory = os.path.dirname(os.path.abspath(db_path)) or "."
    base = os.path.basename(db_path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out = []
    for name in names:
        if not name.startswith(base + ".backup-"):
            continue
        if _PARTIAL_SUFFIX_RE.search(name):
            continue
        if not _BACKUP_SUFFIX_RE.search(name[len(base):]):
            continue
        out.append(os.path.join(directory, name))
    out.sort(key=lambda p: (_mtime(p), p), reverse=True)
    return out


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def latest_backup(db_path: str) -> dict | None:
    """Newest backup as ``{"path", "created_at", "size_bytes"}`` or None."""
    backups = list_backups(db_path)
    if not backups:
        return None
    path = backups[0]
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"path": path, "created_at": st.st_mtime, "size_bytes": st.st_size}


def _next_counter(db_path: str, safe_version: str) -> int:
    """Next free ``<n>`` for this version so a re-run never overwrites an
    earlier snapshot of the same version."""
    base = os.path.basename(db_path)
    prefix = f"{base}.backup-{safe_version}-"
    highest = 0
    for path in list_backups(db_path):
        name = os.path.basename(path)
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def stale_partial_backups(db_path: str) -> list[str]:
    """``<db>.backup-<version>-<n>.part-<pid>`` files whose writer is gone.

    ``snapshot_before_migration`` copies into such a temp name and renames it
    into place at the end; a crash, kill or power loss mid-copy leaves the
    torn file behind. Those are never valid backups (``list_backups`` skips
    them) and are removed by ``prune_backups``. A partial owned by a live
    process is left alone.
    """
    directory = os.path.dirname(os.path.abspath(db_path)) or "."
    base = os.path.basename(db_path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    stale = []
    for name in names:
        if not name.startswith(base + ".backup-"):
            continue
        m = _PARTIAL_SUFFIX_RE.search(name)
        if not m or not _BACKUP_SUFFIX_RE.fullmatch(name[len(base):m.start()]):
            continue
        pid = int(name[m.start() + len(".part-"):])
        if pid != os.getpid() and not _pid_alive(pid):
            stale.append(os.path.join(directory, name))
    return stale


def _pid_alive(pid: int) -> bool:
    import psutil

    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except (OSError, psutil.Error):
        # An uncertain owner must never cause deletion of its partial backup.
        return True


def prune_backups(db_path: str, keep: int = KEEP_BACKUPS) -> list[str]:
    """Delete all but the ``keep`` newest backups, plus torn partial
    snapshots from earlier runs. Returns deleted paths."""
    deleted = []
    for path in stale_partial_backups(db_path):
        try:
            os.remove(path)
            deleted.append(path)
            logger.info("Removed torn partial DB backup %s", path)
        except OSError as exc:
            logger.warning("Could not remove partial DB backup %s: %s", path, exc)
    for path in list_backups(db_path)[keep:]:
        try:
            os.remove(path)
            deleted.append(path)
            logger.info("Pruned old DB backup %s", path)
        except OSError as exc:
            logger.warning("Could not prune old DB backup %s: %s", path, exc)
    return deleted


def snapshot_before_migration(db_path: str, version: str) -> str | None:
    """Snapshot ``db_path`` to ``<db>.backup-<version>-<n>``.

    Returns the backup path, or None when skipped (no DB yet, or DB larger
    than ``MAX_BACKUP_DB_BYTES``). Raises on an actual backup failure so the
    caller can decide (the caller treats that as "continue without a backup",
    logged loudly — a backup problem must not brick startup by itself).
    """
    if not os.path.isfile(db_path):
        logger.debug("No DB at %s yet — nothing to back up", db_path)
        return None
    size = os.path.getsize(db_path)
    if size > MAX_BACKUP_DB_BYTES:
        logger.info(
            "Skipping pre-migration DB backup: %s is %.0f MB (> %.0f MB limit)",
            db_path, size / (1024 * 1024), MAX_BACKUP_DB_BYTES / (1024 * 1024),
        )
        return None

    safe_version = _sanitize_version(version)
    target = f"{db_path}.backup-{safe_version}-{_next_counter(db_path, safe_version)}"
    tmp = f"{target}.part-{os.getpid()}"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            # Online backup: consistent snapshot including WAL contents.
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        src.close()
    os.replace(tmp, target)
    # A same-second rotation must still rank the new file newest.
    try:
        now = time.time()
        os.utime(target, (now, now))
    except OSError:
        pass
    logger.info("Pre-migration DB backup written: %s (%.1f MB)", target, size / (1024 * 1024))
    prune_backups(db_path)
    return target
