"""SQLite state of the Cloud sync engine (tables from alembic 0014_cloud_sync)."""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from core import db as core_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_idempotency_key() -> str:
    return secrets.token_hex(16)


@dataclass
class State:
    organization_id: str
    device_id: Optional[str] = None
    cursor: int = 0
    enabled: bool = False
    choices: dict = field(default_factory=dict)
    last_full_sync_at: Optional[str] = None
    #: Collections skipped for a newer ``schema_version``: {collection: version seen}.
    needs_update: dict = field(default_factory=dict)
    #: Collections to pull from ``since=0`` once (chosen later, or newly supported).
    full_pull: list = field(default_factory=list)


@dataclass
class BaseItem:
    collection: str
    item_id: str
    revision: int
    deleted: bool
    schema_version: int
    content: Optional[dict]


@dataclass
class OutboxRow:
    id: int
    organization_id: str
    collection: str
    item_id: str
    op: str
    idempotency_key: str
    attempts: int
    last_error: Optional[str]
    gen: int = 0


def _base(row) -> BaseItem:
    return BaseItem(
        row["collection"], row["item_id"], row["revision"], bool(row["deleted"]),
        row["schema_version"], json.loads(row["content_json"]) if row["content_json"] else None,
    )


def _outbox(row) -> OutboxRow:
    return OutboxRow(row["id"], row["organization_id"], row["collection"], row["item_id"],
                     row["op"], row["idempotency_key"], row["attempts"], row["last_error"], row["gen"])


class SyncStore:
    """Every call opens its own connection (core.db), so a store is cheap and thread-safe."""

    # ── state ──────────────────────────────────────────────────────────
    def state(self, org: str) -> Optional[State]:
        with core_db.db_conn() as conn:
            row = conn.execute("SELECT * FROM cloud_sync_state WHERE organization_id=?", (org,)).fetchone()
        if not row:
            return None
        return State(org, row["device_id"], row["cursor"], bool(row["enabled"]),
                     json.loads(row["choices_json"] or "{}"), row["last_full_sync_at"],
                     json.loads(row["needs_update_json"] or "{}"), json.loads(row["full_pull_json"] or "[]"))

    def save_state(self, org: str, **fields: Any) -> State:
        current = self.state(org) or State(org)
        for key, value in fields.items():
            setattr(current, key, value)
        with core_db.db_conn() as conn:
            conn.execute(
                """INSERT INTO cloud_sync_state
                   (organization_id, device_id, cursor, enabled, choices_json, last_full_sync_at,
                    needs_update_json, full_pull_json)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(organization_id) DO UPDATE SET device_id=excluded.device_id,
                     cursor=excluded.cursor, enabled=excluded.enabled,
                     choices_json=excluded.choices_json, last_full_sync_at=excluded.last_full_sync_at,
                     needs_update_json=excluded.needs_update_json, full_pull_json=excluded.full_pull_json""",
                (org, current.device_id, current.cursor, int(current.enabled),
                 json.dumps(current.choices), current.last_full_sync_at,
                 json.dumps(current.needs_update), json.dumps(sorted(set(current.full_pull)))),
            )
        return current

    # ── merge bases ────────────────────────────────────────────────────
    def item(self, org: str, collection: str, item_id: str) -> Optional[BaseItem]:
        with core_db.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cloud_sync_items WHERE organization_id=? AND collection=? AND item_id=?",
                (org, collection, item_id),
            ).fetchone()
        return _base(row) if row else None

    def items(self, org: str, collection: str) -> list[BaseItem]:
        with core_db.db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cloud_sync_items WHERE organization_id=? AND collection=?", (org, collection)
            ).fetchall()
        return [_base(r) for r in rows]

    @staticmethod
    def _put_item(conn, org: str, item: dict) -> None:
        conn.execute(
            """INSERT OR REPLACE INTO cloud_sync_items
               (organization_id, collection, item_id, revision, deleted, schema_version, content_json)
               VALUES (?,?,?,?,?,?,?)""",
            (org, item["collection"], item["item_id"], int(item["revision"]), int(bool(item.get("deleted"))),
             int(item.get("schema_version", 1)),
             None if item.get("deleted") or item.get("content") is None else json.dumps(item["content"])),
        )

    def save_item(self, org: str, item: dict) -> None:
        with core_db.db_conn() as conn:
            self._put_item(conn, org, item)

    def save_items(self, org: str, items: Iterable[dict]) -> None:
        with core_db.db_conn() as conn:
            for item in items:
                self._put_item(conn, org, item)

    def save_page(self, org: str, items: Iterable[dict], cursor: int) -> None:
        """The pulled bases and the cursor land in one transaction."""
        with core_db.db_conn() as conn:
            for item in items:
                self._put_item(conn, org, item)
            conn.execute("UPDATE cloud_sync_state SET cursor=? WHERE organization_id=?", (int(cursor), org))

    def forget_item(self, org: str, collection: str, item_id: str) -> None:
        with core_db.db_conn() as conn:
            conn.execute(
                "DELETE FROM cloud_sync_items WHERE organization_id=? AND collection=? AND item_id=?",
                (org, collection, item_id),
            )

    def forget_items(self, org: str, collection: Optional[str] = None) -> None:
        with core_db.db_conn() as conn:
            if collection:
                conn.execute("DELETE FROM cloud_sync_items WHERE organization_id=? AND collection=?", (org, collection))
            else:
                conn.execute("DELETE FROM cloud_sync_items WHERE organization_id=?", (org,))

    # ── outbox ─────────────────────────────────────────────────────────
    def queue(self, org: str, collection: str, item_id: str, op: str) -> OutboxRow:
        """One row per item. A later change is a new logical write: same row, new key and generation.

        The key is reused only when the same write is retried (the row is untouched).
        """
        with core_db.db_conn() as conn:
            conn.execute(
                """INSERT INTO cloud_sync_outbox (organization_id, collection, item_id, op, idempotency_key, queued_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(organization_id, collection, item_id) DO UPDATE SET op=excluded.op,
                     idempotency_key=excluded.idempotency_key, gen=cloud_sync_outbox.gen + 1""",
                (org, collection, item_id, op, new_idempotency_key(), _now()),
            )
            row = conn.execute(
                "SELECT * FROM cloud_sync_outbox WHERE organization_id=? AND collection=? AND item_id=?",
                (org, collection, item_id),
            ).fetchone()
        return _outbox(row)

    def outbox(self, org: str) -> list[OutboxRow]:
        with core_db.db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cloud_sync_outbox WHERE organization_id=? ORDER BY id", (org,)
            ).fetchall()
        return [_outbox(r) for r in rows]

    def outbox_row(self, org: str, collection: str, item_id: str) -> Optional[OutboxRow]:
        with core_db.db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cloud_sync_outbox WHERE organization_id=? AND collection=? AND item_id=?",
                (org, collection, item_id),
            ).fetchone()
        return _outbox(row) if row else None

    def renew_key(self, row_id: int) -> str:
        key = new_idempotency_key()
        with core_db.db_conn() as conn:
            conn.execute("UPDATE cloud_sync_outbox SET idempotency_key=? WHERE id=?", (key, row_id))
        return key

    def mark_attempt(self, row_id: int, error: Optional[str]) -> None:
        with core_db.db_conn() as conn:
            conn.execute(
                "UPDATE cloud_sync_outbox SET attempts=attempts+1, last_error=? WHERE id=?", (error, row_id)
            )

    def drop_outbox(self, row_id: int, gen: Optional[int] = None) -> bool:
        """Remove a pushed row; with ``gen``, only if no newer change arrived meanwhile."""
        with core_db.db_conn() as conn:
            if gen is None:
                return conn.execute("DELETE FROM cloud_sync_outbox WHERE id=?", (row_id,)).rowcount > 0
            return conn.execute("DELETE FROM cloud_sync_outbox WHERE id=? AND gen=?", (row_id, gen)).rowcount > 0

    def drop_deletes(self, org: str, collections: Iterable[str]) -> None:
        with core_db.db_conn() as conn:
            for collection in collections:
                conn.execute(
                    "DELETE FROM cloud_sync_outbox WHERE organization_id=? AND collection=? AND op='delete'",
                    (org, collection),
                )

    # ── renderer mirror ────────────────────────────────────────────────
    def local_seq(self) -> int:
        with core_db.db_conn() as conn:
            return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM cloud_sync_local").fetchone()[0]

    def get_local(self, key: str) -> Any:
        with core_db.db_conn() as conn:
            row = conn.execute("SELECT value_json FROM cloud_sync_local WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else None

    def local_row(self, key: str) -> Optional[tuple[Any, int, str]]:
        """``(value, seq, origin)`` of a mirrored key, or None."""
        with core_db.db_conn() as conn:
            row = conn.execute("SELECT value_json, seq, origin FROM cloud_sync_local WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return (json.loads(row[0]) if row[0] is not None else None, row[1], row[2])

    def local_meta(self, key: str) -> dict:
        with core_db.db_conn() as conn:
            row = conn.execute("SELECT meta_json FROM cloud_sync_local WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_local(self, key: str, value: Any, *, origin: str, meta: Optional[dict] = None) -> int:
        """Store a renderer value. ``origin='sync'`` rows are what the renderer pulls back."""
        with core_db.db_conn() as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM cloud_sync_local").fetchone()[0]
            previous = conn.execute("SELECT meta_json FROM cloud_sync_local WHERE key=?", (key,)).fetchone()
            meta_json = json.dumps(meta) if meta is not None else (previous[0] if previous else "{}")
            conn.execute(
                "INSERT OR REPLACE INTO cloud_sync_local (key, value_json, seq, origin, meta_json) VALUES (?,?,?,?,?)",
                (key, None if value is None else json.dumps(value), seq, origin, meta_json),
            )
        return seq

    def local_since(self, seq: int) -> list[tuple[int, str, Any]]:
        with core_db.db_conn() as conn:
            rows = conn.execute(
                "SELECT seq, key, value_json FROM cloud_sync_local WHERE seq > ? AND origin='sync' ORDER BY seq",
                (int(seq),),
            ).fetchall()
        return [(r[0], r[1], json.loads(r[2]) if r[2] is not None else None) for r in rows]
