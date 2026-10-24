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


@pytest.mark.parametrize("text", ["Ready 2!", "Ready 2?", "Ready 2。", "नमस्ते।", "مرحبا؟", "ሰላም።", "Ready 2؟"])
def test_short_unambiguous_sentences_emit_before_stream_finishes(text):
    chunker = SentenceChunker()
    assert chunker.push(text[:-1]) == []
    assert chunker.push(text[-1]) == [text]
    assert chunker.flush() == []


@pytest.mark.parametrize("text", ["Value 2.", "U.S.", "Dr."])
def test_short_ambiguous_periods_keep_waiting_for_continuation(text):
    chunker = SentenceChunker()
    assert chunker.push(text) == []
    assert chunker.flush() == [text]


@pytest.mark.parametrize('tokens', [['２．', '５'], ['Value ２．', '５']])
def test_short_full_width_decimals_wait_for_fractional_digits(tokens):
    from services.sentence_chunker import SentenceChunker

    chunker = SentenceChunker()
    for token in tokens:
        assert chunker.push(token) == []
    assert chunker.flush() == [''.join(tokens)]


@pytest.mark.parametrize("language", ["en", "ar"])
@pytest.mark.parametrize("aggressive_first_flush", [False, True])
@pytest.mark.parametrize("tokens", [["نعم؛", " ثم"], ["نعم", "؛ ", "ثم"]])
def test_short_arabic_semicolon_keeps_continuing_clause_buffered(
    language: str, aggressive_first_flush: bool, tokens: list[str]
) -> None:
    chunker = SentenceChunker(
        language=language, aggressive_first_flush=aggressive_first_flush
    )
    for token in tokens:
        assert chunker.push(token) == []
    assert chunker.push(" نتابع؟") == ["نعم؛ ثم نتابع؟"]
    assert chunker.flush() == []


@pytest.mark.parametrize("terminator", list("।॥؟۔؏։፧።។៕။༎༏"))
def test_short_non_latin_endings_still_flush_immediately(terminator: str) -> None:
    chunker = SentenceChunker()
    assert chunker.push("Ready 2") == []
    assert chunker.push(terminator) == ["Ready 2" + terminator]
    assert chunker.flush() == []
@pytest.mark.parametrize("text", [
    "Connect to the router at 192.168.1.1 and open the admin page in your browser.",
    "We just shipped version 2.5.1 of the app, which fixes the login crash on startup.",
])
def test_dotted_numbers_stay_in_one_streamed_sentence(text):
    """Every dot between two digits belongs to the number, not just the first."""
    from api.routers.tts_stream import split_stream_sentences

    assert split_stream_sentences(text, "English") == [text]
    # LLM tokens carry their leading space (" 168") on the call-agent path.
    words = text.split(" ")
    assert _run_case([words[0], *(" " + w for w in words[1:])]) == [text]


def test_a_sentence_after_a_dotted_number_still_splits():
    from api.routers.tts_stream import split_stream_sentences

    assert split_stream_sentences(
        "Version 2.5.1 was released today. It fixed many bugs that users had reported.",
        "English",
    ) == ["Version 2.5.1 was released today.", "It fixed many bugs that users had reported."]
def test_space_before_split_point_survives_into_next_push():
    # The telephony reply parser holds back a trailing word that could start
    # "ACTION:", so a push can end in a space; the next word must not be glued on.
    assert _run_case(["Hello there my good friend. How are ", "you today?"]) == [
        "Hello there my good friend.",
        "How are you today?",
    ]
