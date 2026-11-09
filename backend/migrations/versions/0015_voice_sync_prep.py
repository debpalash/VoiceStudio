"""Prepare voice profiles for consent-aware synchronization.

Adds a nullable synchronization identity and consent-basis/attestation fields,
plus a partial unique index for assigned identities. Existing columns are
detected before each additive change and the index uses ``IF NOT EXISTS``, so
reruns preserve prior data. ``core.db`` carries the same columns in its base
schema and additive reconciler, covering databases alembic cannot reach.

Revision ID: 0015_voice_sync_prep
Revises: 0014_cloud_sync
Create Date: 2026-10-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_voice_sync_prep"
down_revision: Union[str, None] = "0014_cloud_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("sync_id", "TEXT DEFAULT NULL"),
    ("consent_basis", "TEXT DEFAULT ''"),
    ("consent_attested_at", "TEXT DEFAULT NULL"),
)


def _columns() -> set[str]:
    rows = op.get_bind().execute(sa.text("PRAGMA table_info(voice_profiles)")).fetchall()
    return {r[1] for r in rows}


def upgrade() -> None:
    have = _columns()
    if not have:
        return  # no voice_profiles yet: init_db creates it from _BASE_SCHEMA
    for name, typedef in _COLUMNS:
        if name not in have:
            op.execute(f"ALTER TABLE voice_profiles ADD COLUMN {name} {typedef}")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_profiles_sync_id "
        "ON voice_profiles(sync_id) WHERE sync_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_voice_profiles_sync_id")
