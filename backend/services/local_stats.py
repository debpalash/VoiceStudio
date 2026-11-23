"""Local-only usage insights — the privacy-preserving answer to "how am I using this?".

The alternative to cloud analytics (PostHog was proposed and rejected, PR #1110):
this collects **nothing new** and transmits **nothing anywhere**. It simply
aggregates the rows the app has *already* written to the user's own SQLite
database in the course of doing its job — generation history, voice profiles,
dubs, exports — and hands back counts and totals for the user's own eyes.

Design rules, so this can never become telemetry by accident:

- **Read-only.** No new tables, no new columns, no new event stream. If the
  feature were deleted tomorrow, not one byte of stored data would change.
- **No content.** Only aggregates (counts, sums, distributions over engine and
  language). The `text` column of a take is never read, never returned. Nothing
  here identifies a person, a file path, or what was said.
- **No network.** There is no client, no endpoint, no token. The data reaches
  exactly one place: the local HTTP response to the user's own UI.

That keeps the product's headline promise intact — *nothing leaves your
machine* — while still answering the question analytics was meant to answer.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from core.db import db_conn

logger = logging.getLogger("omnivoice.local_stats")


def _scalar(conn, sql: str, default: Any = 0) -> Any:
    """One aggregate value, or `default` when the table/column doesn't exist yet
    (a fresh install, or a DB predating a migration). Never raises — an insights
    panel must not 500 because one table is missing."""
    try:
        row = conn.execute(sql).fetchone()
    except Exception:  # noqa: BLE001 — missing table/column on an older DB
        return default
    if not row or row[0] is None:
        return default
    return row[0]


def _distribution(conn, sql: str) -> list[dict]:
    """`[{"name": …, "count": n}, …]`, biggest first. Empty on any error."""
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        name = r[0]
        if name is None or str(name).strip() == "":
            name = "unknown"
        out.append({"name": str(name), "count": int(r[1])})
    return out


def usage_summary() -> dict:
    """Aggregate the user's own local history. Never raises.

    Returns counts/totals only — no text, no paths, no identifiers. Safe to
    render, safe to ignore, and impossible to turn into telemetry: it has no
    way to send anything anywhere."""
    with db_conn() as conn:
        takes = int(_scalar(conn, "SELECT COUNT(*) FROM generation_history"))
        audio_seconds = float(
            _scalar(conn, "SELECT SUM(duration_seconds) FROM generation_history", 0.0)
        )
        compute_seconds = float(
            _scalar(conn, "SELECT SUM(generation_time) FROM generation_history", 0.0)
        )
        starred = int(
            _scalar(conn, "SELECT COUNT(*) FROM generation_history WHERE COALESCE(starred,0)=1")
        )
        first_at = _scalar(conn, "SELECT MIN(created_at) FROM generation_history", None)
        last_at = _scalar(conn, "SELECT MAX(created_at) FROM generation_history", None)

        by_mode = _distribution(
            conn,
            "SELECT COALESCE(NULLIF(TRIM(mode, ' ' || char(9) || char(10) || char(13)), ''), 'unknown') AS name, COUNT(*) "
            "FROM generation_history "
            "GROUP BY name ORDER BY COUNT(*) DESC, name",
        )
        by_language = _distribution(
            conn,
            "SELECT COALESCE(NULLIF(TRIM(language, ' ' || char(9) || char(10) || char(13)), ''), 'unknown') AS name, COUNT(*) "
            "FROM generation_history "
            "GROUP BY name ORDER BY COUNT(*) DESC, name LIMIT 12",
        )

        voices = int(_scalar(conn, "SELECT COUNT(*) FROM voice_profiles"))
        dubs = int(_scalar(conn, "SELECT COUNT(*) FROM dub_history"))
        projects = int(_scalar(conn, "SELECT COUNT(*) FROM studio_projects"))
        exports = int(_scalar(conn, "SELECT COUNT(*) FROM export_history"))

        # Distinct local days with at least one take — an honest "how often do I
        # actually use this", without storing or transmitting a usage timeline.
        active_days = int(
            _scalar(
                conn,
                "SELECT COUNT(DISTINCT DATE(created_at, 'unixepoch', 'localtime')) "
                "FROM generation_history",
            )
        )

    return {
        "takes": takes,
        "starred": starred,
        "audio_seconds": round(audio_seconds, 1),
        "compute_seconds": round(compute_seconds, 1),
        "active_days": active_days,
        "first_at": first_at,
        "last_at": last_at,
        "by_mode": by_mode,
        "by_language": by_language,
        "voices": voices,
        "dubs": dubs,
        "projects": projects,
        "exports": exports,
        # Stated in the payload itself so the guarantee travels with the data
        # and any future consumer sees it.
        "local_only": True,
        "generated_at": time.time(),
    }
