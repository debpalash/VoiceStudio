"""Keep the inputs needed to recreate designed-voice generations.

Adds an optional design-recipe payload to generation history. The migration
checks the table and column first, so repeated or partial upgrades only add
missing schema and leave existing rows intact. ``core.db`` declares the same
column in its base schema and additive reconciliation path for installations
where alembic cannot run.

Revision ID: 0013_generation_history_design_recipe
Revises: 0012_call_sessions
Create Date: 2026-10-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_generation_history_design_recipe"
down_revision: Union[str, None] = "0012_call_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "generation_history"
_COLUMN = "design_recipe"


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return row is not None


def _has_column(table: str, column: str) -> bool:
    rows = op.get_bind().execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    if not _has_column(_TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_table(_TABLE) and _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
