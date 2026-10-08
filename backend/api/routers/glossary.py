"""
Glossary router — Phase 1.3 (ROADMAP.md).

Project-scoped CRUD for translation terms. `project_id` here is the dub-job id
(shared ID space with `studio_projects.id` once saved), so a glossary attached
to a draft job moves with it when the user saves the project.

Endpoints:
    GET    /glossary/{project_id}                 → list terms
    POST   /glossary/{project_id}                 → add one term
    PUT    /glossary/{project_id}/{term_id}       → update
    DELETE /glossary/{project_id}/{term_id}       → remove
    POST   /glossary/{project_id}/auto-extract    → LLM proposes terms from segments

The translator service (`backend/services/translator.py`) already accepts
`glossary: [{source, target, note}]` on its request; the frontend loads the
stored list, passes it into `/dub/translate`, and that's the full loop.
"""
import logging
import os
import time
import unicodedata
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.db import db_conn

logger = logging.getLogger("omnivoice.glossary")
router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────


class GlossaryTerm(BaseModel):
    source: str
    target: str
    note: str = ""


class GlossaryTermUpdate(BaseModel):
    source: Optional[str] = None
    target: Optional[str] = None
    note: Optional[str] = None


class AutoExtractRequest(BaseModel):
    source_lang: str = "en"
    target_lang: str
    segments: List[dict] = Field(
        default_factory=list,
        description="[{text: '...'}] — only text is read; other fields ignored.",
    )
    max_terms: int = 40


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    """Case-insensitive, normalization-insensitive key for source terms."""
    # NFKC also folds compatibility forms (full-width Latin letters, the "fi"
    # ligature), which LLM output in CJK-adjacent projects often contains.
    return unicodedata.normalize("NFKC", text or "").casefold()


def _row_to_dict(r) -> dict:
    d = dict(r)
    d["auto"] = bool(d.get("auto"))
    return d


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("/glossary/{project_id}")
def list_terms(project_id: str):
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM glossary_terms WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("/glossary/{project_id}")
def add_term(project_id: str, term: GlossaryTerm):
    if not term.source.strip() or not term.target.strip():
        raise HTTPException(
            status_code=400,
            detail="Glossary terms need a source and a target. Leave note blank if you don't want one.",
        )
    term_id = str(uuid.uuid4())[:12]
    now = time.time()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO glossary_terms (id, project_id, source, target, note, auto, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (term_id, project_id, term.source.strip(), term.target.strip(), term.note.strip(), now),
        )
    return {"id": term_id, "project_id": project_id, **term.model_dump(), "auto": False, "created_at": now}


@router.put("/glossary/{project_id}/{term_id}")
def update_term(project_id: str, term_id: str, patch: GlossaryTermUpdate):
    fields = []
    params = []
    for col in ("source", "target", "note"):
        val = getattr(patch, col)
        if val is None:
            continue
        if col in ("source", "target") and not val.strip():
            raise HTTPException(
                status_code=400,
                detail=f"Glossary {col} can't be empty.",
            )
        fields.append(f"{col} = ?")
        params.append(val.strip())
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="PUT glossary term body was empty. Include at least one of: source, target, notes — or DELETE the term to remove it.",
        )
    params += [term_id, project_id]
    with db_conn() as conn:
        cur = conn.execute(
            f"UPDATE glossary_terms SET {', '.join(fields)} WHERE id = ? AND project_id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="That term isn't in this project's glossary. It may have been deleted from another tab.",
            )
        row = conn.execute(
            "SELECT * FROM glossary_terms WHERE id = ? AND project_id = ?", (term_id, project_id)
        ).fetchone()
    return _row_to_dict(row)


@router.delete("/glossary/{project_id}/{term_id}")
def delete_term(project_id: str, term_id: str):
    with db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM glossary_terms WHERE id = ? AND project_id = ?",
            (term_id, project_id),
        )
    return {"deleted": cur.rowcount > 0}


@router.delete("/glossary/{project_id}")
def clear_terms(project_id: str, only_auto: bool = False):
    """Clear every term. `only_auto=true` keeps user-added entries."""
    with db_conn() as conn:
        if only_auto:
            cur = conn.execute(
                "DELETE FROM glossary_terms WHERE project_id = ? AND auto = 1",
                (project_id,),
            )
        else:
            cur = conn.execute(
                "DELETE FROM glossary_terms WHERE project_id = ?",
                (project_id,),
            )
    return {"deleted": cur.rowcount}


# ── LLM auto-extract ─────────────────────────────────────────────────────────


_AUTO_EXTRACT_PROMPT = """\
You are a dubbing terminology editor. The user gives you source-language
segments from a video. Identify proper nouns (character names, places,
brands, organisations) and recurring technical / domain-specific terms that
MUST be translated consistently.

For each term, propose a target-language translation. Omit common nouns,
filler words, and anything that's already trivially consistent. Omit entries
that are identical in both languages UNLESS the source is a proper noun that
should be preserved verbatim.

Reply with ONE entry per line in this exact format (no preamble, no JSON, no
numbering, no quotes):

SOURCE || TARGET || one-line note (or empty)

Keep the list to at most {max_terms} entries. Prefer shorter is better."""


@router.post("/glossary/{project_id}/auto-extract")
def auto_extract(project_id: str, req: AutoExtractRequest):
    """Ask the LLM to propose glossary entries from the project's source segments.

    Writes them as `auto=1` rows. Existing terms with the same (source,target)
    are NOT duplicated. Returns the full current glossary after the pass.
    """
    # Resolved through the LLM Skills registry so auto-extract can be toggled
    # or routed to its own provider (Settings → LLM Skills) independently of
    # the translation pipeline. None == disabled or no provider configured.
    from services import llm_skills

    handle = llm_skills.resolve_skill_client("glossary_extract")
    if handle is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Auto-extract needs an LLM. Set one up in Settings → LLM Providers "
                "(pick a provider, add its key, choose a model, Test) — or use local "
                "Ollama / LM Studio for a fully offline setup — and make sure the "
                "Glossary auto-extract skill is enabled in Settings → LLM Skills, "
                "then try again."
            ),
        )

    # Concat all text — capped so we don't blow the context window on long projects.
    source_text = "\n".join(
        (s.get("text") or "").strip() for s in req.segments if (s.get("text") or "").strip()
    )
    if not source_text.strip():
        return list_terms(project_id)

    max_chars = int(os.environ.get("OMNIVOICE_GLOSSARY_MAX_CHARS", "12000"))
    if len(source_text) > max_chars:
        source_text = source_text[:max_chars] + "\n…[truncated]"

    system = _AUTO_EXTRACT_PROMPT.format(max_terms=req.max_terms)
    user = (
        f"Source language: {req.source_lang}\n"
        f"Target language: {req.target_lang}\n"
        f"Segments:\n{source_text}"
    )

    try:
        res = handle.client.chat.completions.create(
            model=handle.model,
            timeout=handle.timeout,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        from services.llm_backend import _strip_reasoning
        body = _strip_reasoning(res.choices[0].message.content or "", prompt=f"{system}\n{user}")
    except Exception as e:
        logger.warning("auto-extract LLM call failed: %s", e)
        # Scrub the provider error — some OpenAI-compatible providers echo the
        # API key or a user_id in the body, which must not reach the UI verbatim.
        from core.scrub import scrub_provider_error
        from services import llm_providers
        _p = llm_providers.active_provider()
        _key = llm_providers.resolve_api_key(_p) if _p else None
        raise HTTPException(
            status_code=502,
            detail=(
                "LLM didn't respond. Check Settings → Logs → Backend for the trace. "
                f"Error: {scrub_provider_error(e, _key)}"
            ),
        )

    # Parse: SOURCE || TARGET || note (lines are allowed to be sloppy — we're forgiving).
    proposed: list[tuple[str, str, str]] = []
    for line in body.splitlines():
        parts = [p.strip() for p in line.split("||")]
        if len(parts) < 2:
            continue
        src, tgt = parts[0], parts[1]
        note = parts[2] if len(parts) >= 3 else ""
        if not src or not tgt:
            continue
        if src == tgt and len(src.split()) > 1:
            # trivial identity on a multi-word phrase — usually noise
            continue
        proposed.append((src, tgt, note))

    # Dedupe against existing (case-insensitive on source).
    with db_conn() as conn:
        existing = conn.execute(
            "SELECT source FROM glossary_terms WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        # Fold in Python: SQLite LOWER() only folds ASCII, so accented and
        # Cyrillic terms would otherwise slip past the dedupe.
        existing_srcs = {_fold(r["source"]) for r in existing}

        inserted = 0
        now = time.time()
        for src, tgt, note in proposed:
            if _fold(src) in existing_srcs:
                continue
            conn.execute(
                "INSERT INTO glossary_terms (id, project_id, source, target, note, auto, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (str(uuid.uuid4())[:12], project_id, src, tgt, note, now),
            )
            existing_srcs.add(_fold(src))
            inserted += 1

        rows = conn.execute(
            "SELECT * FROM glossary_terms WHERE project_id = ? ORDER BY auto DESC, created_at ASC",
            (project_id,),
        ).fetchall()

    return {
        "project_id": project_id,
        "proposed": len(proposed),
        "inserted": inserted,
        "terms": [_row_to_dict(r) for r in rows],
    }
