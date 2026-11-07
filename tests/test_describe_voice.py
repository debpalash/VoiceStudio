"""Tests for the free-text "describe your voice" mapper + endpoint (issue #317).

Covers the description → design-parameter mapping (``core.describe_voice``)
across varied phrasings, the graceful-degradation path (non-matching text),
the taxonomy-validity invariant (every emitted token must pass the engine's
instruct validator), and the ``POST /design/describe`` API contract.

CJK note: the Chinese-dialect expectations reference ``DIALECT_PINYIN`` from
the module under test instead of hardcoding dialect tokens here.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.describe_voice import CATEGORY_ORDER, DIALECT_PINYIN, parse_description
from api.routers import describe_voice as describe_router


# ── Helpers ───────────────────────────────────────────────────────────────────
def attrs_of(description: str) -> dict:
    return parse_description(description)["attrs"]


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(describe_router.router)
    return TestClient(app)


# ── Varied descriptions ───────────────────────────────────────────────────────
def test_issue_example_warm_elderly_british_storyteller():
    """The exact example from issue #317 — partial match + graceful leftovers."""
    res = parse_description("a warm elderly British storyteller, slightly raspy")
    assert res["attrs"]["Age"] == "elderly"
    assert res["attrs"]["EnglishAccent"] == "british accent"
    assert res["attrs"]["Gender"] == "Auto"  # nothing gendered in the text
    assert "elderly" in res["instruct"] and "british accent" in res["instruct"]
    # "slightly raspy" is outside the taxonomy → surfaced, not silently dropped
    assert any("raspy" in frag for frag in res["unmatched"])


def test_young_woman_very_deep_voice():
    a = attrs_of("young woman with a very deep voice")
    assert a["Gender"] == "female"
    assert a["Age"] == "young adult"
    assert a["Pitch"] == "very low pitch"  # "very deep" must not stop at "deep"


def test_deep_male_narrator_in_his_fifties_american():
    a = attrs_of("deep male narrator in his fifties, American")
    assert a["Gender"] == "male"
    assert a["Age"] == "middle-aged"
    assert a["Pitch"] == "low pitch"
    assert a["EnglishAccent"] == "american accent"


def test_whispering_teenage_girl():
    a = attrs_of("a whispering teenage girl")
    assert a["Style"] == "whisper"
    assert a["Age"] == "teenager"
    assert a["Gender"] == "female"


def test_numeric_age_does_not_misfire_elderly():
    """'7 year old' → child; the trailing 'old' must not map to elderly."""
    a = attrs_of("a cheerful 7 year old kid")
    assert a["Age"] == "child"
    a2 = attrs_of("a 25-year-old podcaster")
    assert a2["Age"] == "young adult"
    a3 = attrs_of("a 70 year old man")
    assert a3["Age"] == "elderly"


def test_aged_number_maps_to_its_bracket_not_elderly():
    """'aged 22' is a number, not elderly's bare "aged" synonym."""
    assert attrs_of("a young man, aged 22")["Age"] == "young adult"
    assert attrs_of("a woman aged 30, calm and clear")["Age"] == "young adult"
    assert attrs_of("narrator, age: 45")["Age"] == "middle-aged"
    assert attrs_of("a man aged 8")["Age"] == "child"
    assert attrs_of("a man aged 72")["Age"] == "elderly"
    assert attrs_of("an aged sea captain")["Age"] == "elderly"


def test_sichuan_dialect_grandma():
    res = parse_description("a sichuan dialect grandma")
    a = res["attrs"]
    assert a["ChineseDialect"] == DIALECT_PINYIN["sichuan"]
    assert a["Age"] == "elderly"
    assert a["Gender"] == "female"
    # Dialect voices speak Chinese — a conflicting English accent never co-emits.
    assert a["EnglishAccent"] == "Auto"


def test_squeaky_cartoon_character():
    a = attrs_of("a squeaky cartoon character")
    assert a["Pitch"] == "very high pitch"


def test_moderate_pitch_australian_guy_in_twenties():
    a = attrs_of("moderate pitch australian guy in his twenties")
    assert a["Pitch"] == "moderate pitch"
    assert a["EnglishAccent"] == "australian accent"
    assert a["Gender"] == "male"
    assert a["Age"] == "young adult"


def test_middle_aged_not_elderly():
    """'middle-aged' must win over elderly's bare 'aged' synonym (\\b at hyphen)."""
    a = attrs_of("a middle-aged man")
    assert a["Age"] == "middle-aged"


def test_young_child_maps_to_child_not_young_adult():
    a = attrs_of("a young child telling a story")
    assert a["Age"] == "child"


def test_cjk_taxonomy_tokens_match():
    """Chinese taxonomy tokens (derived from voice_design.py) map to EN attrs."""
    a = attrs_of("中年男性，低音调")
    assert a["Gender"] == "male"
    assert a["Age"] == "middle-aged"
    assert a["Pitch"] == "low pitch"


# ── Graceful degradation ──────────────────────────────────────────────────────
def test_non_matching_description_degrades_gracefully():
    res = parse_description("quarterly finances exceeded projections this spring")
    assert res["attrs"] == {c: "Auto" for c in CATEGORY_ORDER}
    assert res["instruct"] == ""
    assert res["matched"] == []
    assert res["unmatched"]  # the whole clause is reported back


def test_empty_and_whitespace_descriptions():
    for text in ("", "   ", None):
        res = parse_description(text)
        assert res["attrs"] == {c: "Auto" for c in CATEGORY_ORDER}
        assert res["instruct"] == ""
        assert res["unmatched"] == []


def test_case_insensitive():
    a = attrs_of("AN ELDERLY BRITISH MALE")
    assert a["Age"] == "elderly"
    assert a["EnglishAccent"] == "british accent"
    assert a["Gender"] == "male"


# ── Validator-safety invariant ────────────────────────────────────────────────
def test_every_emitted_token_is_taxonomy_valid():
    """No description may ever produce an instruct item the engine rejects."""
    from core.describe_voice import _VD

    valid = _VD._INSTRUCT_ALL_VALID
    samples = [
        "a warm elderly british storyteller, slightly raspy",
        "young woman with a very deep voice",
        "whispering teenage girl from london",
        "squeaky little kid",
        "a sichuan dialect grandma",
        "booming russian gentleman in his sixties",
        "soft-spoken canadian lady, moderate pitch",
        "falsetto japanese man",
    ]
    for s in samples:
        res = parse_description(s)
        for item in filter(None, (i.strip() for i in res["instruct"].split(","))):
            assert item in valid, f"{item!r} (from {s!r}) not in engine taxonomy"
        # attrs and instruct must agree
        emitted = {res["attrs"][c] for c in CATEGORY_ORDER if res["attrs"][c] != "Auto"}
        from_instruct = {i.strip() for i in res["instruct"].split(",") if i.strip()}
        assert emitted == from_instruct


def test_matched_entries_report_category_token_phrase():
    res = parse_description("an elderly woman")
    cats = {m["category"]: m for m in res["matched"]}
    assert cats["Age"]["token"] == "elderly"
    assert cats["Age"]["phrase"] == "elderly"
    assert cats["Gender"]["token"] == "female"
    assert cats["Gender"]["phrase"] == "woman"


# ── API contract ──────────────────────────────────────────────────────────────
def test_endpoint_maps_description(client):
    r = client.post("/design/describe", json={"description": "a deep elderly british man"})
    assert r.status_code == 200
    body = r.json()
    assert body["attrs"]["Age"] == "elderly"
    assert body["attrs"]["Gender"] == "male"
    assert body["attrs"]["Pitch"] == "low pitch"
    assert body["attrs"]["EnglishAccent"] == "british accent"
    assert set(body) == {"attrs", "instruct", "matched", "unmatched"}
    assert set(body["attrs"]) == set(CATEGORY_ORDER)


def test_endpoint_empty_description(client):
    r = client.post("/design/describe", json={"description": ""})
    assert r.status_code == 200
    assert r.json()["instruct"] == ""


def test_endpoint_rejects_oversized_description(client):
    r = client.post("/design/describe", json={"description": "x" * 2001})
    assert r.status_code == 422
