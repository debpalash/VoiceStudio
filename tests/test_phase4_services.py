"""Phase 4 services — director, speech_rate, incremental."""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from services import director, speech_rate, incremental


# ── director (4.2) ──────────────────────────────────────────────────────────


def test_director_heuristic_picks_tokens(monkeypatch):
    # Force Off backend so the LLM path doesn't run in CI.
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    d = director.parse("make this feel urgent and surprised")
    assert d.method == "heuristic"
    assert "urgent" in d.tokens.get("energy", [])
    assert "surprised" in d.tokens.get("emotion", [])


def test_director_empty_input_returns_empty():
    d = director.parse("")
    assert d.is_empty()


def test_director_instruct_prompt_combines_dims(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    d = director.parse("warm whispered casual tone")
    out = d.instruct_prompt()
    assert "warm" in out and "whispered" in out and "casual" in out


def test_director_rate_bias_speeds_up_for_urgent(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    fast = director.parse("urgent rushed delivery")
    slow = director.parse("calm slow delivery")
    assert fast.rate_bias() > 1.0
    assert slow.rate_bias() < 1.0


def test_director_ignores_unknown_tokens(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    d = director.parse("xyzzy plugh")
    assert d.is_empty()


# ── speech_rate (4.4) ──────────────────────────────────────────────────────


def test_speech_rate_within_tolerance_returns_input(monkeypatch):
    # ~15 chars/s English. "Hello world" = 11 chars in 1s → ratio 0.73, low.
    # Use a fitting length instead.
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    text = "A" * 15  # 15 chars in 1s = ratio 1.0
    res = speech_rate.adjust_for_slot(text, slot_seconds=1.0, target_lang="en")
    assert res["text"] == text
    assert abs(res["rate_ratio"] - 1.0) < 0.05
    assert res["attempts"] == 0


def test_speech_rate_no_llm_returns_input_with_marker(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    text = "A" * 100  # massively over a 1s slot
    res = speech_rate.adjust_for_slot(text, slot_seconds=1.0, target_lang="en")
    assert res["text"] == text
    assert res.get("error") == "no-llm"


def test_speech_rate_ratio_calculation():
    assert speech_rate.rate_ratio("A" * 15, 1.0, "en") == pytest.approx(1.0, abs=0.01)
    assert speech_rate.rate_ratio("A" * 30, 1.0, "en") == pytest.approx(2.0, abs=0.01)


# ── incremental (4.1) ──────────────────────────────────────────────────────


def test_speech_rate_reads_the_dub_pages_chinese_tags_as_chinese():
    """The dub page sends Chinese as cmn-Hans / cmn-Hant, not zh."""
    text = "你好，我们今天要去公园散步，然后吃晚饭。"
    zh = speech_rate.expected_duration(text, "zh")
    for tag in ("cmn-Hans", "cmn-Hant", "zh-CN", "zh_TW", "yue"):
        assert speech_rate.expected_duration(text, tag) == zh
    assert speech_rate.rate_ratio(text, 3.0, "cmn-Hans") > 1.0  # too long: trim
    assert speech_rate.expected_duration("hello there", "en-US") == \
        speech_rate.expected_duration("hello there", "en")


def test_incremental_first_run_everything_stale():
    segs = [
        {"id": "s1", "text": "Hello", "target_lang": "de"},
        {"id": "s2", "text": "World", "target_lang": "de"},
    ]
    plan = incremental.plan_incremental(segs)
    assert plan["stale"] == ["s1", "s2"]
    assert plan["fresh"] == []
    assert plan["total"] == 2
    assert "s1" in plan["fingerprints"]
    assert "s2" in plan["fingerprints"]


def test_incremental_unchanged_seg_is_fresh():
    seg = {"id": "s1", "text": "Hello", "target_lang": "de", "profile_id": "p1"}
    fp = incremental.segment_fingerprint(seg)
    plan = incremental.plan_incremental([seg], stored_hashes={"s1": fp})
    assert plan["fresh"] == ["s1"]
    assert plan["stale"] == []


def test_incremental_text_change_makes_stale():
    prev = incremental.segment_fingerprint(
        {"id": "s1", "text": "Hello", "target_lang": "de"}
    )
    next_seg = {"id": "s1", "text": "Goodbye", "target_lang": "de"}
    plan = incremental.plan_incremental([next_seg], stored_hashes={"s1": prev})
    assert plan["stale"] == ["s1"]
    assert plan["fresh"] == []


def test_incremental_gain_change_is_ignored():
    """Gain doesn't affect TTS output, so a gain-only change stays fresh."""
    base = {"id": "s1", "text": "Hello", "target_lang": "de"}
    fp = incremental.segment_fingerprint(base)
    plan = incremental.plan_incremental(
        [{**base, "gain": 1.5}],
        stored_hashes={"s1": fp},
    )
    assert plan["fresh"] == ["s1"]


def test_incremental_direction_change_makes_stale():
    base = {"id": "s1", "text": "Hello", "target_lang": "de"}
    fp = incremental.segment_fingerprint(base)
    plan = incremental.plan_incremental(
        [{**base, "direction": "urgent"}],
        stored_hashes={"s1": fp},
    )
    assert plan["stale"] == ["s1"]
