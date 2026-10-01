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

# Give every row of an affected job a fresh seq, ordered by insertion. `id` is
# the INTEGER PRIMARY KEY, so the count is distinct per row and the result is a
# gapless 1..N in true arrival order. The subquery reads only `id`/`job_id` and
# never `seq`, so SQLite applying earlier row updates partway through the
# statement cannot skew the numbering it produces.
RENUMBER_DUPLICATE_SEQS = """
    UPDATE job_events
       SET seq = (SELECT COUNT(*)
                    FROM job_events AS e2
                   WHERE e2.job_id = job_events.job_id
                     AND e2.id <= job_events.id)
     WHERE job_id IN (SELECT job_id
                        FROM job_events
                       GROUP BY job_id, seq
                      HAVING COUNT(*) > 1)
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
    op.get_bind().execute(sa.text(RENUMBER_DUPLICATE_SEQS))

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
