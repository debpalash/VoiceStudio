"""job_events: one row per (job_id, seq)

Revision ID: 0017_job_events_unique_seq
Revises: 0016_profile_design_recipe
Create Date: 2026-10-27 00:00:00.000000

The old ``SELECT MAX(seq)`` + ``INSERT`` writer could give two events of one
job the same ``seq``, so an SSE replay (``seq > after_seq``) skipped or
repeated one. The writer is atomic now; this makes the schema enforce it.

Existing duplicates are renumbered, never deleted: each colliding row is a real
event. Only jobs that have a duplicate are touched, ordered by (seq, id) — the
autoincrement id is arrival order — and keep their lowest seq as the base, so a
job whose oldest events were trimmed is not rebased. Then the plain index is
replaced by a unique one. ``core/db.py::_ensure_job_events_unique_seq`` runs the
same repair where alembic cannot, so both paths converge.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_job_events_unique_seq"
down_revision: Union[str, None] = "0016_profile_design_recipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_job_events(bind) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_events'")
        ).fetchone()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_job_events(bind):
        return  # init_db creates the table and the unique index
    dup_jobs = [
        r[0]
        for r in bind.execute(
            sa.text(
                "SELECT DISTINCT job_id FROM job_events "
                "GROUP BY job_id, seq HAVING COUNT(*) > 1"
            )
        )
    ]
    for job_id in dup_jobs:
        rows = bind.execute(
            sa.text("SELECT id, seq FROM job_events WHERE job_id = :j ORDER BY seq, id"),
            {"j": job_id},
        ).fetchall()
        base = rows[0][1]
        for offset, (row_id, _seq) in enumerate(rows):
            bind.execute(
                sa.text("UPDATE job_events SET seq = :s WHERE id = :i"),
                {"s": base + offset, "i": row_id},
            )
    op.execute("DROP INDEX IF EXISTS idx_job_events_job_seq")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_events_job_seq_unique "
        "ON job_events(job_id, seq)"
    )


def downgrade() -> None:
    # Alembic-only databases (and the migration tests) never create job_events.
    if not _has_job_events(op.get_bind()):
        return
    op.execute("DROP INDEX IF EXISTS idx_job_events_job_seq_unique")
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_events_job_seq ON job_events(job_id, seq)")
