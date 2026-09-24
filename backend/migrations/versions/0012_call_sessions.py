"""Phone call agent: persisted call sessions

Revision ID: 0012_call_sessions
Revises: 0011_remote_attempt_deadlines
Create Date: 2026-09-23 00:00:00.000000

Adds ``call_sessions`` (docs/integrations/calls.md): one row per call the agent
placed or answered — direction, numbers (full, local only; plus a masked copy
for the API), brief, voice, disclosure, status timeline, transcript, outcome,
summary and an optional local recording path.

Additive + idempotent (guarded by sqlite_master) like 0008, so a fresh-install
DB where ``core/db.py::_BASE_SCHEMA`` already created the table is a no-op. The
same table is mirrored there so both paths converge on one schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_call_sessions"
down_revision: Union[str, None] = "0011_remote_attempt_deadlines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}
    ).fetchone()
    return row is not None


def upgrade() -> None:
    if _has_table("call_sessions"):
        return

    def text(name: str, default: str = "") -> sa.Column:
        return sa.Column(name, sa.Text(), nullable=False, server_default=default)

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        text("direction", "outbound"),
        text("remote_number"),
        text("remote_masked"),
        text("from_number"),
        text("provider", "twilio"),
        text("provider_call_id"),
        text("status", "queued"),
        text("brief"),
        text("profile_id"),
        text("engine"),
        text("language"),
        text("disclosure"),
        sa.Column("max_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("outcome", sa.Text(), nullable=True),
        text("summary"),
        text("transcript_json", "[]"),
        text("timeline_json", "[]"),
        text("recording_path"),
        text("error"),
        sa.Column("created_at", sa.Float(), nullable=True),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("ended_at", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
    )
    op.create_index("idx_call_sessions_created", "call_sessions", ["created_at"])


def downgrade() -> None:
    if _has_table("call_sessions"):
        op.drop_index("idx_call_sessions_created", table_name="call_sessions")
        op.drop_table("call_sessions")
