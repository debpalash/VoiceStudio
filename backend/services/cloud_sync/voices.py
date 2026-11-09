"""The ``voices`` sync content (sync-collections.md, S2): built only for attested voices.

A voice is pushed only with an explicit consent attestation: "This is my voice"
(``own_voice``) or "I have the speaker's permission" (``speaker_permission``).
A recorded own-voice consent counts as ``own_voice``. Local IDs, file paths
and hardware choices never enter the content; files (blobs) join in the S2
server phase.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

BASES = ("own_voice", "speaker_permission")


def utc(seconds: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(seconds), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def consent_of(row: dict) -> Optional[dict]:
    basis = row.get("consent_basis") or ""
    if basis in BASES and row.get("consent_attested_at"):
        return {"attested": True, "attested_at": row["consent_attested_at"], "basis": basis,
                "consent_text": row.get("consent_text") or ""}
    if row.get("verified_own_voice") and row.get("consent_recorded_at"):
        return {"attested": True, "attested_at": utc(row["consent_recorded_at"]), "basis": "own_voice",
                "consent_text": row.get("consent_text") or ""}
    return None


def voice_sync_content(row: dict) -> Optional[dict]:
    """The item content for one voice, or None: an unattested voice is never pushed."""
    consent = consent_of(row)
    if consent is None:
        return None
    vd_states = row.get("vd_states")
    if isinstance(vd_states, str):
        try:
            vd_states = json.loads(vd_states)
        except ValueError:
            vd_states = None
    return {
        "name": row.get("name") or "",
        "kind": row.get("kind") or "clone",
        "language": row.get("language") or "Auto",
        "ref_text": row.get("ref_text") or "",
        "instruct": row.get("instruct") or "",
        "seed": row.get("seed"),
        "vd_states": vd_states,
        "tags": [],
        "created_at": utc(row.get("created_at")),
        "consent": consent,
        "files": {},
    }
