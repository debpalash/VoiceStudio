"""Durable task state for remote work.

The local ``core/job_store.py`` marks every in-flight job failed on startup,
because a local job died with the process that was running it. Remote tasks
invert that: the control plane is a desktop app the user quits at will, and the
GPU on the other machine keeps rendering regardless. So restart must *recover*
in-flight tasks, not bury them.

The one ordering rule that makes at-least-once delivery safe:

    persist the result, THEN send RESULT_ACK

If the acknowledgement goes first and the server dies before writing, the
worker has been told it may drop its copy — and a forty-minute dub is gone with
no error anywhere. ``commit_result`` writes inside the same transaction that
flips the task to completed, so the ack can only follow a durable fact.
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import time
import uuid
from typing import Callable, Iterable, Iterator, Optional

from core.db import db_conn
from core.path_security import UnsafePath, resolve_within, safe_filename
from worker.clock import resolve
from worker.errors import ErrorClass, WorkerError
from worker.deadlines import Deadlines
from worker.lifecycle import Attempt, AttemptState, PriorityClass, Task, TaskState
from worker.params import INPUT_PARAM_KEYS

logger = logging.getLogger("omnivoice.worker")


def _dump_error(error: Optional[WorkerError]) -> Optional[str]:
    return json.dumps(error.to_dict()) if error else None


def _load_error(raw: Optional[str]) -> Optional[WorkerError]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return WorkerError(
            error_class=ErrorClass(data["error_class"]),
            code=data.get("code", "UNKNOWN"),
            message=data.get("message", ""),
            hint=data.get("hint", ""),
        )
    except Exception:
        return None


def _row_to_attempt(row) -> Attempt:
    attempt = Attempt(
        attempt_id=row["id"],
        task_id=row["task_id"],
        worker_id=row["worker_id"],
        session_epoch=int(row["session_epoch"]),
        attempt_number=int(row["attempt_number"]),
        state=AttemptState(row["state"]),
        created_at=float(row["created_at"]),
    )
    if row["deadlines_json"]:
        attempt.deadlines = Deadlines(**json.loads(row["deadlines_json"]))
    attempt.accepted_at = row["accepted_at"]
    attempt.started_at = row["started_at"]
    attempt.finished_at = row["finished_at"]
    attempt.lease_expires_at = row["lease_expires_at"]
    attempt.grace_expires_at = row["grace_expires_at"]
    attempt.progress = float(row["progress"])
    attempt.stage = row["stage"] or ""
    attempt.error = _load_error(row["error_json"])
    return attempt


def _row_to_task(row, attempts: list[Attempt]) -> Task:
    task = Task(
        task_id=row["id"],
        operation=row["operation"],
        engine=row["engine"] or "",
        model_id=row["model_id"] or "",
        params=json.loads(row["params_json"] or "{}"),
        priority=PriorityClass(int(row["priority"])),
        idempotency_key=row["idempotency_key"],
        state=TaskState(row["state"]),
        max_attempts=int(row["max_attempts"]),
        created_at=float(row["created_at"]),
        pinned_worker_id=row["pinned_worker_id"],
    )
    task.attempts = sorted(attempts, key=lambda a: a.attempt_number)
    task.finished_at = row["finished_at"]
    task.deadline_at = row["deadline_at"]
    task.error = _load_error(row["error_json"])
    task.result_ref = row["result_ref"]
    task.excluded_workers = set(json.loads(row["excluded_json"] or "[]"))
    return task


# ── Input artifacts ────────────────────────────────────────────────────────
#
# A worker is another machine. Every file-valued parameter — reference audio
# for a clone, a source video for a dub — lives in ``VOICES_DIR`` or a tempdir
# on the *control plane*, so sending its path is sending a string that names
# nothing on the far side. That is why remote cloning could not work: the
# assignment carried ``ref_audio=/Users/…/voices/x.wav`` and the worker either
# failed to open it or, worse, rendered with the default voice.
#
# Staging copies those files into the artifact directory the control plane
# already serves over ``DownloadArtifact``, which refuses anything outside it.
# The copy is named by the SHA-256 of its contents, so cloning the same voice
# a hundred times keeps exactly one copy on disk and lets the worker's own
# cache skip the transfer entirely on every clone after the first.

# ``INPUT_PARAM_KEYS`` (imported above) is shared with the worker, which
# refuses any file value that did not arrive as a declared input.

# Where staged inputs live under the artifact root, and the key under which a
# task records what was staged for it. The record is what makes the purge
# exact: an input is deletable only when no surviving task still refers to it.
INPUTS_DIRNAME = "inputs"


def artifact_id_for(name: str) -> str:
    """The id a staged input is known by, everywhere.

    This is a PROTOCOL identifier, not a local path: it is persisted in
    ``params_json``, handed to remote workers over gRPC, and matched against
    what a later sweep finds on disk. ``os.path.join`` made it OS-specific, so
    a Windows control plane stored and shipped ``inputs\\<sha>.wav`` — which a
    Linux worker cannot resolve, and which stops matching the moment the same
    data directory is opened on another OS. Always ``/``; ``resolve_within``
    already treats both separators as structural, so resolution is unaffected.
    """
    return f"{INPUTS_DIRNAME}/{name}"


def normalize_artifact_id(artifact_id: str) -> str:
    """Compare ids written by any host on equal terms.

    Rows staged by a Windows control plane before this was canonicalised carry
    a backslash. The sweeper decides whether a file on disk is still
    referenced by comparing ids, so without this an upgraded install would
    read every legacy row as unreferenced and delete inputs that surviving
    tasks still point at.
    """
    return (artifact_id or "").replace("\\", "/")
INPUTS_PARAM_KEY = "inputs"

_HASH_CHUNK_BYTES = 1024 * 1024
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_CONTENT_ARTIFACT = re.compile(r"^([0-9a-f]{64})(?:\.[A-Za-z0-9]{1,8})?$")


class InputStagingError(RuntimeError):
    """A task input could not be staged for transfer to a worker.

    Raised rather than swallowed: a clone whose reference audio silently went
    missing does not fail, it renders someone else's voice.
    """


def _fsync_parent_directory(directory: str) -> None:
    """Persist directory entry changes where the platform supports it."""
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        descriptor = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in unsupported:
            raise
    finally:
        os.close(descriptor)


def _fsync_file(path: str) -> None:
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _durable_makedirs(directory: str) -> None:
    """Create a directory hierarchy and persist each parent entry."""
    target = os.path.abspath(directory)
    missing: list[str] = []
    current = target
    while not os.path.isdir(current):
        if os.path.exists(current):
            raise NotADirectoryError(current)
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for path in reversed(missing):
        try:
            os.mkdir(path)
        except FileExistsError:
            if not os.path.isdir(path):
                raise
        _fsync_parent_directory(os.path.dirname(path) or ".")
    if not missing:
        _fsync_parent_directory(os.path.dirname(target) or ".")


def artifact_root(*, create_dir: bool = True) -> str:
    """The directory the control plane serves artifacts from.

    Imported lazily: ``worker.service`` owns the layout, and a module-level
    import here would tie the durable store to the lifecycle module that
    starts the gRPC server.
    """
    from worker.service import paths  # noqa: PLC0415 — layout owner, not a dependency

    root = paths()["artifacts"]
    if create_dir:
        _durable_makedirs(os.path.join(root, INPUTS_DIRNAME))
    return root


def _extension(source: str) -> str:
    """The source extension when it is a plain one, else nothing.

    Kept for the worker's benefit — soundfile sniffs content, but an engine
    that shells out to ffmpeg reads the suffix — and sanitised because the
    name is about to become a filesystem path.
    """
    suffix = os.path.splitext(str(source))[1]
    return suffix.lower() if _SAFE_EXTENSION.match(suffix) else ""


def _digest(path: str) -> tuple[str, int]:
    """(sha256, size) read in chunks — a source video is not a bytes object."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(_HASH_CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _staged_entry_matches(path: str, entry: dict) -> bool:
    """Verify staged bytes against both metadata and their content address."""
    if not os.path.isfile(path):
        return False
    artifact_id = str(entry.get("artifact_id") or "")
    portable_name = artifact_id.replace("\\", "/").rsplit("/", 1)[-1]
    named = _CONTENT_ARTIFACT.fullmatch(portable_name)
    if named is None:
        return False
    try:
        actual_digest, actual_size = _digest(path)
    except OSError:
        return False
    recorded_digest = str(entry.get("sha256") or "").strip().lower()
    recorded_size = entry.get("size_bytes")
    if recorded_digest and actual_digest != recorded_digest:
        return False
    if recorded_size is not None:
        try:
            if actual_size != int(recorded_size):
                return False
        except (TypeError, ValueError):
            return False
    if actual_digest != named.group(1):
        return False
    # Backfill metadata on a legacy row once its content address proves it.
    entry["sha256"] = actual_digest
    entry["size_bytes"] = actual_size
    return True


def stage_input(
    source: str, *, root: Optional[str] = None, now: Optional[float] = None
) -> dict:
    """Copy one input into the artifact store, keyed by its content hash.

    Returns the record that ends up on the task row. ``source`` is kept in it
    so a local fallback still has the original file, and stripped before the
    record reaches the wire.
    """
    stamp = resolve(now)
    base = root or artifact_root()
    try:
        digest, size = _digest(source)
    except OSError as exc:
        raise InputStagingError(
            f"Could not read the task input {source!r}: {exc}"
        ) from exc

    artifact_id = artifact_id_for(f"{digest}{_extension(source)}")
    try:
        destination = resolve_within(base, artifact_id)
    except UnsafePath as exc:  # pragma: no cover — the id is ours, hex only
        raise InputStagingError(
            f"Refusing to stage {source!r} outside the artifact store"
        ) from exc

    partial = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.part"
    )
    try:
        _durable_makedirs(str(destination.parent))
        expected = {
            "artifact_id": artifact_id,
            "sha256": digest,
            "size_bytes": size,
        }
        if not _staged_entry_matches(str(destination), expected):
            shutil.copyfile(source, partial)
            copied_digest, copied_size = _digest(str(partial))
            if copied_digest != digest or copied_size != size:
                raise InputStagingError(
                    f"The task input {source!r} changed while it was being staged."
                )
            _fsync_file(str(partial))
            os.replace(partial, destination)
            _fsync_parent_directory(str(destination.parent))
        # Freshness, not decoration: the purge dates an unreferenced input by
        # its mtime, so re-using a staged voice has to renew it.
        os.utime(destination, (stamp, stamp))
        _fsync_file(str(destination))
        _fsync_parent_directory(str(destination.parent))
    except OSError as exc:
        raise InputStagingError(
            f"Could not stage the task input {source!r}: {exc}"
        ) from exc
    finally:
        try:
            os.remove(partial)
        except FileNotFoundError:
            pass
        except OSError:
            logger.debug(
                "Could not remove the staged-input partial %s",
                partial,
                exc_info=True,
            )

    filename = os.path.basename(str(source)) or f"{digest}{_extension(source)}"
    return {
        "artifact_id": artifact_id,
        "path": str(destination),
        "source": str(source),
        "filename": filename,
        "sha256": digest,
        "size_bytes": size,
        "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
    }


def _iter_input_values(params: dict) -> Iterator[tuple[str, Optional[int], str]]:
    """``(key, index, value)`` for every parameter that could name a file."""
    for key in INPUT_PARAM_KEYS:
        value = params.get(key)
        if isinstance(value, str):
            yield key, None, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    yield key, index, item


def ensure_staged(
    task: Task, *, root: Optional[str] = None, now: Optional[float] = None
) -> list[dict]:
    """Stage every file-valued parameter of *task*, once.

    Idempotent by design — it runs at submission (so the durable row records
    what a later purge must keep) and again at dispatch (so a task built
    without the store, or a scheduler running unpersisted, still gets inputs
    the worker can fetch). Already-staged keys are skipped, so the second call
    does no I/O.
    """
    params = task.params if isinstance(task.params, dict) else {}
    recorded = params.get(INPUTS_PARAM_KEY)
    entries: list[dict] = (
        [e for e in recorded if isinstance(e, dict)]
        if isinstance(recorded, list)
        else []
    )
    base = root or artifact_root()
    if entries:
        # A task may have been staged when it was submitted under the default
        # store, then dispatched by a servicer configured with another store.
        # Recorded metadata is not proof that this servicer can serve it.
        refreshed: list[dict] = []
        for entry in entries:
            artifact_id = str(entry.get("artifact_id") or "")
            try:
                path = resolve_within(base, artifact_id)
                available = bool(
                    artifact_id and _staged_entry_matches(str(path), entry)
                )
            except UnsafePath:
                available = False
            if available:
                refreshed.append(entry)
                continue
            source = str(entry.get("source") or "")
            if source and os.path.isfile(source):
                replacement = stage_input(source, root=base, now=now)
                replacement.update(key=entry.get("key"), index=entry.get("index"))
                refreshed.append(replacement)
            else:
                raise InputStagingError(
                    f"The staged task input {artifact_id!r} is unavailable in this artifact store."
                )
        entries = refreshed
        params[INPUTS_PARAM_KEY] = entries
    covered = {(e.get("key"), e.get("index")) for e in entries}

    for key, index, value in _iter_input_values(params):
        if (key, index) in covered or not value:
            continue
        # Not every value of these keys is a file: an engine may take a voice
        # id here. Only what exists on this disk is an input.
        if not os.path.isfile(value):
            continue
        entry = stage_input(value, root=base, now=now)
        entry["key"] = key
        entry["index"] = index
        entries.append(entry)
        covered.add((key, index))

    if entries:
        params[INPUTS_PARAM_KEY] = entries
        task.params = params
    return entries


def _durable_params(params: dict) -> dict:
    """Parameters safe to persist after inputs have been staged.

    The live task keeps original paths for a possible local fallback, but the
    durable row needs only content-addressed artifact ids. In particular, it
    must never retain a user's home path in either the operation parameters or
    the staging metadata.
    """
    durable = json.loads(json.dumps(params))
    entries = durable.get(INPUTS_PARAM_KEY)
    if not isinstance(entries, list):
        return durable
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        index = entry.get("index")
        artifact_id = entry.get("artifact_id")
        if isinstance(key, str) and isinstance(artifact_id, str):
            if index is None:
                durable[key] = artifact_id
            elif isinstance(durable.get(key), list) and isinstance(index, int):
                if 0 <= index < len(durable[key]):
                    durable[key][index] = artifact_id
        entry.pop("source", None)
        entry.pop("path", None)
    return durable


def _referenced_artifacts(conn) -> set[str]:
    """Every staged input still named by a surviving task row."""
    referenced: set[str] = set()
    for row in conn.execute("SELECT params_json FROM remote_tasks").fetchall():
        try:
            params = json.loads(row["params_json"] or "{}")
            entries = params.get(INPUTS_PARAM_KEY) or []
        except (ValueError, AttributeError):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("artifact_id"):
                referenced.add(normalize_artifact_id(str(entry["artifact_id"])))
    return referenced


def _purge_result_directories(
    task_ids: Iterable[str], *, root: Optional[str] = None
) -> tuple[list[str], int]:
    """Delete result directories before their owning rows become unreachable."""
    task_ids = list(task_ids)
    try:
        base = root or artifact_root(create_dir=False)
    except Exception:  # pragma: no cover — no data dir at all
        logger.debug("No artifact root to purge", exc_info=True)
        return [], 0
    if not os.path.isdir(base):
        return task_ids, 0

    cleaned: list[str] = []
    removed = 0
    for task_id in task_ids:
        try:
            path = resolve_within(base, safe_filename(task_id))
        except UnsafePath:
            continue
        if not os.path.exists(path):
            try:
                _fsync_parent_directory(base)
            except OSError:
                logger.debug(
                    "Could not persist task artifact cleanup at %s",
                    base,
                    exc_info=True,
                )
                continue
            cleaned.append(task_id)
            continue
        if not os.path.isdir(path):
            logger.warning("Refusing to purge non-directory task artifact %s", path)
            continue
        try:
            shutil.rmtree(path)
        except OSError:
            logger.debug("Could not purge task artifacts at %s", path, exc_info=True)
            continue
        try:
            _fsync_parent_directory(base)
        except OSError:
            # The bytes are gone from this process's view, but the directory
            # deletion is not a crash-durable fact yet. Keep the DB row as the
            # retry index until a later sweep can establish that barrier.
            logger.debug(
                "Could not persist task artifact cleanup at %s",
                base,
                exc_info=True,
            )
            continue
        cleaned.append(task_id)
        removed += 1
    return cleaned, removed


def purge_artifacts(
    task_ids: Iterable[str], referenced: set[str], *, cutoff: float, root: Optional[str] = None
) -> int:
    """Delete the results of purged tasks and every input nothing points at.

    Both directions, deliberately: results are attempt-scoped and die with
    their task, while a content-hashed input is shared, so it may only go once
    no surviving task refers to it *and* it is older than the same cutoff the
    rows were judged by. Nothing here raises — a purge that fails is a disk
    that stays fuller than we wanted, not a failed request.
    """
    _cleaned, removed = _purge_result_directories(task_ids, root=root)
    try:
        base = root or artifact_root(create_dir=False)
    except Exception:  # pragma: no cover — no data dir at all
        logger.debug("No artifact root to purge", exc_info=True)
        return 0
    if not os.path.isdir(base):
        return 0

    inputs_dir = os.path.join(base, INPUTS_DIRNAME)
    try:
        names = os.listdir(inputs_dir)
    except OSError:
        return removed
    for name in names:
        if artifact_id_for(name) in referenced:
            continue
        path = os.path.join(inputs_dir, name)
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) >= cutoff:
                continue
            os.remove(path)
            removed += 1
        except OSError:
            logger.debug("Could not purge the staged input %s", name, exc_info=True)
    return removed


# ── Writes ─────────────────────────────────────────────────────────────────


def create(task: Task, *, project_id: Optional[str] = None, now: Optional[float] = None) -> Task:
    """Persist a new task.

    Idempotent on ``idempotency_key``: a client that retries its HTTP request
    gets the original task back rather than a second render of the same text.

    ``pinned_worker_id`` deliberately follows core.db's additive schema
    reconciliation instead of alembic: remote recovery also runs in bundled
    installs where alembic may be unavailable, and the nullable column is a
    backward-compatible affinity fact rather than a data transformation.

    Inputs are staged before the row is written, so the durable record names
    the artifacts the task owns. Persisting first would leave a task whose
    reference audio no purge can account for.
    """
    stamp = resolve(now)
    if task.idempotency_key:
        existing = get_by_idempotency_key(task.idempotency_key)
        if existing is not None:
            return existing
    ensure_staged(task, now=stamp)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO remote_tasks "
            "(id, idempotency_key, operation, engine, model_id, params_json, priority, state, "
            " max_attempts, excluded_json, project_id, created_at, updated_at, deadline_at, pinned_worker_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.task_id,
                task.idempotency_key,
                task.operation,
                task.engine,
                task.model_id,
                json.dumps(_durable_params(task.params)),
                int(task.priority),
                task.state.value,
                task.max_attempts,
                json.dumps(sorted(task.excluded_workers)),
                project_id,
                stamp,
                stamp,
                task.deadline_at,
                task.pinned_worker_id,
            ),
        )
    return task


def _upsert_attempts(conn, task: Task) -> None:
    """Write every attempt, inserting the ones we have not seen before.

    Upsert rather than UPDATE in both writers: a blind UPDATE silently drops an
    attempt whose row does not exist yet, which loses the audit trail for the
    exact case that matters — a task whose first persisted state is its
    completion.
    """
    for attempt in task.attempts:
        conn.execute(
            "INSERT INTO remote_task_attempts "
            "(id, task_id, worker_id, session_epoch, attempt_number, state, progress, stage, "
            " error_json, created_at, accepted_at, started_at, finished_at, lease_expires_at, "
            " grace_expires_at, deadlines_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state=excluded.state, progress=excluded.progress, "
            " stage=excluded.stage, error_json=excluded.error_json, accepted_at=excluded.accepted_at, "
            " started_at=excluded.started_at, finished_at=excluded.finished_at, "
            " lease_expires_at=excluded.lease_expires_at, grace_expires_at=excluded.grace_expires_at, "
            " deadlines_json=excluded.deadlines_json",
            (
                attempt.attempt_id,
                attempt.task_id,
                attempt.worker_id,
                attempt.session_epoch,
                attempt.attempt_number,
                attempt.state.value,
                attempt.progress,
                attempt.stage,
                _dump_error(attempt.error),
                attempt.created_at,
                attempt.accepted_at,
                attempt.started_at,
                attempt.finished_at,
                attempt.lease_expires_at,
                attempt.grace_expires_at,
                json.dumps(attempt.deadlines.to_dict()) if attempt.deadlines else None,
            ),
        )


def _save_with_conn(conn, task: Task, *, stamp: float) -> None:
    conn.execute(
        "UPDATE remote_tasks SET state=?, excluded_json=?, error_json=?, result_ref=?, "
        "updated_at=?, deadline_at=?, finished_at=?, pinned_worker_id=? WHERE id=?",
        (
            task.state.value,
            json.dumps(sorted(task.excluded_workers)),
            _dump_error(task.error),
            task.result_ref,
            stamp,
            task.deadline_at,
            task.finished_at,
            task.pinned_worker_id,
            task.task_id,
        ),
    )
    _upsert_attempts(conn, task)


def save(task: Task, *, now: Optional[float] = None) -> None:
    """Write the whole task + attempt graph.

    Deliberately a full rewrite rather than a diff: the graph is tiny, and a
    partial update is how a state machine and its persistence drift apart.
    """
    stamp = resolve(now)
    with db_conn() as conn:
        _save_with_conn(conn, task, stamp=stamp)


def save_many(
    tasks: Iterable[Task],
    *,
    now: Optional[float] = None,
    before_save: Optional[Callable[[object], None]] = None,
) -> None:
    """Persist one reconciliation generation atomically."""
    stamp = resolve(now)
    with db_conn() as conn:
        if before_save is not None:
            before_save(conn)
        for task in tasks:
            _save_with_conn(conn, task, stamp=stamp)


def commit_result(
    task: Task, *, result_json: Optional[dict] = None, now: Optional[float] = None
) -> None:
    """Durably record a completed task. Must return before RESULT_ACK is sent.

    Everything lands in one transaction, so there is no window in which the
    task looks complete but its result reference is missing.
    """
    stamp = resolve(now)
    with db_conn() as conn:
        conn.execute(
            "UPDATE remote_tasks SET state=?, result_ref=?, result_json=?, updated_at=?, "
            "finished_at=?, error_json=NULL WHERE id=?",
            (
                task.state.value,
                task.result_ref,
                json.dumps(result_json or {}),
                stamp,
                task.finished_at or stamp,
                task.task_id,
            ),
        )
        _upsert_attempts(conn, task)


def is_committed(task_id: str) -> bool:
    """Has this task already been durably committed?

    The guard for a redelivered result after a control-plane restart: the
    in-memory task graph is gone, but the fact is on disk.
    """
    with db_conn() as conn:
        row = conn.execute(
            "SELECT state, result_ref FROM remote_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return bool(row and row["state"] == TaskState.COMPLETED.value)


# ── Reads ──────────────────────────────────────────────────────────────────


def _attempts_for(conn, task_id: str) -> list[Attempt]:
    rows = conn.execute(
        "SELECT * FROM remote_task_attempts WHERE task_id = ? ORDER BY attempt_number ASC",
        (task_id,),
    ).fetchall()
    return [_row_to_attempt(r) for r in rows]


def get(task_id: str) -> Optional[Task]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM remote_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return _row_to_task(row, _attempts_for(conn, task_id))


def get_by_idempotency_key(key: str) -> Optional[Task]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM remote_tasks WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_task(row, _attempts_for(conn, row["id"]))


def load_unfinished() -> list[Task]:
    """Every task that was still live when the control plane stopped.

    Called at startup. These are NOT failed — the workers holding them may
    still be rendering, and reconciliation decides each one's fate once the
    workers reconnect.
    """
    live = json.dumps([s.value for s in TaskState if not s.terminal])
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM remote_tasks
            WHERE state IN (SELECT value FROM json_each(?))
            ORDER BY priority ASC, created_at ASC
            """,
            (live,),
        ).fetchall()
        return [_row_to_task(r, _attempts_for(conn, r["id"])) for r in rows]


def list_tasks(*, states: Optional[Iterable[TaskState]] = None, limit: int = 100) -> list[Task]:
    if states:
        sql = """
            SELECT * FROM remote_tasks
            WHERE state IN (SELECT value FROM json_each(?))
            ORDER BY created_at DESC LIMIT ?
        """
        params = (json.dumps([s.value for s in states]), limit)
    else:
        sql = "SELECT * FROM remote_tasks ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_task(r, _attempts_for(conn, r["id"])) for r in rows]


def purge_finished(
    *,
    older_than_seconds: float = 7 * 24 * 3600,
    now: Optional[float] = None,
    root: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """Drop old finished tasks — rows *and* the bytes they own.

    Rows only was a leak with no ceiling: every remote render leaves a result
    artifact on disk, and every remote clone leaves a copy of the reference
    audio. Neither was ever deleted, so the feature grew the user's disk for
    as long as they used it.
    """
    if limit is not None and limit <= 0:
        return 0
    cutoff = resolve(now) - older_than_seconds
    terminal = json.dumps([s.value for s in TaskState if s.terminal])
    with db_conn() as conn:
        doomed = [
            row["id"]
            for row in conn.execute(
                """
                SELECT id FROM remote_tasks
                WHERE state IN (SELECT value FROM json_each(?))
                  AND finished_at < ?
                ORDER BY finished_at ASC, id ASC
                LIMIT ?
                """,
                (terminal, cutoff, -1 if limit is None else int(limit)),
            ).fetchall()
        ]
    # Results are attempt-scoped. Delete them before their task rows, so a
    # crash or transient Windows lock cannot erase the only index from which a
    # future sweep could find those bytes.
    cleaned, _artifacts_removed = _purge_result_directories(doomed, root=root)
    with db_conn() as conn:
        eligible: list[str] = []
        if cleaned:
            eligible = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM remote_tasks
                    WHERE id IN (SELECT value FROM json_each(?))
                      AND state IN (SELECT value FROM json_each(?))
                      AND finished_at < ?
                    """,
                    (json.dumps(cleaned), terminal, cutoff),
                ).fetchall()
            ]
        removed = 0
        if eligible:
            conn.execute(
                """
                DELETE FROM remote_task_attempts
                WHERE task_id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(eligible),),
            )
            cur = conn.execute(
                """
                DELETE FROM remote_tasks
                WHERE id IN (SELECT value FROM json_each(?))
                """,
                (json.dumps(eligible),),
            )
            removed = cur.rowcount
        # Read the survivors inside the same transaction that deleted the
        # rows: an input is only unreferenced relative to what is left.
        referenced = _referenced_artifacts(conn)
    # Shared content-addressed inputs remain discoverable without their old
    # task row, so they can be swept after the transaction.
    purge_artifacts((), referenced, cutoff=cutoff, root=root)
    return removed


__all__ = [
    "INPUTS_DIRNAME",
    "artifact_id_for",
    "normalize_artifact_id",
    "INPUTS_PARAM_KEY",
    "INPUT_PARAM_KEYS",
    "InputStagingError",
    "artifact_root",
    "commit_result",
    "create",
    "ensure_staged",
    "get",
    "get_by_idempotency_key",
    "is_committed",
    "list_tasks",
    "load_unfinished",
    "purge_artifacts",
    "purge_finished",
    "save",
    "stage_input",
]
