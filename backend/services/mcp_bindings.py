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
    now = time.time()
    with db_conn() as conn:
        # Merge partial edits inside SQLite's write, rather than against a
        # snapshot from another connection. Concurrent creation is an upsert,
        # and omitted fields preserve the latest committed values.
        row = conn.execute(
            "INSERT INTO mcp_client_bindings "
            "(client_id, label, profile_id, default_engine, last_seen_at, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(client_id) DO UPDATE SET "
            "label=CASE WHEN ? IS NULL THEN mcp_client_bindings.label ELSE excluded.label END, "
            "profile_id=CASE WHEN ? IS NULL THEN mcp_client_bindings.profile_id ELSE excluded.profile_id END, "
            "default_engine=CASE WHEN ? IS NULL THEN mcp_client_bindings.default_engine ELSE excluded.default_engine END "
            "RETURNING *",
            (cid, label or "", profile_id or None, default_engine or None, now,
             label, profile_id, default_engine),
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


def _global_default_profile() -> Optional[str]:
    """The fallback voice when a client has no binding. Reads the same
    pref the Settings 'default playback voice' would set; None if unset."""
    try:
        from core import prefs
        return prefs.get("mcp_default_profile_id") or None
    except Exception:
        return None


def resolve_voice(client_id: Optional[str], explicit_profile_id: Optional[str]) -> dict:
    """Resolve which voice an MCP speak call should use.

    Precedence (Spec 2): explicit tool arg → the client's binding →
    the global default → nothing (caller decides / errors with a hint).

    Returns ``{profile_id, default_engine, source}`` where ``source`` is one
    of ``explicit`` | ``binding`` | ``global`` | ``none`` for diagnostics.
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
    g = _global_default_profile()
    if g:
        return {"profile_id": g, "default_engine": None, "source": "global"}
    return {"profile_id": None, "default_engine": None, "source": "none"}
