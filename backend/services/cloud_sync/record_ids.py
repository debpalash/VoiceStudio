"""Stable ``sync_id``s for synced records (guide §4): filled once, never changed."""
from __future__ import annotations

from core import db as core_db
from services.cloud_sync.ids import new_sync_id

#: Record tables that carry a sync_id today (S2: voices) and their fixed SQL.
#: S3 adds takes and projects. No SQL is built from strings.
_QUERIES = {
    "voice_profiles": (
        "SELECT id FROM voice_profiles WHERE sync_id IS NULL OR sync_id = ''",
        "UPDATE voice_profiles SET sync_id=? WHERE id=? AND (sync_id IS NULL OR sync_id='')",
    ),
}
SYNCED_TABLES = tuple(_QUERIES)


def ensure_sync_ids(table: str = "voice_profiles") -> int:
    """Give every row without a ``sync_id`` a new one; returns how many were filled."""
    if table not in _QUERIES:
        raise ValueError(f"not a synced table: {table}")
    select, update = _QUERIES[table]
    filled = 0
    with core_db.db_conn() as conn:
        for (row_id,) in conn.execute(select).fetchall():
            conn.execute(update, (new_sync_id(), row_id))
            filled += 1
    return filled
