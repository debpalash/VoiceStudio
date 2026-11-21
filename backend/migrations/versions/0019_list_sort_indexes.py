"""Sort indexes for the history, voice, project and export lists

Revision ID: 0019_list_sort_indexes
Revises: 0018_pronunciation_profile_scope
Create Date: 2026-11-21 00:00:00.000000

The list endpoints ORDER BY created_at / updated_at (and starred) over tables
that grow with use. Each index is created only when its table and every column
exist, so an alembic-only database that never created a table (or an old one
that predates a column) is skipped instead of failing the whole upgrade.
``core/db.py::_ensure_perf_indexes`` creates the same indexes where alembic
cannot run, so both paths converge (a test keeps the two lists equal).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_list_sort_indexes"
down_revision: Union[str, None] = "0018_pronunciation_profile_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, columns that must exist, index column spec)
INDEXES = (
    ("idx_voice_profiles_created", "voice_profiles", ("created_at",), "created_at DESC"),
    ("idx_generation_history_created", "generation_history", ("created_at",), "created_at DESC"),
    ("idx_generation_history_profile", "generation_history", ("profile_id",), "profile_id"),
    (
        "idx_generation_history_starred",
        "generation_history",
        ("starred", "created_at"),
        "starred, created_at DESC",
    ),
    ("idx_dub_history_created", "dub_history", ("created_at",), "created_at DESC"),
    ("idx_studio_projects_updated", "studio_projects", ("updated_at",), "updated_at DESC"),
    ("idx_export_history_created", "export_history", ("created_at",), "created_at DESC"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, required, spec in INDEXES:
        have = {row[1] for row in bind.execute(sa.text(f"PRAGMA table_info({table})"))}
        if not set(required) <= have:
            continue  # table or column absent: init_db's helper creates it when it can
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({spec})")


def downgrade() -> None:
    for name, *_rest in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
