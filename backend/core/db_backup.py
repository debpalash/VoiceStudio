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

_BACKUP_SUFFIX_RE = re.compile(r"[.]backup-(?:[A-Za-z0-9._-])+-(?:[0-9]+)\Z")
_PART_FILE_END = re.compile(r"[.]part-(?P<owner>[0-9]+)\Z")


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
        if _PART_FILE_END.search(name):
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


def _reserve_snapshot(db_path: str, safe_version: str) -> tuple[str, str]:
    """Claim a counter atomically across threads/processes before copying.

    Reservations are not backup files. An abandoned reservation consumes one
    counter but never gets offered as recovery data or overwritten by a retry.
    """
    counter = _next_counter(db_path, safe_version)
    while True:
        target = f"{db_path}.backup-{safe_version}-{counter}"
        reservation = target + ".reserve"
        try:
            fd = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            counter += 1
            continue
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))  # ownership for stale_reservations
        finally:
            os.close(fd)
        if os.path.exists(target):
            os.unlink(reservation)
            counter += 1
            continue
        return target, reservation


#: Fallback only for a reservation whose owner PID was never recorded (a writer
#: that died between creating the file and writing its PID).
_RESERVATION_MAX_AGE_S = 24 * 3600


def _reservation_owner(path: str) -> int | None:
    try:
        with open(path, "r", encoding="ascii") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def stale_reservations(db_path: str) -> list[str]:
    """``.reserve`` markers whose writer is confirmed gone.

    A reservation owned by a live process (including this one, other threads)
    is never listed, however old: a snapshot can outlive a long suspend, and
    removing its marker would let another startup claim the same counter.
    ``_pid_alive`` errs toward "alive". Only a marker with no recorded owner
    falls back to an age check."""
    directory = os.path.dirname(os.path.abspath(db_path)) or "."
    base = os.path.basename(db_path)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    stale = []
    now = time.time()
    for name in names:
        if not (name.startswith(base + ".backup-") and name.endswith(".reserve")):
            continue
        if not _BACKUP_SUFFIX_RE.fullmatch(name[: -len(".reserve")][len(base):]):
            continue
        path = os.path.join(directory, name)
        owner = _reservation_owner(path)
        if owner is not None:
            if owner == os.getpid() or _pid_alive(owner):
                continue
            stale.append(path)
        elif now - _mtime(path) > _RESERVATION_MAX_AGE_S:
            stale.append(path)
    return stale


def stale_partial_backups(db_path: str) -> list[str]:
    """Find unfinished snapshots that no running writer can still own."""
    folder = os.path.dirname(os.path.abspath(db_path)) or "."
    db_name = os.path.basename(db_path)
    try:
        directory_entries = os.listdir(folder)
    except OSError:
        return []

    abandoned: list[str] = []
    required_prefix = f"{db_name}.backup-"
    this_pid = os.getpid()
    for entry in directory_entries:
        part_end = _PART_FILE_END.search(entry)
        if not entry.startswith(required_prefix) or part_end is None:
            continue

        completed_name = entry[:part_end.start()]
        completed_tail = completed_name[len(db_name):]
        if _BACKUP_SUFFIX_RE.fullmatch(completed_tail) is None:
            continue

        writer_pid = int(part_end.group("owner"))
        writer_is_active = writer_pid == this_pid or _pid_alive(writer_pid)
        if not writer_is_active:
            abandoned.append(os.path.join(folder, entry))
    return abandoned


def _pid_alive(pid: int) -> bool:
    """Check process ownership without sending a signal to that process."""
    if pid <= 0:
        return False
    import psutil
    try:
        return bool(psutil.pid_exists(pid))
    except (OSError, psutil.Error):
        return True


def prune_backups(db_path: str, keep: int = KEEP_BACKUPS) -> list[str]:
    """Remove abandoned work files and snapshots older than the retention limit."""
    deleted = []
    leftovers = stale_partial_backups(db_path) + stale_reservations(db_path)
    old_backups = list_backups(db_path)[max(0, keep):]
    for path in leftovers + old_backups:
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Could not remove obsolete database backup file %s: %s", path, exc)
            continue
        logger.info("Removed obsolete database backup file: %s", path)
        deleted.append(path)
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
    target, reservation = _reserve_snapshot(db_path, safe_version)
    tmp = f"{target}.part-{os.getpid()}"
    try:
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
                pass  # best effort: the torn temp is pruned later; the backup error below matters more
            raise
        finally:
            src.close()
        os.replace(tmp, target)
        # A same-second rotation must still rank the new file newest.
        try:
            now = time.time()
            os.utime(target, (now, now))
        except OSError:
            pass  # ordering hint only; the snapshot itself is already in place
        logger.info("Pre-migration DB backup written: %s (%.1f MB)", target, size / (1024 * 1024))
        prune_backups(db_path)
        return target
    finally:
        try:
            os.unlink(reservation)
        except OSError:
            pass  # marker already gone; pruning handles any leftover
