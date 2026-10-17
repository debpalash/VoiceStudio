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
A seq may only ever rise: each row keeps its own unless a predecessor already
claimed that number, in which case it takes the next one up. A job the per-job
cap had trimmed keeps its base, a stale low seq left behind a higher one is not
pulled down, and a client reconnecting with ``?after_seq=N`` cannot end up
skipping retained events.

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

# Jobs holding at least one duplicated seq.
JOBS_WITH_DUPLICATE_SEQS = """
    SELECT job_id
      FROM job_events
     GROUP BY job_id
    HAVING COUNT(*) > COUNT(DISTINCT seq)
"""

JOB_EVENTS_IN_INSERTION_ORDER = """
    SELECT id, seq FROM job_events WHERE job_id = :job_id ORDER BY id
"""

SET_SEQ = "UPDATE job_events SET seq = :seq WHERE id = :id"


def repaired_seqs(rows):
    """Plan the seq repair for one job's rows, in insertion order.

    Takes (row_id, seq) ordered by `id` and returns the (new_seq, row_id) pairs
    that need writing. Each row keeps its own seq unless a predecessor already
    claimed that number, in which case it takes the next one up:
    ``new = max(seq, previous_new + 1)``.

    A seq may only ever RISE. ``events_since`` serves a reconnecting SSE client
    as ``seq > after_seq``, so lowering a retained event's seq past that cursor
    would make the client skip it for good. Two things push a seq down if the
    repair renumbers from a base: a job the per-job cap has trimmed starts above
    1, and the old writer could land a stale low seq behind a higher one (it
    read MAX, then inserted, and another caller could commit in between), so the
    values are not even guaranteed non-decreasing by `id`. ``(55, 53, 53)``
    renumbered from MIN(seq) becomes ``(53, 54, 55)`` and loses the first event
    for a reader at cursor 55; the running maximum gives ``(55, 56, 57)``.

    Mirrors ``core/db.py::repaired_seqs`` — the dual-path discipline — and
    tests/test_migration_0013_job_events_unique_seq.py holds the two to the
    same output.
    """
    plan = []
    previous = None
    for row_id, seq in rows:
        new = seq if previous is None else max(seq, previous + 1)
        if new != seq:
            plan.append((new, row_id))
        previous = new
    return plan


def _exists(kind: str, name: str) -> bool:
    """Whether sqlite_master holds an object of this type and name."""
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type=:t AND name=:n"),
        {"t": kind, "n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    """Repair duplicate seqs, then constrain (job_id, seq) UNIQUE."""
    if not _exists("table", "job_events"):
        return
    if _exists("index", _UNIQUE_INDEX):
        return  # core/db.py already converged this database

    # Must precede the index: CREATE UNIQUE INDEX over surviving duplicates
    # would abort the migration, and the databases that carry them are exactly
    # the ones this revision exists to repair.
    #
    # Each job's rows are read in full before any of them is written: the plan
    # depends on the seqs it is about to overwrite.
    bind = op.get_bind()
    for (job_id,) in bind.execute(sa.text(JOBS_WITH_DUPLICATE_SEQS)).fetchall():
        rows = bind.execute(
            sa.text(JOB_EVENTS_IN_INSERTION_ORDER), {"job_id": job_id}
        ).fetchall()
        for new_seq, row_id in repaired_seqs(rows):
            bind.execute(sa.text(SET_SEQ), {"seq": new_seq, "id": row_id})

    if _exists("index", _OLD_INDEX):
        op.drop_index(_OLD_INDEX, table_name="job_events")
    op.create_index(_UNIQUE_INDEX, "job_events", ["job_id", "seq"], unique=True)


def downgrade() -> None:
    """Restore the plain index. The renumbering is not undone — the new
    seqs are valid and reversing them would re-introduce the duplicates."""
    if not _exists("table", "job_events"):
        return
    if _exists("index", _UNIQUE_INDEX):
        op.drop_index(_UNIQUE_INDEX, table_name="job_events")
    if not _exists("index", _OLD_INDEX):
        op.create_index(_OLD_INDEX, "job_events", ["job_id", "seq"])
