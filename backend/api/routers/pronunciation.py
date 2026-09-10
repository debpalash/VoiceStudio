"""
Pronunciation dictionary router — Expressive-TTS Spec 01 Phase 1.

CRUD for the DB-backed, per-language pronunciation dictionary the
``PronunciationPanel`` (Settings → Pronunciation) edits, plus a model-free
``/pronunciation/test`` dry-run. Entries are applied as pure text substitution
before synthesis (see ``services/pronunciation.apply_pronunciation`` and the
generate path), so a saved entry actually changes the audio on every engine.

Endpoints (admin-gated; loopback or authenticated server mode):
    GET    /pronunciation              → list every entry
    POST   /pronunciation              → create one entry
    PUT    /pronunciation/{entry_id}   → update an entry (partial)
    DELETE /pronunciation/{entry_id}   → remove an entry
    POST   /pronunciation/test         → dry-run substitution (no model)
    GET    /pronunciation/export       → all entries as JSON (round-trips import)
    POST   /pronunciation/import       → bulk add entries from JSON

Scope: ``language='*'`` is global (applies to every request); a 2-letter code
(``'en'``, ``'de'``) applies only when the request language matches.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import require_admin
from core.db import db_conn
from services.pronunciation import apply_pronunciation, entries_for_language

logger = logging.getLogger("omnivoice.pronunciation")
router = APIRouter(dependencies=[Depends(require_admin)])

_VALID_TYPES = ("respelling", "ipa", "cmu")
_ALL_LANG = "*"

# IPA: the input is validated as a non-empty string of Unicode letters / IPA
# extension codepoints + the usual suprasegmental marks; we reject ASCII control
# and the bracket/pipe chars that would collide with the inline grammar. This is
# a charset gate (catches obvious garbage early), not a full IPA grammar.
_IPA_BAD = re.compile(r"[\[\]\|\x00-\x1f]")
# CMU / ARPABET: space-separated phoneme tokens (letters + an optional 0-2 stress
# digit), e.g. "N AH0 V AE1 D AH0". Reject anything else.
_CMU_TOKEN = re.compile(r"^[A-Za-z]{1,3}[0-2]?$")


def _validate_type_replacement(etype: str, replacement: str) -> None:
    """Raise 400 on a phoneme replacement that's obviously malformed.

    Respelling rows accept any text. IPA rows must be a non-empty string free of
    bracket/pipe/control chars. CMU rows must be space-separated ARPABET tokens.
    Validating on save (not at synth) means a model never sees garbage phonemes
    (Spec 01 §R3 — never pass unvalidated phoneme strings to a model).
    """
    if etype == "respelling":
        return
    rep = (replacement or "").strip()
    if not rep:
        raise HTTPException(
            status_code=400,
            detail=f"A {etype.upper()} entry needs a phoneme string in 'replacement'.",
        )
    if etype == "ipa":
        if _IPA_BAD.search(rep):
            raise HTTPException(
                status_code=400,
                detail="That IPA string contains brackets, a pipe, or control characters. "
                       "Use plain IPA symbols, e.g. ˈnɛvʌdə.",
            )
    elif etype == "cmu":
        tokens = rep.split()
        if not tokens or any(not _CMU_TOKEN.match(tok) for tok in tokens):
            raise HTTPException(
                status_code=400,
                detail="That doesn't look like CMU/ARPABET. Use space-separated tokens with "
                       "optional stress digits, e.g. N AH0 V AE1 D AH0.",
            )


def _norm_language(language: Optional[str]) -> str:
    """Normalize a scope to '*' (global) or a lowercase 2-letter code."""
    if not language:
        return _ALL_LANG
    s = str(language).strip()
    if not s or s == _ALL_LANG or s.lower() == "auto":
        return _ALL_LANG
    return s.lower()[:2]


def _row_to_dict(r) -> dict:
    d = dict(r)
    d["enabled"] = bool(d.get("enabled"))
    # ``scope`` is the UI-facing alias for ``language`` ('*' shows as Global).
    d["scope"] = d.get("language") or _ALL_LANG
    return d


# ── Schemas ──────────────────────────────────────────────────────────────────


class PronEntry(BaseModel):
    term: str
    replacement: str = ""
    type: str = "respelling"
    language: str = _ALL_LANG
    enabled: bool = True


class PronEntryUpdate(BaseModel):
    term: Optional[str] = None
    replacement: Optional[str] = None
    type: Optional[str] = None
    language: Optional[str] = None
    enabled: Optional[bool] = None


class PronTestRequest(BaseModel):
    text: str
    language: Optional[str] = None


class PronImportRequest(BaseModel):
    entries: List[PronEntry]
    replace: bool = False  # True → clear existing rows first


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("/pronunciation")
def list_entries():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("/pronunciation")
def create_entry(entry: PronEntry):
    term = entry.term.strip()
    if not term:
        raise HTTPException(status_code=400, detail="A pronunciation entry needs a term.")
    etype = (entry.type or "respelling").strip().lower()
    if etype not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown entry type {entry.type!r}. Use one of: {', '.join(_VALID_TYPES)}.",
        )
    _validate_type_replacement(etype, entry.replacement)
    eid = str(uuid.uuid4())[:12]
    now = time.time()
    lang = _norm_language(entry.language)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO pronunciation_entries (id, term, replacement, type, language, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, term, entry.replacement, etype, lang, 1 if entry.enabled else 0, now),
        )
        row = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries WHERE id = ?", (eid,)
        ).fetchone()
    return _row_to_dict(row)


@router.put("/pronunciation/{entry_id}")
def update_entry(entry_id: str, patch: PronEntryUpdate):
    with db_conn() as conn:
        existing = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="No such pronunciation entry.")

        # Resolve the post-update type + replacement so phoneme validation runs
        # against the final state (e.g. switching type without changing text).
        new_type = (patch.type.strip().lower() if patch.type is not None else existing["type"]) or "respelling"
        if new_type not in _VALID_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown entry type {patch.type!r}. Use one of: {', '.join(_VALID_TYPES)}.",
            )
        new_replacement = patch.replacement if patch.replacement is not None else existing["replacement"]
        _validate_type_replacement(new_type, new_replacement)

        fields, params = [], []
        if patch.term is not None:
            term = patch.term.strip()
            if not term:
                raise HTTPException(status_code=400, detail="A pronunciation entry needs a term.")
            fields.append("term = ?"); params.append(term)
        if patch.replacement is not None:
            fields.append("replacement = ?"); params.append(patch.replacement)
        if patch.type is not None:
            fields.append("type = ?"); params.append(new_type)
        if patch.language is not None:
            fields.append("language = ?"); params.append(_norm_language(patch.language))
        if patch.enabled is not None:
            fields.append("enabled = ?"); params.append(1 if patch.enabled else 0)
        if not fields:
            raise HTTPException(
                status_code=400,
                detail="PUT body was empty. Include at least one field to change, or DELETE the entry.",
            )
        params.append(entry_id)
        # nosec B608 - `fields` are fixed literal assignments ("term = ?", …) from
        # the allowlist above; every user value is a bound `?` parameter, never
        # interpolated. The f-string only joins constant column fragments.
        conn.execute(
            f"UPDATE pronunciation_entries SET {', '.join(fields)} WHERE id = ?",  # nosec B608
            params,
        )
        row = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries WHERE id = ?", (entry_id,)
        ).fetchone()
    return _row_to_dict(row)


@router.delete("/pronunciation/{entry_id}")
def delete_entry(entry_id: str):
    with db_conn() as conn:
        cur = conn.execute("DELETE FROM pronunciation_entries WHERE id = ?", (entry_id,))
    return {"deleted": cur.rowcount > 0}


# ── Dry-run + import/export ───────────────────────────────────────────────────


@router.post("/pronunciation/test")
def test_substitution(req: PronTestRequest):
    """Show the post-substitution text for ``req.text`` — no model call.

    Applies the same dictionary + inline ``[[…]]`` resolution the synth path
    runs, so the user sees exactly what the engine will be handed.
    """
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries"
        ).fetchall()
    substituted = apply_pronunciation(req.text, rows, req.language)
    applied, skipped = entries_for_language(rows, req.language, include_skipped=True)
    return {
        "input": req.text,
        "substituted": substituted,
        "changed": substituted != req.text,
        "applied_terms": sorted(applied.keys(), key=len, reverse=True),
        "skipped_terms": skipped,
    }


@router.get("/pronunciation/export")
def export_entries():
    """Every entry as a JSON-serializable list (round-trips ``/import``)."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT term, replacement, type, language, enabled "
            "FROM pronunciation_entries ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return {"entries": [
        {"term": r["term"], "replacement": r["replacement"], "type": r["type"],
         "language": r["language"], "enabled": bool(r["enabled"])}
        for r in rows
    ]}


@router.post("/pronunciation/import")
def import_entries(req: PronImportRequest):
    """Bulk-add entries. ``replace=true`` clears the table first.

    Each entry is validated like ``POST /pronunciation``; one bad row fails the
    whole import (400) so the table is never left half-applied.
    """
    now = time.time()
    cleaned = []
    for e in req.entries:
        term = e.term.strip()
        if not term:
            continue  # silently skip blank terms — they're a no-op anyway
        etype = (e.type or "respelling").strip().lower()
        if etype not in _VALID_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Entry {term!r}: unknown type {e.type!r}.",
            )
        _validate_type_replacement(etype, e.replacement)
        cleaned.append((str(uuid.uuid4())[:12], term, e.replacement, etype,
                        _norm_language(e.language), 1 if e.enabled else 0, now))
    with db_conn() as conn:
        if req.replace:
            conn.execute("DELETE FROM pronunciation_entries")
        conn.executemany(
            "INSERT INTO pronunciation_entries (id, term, replacement, type, language, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            cleaned,
        )
    return {"imported": len(cleaned), "replaced": req.replace}
