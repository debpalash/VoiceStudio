"""
Job-metadata persistence — Phase 2.1 (ROADMAP.md).

Stores `jobs` (state machine) + `job_events` (SSE tail) so tasks survive
restart and SSE clients can reconnect with ?after_seq=N and get a replay.

This module intentionally does NOT persist the work itself — the async queue
+ in-memory dispatcher stay as-is. That's Phase 4.5 ("step-level resumability").
Here we just keep the metadata honest across restarts:

    • server crash mid-job       → next startup marks it failed with a clear message
    • browser reload mid-stream  → reconnect and replay the SSE tail from disk
    • UI post-restart            → queryable `jobs` table fills the batch-queue view

The tables (`jobs`, `job_events`) live in `core/db.py:_BASE_SCHEMA`.

Retention: we cap `job_events` per job (default 500 rows) by trimming the
oldest on every insert above the cap. Keeps the DB bounded. Progress that must
outlive the cap (a longform job's finished chapters, #3562) is kept in the
job's `meta_json` instead, written in the same transaction as its event.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core.db import db_conn

logger = logging.getLogger("omnivoice.jobs")

# Per-job event cap. Above this, the oldest row is dropped on every insert.
_EVENT_CAP_PER_JOB = 500


# ── Lifecycle ──────────────────────────────────────────────────────────────


def create(job_id: str, *, type: str, project_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs "
            "(id, type, project_id, status, created_at, updated_at, meta_json) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, type, project_id, now, now, json.dumps(meta or {})),
        )


def mark_running(job_id: str) -> None:
    _update_status(job_id, "running")


def mark_done(job_id: str) -> None:
    _update_status(job_id, "done", finished=True)


def mark_failed(job_id: str, error: str) -> None:
    _update_status(job_id, "failed", finished=True, error=error)


def mark_cancelled(job_id: str) -> None:
    _update_status(job_id, "cancelled", finished=True)


def retire_if_active(job_id: str, status: str, error: Optional[str] = None) -> bool:
    """Move a still ``pending``/``running`` job to a terminal ``status``.

    For exit paths that cannot know whether the job already settled (a
    cancelled or closed response): a job that already finished, failed or was
    cancelled keeps its terminal status. Returns True if a row was changed.
    """
    now = time.time()
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, updated_at=?, finished_at=?, error=? "
            "WHERE id=? AND status IN ('pending', 'running')",
            (status, now, now, error, job_id),
        )
        return cur.rowcount > 0


def _update_status(job_id: str, status: str, *, finished: bool = False, error: Optional[str] = None) -> None:
    now = time.time()
    with db_conn() as conn:
        if finished:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=?, finished_at=?, error=? WHERE id=?",
                (status, now, now, error, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (status, now, job_id),
            )


# ── Events ─────────────────────────────────────────────────────────────────


def append_event(job_id: str, payload: str, *, completed_chapter: Optional[int] = None) -> int:
    """Persist an SSE payload and return its per-job sequence number.

    ``completed_chapter`` (a longform ``chapter`` event) also records that
    chapter index in the job's metadata, in the same transaction, so resume
    progress survives the event cap (#3562).
    """
    with db_conn() as conn:
        row = conn.execute(
            "INSERT INTO job_events (job_id, seq, created_at, payload) "
            "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ? "
            "FROM job_events WHERE job_id = ? RETURNING seq",
            (job_id, time.time(), payload, job_id),
        ).fetchone()
        seq = int(row["seq"])
        if completed_chapter is not None:
            # Before the trim below, so seeding still sees the retained tail.
            _record_completed_chapter(conn, job_id, completed_chapter)
        conn.execute(
            "DELETE FROM job_events WHERE job_id = ? AND seq NOT IN "
            "(SELECT seq FROM job_events WHERE job_id = ? "
            "ORDER BY seq DESC LIMIT ?)",
            (job_id, job_id, _EVENT_CAP_PER_JOB),
        )
    return seq


# ── Longform chapter progress (#3562) ─────────────────────────────────────

_COMPLETED_CHAPTERS = "completed_chapters"


def _chapter_index(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _load_meta(raw) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _recorded_chapters(meta: dict) -> Optional[set[int]]:
    """The durable completed-chapter set, or ``None`` when never recorded."""
    done = meta.get(_COMPLETED_CHAPTERS)
    if not isinstance(done, list):
        return None
    return {i for i in map(_chapter_index, done) if i is not None}


def _chapters_in_events(conn, job_id: str) -> set[int]:
    """Distinct indices of the retained ``chapter`` events (errors excluded)."""
    done: set[int] = set()
    rows = conn.execute("SELECT payload FROM job_events WHERE job_id = ?", (job_id,))
    for row in rows:
        try:
            event = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "chapter":
            index = _chapter_index(event.get("index"))
            if index is not None:
                done.add(index)
    return done


def _record_completed_chapter(conn, job_id: str, index: int) -> None:
    if _chapter_index(index) is None:
        return
    row = conn.execute("SELECT meta_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return  # no job row: the event tail is all there is
    meta = _load_meta(row["meta_json"])
    done = _recorded_chapters(meta)
    if done is None:
        # First record for this job — including a job an older build started:
        # seed from the events still retained.
        done = _chapters_in_events(conn, job_id)
    done.add(index)
    meta[_COMPLETED_CHAPTERS] = sorted(done)
    conn.execute("UPDATE jobs SET meta_json = ? WHERE id = ?", (json.dumps(meta), job_id))


def completed_chapters(job_id: str) -> set[int]:
    """Indices of a longform job's finished chapters.

    Read from the job's durable record; a job that never recorded one (an
    older build) falls back to its retained event tail, which cannot recover
    history the cap already pruned.
    """
    with db_conn() as conn:
        row = conn.execute("SELECT meta_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        done = _recorded_chapters(_load_meta(row["meta_json"])) if row is not None else None
        return done if done is not None else _chapters_in_events(conn, job_id)


def events_since(job_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    """Return `[{seq, created_at, payload}]` for events with seq > after_seq."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT seq, created_at, payload FROM job_events "
            "WHERE job_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (job_id, after_seq, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Queries ────────────────────────────────────────────────────────────────


def get(job_id: str) -> Optional[dict]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(*, status: Optional[str] = None, project_id: Optional[str] = None,
              types: Optional[tuple[str, ...]] = None, limit: int = 100) -> list[dict]:
    """List jobs, newest first. Filter by status (e.g. `active` = running+pending) or project, with type filtering before the row limit."""
    where = []
    params = []
    if status == "active":
        where.append("status IN ('pending', 'running')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if types is not None:
        if not types:
            return []
        where.append("type IN (" + ",".join("?" for _ in types) + ")")
        params.extend(types)
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Startup recovery ──────────────────────────────────────────────────────


def sweep_orphans_on_startup() -> int:
    """Any job marked `pending` or `running` when the server starts is orphaned
    (previous process died before finishing). Flip to `failed` with a clear
    error so the UI shows the right state instead of a fake spinner.

    Returns the number of jobs swept.
    """
    msg = "Job was interrupted by a server restart. Re-run from the task's project to continue."
    now = time.time()
    with db_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='failed', updated_at=?, finished_at=?, error=? "
            "WHERE status IN ('pending', 'running')",
            (now, now, msg),
        )
        n = cur.rowcount
    if n:
        logger.info("Job sweep: marked %d orphaned job(s) as failed after restart.", n)
    return n


def has_active() -> bool:
    """Whether any persisted job is still queued or running in this process."""
    with db_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM jobs WHERE status IN ('pending', 'running') LIMIT 1"
        ).fetchone() is not None


from core import activity as _activity  # noqa: E402

_activity.register_probe("jobs", has_active)
