"""Phase-2 dictation refinement (Wave 2.1) — prompt builder + maybe_refine.

No real LLM: the active backend is monkeypatched. The pass-through contract
(raw transcript stands on ANY failure) is the load-bearing behavior here.
"""

from __future__ import annotations

import asyncio
import time

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


def test_refine_transcript_passes_reasoning_effort_none(monkeypatch):
    class _ReasoningBackend:
        id = "openai-compat"
        def __init__(self):
            self.last_reasoning_effort = None
        def chat_messages(self, *, messages, timeout=None, reasoning_effort=None):
            self.last_reasoning_effort = reasoning_effort
            return "Cleaned text."

    backend = _ReasoningBackend()
    monkeypatch.setattr("services.llm_backend.get_active_llm_backend", lambda: backend)
    res = refinement.refine_transcript("hello um world", RefinementFlags())
    assert res == "Cleaned text."
    assert backend.last_reasoning_effort == "none"


def test_openai_compat_backend_strips_think_tags(monkeypatch):
    from services.llm_backend import OpenAICompatBackend
    backend = OpenAICompatBackend()

    class _FakeCompletionMessage:
        content = "<think>\nLet me reason through this.\n</think>\nActual refined answer."
    class _FakeChoice:
        message = _FakeCompletionMessage()
    class _FakeResponse:
        choices = [_FakeChoice()]
    class _FakeCompletions:
        def create(self, **kw):
            return _FakeResponse()
    class _FakeChat:
        completions = _FakeCompletions()
    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(backend, "_get_client", lambda: _FakeClient())
    monkeypatch.setattr(backend, "_resolve_provider", lambda: None)
    result = backend.chat_messages(messages=[{"role": "user", "content": "test"}])
    assert result == "Actual refined answer."


def test_strip_reasoning_never_returns_the_models_monologue():
    """A response that is ONLY thinking must come back empty.

    maybe_refine treats an empty completion as "no result" and keeps the raw
    transcript; returning the raw <think> body instead pasted the model's
    private reasoning into the user's dictation.
    """
    from services.llm_backend import _strip_reasoning

    assert _strip_reasoning("<think>weighing the options</think>") == ""
    # truncated mid-thought (token cap): unclosed, still not an answer
    assert _strip_reasoning("<think>never closed, output cut off") == ""
    assert _strip_reasoning("<thinking>x</thinking>\nReal answer.") == "Real answer."
    assert _strip_reasoning("Plain answer.") == "Plain answer."


def _fake_client(seen, reject_reasoning=False, boom=None):
    class _Msg:
        content = "Cleaned."

    class _Choice:
        message = _Msg()

    class _Res:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            seen.append(kw)
            if boom is not None:
                raise boom
            if reject_reasoning and "reasoning_effort" in kw:
                raise RuntimeError("Error code: 400 - Unsupported parameter: 'reasoning_effort'")
            return _Res()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _bound_backend(monkeypatch, client, *, fresh_memo=True):
    from services import llm_backend
    from services.llm_backend import OpenAICompatBackend

    if fresh_memo:
        llm_backend._REASONING_EFFORT_REJECTED.clear()
    backend = OpenAICompatBackend()
    monkeypatch.setattr(backend, "_get_client", lambda: client)
    monkeypatch.setattr(backend, "_resolve_provider", lambda: None)
    return backend


def test_chat_messages_retries_without_reasoning_effort_on_a_server_400(monkeypatch):
    """An endpoint that doesn't know the field answers 400, not TypeError —
    catching only TypeError left dictation refinement permanently broken."""
    seen: list[dict] = []
    client = _fake_client(seen, reject_reasoning=True)
    backend = _bound_backend(monkeypatch, client)

    msgs = [{"role": "user", "content": "x"}]
    assert backend.chat_messages(messages=msgs, reasoning_effort="none") == "Cleaned."
    assert len(seen) == 2 and "reasoning_effort" not in seen[1]

    # The rejection is remembered per ENDPOINT, not per instance: production
    # gets a fresh OpenAICompatBackend on every call (get_active_llm_backend),
    # so a per-instance memo would pay the rejected round trip on each request.
    seen.clear()
    again = _bound_backend(monkeypatch, client, fresh_memo=False)
    assert again.chat_messages(messages=msgs, reasoning_effort="none") == "Cleaned."
    assert len(seen) == 1 and "reasoning_effort" not in seen[0]


def test_chat_messages_only_retries_when_the_server_names_the_field(monkeypatch):
    """A 400 about some OTHER parameter must not trigger the retry — dropping
    reasoning_effort wouldn't fix it, it would just fail twice."""
    import pytest as _pytest

    seen: list[dict] = []
    backend = _bound_backend(
        monkeypatch,
        _fake_client(seen, boom=RuntimeError("400 Unsupported parameter: 'temperature'")),
    )
    with _pytest.raises(RuntimeError, match="temperature"):
        backend.chat_messages(
            messages=[{"role": "user", "content": "x"}], temperature=0.2, reasoning_effort="none"
        )
    assert len(seen) == 1


def test_chat_messages_does_not_swallow_unrelated_failures(monkeypatch):
    import pytest as _pytest

    seen: list[dict] = []
    backend = _bound_backend(
        monkeypatch, _fake_client(seen, boom=RuntimeError("502 upstream is down"))
    )
    with _pytest.raises(RuntimeError, match="502"):
        backend.chat_messages(messages=[{"role": "user", "content": "x"}], reasoning_effort="none")
    assert len(seen) == 1  # no blind retry


def test_reasoning_filter_preserves_literal_tags_in_the_answer():
    from services.llm_backend import _strip_reasoning
    for text in ['Use <think> to start a block.', 'Explain <thinking>step</thinking> literally.', 'Example: <reasoning>unfinished']:
        assert _strip_reasoning(text) == text


def test_internal_type_error_is_not_retried_or_memoized(monkeypatch):
    import pytest
    from services import llm_backend, refinement
    seen = []
    backend = _bound_backend(monkeypatch, _fake_client(seen, boom=TypeError('internal decoder failure')))
    monkeypatch.setattr(refinement, '_skill_llm', lambda: backend)
    with pytest.raises(TypeError, match='internal decoder failure'):
        refinement.refine_transcript('keep my words')
    assert len(seen) == 1
    assert not llm_backend._REASONING_EFFORT_REJECTED


def test_multiple_leading_reasoning_blocks_keep_the_final_answer():
    from services.llm_backend import _strip_reasoning
    assert _strip_reasoning('<think>one</think>\n<thinking>two</thinking>Answer.') == 'Answer.'


def test_prefilled_reasoning_block_is_stripped():
    """Templates that put <think> in the prompt (Spark-X2.5, Qwen3 thinking
    variants) leave only the closing tag in the reply when the server runs
    without a reasoning parser."""
    from services.llm_backend import _strip_reasoning
    assert _strip_reasoning("The user wants Spanish.\n</think>\n\nHola.") == "Hola."
    assert _strip_reasoning("weighing it</thinking>Answer.") == "Answer."
    assert _strip_reasoning("only reasoning, then</think>") == ""
    # An answer that merely mentions a tag is still left alone.
    assert _strip_reasoning("Close it with <think>x</think> here.") == "Close it with <think>x</think> here."


def test_literal_closing_tag_from_the_prompt_is_kept():
    """A reply may repeat a bare </think> only because the input had one —
    translating that input must not cut the answer at the tag."""
    from services.llm_backend import _strip_reasoning
    line = "Use </think> to close the block."
    assert _strip_reasoning(line, prompt="Translate to Spanish:\n" + line) == line
    assert _strip_reasoning("Usa </THINK> para cerrar.", prompt=line) == "Usa </THINK> para cerrar."
    # A tag the prompt never contained still ends a prefilled block.
    assert _strip_reasoning("weighing it</think>Answer.", prompt="Translate: hi") == "Answer."


def test_reasoning_ending_in_a_tag_the_source_also_contains_is_still_removed():
    """Source text with a literal </think> must not switch reasoning removal
    off: the reply has one more tag than the prompt, and that one is the
    boundary (#2425 review)."""
    from services.llm_backend import _strip_reasoning
    prompt = "Translate to Spanish:\nUse </think> to close the block."
    reply = "The user wants Spanish.</think>Usa </think> para cerrar el bloque."
    assert _strip_reasoning(reply, prompt=prompt) == "Usa </think> para cerrar el bloque."
