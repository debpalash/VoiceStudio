"""Persist the full native Voice Design recipe on saved voice profiles.

Revision ID: 0016_profile_design_recipe
Revises: 0015_voice_sync_prep

Existing OmniVoice design profiles remain tag-based (NULL recipe). The same
column is included in core/db.py's _BASE_SCHEMA for fresh installs and in
the additive reconciliation path for installs without Alembic.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_profile_design_recipe"
down_revision: str | None = "0015_voice_sync_prep"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable recipe storage without replacing existing voice profiles."""
    have = {r[1] for r in op.get_bind().execute(
        sa.text("PRAGMA table_info(voice_profiles)")
    ).fetchall()}
    if have and "design_recipe" not in have:
        op.execute("ALTER TABLE voice_profiles ADD COLUMN design_recipe TEXT DEFAULT NULL")


def downgrade() -> None:
    """Keep user-authored voice recipes on downgrade (additive migration)."""
