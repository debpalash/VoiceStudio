"""Canonical longform parser (#27) — pytest side of the cross-impl golden corpus.

This suite and ``electron/src/shared/test/longformParser.test.js`` load the SAME JSON
(`tests/fixtures/longform_parser_cases.json`) and assert both impls produce it
byte-for-byte. A divergence cannot pass both suites — the side that drifts fails
its own assertion against the shared truth.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from services.longform_parser import parse_script_to_spans

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "longform_parser_cases.json"
_CASES = json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_corpus(case):
    got = parse_script_to_spans(
        case["input"],
        default_voice=case["default_voice"],
        default_speed=case.get("default_speed"),
    )
    assert got == case["expected"]


def test_none_input_returns_empty():
    # The wrapper coerces, but the parser itself must not raise on None.
    assert parse_script_to_spans(None) == []


def test_corpus_has_enough_cases():
    # The spec mandates ≥40 cases covering §A–I.
    assert len(_CASES) >= 40


def test_pathological_inputs_are_linear():
    # ReDoS guard: adversarial repeats must finish fast (mirrors the JS suite).
    import time
    for blob in ("[slow]" * 5000, "[pause" * 5000, "[voice:" * 5000,
                 "# \n" * 5000, "[a]" * 5000):
        t0 = time.perf_counter()
        parse_script_to_spans(blob, default_voice="v")
        assert time.perf_counter() - t0 < 1.0
