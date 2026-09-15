"""Autofit translation style — strict fit-to-slot (v0.3.8).

Autofit = Cinematic + a hard "never exceed the segment time" bound. In the
speech-rate fit pass that means the accepted upper ratio is 1.0 (fit within the
slot) instead of Cinematic's looser TOL_HIGH (1.08). Verifies the strict mode
keeps trimming past the loose tolerance and that the no-LLM path still degrades
gracefully.
"""
from __future__ import annotations

import pytest


class _FakeTrimmer:
    """Minimal non-Off LLM stand-in that trims any line to a short fixed length
    so the fit loop converges deterministically."""
    def chat(self, *, system, user, timeout=None, temperature=None):
        return "A" * 14  # ratio 0.93 for slot 1.0s @ en 15 cps → inside [0.92, 1.0]


class _FakeChatter:
    """Non-Off LLM stand-in that always returns one fixed reply; counts calls."""
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.last_temperature = None

    def chat(self, *, system, user, timeout=None, temperature=None):
        self.calls += 1
        self.last_temperature = temperature
        return self.reply


@pytest.fixture
def speech_rate(monkeypatch):
    from services import speech_rate as _sr
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: _FakeTrimmer())
    return _sr


def test_loose_accepts_slight_overrun_without_llm(speech_rate):
    # 16 chars @ 15 cps over a 1.0s slot → ratio ~1.067: within Cinematic's
    # TOL_HIGH (1.08), so it's accepted as-is with no LLM call.
    res = speech_rate.adjust_for_slot("A" * 16, slot_seconds=1.0, target_lang="en", strict=False)
    assert res["attempts"] == 0
    assert res["rate_ratio"] > 1.0  # it overran the slot but was tolerated


def test_autofit_strict_trims_until_within_slot(speech_rate):
    # Same overrun, but Autofit must not accept > 1.0 — it trims via the LLM.
    res = speech_rate.adjust_for_slot("A" * 16, slot_seconds=1.0, target_lang="en", strict=True)
    assert res["rate_ratio"] <= 1.0 + 1e-6
    assert res["attempts"] >= 1  # the loose path would have been 0


def test_strict_no_llm_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    from services import speech_rate as _sr
    res = _sr.adjust_for_slot("A" * 30, slot_seconds=1.0, target_lang="en", strict=True)
    assert res.get("error") == "no-llm"
    assert res["text"] == "A" * 30  # unchanged, no crash


def test_strict_within_tolerance_is_noop(speech_rate):
    # A line already at ratio ~1.0 needs no work even in strict mode.
    res = speech_rate.adjust_for_slot("A" * 15, slot_seconds=1.0, target_lang="en", strict=True)
    assert res["attempts"] == 0
    assert res["rate_ratio"] == pytest.approx(1.0, abs=0.01)


# ── Divergence guard (v0.3.9 field report: Autofit invented dialogue) ───────


def test_expand_hallucination_rejected_output_stays_input(monkeypatch):
    """The reported bug: a short line over a long slot invited the LLM to
    fabricate a slot-filling wall of dialogue, and the accept step took ANY
    non-empty reply (the best-tracker then favored the most-padded one). The
    guard must discard it and keep the input text."""
    from services import speech_rate as _sr
    text = "B" * 24                     # 1.6s @ en 15cps over a 10s slot → ratio 0.16
    fake = _FakeChatter("A" * 145)      # a "perfect" slot fill = ~6× the input line
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    res = _sr.adjust_for_slot(text, slot_seconds=10.0, target_lang="en", strict=True)
    assert res["text"] == text                    # hallucination never adopted
    assert res.get("error") == "fit-diverged"
    assert fake.calls == _sr.MAX_ATTEMPTS         # attempts burned, loop still bounded


def test_refusal_reply_rejected_keeps_input(monkeypatch):
    """A refusal/commentary reply must not replace a long line just because
    its length happens to land near the slot budget."""
    from services import speech_rate as _sr
    text = "C" * 60                            # 4s over a 1s slot → heavy trim ask
    fake = _FakeChatter("Sorry, I cannot.")    # 16 chars ≈ the 15-char slot budget
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    res = _sr.adjust_for_slot(text, slot_seconds=1.0, target_lang="en")
    assert res["text"] == text
    assert res.get("error") == "fit-diverged"


def test_legitimate_expansion_accepted(monkeypatch):
    from services import speech_rate as _sr
    text = "D" * 40                     # ratio 0.67 over a 4s slot @ en
    fake = _FakeChatter("E" * 57)       # ~1.4× the line → ratio 0.95, honest fill
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    res = _sr.adjust_for_slot(text, slot_seconds=4.0, target_lang="en", strict=True)
    assert res["text"] == "E" * 57
    assert "error" not in res


def test_tiny_line_skips_llm_expansion(monkeypatch):
    """A line under 15% of its slot can never honestly fill it — the LLM must
    not even be asked (it could only fabricate), and the line stays short."""
    from services import speech_rate as _sr
    fake = _FakeChatter("A" * 150)
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    res = _sr.adjust_for_slot("Hi.", slot_seconds=10.0, target_lang="en", strict=True)
    assert res["text"] == "Hi."
    assert res["attempts"] == 0
    assert res.get("error") == "fit-skip-short"
    assert fake.calls == 0


def test_fit_llm_call_pins_low_temperature(monkeypatch):
    """The fit pass pins temperature=0.2 like the Fast translate path — the
    provider default of 1.0 is part of what made expansions drift."""
    from services import speech_rate as _sr
    fake = _FakeChatter("A" * 14)
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    _sr.adjust_for_slot("A" * 16, slot_seconds=1.0, target_lang="en", strict=True)
    assert fake.last_temperature == 0.2


def test_measured_agent_uses_real_render_duration_once(monkeypatch):
    """The agent pass consumes real TTS evidence and makes one rewrite; the
    caller must render again before another correction is allowed."""
    from services import speech_rate as _sr

    class _MeasuredAgent:
        def __init__(self):
            self.calls = []

        def chat(self, **kwargs):
            self.calls.append(kwargs)
            return "This line is too long."

    fake = _MeasuredAgent()
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    result = _sr.adjust_for_measured_slot(
        "This sentence is much too long for its slot.",
        slot_seconds=2.0,
        measured_seconds=3.2,
        target_lang="en",
        source_text="This sentence is much too long for its slot.",
        translation_instructions="Use warm conversational language.",
        context_before="The speaker opens the topic.",
        context_after="The next speaker replies.",
    )

    assert result["changed"] is True
    assert result["measured_ratio"] == pytest.approx(1.6)
    assert len(fake.calls) == 1
    assert "Use warm conversational language." in fake.calls[0]["system"]
    assert "Exact target duration: 2.00s" in fake.calls[0]["user"]
    assert "Measured duration of this line: 3.20s" in fake.calls[0]["user"]
    assert "Previous source line (context only)" in fake.calls[0]["user"]
    assert "Next source line (context only)" in fake.calls[0]["user"]
    assert fake.calls[0]["temperature"] == 0.15


def test_measured_agent_keeps_already_fitting_line_without_llm(monkeypatch):
    from services import speech_rate as _sr

    fake = _FakeChatter("should not be used")
    monkeypatch.setattr(_sr, "get_active_llm_backend", lambda: fake)
    result = _sr.adjust_for_measured_slot(
        "Already timed.", slot_seconds=2.0, measured_seconds=2.02, target_lang="en"
    )

    assert result["changed"] is False
    assert result["error"] == "already-fits"
    assert fake.calls == 0
