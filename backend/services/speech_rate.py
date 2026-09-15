"""
Speech-rate engineering — Phase 4.4 (ROADMAP.md).

Before TTS generation, predict how long the target-language text will take
to read at a natural pace. If it overshoots the source slot (= lip-sync
will drift), ask the LLM to trim filler or slightly reflow. If it undershoots,
expand. Loop until fit or max retries.

Runs as a post-process on Cinematic translator output or manually via
`adjust_for_slot(...)`. Fast mode skips this — the whole point is the
LLM-aware trimming, and Fast-mode users opted out of LLM calls.

Returns the fit text + a `rate_ratio` (text chars / slot-aligned chars).
"""
from __future__ import annotations

import logging
from core.logging_utils import log_safe
from typing import Iterable, Optional

from services.llm_backend import get_active_llm_backend, OffBackend
# Shared LLM-output divergence guard (length window + target-script +
# critique-echo). Lives in translator; translator never imports this module,
# so there is no import cycle.
from services.translator import refine_output_ok

logger = logging.getLogger("omnivoice.speech_rate")

# LLM Skills registry id — Settings → LLM Skills can disable the slot-fit
# LLM pass or route it to a specific provider. Disabled == the no-llm path.
_SKILL_ID = "slot_fitting"

# Per-language read-speed estimates (chars/sec at natural pace, counting
# Python `len()` codepoints — not phonemes or graphemes). These are
# rough; real speakers vary wildly. Numbers below come from a mix of
# Pellegrino et al. 2011 (Cross-language information rate) and informal
# calibration against TTS engine outputs.
#
# Codepoint density matters a lot here because Indic scripts (Devanagari,
# Bengali, Tamil…) encode vowel-marks as separate codepoints, inflating
# `len(text)` for the same spoken duration. Without an explicit entry,
# `expected_duration` falls back to 13.0 cps — which produces ratios
# 1.3-1.7× the truth for Bengali/Hindi/Tamil and forces aggressive
# slot-compression in TTS that the WSOLA stretch then has to repair.
_RATE_CPS = {
    "en": 15.0, "de": 14.0, "fr": 15.0, "es": 15.5, "it": 15.0, "pt": 15.0,
    # CJK — logographic / mora-based scripts, fewer chars per second.
    "ja": 10.0, "ko": 10.0, "zh": 6.0,
    # Indic — Devanagari/Bengali/Tamil/Telugu/etc. compound graphemes
    # decompose into multiple codepoints; spoken syllable rate is
    # closer to English but the codepoint count is higher.
    "hi": 17.0, "bn": 17.0, "ta": 14.0, "te": 14.0, "mr": 16.0,
    "gu": 16.0, "kn": 14.0, "ml": 14.0, "pa": 16.0, "or": 16.0,
    "ur": 13.0,
    # RTL / Semitic — Arabic & Hebrew have shorter codepoint counts per
    # word than English (no vowel chars written) so cps reads lower.
    "ar": 12.0, "he": 12.0, "fa": 13.0,
    # Southeast Asian — Thai is contiguous (no spaces); Vietnamese is
    # concise; Indonesian is concise but Latin-scripted.
    "th": 10.0, "vi": 16.0, "id": 14.0, "ms": 14.0,
    # Slavic + Turkic — agglutinative or compound-heavy; long words.
    "ru": 13.0, "pl": 13.0, "uk": 13.0, "cs": 13.0, "tr": 12.0,
    # Nordic / Greek — close to mainland European baseline.
    "el": 14.0, "nl": 14.0, "sv": 14.0, "no": 14.0, "da": 14.0, "fi": 13.0,
}

# Tolerance window — if predicted ratio is within this of 1.0 we accept.
TOL_LOW = 0.92
TOL_HIGH = 1.08

# Max LLM attempts per segment. Past this we just return the best we got.
MAX_ATTEMPTS = 3

# Acceptance window for real TTS measurements. One rewrite is made between
# renders; repeating guesses inside a call would discard the useful evidence.
MEASURED_TOL_LOW = 0.9
MEASURED_TOL_HIGH = 1.04


def expected_duration(text: str, lang: str = "en") -> float:
    """Rough CPS-based duration estimate. Returns seconds."""
    cps = _RATE_CPS.get(lang.split("-")[0].lower(), 13.0)
    return len(text) / max(1.0, cps)


def rate_ratio(text: str, slot_seconds: float, lang: str = "en") -> float:
    """How far off the text is from the slot. 1.0 = perfect."""
    if slot_seconds <= 0:
        return 1.0
    return expected_duration(text, lang) / slot_seconds


_TRIM_PROMPT = """\
You are a dubbing writer. The user will give you a translated line + the exact
time slot it must fit. The current line is TOO LONG — trim filler words,
tighten phrasing, or drop less essential clauses while preserving the meaning.
Never change character names or proper nouns.
Reply with ONLY the new line. No quotes, no commentary."""

_EXPAND_PROMPT = """\
You are a dubbing writer. The user will give you a translated line + the exact
time slot it must fit. The current line is TOO SHORT — add natural filler or
gently flesh out the thought while keeping the meaning the same. Aim for a
reading duration that matches the slot. Never invent new information, names,
or dialogue that is not already in the line; do not more than double the line.
Reply with ONLY the new line. No quotes, no commentary."""

# Below this predicted rate ratio a line can never honestly fill its slot —
# any LLM "expansion" that far would be fabricated dialogue. Skip the expand
# pass entirely and keep the short line (slot-aware TTS absorbs the silence).
_MIN_EXPANDABLE_RATIO = 0.15


_MEASURED_PROMPT = """\
You are a dialogue adaptation agent for precise dubbing. Rewrite the translated
line so the SAME voice can speak it inside the exact target duration. The user
provides the duration measured from a real render, so use the requested length
change as a concrete constraint. Preserve meaning, tone, names, numbers,
technical terms, and the target language. Shorten natural phrasing when long;
gently expand only when short without inventing facts or dialogue.
Reply with ONLY the revised line. No quotes or commentary."""


def adjust_for_measured_slot(
    text: str,
    *,
    slot_seconds: float,
    measured_seconds: float,
    target_lang: str,
    source_text: Optional[str] = None,
    context_before: Optional[str] = None,
    context_after: Optional[str] = None,
    translation_instructions: Optional[str] = None,
) -> dict:
    """Make one evidence-based rewrite between real TTS measurements."""
    text = (text or "").strip()
    slot = max(0.0, float(slot_seconds or 0.0))
    measured = max(0.0, float(measured_seconds or 0.0))
    ratio = measured / slot if slot else 1.0
    base = {
        "text": text,
        "measured_seconds": round(measured, 3),
        "target_seconds": round(slot, 3),
        "measured_ratio": round(ratio, 3),
        "changed": False,
    }
    if not text or slot <= 0 or measured <= 0:
        return {**base, "error": "invalid-timing"}
    if MEASURED_TOL_LOW <= ratio <= MEASURED_TOL_HIGH:
        return {**base, "error": "already-fits"}
    # Leave honest silence for extremely short dialogue instead of inventing
    # speech merely to fill a long shot.
    if ratio < 0.45:
        return {**base, "error": "fit-skip-short"}

    from services import llm_skills
    llm = llm_skills.skill_backend(_SKILL_ID, active=lambda: get_active_llm_backend())
    if isinstance(llm, OffBackend):
        return {**base, "error": "no-llm"}

    desired = max(0.2, min(2.0, slot / measured))
    user_lines = [
        f"Target language: {target_lang}",
        f"Exact target duration: {slot:.2f}s",
        f"Measured duration of this line: {measured:.2f}s",
        f"Measured ratio: {ratio:.3f} (1.000 is exact)",
        f"Requested text-length factor: about {desired:.3f}x",
        f"Current translated line: {text}",
    ]
    if source_text:
        user_lines.append(f"Source line (meaning authority): {source_text}")
    if context_before:
        user_lines.append(f"Previous source line (context only): {context_before}")
    if context_after:
        user_lines.append(f"Next source line (context only): {context_after}")
    try:
        reply = llm.chat(
            system=_MEASURED_PROMPT + ("\nUser translation style brief (preserve meaning and output format): " + translation_instructions if translation_instructions else ""),
            user="\n".join(user_lines),
            temperature=0.15,
        )
    except Exception:
        logger.warning("measured slot-fit provider failed")
        return {**base, "error": "fit-provider-failed"}
    candidate = (reply or "").strip()
    if not candidate or candidate == text:
        return {**base, "error": "fit-unchanged"}
    ok, reason = refine_output_ok(text, candidate, target_lang)
    if not ok:
        logger.warning("measured slot-fit reply rejected (%s)", log_safe(reason))
        return {**base, "error": "fit-diverged"}
    return {**base, "text": candidate, "changed": True}


async def adjust_for_measured_slot_many(
    items: Iterable[tuple], *, executor=None, concurrency: Optional[int] = None,
    translation_instructions: Optional[str] = None,
) -> dict:
    """Run one bounded measured rewrite per segment, keyed by segment id."""
    import asyncio
    import os

    rows = list(items)
    if not rows:
        return {}
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(concurrency or int(os.environ.get("OMNIVOICE_LLM_CONCURRENCY", "6")))

    async def _one(key, line, slot, measured, lang, source, before, after):
        async with sem:
            result = await loop.run_in_executor(
                executor,
                lambda: adjust_for_measured_slot(
                    line,
                    slot_seconds=slot,
                    measured_seconds=measured,
                    target_lang=lang,
                    source_text=source,
                    context_before=before,
                    context_after=after,
                    translation_instructions=translation_instructions,
                ),
            )
        return key, result

    return dict(await asyncio.gather(*(_one(*row) for row in rows)))


def adjust_for_slot(
    text: str,
    *,
    slot_seconds: float,
    target_lang: str,
    source_text: Optional[str] = None,
    strict: bool = False,
) -> dict:
    """Return `{text, rate_ratio, attempts, error?}`.

    Falls back to the input text if the LLM is off or the loop gives up.

    ``strict`` (Autofit mode) changes exactly one thing: the accepted upper
    bound is 1.0 instead of ``TOL_HIGH`` — the line must fit *within* the
    slot, never overrun it — so the target-language reading time can't exceed
    the segment and push the video timing out. Lines under ``TOL_LOW`` still
    go through the LLM expand pass in strict mode too (same as loose mode);
    padding is bounded by the divergence guard below, and a line under
    ``_MIN_EXPANDABLE_RATIO`` is never expanded at all — it could only "fill"
    the slot with fabricated dialogue, so it stays short. Best-effort: after
    ``MAX_ATTEMPTS`` it returns the closest candidate seen, so a stubborn line
    degrades gracefully.

    Every LLM reply is validated with ``translator.refine_output_ok`` against
    the ORIGINAL input ``text`` (not the previous candidate — divergence
    compounds across attempts otherwise). A reply that fails the guard is
    discarded: the attempt is burned, ``current``/``best`` stay put, and if
    nothing valid ever came back the input text is returned with
    ``error="fit-diverged"`` — a hallucinating model can no longer invent the
    dub line (v0.3.9 field report).
    """
    tol_high = 1.0 if strict else TOL_HIGH
    initial_ratio = rate_ratio(text, slot_seconds, target_lang)
    if TOL_LOW <= initial_ratio <= tol_high:
        return {"text": text, "rate_ratio": initial_ratio, "attempts": 0}
    if initial_ratio < _MIN_EXPANDABLE_RATIO:
        return {
            "text": text,
            "rate_ratio": initial_ratio,
            "attempts": 0,
            "error": "fit-skip-short",
        }

    from services import llm_skills
    # `active=` forwards this module's (monkeypatch-able) name so the
    # no-override path is byte-identical to the pre-skills behavior.
    llm = llm_skills.skill_backend(_SKILL_ID, active=lambda: get_active_llm_backend())
    if isinstance(llm, OffBackend):
        return {
            "text": text,
            "rate_ratio": initial_ratio,
            "attempts": 0,
            "error": "no-llm",
        }

    current = text
    best = (current, initial_ratio)
    diverged = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        r = rate_ratio(current, slot_seconds, target_lang)
        if TOL_LOW <= r <= tol_high:
            return {"text": current, "rate_ratio": r, "attempts": attempt - 1}

        system = _TRIM_PROMPT if r > 1.0 else _EXPAND_PROMPT
        user_lines = [
            f"Target language: {target_lang}",
            f"Slot: {slot_seconds:.2f}s",
            f"Current line: {current}",
            f"Current reading duration: ~{expected_duration(current, target_lang):.2f}s "
            f"(ratio {r:.2f})",
        ]
        if source_text:
            user_lines.append(f"Source line (for meaning): {source_text}")

        try:
            next_text = llm.chat(
                system=system, user="\n".join(user_lines),
                temperature=0.2,  # pinned like the Fast path — default 1.0 drifts/invents
            )
        except Exception:
            logger.warning("speech-rate provider attempt %d failed", attempt)
            return {
                "text": best[0], "rate_ratio": best[1],
                "attempts": attempt - 1, "error": "fit-provider-failed",
            }

        if next_text and next_text.strip():
            candidate = next_text.strip()
            # Divergence guard — validate against the ORIGINAL text, not
            # `current`: each accepted reply becomes the next prompt's input,
            # so per-step checks would let drift compound across attempts.
            ok, reason = refine_output_ok(text, candidate, target_lang)
            if not ok:
                diverged = True
                logger.warning(
                    "speech-rate attempt %d rejected (%s) — discarding candidate",
                    attempt, log_safe(reason),
                )
                continue  # attempt burned; current/best untouched
            current = candidate
            new_r = rate_ratio(current, slot_seconds, target_lang)
            # Keep the best candidate seen so far in case we exhaust retries.
            if abs(new_r - 1.0) < abs(best[1] - 1.0):
                best = (current, new_r)

    out = {
        "text": best[0],
        "rate_ratio": best[1],
        "attempts": MAX_ATTEMPTS,
    }
    # Every usable reply diverged and the input text survived unchanged —
    # surface it on the row (rate_error in dub_translate, like fit-budget).
    if diverged and best[0] == text:
        out["error"] = "fit-diverged"
    return out


def adjust_many(pairs: Iterable[tuple[str, float, str, Optional[str]]]) -> list[dict]:
    """Apply `adjust_for_slot` over many items synchronously.

    `pairs`: iterable of `(text, slot_seconds, target_lang, source_text_or_None)`.
    """
    return [
        adjust_for_slot(t, slot_seconds=s, target_lang=tl, source_text=src)
        for (t, s, tl, src) in pairs
    ]


async def adjust_for_slot_many(
    items: Iterable[tuple],
    *,
    executor=None,
    concurrency: Optional[int] = None,
    deadline: Optional[float] = None,
    loop=None,
) -> dict:
    """Fan `adjust_for_slot` out across many segments concurrently, bounded by a
    shared wall-clock ``deadline``.

    ``items``: iterable of ``(key, text, slot_seconds, target_lang,
    source_text_or_None, strict)``. Returns ``{key: adjust_for_slot_result}``.

    Why this exists: the Autofit fit pass used to run one `adjust_for_slot` per
    segment *sequentially* and *outside* any budget, so a 50-segment dub against
    a slow/rate-limited LLM spun ~50×(per-call timeout) unbounded. Here every
    segment runs on the executor under a bounded ``asyncio.Semaphore``, and any
    segment still running when the shared ``deadline`` passes degrades to a
    no-fit result (input text kept, predicted ``rate_ratio``, ``error`` =
    ``"fit-budget"``) instead of hanging the translate. ``deadline`` is an
    absolute ``loop.time()``; ``None`` disables the bound (run to completion).
    """
    import asyncio
    import os

    loop = loop or asyncio.get_running_loop()
    items = list(items)
    if not items:
        return {}
    sem = asyncio.Semaphore(concurrency or int(os.environ.get("OMNIVOICE_LLM_CONCURRENCY", "6")))

    async def _one(key, text, slot, tgt, src, strict):
        async with sem:
            res = await loop.run_in_executor(
                executor,
                lambda: adjust_for_slot(
                    text, slot_seconds=slot, target_lang=tgt,
                    source_text=src, strict=strict,
                ),
            )
        return key, res

    def _degraded(text, slot, tgt) -> dict:
        return {
            "text": text,
            "rate_ratio": rate_ratio(text, slot, tgt),
            "attempts": 0,
            "error": "fit-budget",
        }

    tasks = [asyncio.ensure_future(_one(*it)) for it in items]

    if deadline is None:
        pairs_out = await asyncio.gather(*tasks)
        return dict(pairs_out)

    timeout = max(0.0, deadline - loop.time())
    done, _pending = await asyncio.wait(tasks, timeout=timeout)
    out: dict = {}
    for task, it in zip(tasks, items):
        key, text, slot, tgt = it[0], it[1], it[2], it[3]
        if task in done and not task.cancelled():
            try:
                k, res = task.result()
                out[k] = res
                continue
            except Exception as e:  # noqa: BLE001 — one slow seg must not sink the pass
                logger.warning("fit segment %s failed: %s", log_safe(key), log_safe(e))
        else:
            task.cancel()  # stop awaiting; the executor thread is abandoned (#730 pattern)
        out[key] = _degraded(text, slot, tgt)
    return out
