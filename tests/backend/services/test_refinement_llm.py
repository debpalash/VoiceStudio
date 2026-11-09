"""Phase-2 dictation refinement (Wave 2.1) — prompt builder + maybe_refine.

No real LLM: the active backend is monkeypatched. The pass-through contract
(raw transcript stands on ANY failure) is the load-bearing behavior here.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from services import refinement
from services.refinement import (
    REFINEMENT_EXAMPLES,
    RefinementFlags,
    build_refinement_prompt,
)


# ── Prompt builder ──────────────────────────────────────────────────────────

def test_all_flags_on_includes_all_sections():
    p = build_refinement_prompt(RefinementFlags())
    assert "text filter, not an assistant" in p
    assert "Remove disfluencies" in p
    assert "changes their mind mid-utterance" in p
    assert "Preserve technical terms" in p


def test_flags_off_drop_sections():
    p = build_refinement_prompt(RefinementFlags(self_correction=False, preserve_technical=False))
    assert "Remove disfluencies" in p
    assert "changes their mind mid-utterance" not in p
    assert "Preserve technical terms" not in p


def test_no_flags_yields_passthrough_prompt():
    p = build_refinement_prompt(
        RefinementFlags(smart_cleanup=False, self_correction=False, preserve_technical=False)
    )
    assert "Return the transcript unchanged" in p


def test_examples_are_user_assistant_pairs():
    assert len(REFINEMENT_EXAMPLES) == 7
    for user_turn, assistant_turn in REFINEMENT_EXAMPLES:
        assert user_turn and assistant_turn


# ── refine_transcript message shape ─────────────────────────────────────────

class _FakeBackend:
    id = "openai-compat"

    def __init__(self, reply="Refined."):
        self.reply = reply
        self.seen_messages = None

    def chat_messages(self, *, messages, timeout=None):
        self.seen_messages = messages
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_refine_transcript_builds_structured_few_shot(monkeypatch):
    fake = _FakeBackend("  Cleaned text.  ")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)

    out = refinement.refine_transcript("um hello there", RefinementFlags())
    assert out == "Cleaned text."

    msgs = fake.seen_messages
    assert msgs[0]["role"] == "system"
    # 7 example pairs as real chat turns, then the live transcript last.
    assert len(msgs) == 1 + 2 * len(REFINEMENT_EXAMPLES) + 1
    assert msgs[1]["role"] == "user" and msgs[2]["role"] == "assistant"
    assert msgs[-1] == {"role": "user", "content": "um hello there"}


# ── maybe_refine pass-through contract ──────────────────────────────────────

@pytest.fixture
def stored_config(monkeypatch):
    """In-memory settings_store so config round-trips without SQLite."""
    store = {}
    monkeypatch.setattr("services.settings_store.get_text",
                        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr("services.settings_store.set_text",
                        lambda key, value: store.__setitem__(key, value))
    return store


def test_maybe_refine_off_backend_returns_none(monkeypatch, stored_config):
    class _Off:
        id = "off"
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: _Off())
    assert refinement.maybe_refine("some words here") is None


def test_maybe_refine_disabled_config_returns_none(monkeypatch, stored_config):
    refinement.set_refinement_config({"auto": False})
    fake = _FakeBackend("never called")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)
    assert refinement.maybe_refine("some words here") is None
    assert fake.seen_messages is None


def test_maybe_refine_llm_failure_returns_none(monkeypatch, stored_config):
    fake = _FakeBackend(RuntimeError("connection refused"))
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)
    assert refinement.maybe_refine("some words here") is None


def test_maybe_refine_empty_reply_returns_none(monkeypatch, stored_config):
    fake = _FakeBackend("   ")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)
    assert refinement.maybe_refine("some words here") is None


def test_maybe_refine_success(monkeypatch, stored_config):
    fake = _FakeBackend("So the meeting is at 3pm on Tuesday.")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)
    out = refinement.maybe_refine("so um the meeting is at 3pm you know on tuesday")
    assert out == "So the meeting is at 3pm on Tuesday."


def test_maybe_refine_empty_transcript_short_circuits(stored_config):
    assert refinement.maybe_refine("") is None
    assert refinement.maybe_refine("   ") is None


def test_maybe_refine_respects_flag_config(monkeypatch, stored_config):
    refinement.set_refinement_config({"preserve_technical": False})
    fake = _FakeBackend("ok")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)
    refinement.maybe_refine("hello world out there")
    assert "Preserve technical terms" not in fake.seen_messages[0]["content"]


# ── maybe_refine_async: hard timeout budget (P0 — 51s stall) ─────────────────


class _SlowBackend:
    """A live-but-unresponsive LLM: accepts the call, never answers in time —
    the class of endpoint (placeholder key, dead Ollama) that stalled dictation."""

    id = "openai-compat"

    def __init__(self, sleep_s=5.0):
        self.sleep_s = sleep_s

    def chat_messages(self, *, messages, timeout=None):
        time.sleep(self.sleep_s)
        return "too late"


def test_refine_timeout_env_default_and_override(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_REFINE_TIMEOUT_S", raising=False)
    assert refinement._refine_timeout_s() == 4.0
    monkeypatch.setenv("OMNIVOICE_REFINE_TIMEOUT_S", "1.5")
    assert refinement._refine_timeout_s() == 1.5
    # Invalid / non-positive values can never disable the bound.
    monkeypatch.setenv("OMNIVOICE_REFINE_TIMEOUT_S", "junk")
    assert refinement._refine_timeout_s() == 4.0
    monkeypatch.setenv("OMNIVOICE_REFINE_TIMEOUT_S", "-3")
    assert refinement._refine_timeout_s() == 4.0


def test_maybe_refine_async_hard_timeout_returns_none_fast(monkeypatch, stored_config):
    """A slow LLM (5s) must NOT block past the 0.3s budget — the raw text stands
    and the outcome is recorded as a timeout. Fail-before: the WS handler used
    to `await asyncio.to_thread(maybe_refine, ...)` unbounded (the ~51s stall)."""
    monkeypatch.setattr(
        "services.llm_backend.get_active_llm_backend", lambda: _SlowBackend(3.0))

    async def _timed():
        # Measure the AWAIT inside the loop — the caller (the WS handler) is
        # unblocked here, and the status is read at the instant dictation
        # completes (before the orphaned to_thread finishes at loop shutdown;
        # the long-lived app loop never waits on it).
        t0 = time.perf_counter()
        out = await refinement.maybe_refine_async("um hello there", timeout_s=0.3)
        return out, time.perf_counter() - t0, refinement.get_last_refine_status()

    out, dt, status = asyncio.run(_timed())
    assert out is None
    assert dt < 2.0, f"refinement blocked the caller {dt:.1f}s — the budget was 0.3s"
    assert status and status["ok"] is False and status["reason"] == "timeout"


def test_maybe_refine_async_success_records_ok(monkeypatch, stored_config):
    fake = _FakeBackend("So the meeting is at 3pm.")
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: fake)

    out = asyncio.run(refinement.maybe_refine_async("so um the meeting is at 3pm"))
    assert out == "So the meeting is at 3pm."
    status = refinement.get_last_refine_status()
    assert status and status["ok"] is True


def test_maybe_refine_async_off_backend_is_noop(monkeypatch, stored_config):
    class _Off:
        id = "off"
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: _Off())
    assert asyncio.run(refinement.maybe_refine_async("some words here")) is None


def test_maybe_refine_async_empty_transcript(stored_config):
    assert asyncio.run(refinement.maybe_refine_async("")) is None
    assert asyncio.run(refinement.maybe_refine_async("   ")) is None


# ── Config round-trip ───────────────────────────────────────────────────────

def test_config_roundtrip_and_unknown_keys_ignored(stored_config):
    out = refinement.set_refinement_config({"self_correction": False, "bogus": True})
    assert out["self_correction"] is False
    assert "bogus" not in out
    again = refinement.get_refinement_config()
    assert again["self_correction"] is False
    assert again["auto"] is True


def test_config_invalid_json_falls_back_to_defaults(stored_config):
    stored_config[refinement._SETTINGS_KEY] = "{not json"
    cfg = refinement.get_refinement_config()
    assert cfg["auto"] is True and cfg["smart_cleanup"] is True


def test_refine_transcript_requests_no_reasoning(monkeypatch):
    seen = []

    class Backend:
        def chat_messages(self, **kwargs):
            seen.append(kwargs)
            return "  Tidied.  "

    monkeypatch.setattr(refinement, "_skill_llm", lambda: Backend())
    assert refinement.refine_transcript("um tidied") == "Tidied."
    assert seen[0]["reasoning_effort"] == "none"


def _reply(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _openai_backend(monkeypatch, create):
    from services.llm_backend import OpenAICompatBackend

    backend = OpenAICompatBackend()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(backend, "_get_client", lambda: client)
    monkeypatch.setattr(backend, "_resolve_provider", lambda: None)
    monkeypatch.setenv("TRANSLATE_MODEL", "fixture-model")
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://fixture/v1")
    return backend


def test_openai_backend_removes_leading_reasoning(monkeypatch):
    backend = _openai_backend(
        monkeypatch, lambda **kwargs: _reply("<think>work</think>Answer")
    )
    assert backend.chat_messages(messages=[{"role": "user", "content": "Q"}]) == "Answer"


@pytest.mark.parametrize(
    "raw",
    ["<think>work</think>", "<reasoning>unfinished", "hidden</think>"],
)
def test_reasoning_only_returns_empty(raw):
    from services.llm_backend import _strip_reasoning

    assert _strip_reasoning(raw) == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<thinking>notes</thinking> Result", "Result"),
        ("<think>a</think><reasoning>b</reasoning>Result", "Result"),
        ("private</think>Result", "Result"),
        ("private</thinking>Result", "Result"),
        ("Use <think> to start a block.", "Use <think> to start a block."),
        ("Answer <thinking>x</thinking> remains", "Answer <thinking>x</thinking> remains"),
    ],
)
def test_reasoning_stripping_boundaries(raw, expected):
    from services.llm_backend import _strip_reasoning

    assert _strip_reasoning(raw) == expected


def test_prefilled_close_is_kept_when_copied_from_prompt():
    from services.llm_backend import _strip_reasoning

    assert _strip_reasoning("literal </THINK> answer", "copy </think>") == "literal </THINK> answer"
    assert _strip_reasoning(
        "notes </think> literal </think> answer", "copy </THINK>"
    ) == "literal </think> answer"


def test_reasoning_effort_rejection_retries_and_is_memoized(monkeypatch):
    from services.llm_backend import _REASONING_EFFORT_REJECTED

    _REASONING_EFFORT_REJECTED.clear()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "reasoning_effort" in kwargs:
            raise RuntimeError("400 unknown field reasoning_effort")
        return _reply("ok")

    first = _openai_backend(monkeypatch, create)
    assert first.chat_messages(messages=[], reasoning_effort="none") == "ok"
    second = _openai_backend(monkeypatch, create)
    assert second.chat_messages(messages=[], reasoning_effort="none") == "ok"
    assert len(calls) == 3
    assert "reasoning_effort" not in calls[-1]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("400 unsupported temperature"),
        RuntimeError("502 bad gateway"),
        TypeError("client bug"),
    ],
)
def test_unrelated_client_failures_are_not_retried(monkeypatch, error):
    from services.llm_backend import _REASONING_EFFORT_REJECTED

    _REASONING_EFFORT_REJECTED.clear()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise error

    backend = _openai_backend(monkeypatch, create)
    with pytest.raises(type(error), match=str(error)):
        backend.chat_messages(messages=[], reasoning_effort="none", temperature=0.2)
    assert len(calls) == 1
    assert not _REASONING_EFFORT_REJECTED


def test_refinement_propagates_internal_type_error_without_retry(monkeypatch):
    calls = []

    class Backend:
        def chat_messages(self, **kwargs):
            calls.append(kwargs)
            raise TypeError("internal client failure")

    monkeypatch.setattr(refinement, "_skill_llm", lambda: Backend())
    with pytest.raises(TypeError, match="internal client failure"):
        refinement.refine_transcript("hello")
    assert len(calls) == 1
