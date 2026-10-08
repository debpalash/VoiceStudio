"""Voice Design takes: design_recipe on generation_history

Revision ID: 0013_generation_history_design_recipe
Revises: 0012_call_sessions
Create Date: 2026-09-28 00:00:00.000000

Adds ``generation_history.design_recipe TEXT DEFAULT NULL`` — the Voice Design
draft behind a take, as JSON ``{"description", "picks", "mapped"}`` (#2389),
where ``mapped`` is derived server-side from the description.
The instruct alone cannot say which details the user wrote, picked or had
mapped from the description, so reopening a take could not rebuild the draft
that produced it. Rows written before this column stay NULL and keep the
instruct-based restore.

Additive + idempotent (guarded by PRAGMA table_info, matching 0009), so a
fresh install whose ``_BASE_SCHEMA`` already declares the column is a no-op.
The same column is mirrored into ``core/db.py::_BASE_SCHEMA`` so fresh
installs and migrated DBs converge, and ``_reconcile_additive_columns`` heals
DBs where alembic cannot run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_generation_history_design_recipe"
down_revision: Union[str, None] = "0012_call_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    # A DB that missed init has no generation_history; the startup self-heal
    # (#710) creates it from _BASE_SCHEMA with the column already present.
    if not _has_table("generation_history"):
        return
    if not _has_column("generation_history", "design_recipe"):
        op.add_column(
            "generation_history",
            sa.Column("design_recipe", sa.Text(), nullable=True, server_default=None),
        )


def downgrade() -> None:
    if _has_table("generation_history") and _has_column("generation_history", "design_recipe"):
        op.drop_column("generation_history", "design_recipe")
