"""VoiceStudio Cloud settings sync tables (S1)

Revision ID: 0014_cloud_sync
Revises: 0013_generation_history_design_recipe
Create Date: 2026-10-16 00:00:00.000000

The local state of the Cloud sync engine (vssaas desktop-app-guide §4):
per-Organization device and cursor, the last agreed version of each item (the
merge base), the outbox of local changes (one row per item), and the mirror
of renderer localStorage values the engine reads and writes.

Named ``cloud_sync_*`` because VoiceStudio Pro already owns a ``sync_state``
table (folder sync) with another schema. Idempotent: every statement is
``IF NOT EXISTS`` and the same DDL is in ``core/db.py::_BASE_SCHEMA``.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014_cloud_sync"
down_revision: Union[str, None] = "0013_generation_history_design_recipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DDL = (
    """CREATE TABLE IF NOT EXISTS cloud_sync_state (
        organization_id TEXT PRIMARY KEY,
        device_id TEXT,
        cursor INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 0,
        choices_json TEXT NOT NULL DEFAULT '{}',
        last_full_sync_at TEXT,
        needs_update_json TEXT NOT NULL DEFAULT '{}',
        full_pull_json TEXT NOT NULL DEFAULT '[]'
    )""",
    """CREATE TABLE IF NOT EXISTS cloud_sync_items (
        organization_id TEXT NOT NULL,
        collection TEXT NOT NULL,
        item_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        deleted INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        content_json TEXT,
        PRIMARY KEY (organization_id, collection, item_id)
    )""",
    """CREATE TABLE IF NOT EXISTS cloud_sync_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        collection TEXT NOT NULL,
        item_id TEXT NOT NULL,
        op TEXT NOT NULL CHECK (op IN ('put','delete')),
        idempotency_key TEXT NOT NULL,
        queued_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        gen INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS cloud_sync_outbox_one_per_item
        ON cloud_sync_outbox (organization_id, collection, item_id)""",
    """CREATE TABLE IF NOT EXISTS cloud_sync_local (
        key TEXT PRIMARY KEY,
        value_json TEXT,
        seq INTEGER NOT NULL,
        origin TEXT NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    )""",
)


def upgrade() -> None:
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    for table in ("cloud_sync_local", "cloud_sync_outbox", "cloud_sync_items", "cloud_sync_state"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
