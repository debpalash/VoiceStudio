"""Pre-synthesis duration planning for dub segments.

The Smart Fit planner (services/fit_planner.py) reconciles dubbed audio
with the timeline AFTER synthesis — by then a doomed segment has already
burned GPU time and can only be sped up or trimmed. This module predicts
BEFORE TTS whether a translated segment can possibly fit its slot, so the
UI can badge it (and optionally offer a shorter rewrite) while the text is
still cheap to change. It never blocks generation — it informs.

Three pieces, all pure and unit-testable:

1. **Estimator** — predict the natural speech duration of target-language
   text. Self-calibrating: segments already synthesized in this job carry
   ``(chars, natural duration)`` records (written by dub_generate for every
   natural-rate strategy), and the median chars-per-second of those is a
   far better predictor for *this* voice/engine/language than any table.
   With no (or too little) calibration data it falls back to the
   conservative static per-language rate table in ``services.speech_rate``
   (the same one the rate-ratio badge uses).

2. **Classifier** — per segment, compare the estimate against the
   *available* time: the slot plus silence borrowable from the gap to the
   next segment (mirroring fit_planner's slack absorption, but with a
   deliberate cap — see ``GAP_BORROW_MAX_S``). The verdict thresholds are
   derived from the SAME ``FitParams`` caps fit_planner enforces, so:

       fits        need ≤ max_audio_only_rate   — absorbed imperceptibly
       tight       need ≤ what the caps absorb  — audible speed-up and/or
                                                  video slow-down
       impossible  beyond the caps              — fit_planner will trim

3. **Condensation** (optional, caller-gated) — for ``impossible`` segments,
   ask the configured LLM for a meaning-preserving shorter rewrite
   targeting the available duration. Strictly best-effort: no LLM, an LLM
   error, or a divergent reply all degrade to a no-op.

No I/O, no torch; the only side-effectful function is ``condense_for_slot``
(network LLM call), which callers opt into explicitly.
"""
from __future__ import annotations

import math

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from services.fit_planner import MAX_AUDIO_RATE_HARD, FitParams
from services.llm_backend import OffBackend, get_active_llm_backend
from services.speech_rate import expected_duration
# Shared LLM-output divergence guard (target-script + length window +
# critique-echo) — same seam speech_rate's Autofit pass uses.
from services.translator import refine_output_ok

logger = logging.getLogger("omnivoice.duration_planner")

# LLM Skills registry id — condensation is the same "make the line fit its
# slot" skill family as the Autofit pass, so it routes (and can be disabled)
# through the same Settings → LLM Skills entry.
_SKILL_ID = "slot_fitting"

# ── Calibration ─────────────────────────────────────────────────────────

# A calibration only counts once this many usable samples exist — below
# that, one odd segment (a sound effect, a mumbled clone ref) would swing
# the estimate more than the static table's error.
MIN_CALIBRATION_SAMPLES = 3
# Per-sample sanity floor: shorter/tinier segments carry more silence
# padding and TTS ramp-up than speech, so their chars/sec is noise.
MIN_SAMPLE_DUR_S = 0.4
MIN_SAMPLE_CHARS = 4

# How far a segment may borrow into the silent gap before the next segment
# (or the video tail). fit_planner itself absorbs the WHOLE gap, so this cap
# makes the pre-synthesis verdict deliberately conservative: a huge gap
# (scene change, music bed) is real slack at mix time, but planning speech
# to sprawl seconds past its slot is rarely what the user wants — and the
# estimate is fuzzy enough that promising it would over-sell.
GAP_BORROW_MAX_S = 3.0


@dataclass(frozen=True)
class Calibration:
    """Observed speech rate for one (job, language) pair."""
    cps: float      # chars per second at natural TTS rate
    samples: int    # how many segments backed it


def calibrate_cps(samples: Iterable[tuple[float, float]]) -> Optional[Calibration]:
    """Derive a chars-per-second calibration from ``(chars, natural_dur_s)``
    pairs of already-synthesized segments.

    Median of the per-segment rates — robust against the occasional outlier
    (a segment that's mostly a breath, an engine hiccup) that would drag a
    mean. Returns None when fewer than ``MIN_CALIBRATION_SAMPLES`` usable
    samples exist; callers then fall back to the static table.
    """
    rates: list[float] = []
    for chars, dur in samples:
        try:
            chars = float(chars)
            dur = float(dur)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(chars) or not math.isfinite(dur):
            continue
        if dur >= MIN_SAMPLE_DUR_S and chars >= MIN_SAMPLE_CHARS:
            rates.append(chars / dur)
    if len(rates) < MIN_CALIBRATION_SAMPLES:
        return None
    rates.sort()
    n = len(rates)
    mid = n // 2
    median = rates[mid] if n % 2 else (rates[mid - 1] + rates[mid]) / 2.0
    if median <= 0:
        return None
    return Calibration(cps=median, samples=n)


def calibration_from_job(job: dict, lang: str) -> Optional[Calibration]:
    """Build a Calibration from the ``seg_natural_durs_by_lang`` records
    dub_generate persists on the job. Tolerates any legacy/partial shape."""
    try:
        recs = (job.get("seg_natural_durs_by_lang") or {}).get(lang) or {}
        return calibrate_cps(
            (r.get("chars", 0), r.get("dur", 0))
            for r in recs.values()
            if isinstance(r, dict)
        )
    except Exception as e:  # noqa: BLE001 — calibration is best-effort by design
        logger.debug("calibration_from_job skipped: %s", e)
        return None


# ── Estimator ───────────────────────────────────────────────────────────


def estimate_natural_duration(
    text: str, lang: str, calibration: Optional[Calibration] = None,
) -> float:
    """Predicted natural-rate speech duration (seconds) of ``text``.

    Calibrated rate when available, else the static per-language table
    (``speech_rate.expected_duration``, 13 cps default for unknown codes).
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    if calibration is not None and calibration.cps > 0:
        return len(text) / calibration.cps
    return expected_duration(text, lang)


# ── Classifier ──────────────────────────────────────────────────────────


def absorb_caps(params: FitParams) -> tuple[float, float]:
    """(fits_cap, absorb_cap) need-ratios aligned with fit_planner.

    ``fits_cap``: up to here the audio-only speed-up is imperceptible.
    ``absorb_cap``: up to here fit_planner's knobs absorb the overrun
    (audio cap × video cap in hybrid mode; the legacy hard audio ceiling
    when video retiming is off). Beyond it, fit_planner trims.
    """
    if params.allow_video_retime:
        return params.max_audio_only_rate, params.audio_rate_cap * params.video_slow_cap
    return params.max_audio_only_rate, MAX_AUDIO_RATE_HARD


def classify_segments(
    segments: list[dict],
    target_lang: str,
    *,
    calibration: Optional[Calibration] = None,
    fit_params: Optional[FitParams] = None,
    total_dur_s: float = 0.0,
    gap_borrow_max_s: float = GAP_BORROW_MAX_S,
) -> list[dict]:
    """Classify each segment's translated text against its timeline slot.

    ``segments``: chronological dicts with ``id``, ``start``, ``end``
    (seconds) and ``text`` (the translated text about to be synthesized).
    ``total_dur_s``: original video duration (0/unknown → the last segment
    gets no tail borrow), mirroring ``fit_planner.plan_fit``.

    Returns one dict per segment::

        {id, status, est_dur_s, available_s, est_overrun_s, calibrated}

    ``status`` ∈ {"fits", "tight", "impossible"}; ``est_overrun_s`` is the
    predicted seconds of speech past the available time (0 when it fits).
    Pure function: no I/O, deterministic.
    """
    params = fit_params or FitParams()
    fits_cap, cap = absorb_caps(params)
    n = len(segments)
    out: list[dict] = []
    for i, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])
        slot = max(0.0, end - start)

        # Borrowable silence — fit_planner's slack absorption, capped.
        if i + 1 < n:
            gap = max(0.0, float(segments[i + 1]["start"]) - end)
            borrow = min(max(0.0, gap - params.gap_guard_s), gap_borrow_max_s)
        elif total_dur_s > 0:
            borrow = min(max(0.0, float(total_dur_s) - end), gap_borrow_max_s)
        else:
            borrow = 0.0
        available = slot + borrow

        est = estimate_natural_duration(seg.get("text") or "", target_lang, calibration)
        if est <= 0.0:
            status = "fits"
            overrun = 0.0
        elif available <= 0.0:
            status = "impossible"
            overrun = est
        else:
            need = est / available
            # Same boundary tolerance as fit_planner's _EPS: a need that
            # lands exactly on a cap is absorbed, not escalated.
            if need <= fits_cap + 1e-9:
                status = "fits"
            elif need <= cap + 1e-9:
                status = "tight"
            else:
                status = "impossible"
            overrun = max(0.0, est - available)

        out.append({
            "id": str(seg.get("id", f"seg_{i}")),
            "status": status,
            "est_dur_s": round(est, 3),
            "available_s": round(available, 3),
            "est_overrun_s": round(overrun, 3),
            "calibrated": calibration is not None,
        })
    return out


# ── Optional LLM condensation ───────────────────────────────────────────

_CONDENSE_PROMPT = """\
You are a dubbing writer. The user will give you a translated line that is
TOO LONG for its time slot. Rewrite it shorter so it can be read aloud
within the target duration: cut filler words, tighten phrasing, and drop
the least essential clauses — but preserve the meaning. Never change
character names, proper nouns, numbers, or technical terms. Stay in the
same language as the line.
Reply with ONLY the rewritten line. No quotes, no commentary."""

# Bound the LLM loop — condensation is a per-segment *suggestion*, not a
# fit guarantee, so two shots are plenty before degrading to a no-op.
_CONDENSE_ATTEMPTS = 2


def condense_for_slot(
    text: str,
    *,
    available_s: float,
    target_lang: str,
    source_text: Optional[str] = None,
    calibration: Optional[Calibration] = None,
) -> dict:
    """Meaning-preserving shorter rewrite of ``text`` targeting ``available_s``.

    Returns ``{"text", "applied", "est_dur_s"}`` (+ ``"error"`` on the no-op
    paths). ``applied=False`` keeps the input text untouched — no LLM
    configured, LLM failure, and divergent/too-aggressive replies all
    degrade there. The best (shortest-estimate) candidate that passes the
    divergence guard AND is actually shorter than the input wins; a reply
    that fits ``available_s`` returns immediately.
    """
    text = (text or "").strip()
    base_est = estimate_natural_duration(text, target_lang, calibration)
    if not text or available_s <= 0:
        return {"text": text, "applied": False, "est_dur_s": round(base_est, 3),
                "error": "nothing-to-condense"}
    if base_est <= available_s:
        return {"text": text, "applied": False, "est_dur_s": round(base_est, 3),
                "error": "already-fits"}

    from services import llm_skills
    # `active=` forwards this module's (monkeypatch-able) name so the
    # no-override path matches the plain get_active_llm_backend behavior.
    llm = llm_skills.skill_backend(_SKILL_ID, active=lambda: get_active_llm_backend())
    if isinstance(llm, OffBackend):
        return {"text": text, "applied": False, "est_dur_s": round(base_est, 3),
                "error": "no-llm"}

    best: Optional[tuple[str, float]] = None  # (candidate, est)
    for attempt in range(1, _CONDENSE_ATTEMPTS + 1):
        user_lines = [
            f"Target language: {target_lang}",
            f"Target duration: {available_s:.2f}s",
            f"Current line: {text}",
            f"Current reading duration: ~{base_est:.2f}s",
        ]
        if source_text:
            user_lines.append(f"Source line (for meaning): {source_text}")
        if attempt > 1 and best is not None:
            user_lines.append(
                f"Your previous rewrite was still ~{best[1]:.2f}s. Cut further."
            )
        try:
            reply = llm.chat(
                system=_CONDENSE_PROMPT, user="\n".join(user_lines),
                temperature=0.2,  # pinned like Autofit — default 1.0 drifts/invents
            )
        except Exception as e:  # noqa: BLE001 — LLM failure must no-op, never raise
            logger.warning("condense attempt %d failed: %s", attempt, e)
            break
        candidate = (reply or "").strip()
        if not candidate:
            continue
        ok, reason = refine_output_ok(text, candidate, target_lang)
        if not ok:
            logger.warning("condense attempt %d rejected (%s)", attempt, reason)
            continue
        est = estimate_natural_duration(candidate, target_lang, calibration)
        if est >= base_est:
            continue  # not actually shorter — useless as a suggestion
        if best is None or est < best[1]:
            best = (candidate, est)
        if est <= available_s:
            break  # fits — done

    if best is None:
        return {"text": text, "applied": False, "est_dur_s": round(base_est, 3),
                "error": "condense-failed"}
    return {"text": best[0], "applied": True, "est_dur_s": round(best[1], 3)}
