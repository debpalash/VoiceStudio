"""Phase 1.1 / 2.7 — translator service.

Validates the glossary prompt-prefixing and the graceful "no LLM" path.
Does NOT hit a real LLM — the client is mocked.
"""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from unittest.mock import MagicMock, patch
import pytest

from services import translator as tr


# ── Glossary preamble ──────────────────────────────────────────────────────


def test_glossary_text_empty_inputs():
    assert tr._glossary_text(None) == ""
    assert tr._glossary_text([]) == ""
    assert tr._glossary_text([{"source": "", "target": "y"}]) == ""


def test_glossary_text_includes_each_term():
    out = tr._glossary_text([
        {"source": "Marcus", "target": "Marcus", "note": "character name"},
        {"source": "breakthrough", "target": "Durchbruch"},
    ])
    assert "Marcus → Marcus" in out
    assert "breakthrough → Durchbruch" in out
    assert "character name" in out


# ── No-LLM graceful path ───────────────────────────────────────────────────


def test_cinematic_no_llm_returns_literal_with_marker(monkeypatch):
    monkeypatch.setattr(tr, "_llm_client", lambda: None)
    res = tr.cinematic_refine_sync(
        "Hello.",
        "Hola.",
        source_lang="en",
        target_lang="es",
    )
    # With no LLM, "text" falls back to literal and an error marker is present.
    assert res["text"] == "Hola."
    assert res["literal"] == "Hola."
    assert res["critique"] == ""
    assert res.get("degraded") == "no-llm"
    assert "error" not in res


def test_cinematic_empty_literal_is_passthrough():
    res = tr.cinematic_refine_sync(
        "Source",
        "",
        source_lang="en",
        target_lang="es",
    )
    assert res["text"] == ""
    assert res["literal"] == ""
    # No error when there's literally nothing to refine.
    assert "error" not in res


# ── Happy-path 3-step chain (mocked client) ────────────────────────────────


def test_cinematic_full_chain_with_mocked_llm(monkeypatch):
    calls = []
    responses = iter([
        "reads stiff; prefer idiomatic phrasing",  # reflect
        "Hola, mundo.",                             # adapt
    ])

    def fake_chat(client, *, system, user):
        calls.append({"system": system, "user": user})
        return next(responses)

    mock_client = MagicMock()
    monkeypatch.setattr(tr, "_llm_client", lambda: mock_client)
    monkeypatch.setattr(tr, "_chat", fake_chat)

    res = tr.cinematic_refine_sync(
        "Hello, world.",
        "Hola mundo.",
        source_lang="en",
        target_lang="es",
        glossary=[{"source": "world", "target": "mundo"}],
    )
    assert res["literal"] == "Hola mundo."
    assert res["text"] == "Hola, mundo."
    assert res["critique"] == "reads stiff; prefer idiomatic phrasing"
    assert len(calls) == 2
    # Glossary prepended to both system prompts (reflect + adapt).
    assert "world → mundo" in calls[0]["system"]
    assert "world → mundo" in calls[1]["system"]


def test_cinematic_reflect_failure_returns_literal(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(tr, "_llm_client", lambda: mock_client)

    def boom(*a, **kw):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(tr, "_chat", boom)

    res = tr.cinematic_refine_sync(
        "Hello",
        "Hola",
        source_lang="en",
        target_lang="es",
    )
    assert res["text"] == "Hola"
    assert res["literal"] == "Hola"
    assert "reflect" in res.get("degraded", "")
    assert "error" not in res


# ── Divergence guard (v0.3.9 field report: hallucinated dub lines) ─────────


def _mock_chain(monkeypatch, reflect: str, adapt: str):
    """Wire cinematic_refine_sync to a mocked 2-step REFLECT→ADAPT chain."""
    responses = iter([reflect, adapt])
    monkeypatch.setattr(tr, "_llm_client", lambda: MagicMock())
    monkeypatch.setattr(tr, "_chat", lambda client, *, system, user: next(responses))


def test_cinematic_adapt_runaway_length_falls_back_to_literal(monkeypatch):
    """The reported bug: for a Latin-script target the script check passes ANY
    text, so a hallucinated wall of dialogue used to ship as the dub line."""
    literal = "¿Cómo estás hoy, amigo mío?"
    _mock_chain(monkeypatch, "fine but a bit stiff", "Hola amigo. " * 30)  # ~13× the literal
    res = tr.cinematic_refine_sync(
        "How are you doing today, my friend?", literal,
        source_lang="en", target_lang="es",
    )
    assert res["text"] == literal
    assert res.get("degraded") == "adapt-diverged"
    assert "error" not in res
    assert res["critique"] == "fine but a bit stiff"  # UI still sees what happened


def test_cinematic_adapt_critique_echo_rejected(monkeypatch):
    """ADAPT returning the critique itself must not become the dub line."""
    literal = "¿Cómo estás hoy, amigo mío? Hace mucho que no te veo por aquí."
    critique = (
        "The literal translation reads stiff and does not fit the slot; "
        "prefer a shorter, more idiomatic phrasing with warmer tone."
    )
    _mock_chain(monkeypatch, critique, critique)  # adapt echoes the critique verbatim
    res = tr.cinematic_refine_sync(
        "How are you doing today, my friend? Long time no see.", literal,
        source_lang="en", target_lang="es",
    )
    assert res["text"] == literal
    assert res.get("degraded") == "adapt-diverged"
    assert "error" not in res


def test_cinematic_sane_adaptation_accepted(monkeypatch):
    """A faithful, idiomatic rewrite passes every guard untouched."""
    literal = "¿Cómo estás hoy, amigo mío? Hace mucho que no te veo."
    adapted = "¿Qué tal, amigo? ¡Cuánto tiempo sin verte!"
    _mock_chain(monkeypatch, "a bit formal; contract it", adapted)
    res = tr.cinematic_refine_sync(
        "How are you doing today, my friend? Long time no see.", literal,
        source_lang="en", target_lang="es",
    )
    assert res["text"] == adapted
    assert "error" not in res


def test_cinematic_adapt_wrong_script_falls_back_to_literal(monkeypatch):
    """ADAPT output off the target script degrades to the literal with the
    script-specific marker (the pre-existing fallback, previously untested)."""
    literal = "नमस्ते मेरे दोस्त, आप कैसे हैं?"
    _mock_chain(monkeypatch, "solid but wordy", "This is English, not Hindi, sorry.")
    res = tr.cinematic_refine_sync(
        "Hello my friend, how are you?", literal,
        source_lang="en", target_lang="hi",
    )
    assert res["text"] == literal
    assert res.get("degraded") == "adapt-wrong-script:hi"
    assert "error" not in res


def test_chat_pins_low_temperature(monkeypatch):
    """Cinematic reflect/adapt must pin temperature like the Fast path does —
    the provider default of 1.0 is what let local models drift into invention."""
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok"))])
    monkeypatch.setattr(tr, "_llm_model", lambda: "test-model")
    assert tr._chat(client, system="s", user="u") == "ok"
    assert client.chat.completions.create.call_args.kwargs["temperature"] == 0.2


def test_refine_guard_short_reference_uses_absolute_cap():
    # A 2-word line legitimately triples — the ratio window must not apply.
    ok, _ = tr.refine_output_ok("¡No!", "¡Claro que no, jamás!", "es")
    assert ok
    # …but a wall of text after a 2-word line is still divergence.
    ok, reason = tr.refine_output_ok("¡No!", "x" * 200, "es")
    assert not ok and reason.startswith("length-abs")


def test_refine_guard_ratio_env_override(monkeypatch):
    literal = "¿Cómo estás hoy, amigo mío?"
    ok, reason = tr.refine_output_ok(literal, literal * 4, "es")
    assert not ok and reason.startswith("length-ratio")
    monkeypatch.setenv("OMNIVOICE_REFINE_RATIO_MAX", "5.0")
    ok, _ = tr.refine_output_ok(literal, literal * 4, "es")
    assert ok  # ceiling raised via env, mirroring the _cinematic_budget pattern


# ── Cinematic pass wall-clock budget (#stall follow-up) ────────────────────

def test_cinematic_budget_degrades_slow_segments_to_literal(monkeypatch):
    """A slow LLM must not hang the translate: the pass returns within the
    budget and unfinished segments fall back to their literal translation."""
    import asyncio
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("OMNIVOICE_CINEMATIC_BUDGET_S", "0.3")

    def _slow(src, lit, **kw):
        time.sleep(3.0)  # far over the 0.3s budget
        return {"text": "REFINED", "literal": lit, "critique": ""}

    monkeypatch.setattr(tr, "cinematic_refine_sync", _slow)
    pairs = [("s1", "hi", "hola"), ("s2", "world", "mundo")]

    async def _run():
        ex = ThreadPoolExecutor(max_workers=4)
        t0 = time.time()
        out = await tr.cinematic_refine_many(
            pairs, source_lang="en", target_lang="es", executor=ex,
        )
        return time.time() - t0, out

    dt, out = asyncio.run(_run())
    assert dt < 2.0, f"budget did not bound the pass (took {dt:.1f}s)"
    assert [r["id"] for r in out] == ["s1", "s2"]      # order + length preserved
    for r in out:
        assert r["text"] == r["literal"]                # degraded to literal
        assert r.get("degraded") == "cinematic-budget"
        assert "error" not in r


def test_cinematic_budget_disabled_runs_to_completion(monkeypatch):
    """Budget <= 0 disables the bound — every segment gets its refine."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("OMNIVOICE_CINEMATIC_BUDGET_S", "0")
    monkeypatch.setattr(
        tr, "cinematic_refine_sync",
        lambda src, lit, **kw: {"text": f"R:{lit}", "literal": lit, "critique": ""},
    )

    async def _run():
        return await tr.cinematic_refine_many(
            [("s1", "hi", "hola")], source_lang="en", target_lang="es",
            executor=ThreadPoolExecutor(max_workers=2),
        )

    out = asyncio.run(_run())
    assert out[0]["text"] == "R:hola" and "error" not in out[0]


# ── Retry-After honoring (#1133 class: a 2s throttle used to fail the pass) ──


class _RateLimit(Exception):
    """Shaped like an openai APIStatusError: status_code + response.headers."""

    def __init__(self, retry_after=None):
        super().__init__("429 simulated")
        self.status_code = 429

        class _Resp:
            headers = {"retry-after": retry_after} if retry_after is not None else {}

        self.response = _Resp()


def _client_429_then_ok(retry_after="2"):
    """chat.completions.create raises one 429, then succeeds."""
    from unittest.mock import MagicMock

    client = MagicMock()
    calls = {"n": 0}

    def create(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _RateLimit(retry_after)
        res = MagicMock()
        res.choices[0].message.content = "recovered"
        return res

    client.chat.completions.create = create
    return client, calls


def _record_sleeps(monkeypatch):
    """Patch ``time.sleep`` and return only the waits THIS thread asked for.

    ``translator`` does a plain ``import time``, so ``tr.time`` is the stdlib
    module itself and patching it replaces ``time.sleep`` process-wide. Any
    daemon thread a previous test left running then writes into the list —
    and, worse, stops sleeping at all. ``subprocess_backend``'s sidecar idle
    reaper does exactly that on a 30s loop, so a full-suite run turned
    ``assert not slept`` into ``assert not [30.0, 30.0, …]`` while the reaper
    busy-looped. Filtering by thread makes the assertion mean what it says:
    the waits _chat itself performed.
    """
    import threading
    import time as _time

    mine = threading.get_ident()
    waits: list = []
    real = _time.sleep

    def fake(seconds):
        if threading.get_ident() == mine:
            waits.append(seconds)
            return
        real(seconds)  # someone else's thread: let it really sleep

    monkeypatch.setattr(tr.time, "sleep", fake)
    return waits


def test_chat_honors_retry_after_once(monkeypatch):
    """A 429 with a small Retry-After gets ONE polite wait + retry, not a hard
    fail. OpenRouter's free pool says 'Retry-After: 2' — giving up instantly
    turned a two-second wait into a whole failed reflect pass."""
    sleeps = _record_sleeps(monkeypatch)
    client, calls = _client_429_then_ok("2")
    out = tr._chat(client, system="s", user="u")
    assert out == "recovered"
    assert calls["n"] == 2
    assert len(sleeps) == 1 and 2.0 <= sleeps[0] <= 3.5  # Retry-After + jitter


def test_chat_caps_absurd_retry_after(monkeypatch):
    """A provider demanding a 10-minute wait gets the cap, not a stalled dub."""
    sleeps = _record_sleeps(monkeypatch)
    client, _ = _client_429_then_ok("600")
    tr._chat(client, system="s", user="u")
    assert sleeps and sleeps[0] <= tr._RETRY_AFTER_CAP_S + 1.5


def test_chat_second_429_propagates(monkeypatch):
    """One retry only — a persistent throttle degrades the segment instead of
    looping."""
    _record_sleeps(monkeypatch)
    from unittest.mock import MagicMock

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=_RateLimit("1"))
    import pytest as _pytest
    with _pytest.raises(_RateLimit):
        tr._chat(client, system="s", user="u")
    assert client.chat.completions.create.call_count == 2


def test_chat_non_429_does_not_retry(monkeypatch):
    """Only rate limits are retryable; real errors propagate immediately."""
    slept = _record_sleeps(monkeypatch)
    from unittest.mock import MagicMock

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=RuntimeError("boom"))
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        tr._chat(client, system="s", user="u")
    assert client.chat.completions.create.call_count == 1
    assert not slept


def test_chat_strips_prefilled_reasoning(monkeypatch):
    """A local reasoning model served without a reasoning parser must not leak
    its monologue into the Cinematic output."""
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Keep it short.\n</think>\n\nHola, amigo."))])
    monkeypatch.setattr(tr, "_llm_model", lambda: "test-model")
    assert tr._chat(client, system="s", user="u") == "Hola, amigo."


def test_chat_keeps_a_closing_tag_quoted_from_the_source(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Usa </think> para cerrar el bloque."))])
    monkeypatch.setattr(tr, "_llm_model", lambda: "test-model")
    out = tr._chat(client, system="s", user="Use </think> to close the block.")
    assert out == "Usa </think> para cerrar el bloque."


# ── #2576: Japanese is written in kana AND kanji ─────────────────────────────
# Escaped so the CJK guard (tests/test_no_hardcoded_cjk.py) stays untouched.
_JA_KANJI_MAJORITY = "\u6771\u4eac\u90fd\u5e81\u306b\u884c\u304f\u3002"  # Tokyo city hall ni iku.
_JA_ALL_KANJI = "日本語教室"                                # nihongo kyoushitsu
_JA_SUPPLEMENTARY = "\U00020b9fる。人々"                        # rare Han + iteration mark


@pytest.mark.parametrize("text", [_JA_KANJI_MAJORITY, _JA_ALL_KANJI, _JA_SUPPLEMENTARY])
def test_japanese_han_letters_count_as_target_script(text):
    from api.routers.dub_translate import _looks_like_target

    assert tr._looks_like_target_script(text, "ja")
    assert _looks_like_target(text, "ja")


@pytest.mark.parametrize("text", ["This is English.", "Это русский.", "هذا عربي"])
def test_japanese_gate_still_rejects_other_scripts(text):
    from api.routers.dub_translate import _looks_like_target

    assert not tr._looks_like_target_script(text, "ja")
    assert not _looks_like_target(text, "ja")


# Chinese sentence: "We go to the store today to buy things, then cook at home."
_ZH_SENTENCE = "\u6211\u4eec\u4eca\u5929\u53bb\u5546\u5e97\u4e70\u4e1c\u897f\uff0c\u7136\u540e\u56de\u5bb6\u505a\u996d\u3002"
_JA_SENTENCE = "\u4eca\u65e5\u306f\u5546\u5e97\u3067\u8cb7\u3044\u7269\u3092\u3057\u3066\u3001\u5bb6\u3067\u6599\u7406\u3092\u3057\u307e\u3059\u3002"
_JA_NAME = "\u6771\u4eac"


def test_japanese_gate_rejects_chinese_output():
    from api.routers.dub_translate import _looks_like_target

    assert tr.script_ratio(_ZH_SENTENCE, "ja") == 0.0
    assert not tr._looks_like_target_script(_ZH_SENTENCE, "ja")
    assert not _looks_like_target(_ZH_SENTENCE, "ja")
    assert tr.script_ratio(_ZH_SENTENCE, "zh") == 1.0


@pytest.mark.parametrize("text", [_JA_SENTENCE, _JA_NAME, _JA_ALL_KANJI])
def test_japanese_gate_accepts_kana_and_short_kanji(text):
    from api.routers.dub_translate import _looks_like_target

    assert tr.script_ratio(text, "ja") == 1.0
    assert _looks_like_target(text, "ja")


def test_hindi_and_chinese_gates_unchanged_by_shared_ranges():
    from api.routers.dub_translate import LANG_REQUIRED_SCRIPT, _script_ratio

    assert _script_ratio("नमस्ते दोस्त.", "hi") == 1.0
    assert _script_ratio("Hello friend", "hi") == 0.0
    assert _script_ratio(_JA_ALL_KANJI, "zh") == 1.0
    assert _script_ratio("Hello", "es") == 1.0
    assert LANG_REQUIRED_SCRIPT["ja"][0] == "JAPANESE"


def test_cinematic_accepts_kanji_majority_japanese(monkeypatch):
    literal = "私は東京へ行く。"
    _mock_chain(monkeypatch, "fine", _JA_KANJI_MAJORITY)
    res = tr.cinematic_refine_sync(
        "I am going to Tokyo.", literal, source_lang="en", target_lang="ja",
    )
    assert res["text"] == _JA_KANJI_MAJORITY
    assert "degraded" not in res
