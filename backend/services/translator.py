"""
Cinematic translation pipeline — Phase 1.1 (ROADMAP.md).

Takes the literal translation of a segment (from any provider — Argos, Google,
NLLB, OpenAI, …) and runs it through a 3-step LLM chain:

    1. LITERAL    — already done by the provider caller; passed in as input.
    2. REFLECT    — LLM critiques the literal against tone, idiom, length,
                    pacing, and any project glossary.
    3. ADAPT      — LLM rewrites for cinematic delivery using the critique.

Output contract per segment:

    {
      "id":        seg.id,
      "text":      final adapted text,       ← what the dub uses
      "literal":   step-1 text,              ← kept for UI "3-column view"
      "critique":  step-2 text,              ← kept for UI "3-column view"
    }

Graceful degradation: if the LLM is unreachable / unconfigured, each segment
falls back to the literal text with a `translate_error` marker so the UI can
surface "Cinematic unavailable — showing Fast result for N segments".

The reflect + adapt calls go through an OpenAI-compatible client, configurable
via env:

    TRANSLATE_BASE_URL   # default: https://api.openai.com/v1
    TRANSLATE_API_KEY    # or OPENAI_API_KEY
    TRANSLATE_MODEL      # default: gpt-4o-mini
    OMNIVOICE_LLM_TIMEOUT=45          # seconds per LLM call

Works with real OpenAI, Ollama (base_url=http://localhost:11434/v1), LM Studio,
Together, Anyscale — anything that speaks the OpenAI chat-completion shape.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Iterable, Optional

logger = logging.getLogger("omnivoice.translator")

# ── Prompts ──────────────────────────────────────────────────────────────────
# Kept short + direct. These run N × 2 times per dub, so verbosity = wall time.

_REFLECT_PROMPT = """\
You are a professional dubbing script editor. The user will give you a source
line and its literal translation. Critique the literal translation in 2-3
crisp sentences, focusing on:
- natural idiom in the target language
- emotional tone (does it match what the speaker would convey?)
- length (will it fit in the same time slot as the source?)
- any proper nouns or recurring terms that should stay consistent
Reply ONLY with the critique — no headers, no bullet points, no code fences."""

_ADAPT_PROMPT = """\
You are a cinematic dubbing writer. Rewrite the literal translation using the
editor's critique so it sounds natural, in-character, and fits the speaker's
time slot. Keep meaning faithful but prefer native idiom over word-for-word
accuracy. Never introduce facts, names, or dialogue that are not present in
the source line. The output MUST be written in the same target language and
script as the literal translation — never switch language or transliterate.
Reply ONLY with the adapted translation — no quotes, no headers, no code
fences, no commentary."""

# Han letters: CJK unified ideographs (basic, extension A, supplementary
# extensions B onward), compatibility ideographs, and the ideographic iteration
# and closing marks (U+3005, U+3006) that ``str.isalpha`` counts as letters.
_HAN_RANGES = (
    (0x3005, 0x3006),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x323AF),
)
# Kana: hiragana and katakana with their marks, katakana phonetic extensions,
# halfwidth katakana and the historic kana supplements.
_KANA_RANGES = (
    (0x3040, 0x30FF),
    (0x31F0, 0x31FF),
    (0xFF66, 0xFF9F),
    (0x1B000, 0x1B16F),
)

# Letters each distinctive-script target may be written in. The single source
# for the primary translation gate (dub_translate.LANG_REQUIRED_SCRIPT) and the
# Cinematic/Autofit refine gate below. Japanese is written in kana AND kanji;
# counting kana alone rejected every kanji-heavy Japanese line (#2576).
SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "hi":    ((0x0900, 0x097F),),
    "ar":    ((0x0600, 0x06FF),),
    "zh":    _HAN_RANGES,
    "zh-CN": _HAN_RANGES,
    "ja":    _KANA_RANGES + _HAN_RANGES,
    "ko":    ((0xAC00, 0xD7AF),),
    "th":    ((0x0E00, 0x0E7F),),
    "ru":    ((0x0400, 0x04FF),),
    "uk":    ((0x0400, 0x04FF),),
}


# Han-only text is Chinese, not Japanese: real Japanese prose carries kana
# (particles, inflections). Short all-kanji lines (names, headings) are common
# Japanese, so only a run of this many Han letters with zero kana is rejected.
_JA_HAN_ONLY_MIN = 6


def _in_ranges(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in ranges)


def script_ratio(text: str, code: str) -> float:
    """Fraction of letters in ``text`` inside the scripts expected for ``code``.

    Punctuation, digits and whitespace are excluded from the denominator.
    Targets without a distinctive script (and text without letters) score 1.0.
    """
    ranges = SCRIPT_RANGES.get(code)
    if not ranges:
        return 1.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    if code == "ja" and not any(_in_ranges(c, _KANA_RANGES) for c in letters):
        if sum(1 for c in letters if _in_ranges(c, _HAN_RANGES)) >= _JA_HAN_ONLY_MIN:
            return 0.0
    inside = sum(1 for c in letters if _in_ranges(c, ranges))
    return inside / len(letters)


def _looks_like_target_script(text: str, code: str, threshold: float = 0.5) -> bool:
    return script_ratio(text, code) >= threshold


# ── Divergence guard (shared with speech_rate's Autofit fit pass) ────────────
# For every Latin-script target `_looks_like_target_script` passes ANY text
# unconditionally (no `_SCRIPT_RANGES` entry), so it was the only — and for
# es/de/fr/… a no-op — gate on the ADAPT/fit LLM output. These checks close
# that gap for the whole class: runaway length (hallucinated dialogue,
# refusals, commentary) and the REFLECT critique echoed back as the "line".

_SHORT_REF_CHARS = 20        # below this, a length *ratio* is meaningless
_SHORT_REF_ABS_SLACK = 120   # …use an absolute cap instead: ref + this many chars


def _refine_ratio_bounds() -> tuple[float, float]:
    """Accepted ``len(candidate)/len(reference)`` window for LLM refine output.
    Anything outside is treated as divergence and the caller degrades to its
    input text. Defaults [0.4, 2.5]; env-tunable like the cinematic budget."""
    try:
        lo = float(os.environ.get("OMNIVOICE_REFINE_RATIO_MIN", "0.4"))
    except ValueError:
        lo = 0.4
    try:
        hi = float(os.environ.get("OMNIVOICE_REFINE_RATIO_MAX", "2.5"))
    except ValueError:
        hi = 2.5
    return lo, hi


def _norm_overlap_text(s: str) -> str:
    return " ".join(s.lower().split())


def _echoes_critique(candidate: str, critique: str) -> bool:
    """True when the "adaptation" is really the REFLECT critique leaking through.

    Deterministic on purpose (no fuzzy matching): exact match after
    case/whitespace normalization; containment — the full critique inside the
    candidate always counts, the candidate inside the critique only when it
    covers most of it (critiques legitimately quote short phrases from the
    line); or >0.8 token-set overlap.
    """
    c = _norm_overlap_text(candidate)
    k = _norm_overlap_text(critique)
    if not c or not k:
        return False
    if c == k:
        return True
    if k in c:                                  # critique embedded in the output
        return True
    if c in k and len(c) >= 0.6 * len(k):       # output ≈ a big chunk of the critique
        return True
    ct, kt = set(c.split()), set(k.split())
    union = ct | kt
    return bool(union) and len(ct & kt) / len(union) > 0.8


def refine_output_ok(
    reference: str,
    candidate: str,
    target_lang: str,
    *,
    critique: str | None = None,
    max_ratio: float | None = None,
) -> tuple[bool, str | None]:
    """Sanity-check one LLM refine output against the text it was rewriting.

    Shared by the Cinematic ADAPT step here and by ``speech_rate``'s Autofit
    fit pass (speech_rate imports this; translator never imports speech_rate,
    so there is no cycle). Returns ``(ok, reason)`` — ``reason`` is ``None``
    when ok, otherwise a short machine-readable tag for logs/error mapping.

    Checks, in order:
      • script — candidate must look like the target language's script
        (``_looks_like_target_script``; Latin-script targets pass, as before);
      • length — ``len(candidate)/len(reference)`` must sit inside
        [``OMNIVOICE_REFINE_RATIO_MIN``, ``OMNIVOICE_REFINE_RATIO_MAX``]
        (default 0.4–2.5; ``max_ratio`` overrides the upper bound). References
        shorter than ~20 chars use an absolute cap (reference + 120 chars)
        instead — a two-word line legitimately doubles or halves;
      • critique echo — the candidate must not be the critique itself.
    """
    cand = (candidate or "").strip()
    ref = (reference or "").strip()
    if not cand:
        return False, "empty"
    if not _looks_like_target_script(cand, target_lang):
        return False, f"wrong-script:{target_lang}"
    lo, hi = _refine_ratio_bounds()
    if max_ratio is not None:
        hi = max_ratio
    if ref:
        if len(ref) < _SHORT_REF_CHARS:
            if len(cand) > len(ref) + _SHORT_REF_ABS_SLACK:
                return False, f"length-abs:{len(cand)}>{len(ref)}+{_SHORT_REF_ABS_SLACK}"
        else:
            ratio = len(cand) / len(ref)
            if not (lo <= ratio <= hi):
                return False, f"length-ratio:{ratio:.2f}"
    if critique and _echoes_critique(cand, critique):
        return False, "critique-echo"
    return True, None


# The LLM Skills registry entry this pipeline resolves through — lets the
# user disable Cinematic/Autofit's LLM use or route it to a specific provider
# (Settings → LLM Skills) independently of the other LLM features.
_SKILL_ID = "cinematic_translation"


def _llm_client():
    """Lazy-build the OpenAI-compatible client for the Cinematic skill.

    Resolves through the LLM Skills registry: per-skill provider override →
    global active provider (Settings → LLM Providers). The registry's
    ``custom`` provider still maps ``TRANSLATE_BASE_URL``/``TRANSLATE_API_KEY``,
    so legacy env setups keep working. Returns None if the skill is disabled
    or no provider is configured — the callers' Fast-fallback path.

    The registry builds the client with ``max_retries=0`` (see
    ``llm_skills.resolve_skill_client``) so a 429 + long Retry-After can't make
    one call sleep+retry past the cinematic wall-clock budget from inside a
    single request. The pass-level budget (``cinematic_refine_many``) and the
    per-call timeout stay the only bounds.
    """
    from services import llm_skills
    handle = llm_skills.resolve_skill_client(_SKILL_ID)
    return handle.client if handle is not None else None


def _llm_model() -> str:
    from services import llm_providers, llm_skills
    p = llm_skills.effective_provider(_SKILL_ID)
    if p is not None:
        return llm_providers.resolve_model(p)
    return os.environ.get("TRANSLATE_MODEL", "gpt-4o-mini")


def _llm_timeout() -> float:
    try:
        return float(os.environ.get("OMNIVOICE_LLM_TIMEOUT", "45"))
    except ValueError:
        return 45.0


def _cinematic_budget() -> float:
    """Overall wall-clock cap for a whole cinematic/autofit refine pass (seconds).
    Unfinished segments degrade to their literal (Fast) translation once hit, so
    a slow provider can't hang the translate. Default 180s; <=0 disables."""
    try:
        return float(os.environ.get("OMNIVOICE_CINEMATIC_BUDGET_S", "180"))
    except ValueError:
        return 180.0


def _glossary_text(glossary: Iterable[dict] | None) -> str:
    """Format the project glossary as a preamble for the LLM prompts.

    Empty / None → empty string. Otherwise one "SRC → TGT" per line.
    """
    if not glossary:
        return ""
    lines = []
    for entry in glossary:
        src = (entry.get("source") or "").strip()
        tgt = (entry.get("target") or "").strip()
        if not src or not tgt:
            continue
        note = (entry.get("note") or "").strip()
        lines.append(f"- {src} → {tgt}" + (f"  (note: {note})" if note else ""))
    if not lines:
        return ""
    return (
        "Project glossary — every occurrence of a source term must be rendered "
        "as its target, unless the critique explicitly overrides it:\n"
        + "\n".join(lines)
    )


#: Longest Retry-After we'll honor with an in-place wait. Anything above this
#: means "the provider is down for a while" — fail fast and let the segment
#: degrade to its literal translation instead of stalling the whole dub.
_RETRY_AFTER_CAP_S = 30.0


def _retry_after_seconds(exc) -> float | None:
    """Retry-After from a rate-limit error, or None when this isn't a 429.

    Providers frequently 429 with a *tiny* hint (OpenRouter's free pool says
    "Retry-After: 2"); giving up instantly on those turned a two-second wait
    into a whole failed reflect pass — 6 segments fire concurrently, so one
    throttle window used to take out every segment at once. Defensive on
    purpose: the exception shape differs across openai-lib versions and
    OpenAI-compatible servers, and a parsing surprise must never break the
    caller's own error handling.
    """
    try:
        if getattr(exc, "status_code", None) != 429:
            return None
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        seconds = float(raw) if raw is not None else 2.0
        return max(0.5, min(seconds, _RETRY_AFTER_CAP_S))
    except Exception:  # noqa: BLE001 — a weird header is not worth a crash
        return None


def _chat(client, *, system: str, user: str) -> str:
    """One-shot chat completion. Raises on failure.

    One polite retry on a rate limit: when the provider sends a 429 with a
    bounded Retry-After, wait it out once (plus jitter so the 6-wide
    concurrent segment fan-out doesn't re-stampede the same window) and try
    again. A second 429 propagates — the caller degrades to the literal text.
    """
    attempts = 0
    while True:
        try:
            res = client.chat.completions.create(
                model=_llm_model(),
                timeout=_llm_timeout(),
                temperature=0.2,  # pinned like the Fast path — default 1.0 drifts/invents
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            from services.llm_backend import _strip_reasoning
            return _strip_reasoning(res.choices[0].message.content or "",
                                    prompt=f"{system}\n{user}")
        except Exception as e:  # noqa: BLE001 — re-raised unless a retryable 429
            wait = _retry_after_seconds(e)
            if wait is None or attempts >= 1:
                raise
            attempts += 1
            logger.info("LLM rate-limited; honoring Retry-After=%.1fs (one retry)", wait)
            time.sleep(wait + random.uniform(0.1, 1.0))


# ── Public API ──────────────────────────────────────────────────────────────


def cinematic_available() -> bool:
    """Cheap check so callers can warn early rather than after a full translate run."""
    return _llm_client() is not None


def cinematic_refine_sync(
    source_text: str,
    literal_text: str,
    *,
    source_lang: str,
    target_lang: str,
    glossary: Iterable[dict] | None = None,
    direction: Optional[str] = None,
    dialect_hint: Optional[str] = None,
) -> dict:
    """Blocking: run REFLECT + ADAPT on a single segment.

    Returns `{"text", "literal", "critique"}` on success. On LLM failure,
    returns `{"text": literal_text, "literal": literal_text, "critique": "",
    "error": "…"}` so the caller can keep going and surface a warning.

    Meant to run in a threadpool; the async wrapper below handles dispatch.
    """
    result_ok = {
        "text": literal_text,
        "literal": literal_text,
        "critique": "",
    }
    if not literal_text or not literal_text.strip():
        return result_ok

    client = _llm_client()
    if client is None:
        return {**result_ok, "degraded": "no-llm"}

    glossary_preamble = _glossary_text(glossary)

    # Phase 4.2 — if a direction was supplied, compute a translate hint that
    # feeds into both reflect and adapt prompts. Parser picks up taxonomy
    # tokens via LLM when configured, falls back to a keyword heuristic.
    direction_hint = ""
    if direction and direction.strip():
        try:
            from services.director import parse as _parse_direction
            d = _parse_direction(direction)
            direction_hint = d.translate_hint()
        except Exception as e:
            logger.debug("director parse skipped: %s", e)

    def _with_preamble(base: str) -> str:
        out = base
        if glossary_preamble:
            out = out + "\n\n" + glossary_preamble
        if direction_hint:
            out = out + "\n\nDirection: " + direction_hint
        # #280 item 2 — regional dialect/vocabulary hint (e.g. Argentinian
        # voseo). Caller builds the clause; we just ride it on both prompts.
        if dialect_hint and dialect_hint.strip():
            out = out + "\n\nDialect: " + dialect_hint.strip()
        return out

    # Step 2 — reflect
    try:
        reflect_user = (
            f"Source ({source_lang}): {source_text}\n"
            f"Literal translation ({target_lang}): {literal_text}"
        )
        critique = _chat(client, system=_with_preamble(_REFLECT_PROMPT), user=reflect_user)
    except Exception as e:
        logger.warning("cinematic reflect failed: %s", e)
        return {**result_ok, "degraded": f"reflect: {e}"}

    # Step 3 — adapt
    try:
        adapt_user = (
            f"Source ({source_lang}): {source_text}\n"
            f"Literal translation ({target_lang}): {literal_text}\n"
            f"Editor's critique: {critique}"
        )
        adapted = _chat(client, system=_with_preamble(_ADAPT_PROMPT), user=adapt_user)
    except Exception as e:
        logger.warning("cinematic adapt failed: %s", e)
        return {
            "text": literal_text,
            "literal": literal_text,
            "critique": critique,
            "degraded": f"adapt: {e}",
        }

    final = (adapted or "").strip() or literal_text
    # Refuse adaptations that diverged from the line they were rewriting:
    # wrong script (e.g. a local LLM rewrote a Devanagari line in
    # Latin/German), runaway length (hallucinated dialogue, refusals,
    # commentary — the script check alone passes ANY text for Latin-script
    # targets), or the critique echoed back as the "adaptation". Caller still
    # gets the critique so the UI can show what happened, but the live text
    # falls back to the literal translation rather than corrupting the dub.
    if final is not literal_text:
        ok, reason = refine_output_ok(literal_text, final, target_lang, critique=critique)
        if not ok:
            logger.warning(
                "cinematic adapt diverged for %s (%s) — falling back to literal",
                target_lang, reason,
            )
            wrong_script = (reason or "").startswith("wrong-script")
            return {
                "text": literal_text,
                "literal": literal_text,
                "critique": critique,
                "degraded": (f"adapt-wrong-script:{target_lang}" if wrong_script
                             else "adapt-diverged"),
            }
    return {
        "text": final,
        "literal": literal_text,
        "critique": critique,
    }


async def cinematic_refine_many(
    pairs: list[tuple],
    *,
    source_lang: str,
    target_lang: str,
    glossary: Iterable[dict] | None = None,
    directions: Optional[dict[str, str]] = None,
    dialect_hint: Optional[str] = None,
    executor=None,
    concurrency: int | None = None,
) -> list[dict]:
    """Fan out REFLECT + ADAPT across N segments on `executor`.

    `pairs`: list of `(id, source_text, literal_text)`.
    `directions`: optional `{seg_id: "natural-language direction"}` — when
        present, the matching segment's reflect/adapt prompts get the parsed
        direction hint prepended.
    `dialect_hint`: optional regional-dialect clause (#280) applied to every
        segment's reflect/adapt prompts.
    Returns a list of dicts keyed the same length + order, each carrying
    `id`, `text`, `literal`, `critique`, optional `error`.
    """
    loop = asyncio.get_running_loop()
    directions = directions or {}

    # Bound concurrency so we don't fan out 500 simultaneous requests.
    sem = asyncio.Semaphore(concurrency or int(os.environ.get("OMNIVOICE_LLM_CONCURRENCY", "6")))

    async def _one(seg_id: str, src: str, lit: str) -> dict:
        async with sem:
            res = await loop.run_in_executor(
                executor,
                lambda: cinematic_refine_sync(
                    src, lit,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    glossary=glossary,
                    direction=directions.get(seg_id),
                    dialect_hint=dialect_hint,
                ),
            )
        return {"id": seg_id, **res}

    # Overall wall-clock budget for the whole pass. Per-call timeout + bounded
    # concurrency already cap it, but a slow/rate-limited provider on a large dub
    # can still stall the "Translating…" spinner for minutes. Bound it: segments
    # that finish in time keep their cinematic refine; any still-running segment
    # degrades to its literal (Fast) translation so the translate ALWAYS returns
    # within the budget instead of hanging. 0/negative disables the bound.
    budget = _cinematic_budget()
    tasks = [asyncio.ensure_future(_one(sid, src, lit)) for sid, src, lit in pairs]
    if budget <= 0:
        return await asyncio.gather(*tasks)

    done, pending = await asyncio.wait(tasks, timeout=budget)
    if pending:
        logger.warning(
            "Cinematic pass hit its %.0fs budget with %d/%d segment(s) unfinished "
            "— falling back to the literal translation for those (slow LLM "
            "provider?). Raise OMNIVOICE_CINEMATIC_BUDGET_S or pick a faster "
            "provider.", budget, len(pending), len(tasks),
        )
    out: list[dict] = []
    for task, (sid, _src, lit) in zip(tasks, pairs):
        if task in done and not task.cancelled():
            try:
                out.append(task.result())
                continue
            except Exception as e:  # noqa: BLE001 — never let one seg sink the pass
                logger.warning("cinematic segment %s failed: %s", sid, e)
        else:
            task.cancel()  # stop awaiting; the executor thread is abandoned (#730 pattern)
        # "degraded", not "error": the literal translation is used, so the
        # segment is fully usable — downstream passes (speech-rate fit,
        # duration planning) must still run on it, and the UI must not count
        # it as a failed segment. `error` is reserved for rows with no usable
        # text at all (the base translation itself failed).
        out.append({"id": sid, "text": lit, "literal": lit, "critique": "",
                    "degraded": "cinematic-budget"})
    return out
