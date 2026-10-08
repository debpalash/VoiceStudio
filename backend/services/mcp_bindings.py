"""Per-agent MCP voice bindings (Wave 2.2 / Spec 2).

An MCP client identifies itself with the ``X-OmniVoice-Client-Id`` header.
Each client can be bound to a default voice profile + engine so different
agents speak in different voices ("Claude Code in Morgan, Cursor in
Scarlett"). Pure data layer over the ``mcp_client_bindings`` table — the
FastMCP tools call :func:`resolve_voice`; the Settings UI calls the CRUD
helpers via the REST router.
"""

from __future__ import annotations

import time
from typing import Optional

from core.db import db_conn


def list_bindings() -> list[dict]:
    with db_conn() as conn:
        # SQLite sorts NULL as smallest, so DESC naturally puts never-seen
        # bindings after recently-active ones.
        rows = conn.execute(
            "SELECT * FROM mcp_client_bindings ORDER BY last_seen_at DESC, created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_binding(client_id: str) -> Optional[dict]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mcp_client_bindings WHERE client_id=?", (client_id,)
        ).fetchone()
    return dict(row) if row else None


def upsert_binding(
    client_id: str,
    *,
    label: Optional[str] = None,
    profile_id: Optional[str] = None,
    default_engine: Optional[str] = None,
) -> dict:
    """Create or update a binding. Fields left as None on an existing row are
    preserved; on a new row they default to empty/null."""
    if not client_id or not client_id.strip():
        raise ValueError("client_id must be non-empty")
    cid = client_id.strip()
    # One statement, one transaction: SQLite resolves the insert/update race
    # itself and an omitted field keeps whatever the row holds at write time.
    # Reading a snapshot on another connection first and writing it back for
    # the omitted fields reverted a concurrent client's edit to those fields,
    # and two first saves could both choose INSERT and hit the primary key
    # (#2568). ``""`` still clears profile/engine to NULL.
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO mcp_client_bindings "
            "(client_id, label, profile_id, default_engine, last_seen_at, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(client_id) DO UPDATE SET "
            "label = CASE WHEN ? THEN excluded.label ELSE label END, "
            "profile_id = CASE WHEN ? THEN excluded.profile_id ELSE profile_id END, "
            "default_engine = CASE WHEN ? THEN excluded.default_engine ELSE default_engine END",
            (
                cid, label or "", profile_id or None, default_engine or None, time.time(),
                label is not None, profile_id is not None, default_engine is not None,
            ),
        )
        row = conn.execute(
            "SELECT * FROM mcp_client_bindings WHERE client_id=?", (cid,)
        ).fetchone()
    return dict(row)


def delete_binding(client_id: str) -> bool:
    with db_conn() as conn:
        cur = conn.execute("DELETE FROM mcp_client_bindings WHERE client_id=?", (client_id,))
    return cur.rowcount > 0


def touch_last_seen(client_id: str) -> None:
    """Best-effort 'last heard from this agent' stamp. Never raises — it's
    telemetry for the Settings list, not load-bearing."""
    if not client_id:
        return
    try:
        with db_conn() as conn:
            conn.execute(
                "UPDATE mcp_client_bindings SET last_seen_at=? WHERE client_id=?",
                (time.time(), client_id),
            )
    except Exception:
        pass


def resolve_voice(client_id: Optional[str], explicit_profile_id: Optional[str]) -> dict:
    """Resolve which voice an MCP speak call should use.

    Precedence (Spec 2): explicit tool arg → the client's binding →
    nothing (the backend then uses its default voice). A "global default"
    tier read a preference nothing ever wrote, so it was removed.

    Returns ``{profile_id, default_engine, source}`` where ``source`` is one
    of ``explicit`` | ``binding`` | ``none`` for diagnostics.
    """
    if explicit_profile_id:
        return {"profile_id": explicit_profile_id, "default_engine": None, "source": "explicit"}
    if client_id:
        binding = get_binding(client_id)
        if binding and binding.get("profile_id"):
            return {
                "profile_id": binding["profile_id"],
                "default_engine": binding.get("default_engine"),
                "source": "binding",
            }
    return {"profile_id": None, "default_engine": None, "source": "none"}
