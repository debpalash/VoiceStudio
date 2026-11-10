"""Per-voice pronunciation dictionaries (#3873)

Revision ID: 0018_pronunciation_profile_scope
Revises: 0017_job_events_unique_seq

Adds ``pronunciation_entries.profile_id`` (``''`` = the entry applies to every
voice, exactly as before; otherwise the ``voice_profiles.id`` it belongs to).
Additive and idempotent: existing rows keep ``''`` so every current dictionary
behaves as it did, and a DB where core/db.py's schema already created the
column is left alone. The same column is declared in ``_BASE_SCHEMA`` and
added by the additive reconcile where alembic cannot run.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_pronunciation_profile_scope"
down_revision: str | None = "0017_job_events_unique_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    have = {r[1] for r in op.get_bind().execute(
        sa.text("PRAGMA table_info(pronunciation_entries)")
    ).fetchall()}
    if have and "profile_id" not in have:
        op.execute(
            "ALTER TABLE pronunciation_entries ADD COLUMN profile_id TEXT NOT NULL DEFAULT ''"
        )


def downgrade() -> None:
    """Keep user-authored per-voice entries on downgrade (additive migration)."""
