"""
Two-stage translation quality for the LLM dub engine (provider="openai").

Stage 1 — auto-glossary. ONE up-front LLM pass over the full transcript
extracts a short theme summary plus a source→target terminology map for the
target language. The caller merges it with the user's manual glossary
(user entries always win) and injects the result into every per-segment
translation prompt, so recurring names/terms are rendered the same way in
segment 3 and segment 300. The extraction result is cached on the dub job
dict (``job["translation_context"][target_lang]``) and rides the existing
``job_data`` JSON blob — no schema change; a transcript fingerprint keys the
cache so edited segments re-extract.

Stage 2 — reflect pass. After a segment's direct LLM translation, a
critique-then-rewrite step reviews the draft for wordiness / stiff or
unnatural register and produces the final natural line. It runs on the SAME
client/model the translation used (the dub_translation skill's provider).

Failure policy for BOTH stages: refinement must never fail a segment. Any
error, timeout, empty output, or divergent rewrite silently keeps the direct
translation — callers get ``None`` back and move on.

MT engines (argos/nllb/google/deepl/…) never reach this module: they have no
prompts to inject into and no LLM to critique with. The Cinematic/Autofit
refine for those engines lives in ``services/translator.py``.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Iterable, Optional

logger = logging.getLogger("omnivoice.translation_quality")

# ── Prompts ──────────────────────────────────────────────────────────────────
# The context pass runs ONCE per (job, target language, transcript); the
# reflect prompts run twice per segment — keep them short, verbosity = wall time.

_CONTEXT_PROMPT = """\
You are a dubbing terminology editor preparing a translation brief. The user
gives you the full source-language transcript of one video. Reply in this
exact plain-text format (no JSON, no code fences, no commentary):

THEME: one or two sentences — what the video is about, its register
(casual / formal / technical) and audience.
TERM: SOURCE || TARGET
TERM: SOURCE || TARGET

TERM lines list proper nouns (people, places, brands, product names) and
recurring domain terms that must be translated identically every time, each
with your preferred {target_name} rendering. At most {max_terms} TERM lines;
fewer is better. Skip one-off words and anything trivially consistent."""

_REVIEW_PROMPT = """\
You are a dubbing script reviewer. The user gives you a source line and its
draft {target_name} translation. In 1-2 short sentences, point out where the
draft is wordy, stiff, or uses a register nobody would use in spoken
dialogue, and whether recurring terms follow the brief. If the draft already
sounds natural, say so. Reply ONLY with the critique — no headers, no lists,
no code fences."""

_POLISH_PROMPT = """\
You are a dubbing script writer. Rewrite the draft translation using the
reviewer's notes so it reads like natural spoken {target_name}. Keep the
meaning faithful to the source line, keep required terminology, and never add
content that is not in the source. Prefer the same length or shorter than the
draft. The output MUST stay in the same language and script as the draft —
never switch language or transliterate. Reply ONLY with the final translation
— no quotes, no notes, no commentary."""


def _chat(client, model: str, timeout: float, *, system: str, user: str) -> str:
    """One-shot chat completion on the caller's client. Raises on failure."""
    res = client.chat.completions.create(
        model=model,
        timeout=timeout,
        temperature=0.2,  # pinned like the direct-translate path — 1.0 drifts
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    from services.llm_backend import _strip_reasoning
    return _strip_reasoning(res.choices[0].message.content or "", prompt=f"{system}\n{user}")


# ── Stage 1: auto-glossary (theme + terminology) ────────────────────────────


def transcript_fingerprint(segment_texts: Iterable[str]) -> str:
    """Stable hash of the transcript, so the per-job context cache invalidates
    when the user edits segments between translate runs."""
    h = hashlib.sha256()
    for t in segment_texts:
        h.update((t or "").strip().encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def extract_context_sync(
    client,
    model: str,
    timeout: float,
    *,
    segment_texts: Iterable[str],
    source_lang: str,
    target_lang: str,
    source_name: Optional[str] = None,
    target_name: Optional[str] = None,
    max_terms: int = 30,
) -> Optional[dict]:
    """One LLM pass over the whole transcript → ``{"theme", "terms"}``.

    ``terms`` is ``[{"source", "target"}]``. Returns None on ANY failure or
    when the response yields neither a theme nor terms — the caller proceeds
    without context, never errors. Blocking; run in an executor.
    """
    text = "\n".join(t.strip() for t in segment_texts if t and t.strip())
    if not text:
        return None
    # Same cap as the explicit glossary auto-extract endpoint — one shared
    # knob for "how much transcript may ride a single LLM context call".
    try:
        max_chars = int(os.environ.get("OMNIVOICE_GLOSSARY_MAX_CHARS", "12000"))
    except ValueError:
        max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"

    system = _CONTEXT_PROMPT.format(
        target_name=target_name or target_lang, max_terms=max_terms,
    )
    user = (
        f"Source language: {source_name or source_lang}\n"
        f"Target language: {target_name or target_lang}\n"
        f"Transcript:\n{text}"
    )
    try:
        body = _chat(client, model, timeout, system=system, user=user)
    except Exception as e:  # noqa: BLE001 — context is an enhancement, never a gate
        logger.warning("auto-glossary context pass failed: %s", e)
        return None

    theme = ""
    terms: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("THEME:"):
            theme = line[len("THEME:"):].strip()
            continue
        if upper.startswith("TERM:"):
            line = line[len("TERM:"):].strip()
        if "||" not in line:
            continue
        parts = [p.strip() for p in line.split("||")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        terms.append({"source": parts[0], "target": parts[1]})
        if len(terms) >= max_terms:
            break
    if not theme and not terms:
        logger.warning("auto-glossary context pass returned nothing parseable")
        return None
    return {"theme": theme, "terms": terms}


def merge_glossary(
    user_terms: Optional[Iterable[dict]],
    auto_terms: Optional[Iterable[dict]],
) -> list[dict]:
    """Merge manual + auto glossaries. User entries ALWAYS win: an auto term
    whose source matches a user source (case-insensitive) is dropped."""
    merged: list[dict] = []
    seen: set[str] = set()
    for entry in user_terms or []:
        src = (entry.get("source") or "").strip()
        tgt = (entry.get("target") or "").strip()
        if not src or not tgt:
            continue
        merged.append(entry)
        seen.add(src.lower())
    for entry in auto_terms or []:
        src = (entry.get("source") or "").strip()
        tgt = (entry.get("target") or "").strip()
        if not src or not tgt or src.lower() in seen:
            continue
        merged.append({"source": src, "target": tgt})
        seen.add(src.lower())
    return merged


def context_clause(theme: str, terms: Optional[Iterable[dict]]) -> str:
    """Prompt fragment carrying the theme + merged glossary into every
    per-segment translation prompt. Empty string when there's nothing."""
    parts: list[str] = []
    theme = (theme or "").strip()
    if theme:
        parts.append(f"Video context: {theme}")
    lines = []
    for entry in terms or []:
        src = (entry.get("source") or "").strip()
        tgt = (entry.get("target") or "").strip()
        if not src or not tgt:
            continue
        note = (entry.get("note") or "").strip()
        lines.append(f"- {src} → {tgt}" + (f"  (note: {note})" if note else ""))
    if lines:
        parts.append(
            "Terminology — render every occurrence of a source term exactly "
            "as its target:\n" + "\n".join(lines)
        )
    return "\n".join(parts)


# ── Stage 2: reflect pass (critique → rewrite) ──────────────────────────────


def reflect_translation_sync(
    client,
    model: str,
    timeout: float,
    *,
    source_text: str,
    direct_text: str,
    source_lang: str,
    target_lang: str,
    target_name: Optional[str] = None,
    extra_clause: str = "",
) -> Optional[str]:
    """Critique-then-rewrite the direct translation of one segment.

    Returns the polished line, or None whenever the direct translation should
    stand: any LLM failure/timeout, an empty rewrite, or a rewrite that
    diverged from the draft (wrong script, runaway length, critique echoed
    back — the shared ``refine_output_ok`` guard). Never raises. Blocking;
    run in an executor.
    """
    if not direct_text or not direct_text.strip():
        return None
    tgt_name = target_name or target_lang

    def _with_clause(base: str) -> str:
        return base + "\n\n" + extra_clause if extra_clause.strip() else base

    try:
        review_user = (
            f"Source ({source_lang}): {source_text}\n"
            f"Draft translation ({target_lang}): {direct_text}"
        )
        critique = _chat(
            client, model, timeout,
            system=_with_clause(_REVIEW_PROMPT.format(target_name=tgt_name)),
            user=review_user,
        )
        polish_user = review_user + f"\nReviewer's notes: {critique}"
        polished = _chat(
            client, model, timeout,
            system=_with_clause(_POLISH_PROMPT.format(target_name=tgt_name)),
            user=polish_user,
        )
    except Exception as e:  # noqa: BLE001 — refinement must never fail a segment
        logger.warning("reflect pass failed (%s) — keeping direct translation", e)
        return None

    polished = (polished or "").strip()
    if not polished or polished == direct_text:
        return None
    # Same divergence guard the Cinematic ADAPT step uses: wrong script,
    # runaway length, or the critique leaking through as the "translation".
    from services.translator import refine_output_ok

    ok, reason = refine_output_ok(direct_text, polished, target_lang, critique=critique)
    if not ok:
        logger.warning(
            "reflect pass diverged for %s (%s) — keeping direct translation",
            target_lang, reason,
        )
        return None
    return polished
