"""Pre-synthesis duration planning (services/duration_planner.py).

Covers the pure planning layer that runs after translation, before TTS:

  - estimator: static per-language fallback vs. self-calibrated rate;
  - calibration: median chars-per-second from synthesized samples, garbage
    filtering, the minimum-sample gate, and the job-record reader;
  - classifier: fits/tight/impossible thresholds derived from fit_planner's
    caps, gap borrowing (and its cap), the tail borrow, audio-only mode;
  - alignment: an "impossible" verdict must mean fit_planner would trim,
    and "tight"/"fits" must mean it would not;
  - condensation: opt-in LLM shorter-rewrite suggestions — no-LLM no-op,
    already-fits no-op (no LLM call), divergence-guard rejection, LLM
    failure no-op, and the retry-then-accept path.
"""
from __future__ import annotations

import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import duration_planner as dp
from services.fit_planner import MAX_AUDIO_RATE_HARD, FitParams, plan_fit


# ── Estimator ───────────────────────────────────────────────────────────


def test_static_fallback_matches_rate_table():
    # 30 chars @ en 15 cps → 2.0s (same table as speech_rate's badge).
    assert dp.estimate_natural_duration("A" * 30, "en") == pytest.approx(2.0)


def test_unknown_language_uses_conservative_default():
    # Unknown code falls back to 13 cps — never a crash, never zero.
    assert dp.estimate_natural_duration("A" * 26, "xx") == pytest.approx(2.0)


def test_calibrated_rate_overrides_table():
    calib = dp.Calibration(cps=10.0, samples=5)
    assert dp.estimate_natural_duration("A" * 30, "en", calib) == pytest.approx(3.0)


def test_empty_text_estimates_zero():
    assert dp.estimate_natural_duration("", "en") == 0.0
    assert dp.estimate_natural_duration("   ", "en", dp.Calibration(10.0, 3)) == 0.0


# ── Calibration ─────────────────────────────────────────────────────────


def test_calibrate_cps_median_of_sample_rates():
    # Rates 10, 15, 20 cps → median 15; the outlier-resistant middle wins.
    calib = dp.calibrate_cps([(20, 2.0), (30, 2.0), (40, 2.0)])
    assert calib is not None
    assert calib.cps == pytest.approx(15.0)
    assert calib.samples == 3


def test_calibrate_cps_even_count_averages_middle_pair():
    calib = dp.calibrate_cps([(10, 1.0), (20, 1.0), (30, 1.0), (40, 1.0)])
    assert calib.cps == pytest.approx(25.0)


def test_calibrate_cps_requires_min_samples():
    assert dp.calibrate_cps([(30, 2.0), (30, 2.0)]) is None
    assert dp.calibrate_cps([]) is None


def test_calibrate_cps_filters_garbage_samples():
    # Sub-0.4s durations and tiny texts are TTS ramp/silence noise, not
    # speech rate — after filtering only 2 usable samples remain → None.
    assert dp.calibrate_cps([
        (30, 2.0), (30, 2.0),          # usable
        (100, 0.1), (2, 5.0),          # garbage: too short / too few chars
        ("x", 1.0), (None, None),      # garbage: non-numeric
    ]) is None
    # With a third usable sample the garbage no longer blocks calibration.
    calib = dp.calibrate_cps([(30, 2.0), (30, 2.0), (30, 2.0), (100, 0.1)])
    assert calib is not None and calib.cps == pytest.approx(15.0)


def test_calibration_from_job_reads_generate_records():
    job = {"seg_natural_durs_by_lang": {"es": {
        "a": {"chars": 20, "dur": 2.0},
        "b": {"chars": 30, "dur": 2.0},
        "c": {"chars": 40, "dur": 2.0},
        "legacy-junk": "not-a-dict",   # tolerated, skipped
    }}}
    calib = dp.calibration_from_job(job, "es")
    assert calib is not None and calib.cps == pytest.approx(15.0)
    # Other language / missing map / legacy job → None (static fallback).
    assert dp.calibration_from_job(job, "fr") is None
    assert dp.calibration_from_job({}, "es") is None


# ── Classifier thresholds (aligned with FitParams caps) ─────────────────


def _seg(i, start, end, text):
    return {"id": f"s{i}", "start": start, "end": end, "text": text}


def _classify_one(text, slot, **kw):
    return dp.classify_segments([_seg(0, 0.0, slot, text)], "en", **kw)[0]


def test_fits_when_need_within_audio_only_cap():
    # est 2.4s over 2.0s slot → need 1.2 == max_audio_only_rate → still fits
    # (fit_planner absorbs it with an imperceptible audio-only speed-up).
    v = _classify_one("A" * 36, 2.0)
    assert v["status"] == "fits"
    assert v["est_dur_s"] == pytest.approx(2.4)
    # Overrun is still reported truthfully even for a "fits" verdict.
    assert v["est_overrun_s"] == pytest.approx(0.4)


def test_tight_between_audio_only_and_absorb_cap():
    # need = est/slot: just above 1.2 → tight; at the hybrid absorb cap
    # (audio 1.5 × video 2.0 = 3.0) → still tight (planner absorbs, no trim).
    assert _classify_one("A" * 39, 2.0)["status"] == "tight"     # need 1.3
    assert _classify_one("A" * 90, 2.0)["status"] == "tight"     # need 3.0


def test_impossible_beyond_absorb_cap_with_overrun():
    # need 4.0 > 3.0 → even hybrid caps can't absorb it: fit_planner trims.
    v = _classify_one("A" * 120, 2.0)   # est 8.0s vs 2.0s available
    assert v["status"] == "impossible"
    assert v["est_overrun_s"] == pytest.approx(6.0)


def test_audio_only_mode_caps_at_legacy_hard_ceiling():
    params = FitParams(allow_video_retime=False)
    # need 2.0: hybrid would absorb it, audio-only (hard cap 1.8) cannot.
    text = "A" * 60  # est 4.0s over 2.0s
    assert _classify_one(text, 2.0)["status"] == "tight"
    assert _classify_one(text, 2.0, fit_params=params)["status"] == "impossible"
    assert MAX_AUDIO_RATE_HARD == pytest.approx(1.8)


def test_calibration_changes_the_verdict():
    # 45 chars over 2.0s: static en (15 cps) → est 3.0, need 1.5 → tight.
    # A fast calibrated voice (30 cps) → est 1.5 → fits.
    text = "A" * 45
    assert _classify_one(text, 2.0)["status"] == "tight"
    v = _classify_one(text, 2.0, calibration=dp.Calibration(cps=30.0, samples=4))
    assert v["status"] == "fits"
    assert v["calibrated"] is True


def test_empty_text_and_zero_slot_edge_cases():
    assert _classify_one("", 2.0)["status"] == "fits"
    # Speech but literally no available time → impossible, overrun = est.
    v = dp.classify_segments([_seg(0, 1.0, 1.0, "A" * 30)], "en")[0]
    assert v["status"] == "impossible"
    assert v["est_overrun_s"] == pytest.approx(2.0)


# ── Gap borrowing ───────────────────────────────────────────────────────


def test_gap_borrow_extends_available_time():
    # est 3.0s over a 2.0s slot (need 1.5 → tight)… but a 1.05s gap to the
    # next segment lends 1.0s (gap − guard) → available 3.0 → need 1.0 → fits.
    segs = [_seg(0, 0.0, 2.0, "A" * 45), _seg(1, 3.05, 4.0, "hi")]
    v = dp.classify_segments(segs, "en")
    assert v[0]["status"] == "fits"
    assert v[0]["available_s"] == pytest.approx(3.0)
    # Without the gap the same segment is tight.
    assert _classify_one("A" * 45, 2.0)["status"] == "tight"


def test_gap_borrow_is_capped():
    # A 60s gap must not promise 60s of slack: borrow caps at GAP_BORROW_MAX_S.
    segs = [_seg(0, 0.0, 2.0, "A" * 45), _seg(1, 62.0, 63.0, "hi")]
    v = dp.classify_segments(segs, "en")[0]
    assert v["available_s"] == pytest.approx(2.0 + dp.GAP_BORROW_MAX_S)


def test_last_segment_borrows_capped_tail():
    v = dp.classify_segments([_seg(0, 0.0, 2.0, "A" * 45)], "en", total_dur_s=2.5)[0]
    assert v["available_s"] == pytest.approx(2.5)  # tail 0.5s, under the cap
    v = dp.classify_segments([_seg(0, 0.0, 2.0, "A" * 45)], "en", total_dur_s=60.0)[0]
    assert v["available_s"] == pytest.approx(2.0 + dp.GAP_BORROW_MAX_S)
    # Unknown video duration → no tail borrow (mirrors plan_fit).
    v = dp.classify_segments([_seg(0, 0.0, 2.0, "A" * 45)], "en")[0]
    assert v["available_s"] == pytest.approx(2.0)


# ── Alignment with fit_planner ──────────────────────────────────────────


@pytest.mark.parametrize("chars,expected_status", [
    (30, "fits"),          # est 2.0s / 2.95s avail → need <1.2
    (90, "tight"),         # est 6.0s → need ~2.03: hybrid absorbs
    (140, "impossible"),   # est ~9.3s → need >3.0: beyond the caps
])
def test_verdict_matches_what_fit_planner_would_do(chars, expected_status):
    """"impossible" must mean "fit_planner will trim" — feed the classifier's
    own estimate to plan_fit as the natural duration and cross-check."""
    segs = [_seg(0, 0.0, 2.0, "A" * chars), _seg(1, 3.0, 4.0, "hi")]
    verdict = dp.classify_segments(segs, "en")[0]
    assert verdict["status"] == expected_status

    est = dp.estimate_natural_duration("A" * chars, "en")
    plan = plan_fit(
        [{"id": s["id"], "start": s["start"], "end": s["end"]} for s in segs],
        [est, 0.5],
        total_dur_s=4.0,
    )
    if expected_status == "impossible":
        assert plan.segments[0].status == "overflow_trimmed"
        assert plan.segments[0].overflow_s > 0
    else:
        assert plan.segments[0].status != "overflow_trimmed"
        assert plan.segments[0].overflow_s == 0


# ── Condensation ────────────────────────────────────────────────────────


class _FakeLLM:
    """Non-Off LLM stand-in returning scripted replies; counts calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_temperature = None

    def chat(self, *, system, user, timeout=None, temperature=None):
        self.calls += 1
        self.last_temperature = temperature
        if not self.replies:
            raise RuntimeError("no more scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def fake_llm(monkeypatch):
    def _install(replies):
        llm = _FakeLLM(replies)
        monkeypatch.setattr(dp, "get_active_llm_backend", lambda: llm)
        return llm
    return _install


def test_condense_no_llm_is_noop(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    text = "A" * 90
    res = dp.condense_for_slot(text, available_s=2.0, target_lang="en")
    assert res["applied"] is False
    assert res["text"] == text
    assert res["error"] == "no-llm"


def test_condense_already_fitting_never_calls_llm(fake_llm):
    llm = fake_llm(["SHOULD-NOT-BE-CALLED"])
    text = "A" * 15  # est 1.0s, well inside 2.0s
    res = dp.condense_for_slot(text, available_s=2.0, target_lang="en")
    assert res["applied"] is False
    assert res["error"] == "already-fits"
    assert llm.calls == 0


def test_condense_accepts_shorter_rewrite(fake_llm):
    llm = fake_llm(["B" * 28])  # est ~1.87s ≤ 2.0s available
    text = "A" * 60             # est 4.0s
    res = dp.condense_for_slot(text, available_s=2.0, target_lang="en")
    assert res["applied"] is True
    assert res["text"] == "B" * 28
    assert res["est_dur_s"] == pytest.approx(28 / 15.0, abs=0.01)
    assert llm.calls == 1
    assert llm.last_temperature == 0.2  # pinned, like the Autofit pass


def test_condense_retries_then_keeps_best(fake_llm):
    # First reply is shorter but still overruns; second fits — second wins.
    fake_llm(["B" * 45, "C" * 25])
    res = dp.condense_for_slot("A" * 60, available_s=2.0, target_lang="en")
    assert res["applied"] is True
    assert res["text"] == "C" * 25


def test_condense_llm_failure_is_noop(fake_llm):
    fake_llm([RuntimeError("provider down")])
    text = "A" * 60
    res = dp.condense_for_slot(text, available_s=2.0, target_lang="en")
    assert res["applied"] is False
    assert res["text"] == text
    assert res["error"] == "condense-failed"


def test_condense_divergent_reply_rejected(fake_llm):
    # 6 chars from a 100-char line → length-ratio 0.06 < the divergence
    # guard's floor: the "rewrite" nuked the meaning, so it's discarded.
    llm = fake_llm(["short!", "gone!!"])
    text = "A" * 100
    res = dp.condense_for_slot(text, available_s=1.0, target_lang="en")
    assert res["applied"] is False
    assert res["text"] == text
    assert res["error"] == "condense-failed"
    assert llm.calls == 2  # both attempts burned, none accepted


def test_condense_longer_reply_is_useless(fake_llm):
    # A reply that isn't actually shorter can't be suggested.
    fake_llm(["A" * 80, "A" * 70])
    res = dp.condense_for_slot("A" * 60, available_s=2.0, target_lang="en")
    assert res["applied"] is False
    assert res["error"] == "condense-failed"


@pytest.mark.parametrize("invalid", [(float("inf"), 2), (30, float("inf"))])
def test_calibration_ignores_nonfinite_samples(invalid):
    assert dp.calibrate_cps([(20, 2), (40, 2), invalid]) is None
    result = dp.calibrate_cps([(20, 2), (40, 2), (30, 2), invalid])
    assert result == dp.Calibration(cps=15.0, samples=3)
