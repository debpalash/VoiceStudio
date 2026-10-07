"""Wave 1.4 — sentence chunker golden-parity suite.

Drives all 61 scenarios from Patter's parity corpus (MIT) — copied verbatim
to tests/fixtures/sentence_chunker_scenarios.json — through our port. Cases
carrying ``current_behavior`` are accepted as documented xfails (matching
upstream's runner semantics): the port must reproduce upstream behavior
exactly, including its documented quirks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.sentence_chunker import SentenceChunker

_SCENARIOS = json.loads(
    (Path(__file__).parent / "fixtures" / "sentence_chunker_scenarios.json")
    .read_text(encoding="utf-8")
)


def _run_case(tokens: list[str]) -> list[str]:
    chunker = SentenceChunker()
    emitted: list[str] = []
    for token in tokens:
        emitted.extend(chunker.push(token))
    emitted.extend(chunker.flush())
    return emitted


@pytest.mark.parametrize(
    "case", _SCENARIOS["cases"], ids=[c["name"] for c in _SCENARIOS["cases"]]
)
def test_golden_parity(case):
    got = _run_case(case["tokens"])
    expected = case["expected_sentences"]
    current = case.get("current_behavior")
    if got == expected:
        return
    if current is not None and got == current:
        # Upstream documents this divergence (quirk or known regression);
        # parity with upstream's ACTUAL behavior is the port contract.
        return
    raise AssertionError(
        f"{case['name']}: got {got!r}\n expected {expected!r}"
        + (f"\n or documented current {current!r}" if current is not None else "")
    )


# ── OmniVoice integration shape (how /ws/tts drives it) ────────────────────

def test_whole_request_text_splits_to_sentences():
    chunker = SentenceChunker()
    out = chunker.push("First thing here today. Second thing follows it. Third!")
    out.extend(chunker.flush())
    assert out == ["First thing here today.", "Second thing follows it.", "Third!"]


def test_single_sentence_request_stays_whole():
    chunker = SentenceChunker()
    out = chunker.push("Just the one sentence without much else going on.")
    out.extend(chunker.flush())
    assert out == ["Just the one sentence without much else going on."]


def test_italian_language_disables_aggressive_flush():
    chunker = SentenceChunker(language="it", aggressive_first_flush=True)
    assert chunker._aggressive_first_flush is False


def test_short_flush_ends_the_first_flush_window():
    """#2628: after a short reply flushes, a later comma must not clause-flush."""
    chunker = SentenceChunker(aggressive_first_flush=True)
    assert chunker.push("Yes.") == ["Yes."]
    assert chunker._is_first_flush is False
    clause = "However, the remaining answer keeps going for quite a while, and then"
    assert chunker.push(clause) == []
    assert chunker.flush() == [clause]
