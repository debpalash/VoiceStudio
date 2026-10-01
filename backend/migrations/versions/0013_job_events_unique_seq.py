"""Make job_events(job_id, seq) unique, renumbering duplicates first

Revision ID: 0013_job_events_unique_seq
Revises: 0012_call_sessions
Create Date: 2026-10-01 00:00:00.000000

``job_store.append_event`` used to assign the SSE sequence number with a
``SELECT COALESCE(MAX(seq), 0)`` followed by a separate ``INSERT``. Each call
opens its own connection and WAL lets a reader run while another writer is
mid-transaction, so two concurrent callers could read the same MAX and both
insert it. The index on ``(job_id, seq)`` was not UNIQUE, so the duplicates
landed silently and an SSE client reconnecting with ``?after_seq=N`` replayed
one event twice and skipped another.

The write path is atomic now. This makes the invariant enforced by the schema
rather than merely upheld by the caller, and repairs databases that already
carry duplicates.

Duplicates are RENUMBERED, not deleted: both rows are genuine events with
distinct payloads, and dropping one would lose a line from the replay tail.
Each job is renumbered from its OWN lowest seq, never from 1, so no event's seq
can fall — a job the per-job cap had trimmed keeps its base, and a client
reconnecting with ``?after_seq=N`` cannot end up skipping retained events.

Self-contained and idempotent (guarded by sqlite_master) in the style of 0008
and 0012. ``core/db.py::_ensure_job_events_seq_unique`` performs the identical
repair for installs where alembic cannot run — the dual-path discipline — and
whichever reaches the database first makes the other a no-op.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_job_events_unique_seq"
down_revision: Union[str, None] = "0012_call_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_INDEX = "idx_job_events_job_seq"
_UNIQUE_INDEX = "idx_job_events_job_seq_unique"

# Jobs holding at least one duplicated seq, with the lowest seq each still has.
JOBS_WITH_DUPLICATE_SEQS = """
    SELECT job_id, MIN(seq) AS base
      FROM job_events
     GROUP BY job_id
    HAVING COUNT(*) > COUNT(DISTINCT seq)
"""

# Renumber one job from its own base, in insertion order: the k-th row by `id`
# becomes base + k - 1. `id` is the INTEGER PRIMARY KEY so the count is distinct
# per row, making the result gapless and unique.
#
# Anchored to the job's existing base rather than to 1. Seqs were handed out as
# MAX+1, so they are non-decreasing and the k-th row's old value is at most
# base + k - 1: every seq either rises or stays put, never falls. Rebasing to 1
# would push the retained events of a trimmed job below a reconnecting client's
# `?after_seq=N` cursor, and it would silently skip them.
RENUMBER_ONE_JOB = """
    UPDATE job_events
       SET seq = :base + (SELECT COUNT(*)
                            FROM job_events AS e2
                           WHERE e2.job_id = job_events.job_id
                             AND e2.id <= job_events.id) - 1
     WHERE job_id = :job_id
"""


def _exists(kind: str, name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type=:t AND name=:n"),
        {"t": kind, "n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    if not _exists("table", "job_events"):
        return
    if _exists("index", _UNIQUE_INDEX):
        return  # core/db.py already converged this database

    # Must precede the index: CREATE UNIQUE INDEX over surviving duplicates
    # would abort the migration, and the databases that carry them are exactly
    # the ones this revision exists to repair.
    #
    # Every base is read before anything is written — the UPDATE changes `seq`,
    # so a base still being derived from it mid-statement could drift.
    bind = op.get_bind()
    for job_id, base in bind.execute(sa.text(JOBS_WITH_DUPLICATE_SEQS)).fetchall():
        bind.execute(sa.text(RENUMBER_ONE_JOB), {"base": base, "job_id": job_id})

    if _exists("index", _OLD_INDEX):
        op.drop_index(_OLD_INDEX, table_name="job_events")
    op.create_index(_UNIQUE_INDEX, "job_events", ["job_id", "seq"], unique=True)


def downgrade() -> None:
    if not _exists("table", "job_events"):
        return
    if _exists("index", _UNIQUE_INDEX):
        op.drop_index(_UNIQUE_INDEX, table_name="job_events")
    if not _exists("index", _OLD_INDEX):
        op.create_index(_OLD_INDEX, "job_events", ["job_id", "seq"])
