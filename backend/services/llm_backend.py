"""
LLM adapter interface — Phase 3.4 (ROADMAP.md).

The translator (Phase 1.1) already speaks the OpenAI chat-completions shape.
This module formalises that surface into an `LLMBackend` protocol so other
call sites (glossary auto-extract, directorial AI in Phase 4, reflection
passes) can depend on the interface instead of duplicating the client
construction logic.

Today we ship:

    • OpenAICompatBackend — wraps the `openai` package pointing at whatever
      TRANSLATE_BASE_URL + TRANSLATE_API_KEY say. Works with real OpenAI,
      Ollama (`base_url=http://localhost:11434/v1`), LM Studio, Together,
      Anyscale, Claude-via-OpenAI-compat proxies.
    • OffBackend — explicit no-op. Gets returned when no LLM is configured
      so callers fail fast with a clear message instead of a KeyError.

Selection: auto — if env is configured, return OpenAICompatBackend; else
OffBackend. Callers can override with `OMNIVOICE_LLM_BACKEND`.

NOTE: cloud providers stay **opt-in** per the ROADMAP's privacy policy.
Even with `TRANSLATE_API_KEY` set, the flag only turns on this backend;
individual features (Cinematic translate, glossary auto-extract) still
check their own `quality="cinematic"` gate / user action before calling.
"""
from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Iterator, Optional

logger = logging.getLogger("omnivoice.llm")

_REASONING_NAMES = r"think|thinking|reasoning"
_OPEN_REASONING_TAG = re.compile(rf"<({_REASONING_NAMES})>", re.IGNORECASE)
_CLOSE_REASONING_TAG = re.compile(rf"</({_REASONING_NAMES})>", re.IGNORECASE)
_REASONING_EFFORT_REJECTED: set[str] = set()


def _conversation_text(messages: list[dict]) -> str:
    parts: list[str] = []
    for turn in messages:
        value = turn.get("content")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _reasoning_option_was_rejected(exc: Exception) -> bool:
    provider_message = str(exc).casefold()
    return any(
        term in provider_message
        for term in ("reasoning_effort", "reasoning effort")
    )


def _strip_reasoning(raw: str, prompt: str = "") -> str:
    """Discard reasoning markup only when it occupies the reply prefix."""
    answer = raw
    first_closer = _CLOSE_REASONING_TAG.search(answer)
    if first_closer is not None:
        prefix = answer[:first_closer.start()]
        closer = first_closer.group(0).casefold()
        is_prefilled = _OPEN_REASONING_TAG.search(prefix) is None
        repeats_input = answer.casefold().count(closer) <= prompt.casefold().count(closer)
        if is_prefilled and not repeats_input:
            answer = answer[first_closer.end():]

    while True:
        candidate = answer.lstrip()
        opening = _OPEN_REASONING_TAG.match(candidate)
        if opening is None:
            break
        tag_name = opening.group(1)
        after_open = candidate[opening.end():]
        closing = re.search(rf"</{re.escape(tag_name)}>", after_open, re.IGNORECASE)
        if closing is None:
            return ""
        answer = after_open[closing.end():]

    return answer.strip()


class LLMBackend(ABC):
    id: str = "base"
    display_name: str = "Base LLM"
    # LLM backends call out over the network (OpenAI/Ollama/LM Studio) or are a
    # no-op — none run a model on the user's GPU. So `gpu_compat` is empty and
    # list_backends() labels the family `effective_device:"network"` /
    # routing_status:"n/a" rather than asserting a false GPU claim. Routing is
    # never gated for LLM (see engines.select_engine + diagnose).
    gpu_compat: tuple[str, ...] = ()

    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def chat(self, *, system: str, user: str, timeout: Optional[float] = None,
             temperature: Optional[float] = None,
             reasoning_effort: Optional[str] = None) -> str:
        """One-shot chat completion. Returns the assistant content string.
        Raises on failure — callers decide whether to fallback gracefully.
        ``temperature`` is only sent to the provider when set — callers that
        leave it None keep the provider default (existing behavior).
        ``reasoning_effort`` hints that latency-sensitive work should skip or
        shorten model thinking; backends without that capability may ignore it.
        """
        ...

    def chat_messages(self, *, messages: list[dict], timeout: Optional[float] = None,
                      temperature: Optional[float] = None,
                      reasoning_effort: Optional[str] = None) -> str:
        """Adapt message turns for a two-message backend.

        Backends that need complete few-shot conversations must override this.
        """
        begins_with_system = bool(messages and messages[0].get("role") == "system")
        system_text = messages[0]["content"] if begins_with_system else ""
        user_text = messages[-1]["content"] if messages else ""
        retained_turns = 2 if system_text else 1
        omitted_turns = max(len(messages) - retained_turns, 0)
        if omitted_turns:
            logger.warning(
                "%s omitted %d intermediate turn(s) from its two-message request",
                type(self).__name__,
                omitted_turns,
            )
        return self.chat(
            system=system_text,
            user=user_text,
            timeout=timeout,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def chat_messages_stream(self, *, messages: list[dict], timeout: Optional[float] = None,
                             temperature: Optional[float] = None) -> Iterator[str]:
        """Yield the reply as text deltas while it is generated.

        Latency-critical callers (the phone call agent starts speaking on the
        first sentence) use this. Backends without streaming yield the whole
        reply once. Reasoning blocks are NOT stripped here — a streaming
        caller must handle a leading ``<think>`` block itself.
        """
        yield self.chat_messages(messages=messages, timeout=timeout, temperature=temperature)


# ── OpenAI-compatible (the only backend that actually calls out today) ─────


class OpenAICompatBackend(LLMBackend):
    id = "openai-compat"
    display_name = "OpenAI-compatible (real OpenAI, Ollama, LM Studio, …)"

    def __init__(self, provider=None):
        """``provider``: optional ``llm_providers.Provider`` to bind this
        instance to (LLM Skills per-skill routing). None keeps the historical
        behavior — resolve the ACTIVE provider at call time."""
        self._client = None
        self._provider = provider

    def _resolve_provider(self):
        if self._provider is not None:
            return self._provider
        from services import llm_providers
        return llm_providers.active_provider()

    def _base_url(self) -> Optional[str]:
        try:
            provider = self._resolve_provider()
            if provider is not None:
                from services import llm_providers
                return llm_providers.resolve_base_url(provider)
            return os.environ.get("TRANSLATE_BASE_URL") or None
        except Exception:
            return None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "openai package missing (install with `pip install openai`)."
        # Resolve through the provider registry — the active provider carries
        # its own base_url/key/model. Legacy single-endpoint setups (a lone
        # TRANSLATE_BASE_URL) resolve to the "custom" provider, so this stays
        # backward-compatible with pre-registry configs.
        from services import llm_providers
        p = llm_providers.active_provider()
        if p is None:
            return False, (
                "Add a provider key in Settings -> LLM Providers (for example OpenAI, "
                "Cheaper Inference, or Groq), choose local Ollama, or set "
                "TRANSLATE_BASE_URL with optional TRANSLATE_API_KEY."
            )
        error = llm_providers.configuration_error(p)
        if error:
            return False, error
        return True, f"ready ({p.display_name})"

    @property
    def model_name(self) -> str:
        from services import llm_providers
        p = self._resolve_provider()
        if p is not None:
            return llm_providers.resolve_model(p)
        return os.environ.get("TRANSLATE_MODEL", "gpt-4o-mini")

    def _get_client(self):
        if self._client is not None:
            return self._client
        from services import llm_providers
        p = self._resolve_provider()
        if p is None:
            raise RuntimeError("LLM not configured. See `is_available()` for the hint.")
        from services.llm_transport import create_client
        self._client = create_client(p)
        return self._client

    def chat(self, *, system: str, user: str, timeout: Optional[float] = None,
             temperature: Optional[float] = None,
             reasoning_effort: Optional[str] = None) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat_messages(
            messages=messages,
            timeout=timeout,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def chat_messages(self, *, messages: list[dict], timeout: Optional[float] = None,
                      temperature: Optional[float] = None,
                      reasoning_effort: Optional[str] = None) -> str:
        """Complete a structured conversation without flattening few-shot turns.

        Inline examples are often echoed by small local models, so examples
        remain earlier chat turns. Temperature is omitted unless selected;
        Cinematic and Autofit use 0.2 to avoid drift from provider defaults.
        Latency-sensitive callers may pass ``reasoning_effort="none"`` to
        avoid long reasoning pauses.
        """
        if timeout is None:
            try:
                timeout = float(os.environ.get("OMNIVOICE_LLM_TIMEOUT", "45"))
            except ValueError:
                timeout = 45.0
        selected_model = self.model_name
        route_signature = f"{self._base_url() or ''}|{selected_model}"
        optional_fields = {}
        if temperature is not None:
            optional_fields["temperature"] = temperature
        if reasoning_effort is not None and route_signature not in _REASONING_EFFORT_REJECTED:
            optional_fields["reasoning_effort"] = reasoning_effort

        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=selected_model,
                timeout=timeout,
                messages=messages,
                **optional_fields,
            )
        except Exception as exc:
            sent_reasoning_hint = "reasoning_effort" in optional_fields
            if not sent_reasoning_hint or not _reasoning_option_was_rejected(exc):
                raise
            _REASONING_EFFORT_REJECTED.add(route_signature)
            logger.debug("Disabling the reasoning hint for endpoint %s", route_signature)
            optional_fields.pop("reasoning_effort")
            response = client.chat.completions.create(
                model=selected_model,
                timeout=timeout,
                messages=messages,
                **optional_fields,
            )

        reply_text = response.choices[0].message.content or ""
        source_text = _conversation_text(messages)
        return _strip_reasoning(reply_text, prompt=source_text)

    def chat_messages_stream(self, *, messages: list[dict], timeout: Optional[float] = None,
                             temperature: Optional[float] = None) -> Iterator[str]:
        if timeout is None:
            try:
                timeout = float(os.environ.get("OMNIVOICE_LLM_TIMEOUT", "45"))
            except ValueError:
                timeout = 45.0
        kw = {} if temperature is None else {"temperature": temperature}
        stream = self._get_client().chat.completions.create(
            model=self.model_name, timeout=timeout, messages=messages, stream=True, **kw,
        )
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                delta = getattr(choices[0].delta, "content", None) if choices else None
                if delta:
                    yield delta
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


# ── Off — explicit no-LLM path ────────────────────────────────────────────


class OffBackend(LLMBackend):
    id = "off"
    display_name = "Off (no LLM)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "ready"

    @property
    def model_name(self) -> str:
        return "none"

    def chat(self, **kw) -> str:
        raise RuntimeError(
            "No LLM backend configured. Set TRANSLATE_BASE_URL (+ TRANSLATE_API_KEY) "
            "to use features that need one (Cinematic translate, glossary auto-extract)."
        )

    def chat_messages(self, **kw) -> str:
        return self.chat(**kw)


_REGISTRY: dict[str, type[LLMBackend]] = {
    "openai-compat": OpenAICompatBackend,
    "off":           OffBackend,
}


# Most-recent failure per backend (parity with tts/asr list_backends).
_LAST_ERRORS: dict[str, str] = {}

_INSTALL_HINTS: dict[str, str] = {
    "openai-compat": "Set TRANSLATE_BASE_URL (+ TRANSLATE_API_KEY) — OpenAI, "
                     "Ollama (http://localhost:11434/v1), or any compatible host.",
}


def list_backends() -> list[dict]:
    """Same 11-key shape as tts/asr so the matrix renders families uniformly.

    LLM is NOT a GPU family: every entry carries literal
    ``effective_device:"network"`` / ``routing_status:"n/a"`` /
    ``routing_reason:null`` (NOT via resolve_routing — that would be a false
    GPU claim). ``effective_device:"network"`` is a label, not a probe: nothing
    here touches the network (local-first).
    """
    from core.scrub import scrub_text

    out: list[dict] = []
    for bid, cls in _REGISTRY.items():
        try:
            ok, msg = cls.is_available()
        except Exception:
            ok = False
            msg = "Availability probe failed; check the backend log."
            logger.warning("llm list_backends: availability probe failed for registered backend %s", bid)
        if ok:
            _LAST_ERRORS.pop(bid, None)
        else:
            _LAST_ERRORS[bid] = scrub_text(msg)
        out.append({
            "id": bid,
            "display_name": cls.display_name,
            "available": ok,
            "reason": None if ok else scrub_text(msg),
            "install_hint": _INSTALL_HINTS.get(bid),
            "last_error": _LAST_ERRORS.get(bid),
            "isolation_mode": "in-process",
            "gpu_compat": list(getattr(cls, "gpu_compat", ())),
            "effective_device": "network",
            "routing_status": "n/a",
            "routing_reason": None,
            # The openai-compat family entry and the LLM Providers panel are
            # ONE system (this backend resolves through the active provider),
            # but the UI presented them as unrelated. Naming the resolved
            # provider + model here lets the catalogue row say which endpoint
            # actually answers, instead of a generic family label.
            "hint": _provider_hint(bid) if ok else None,
        })
    return out


def _provider_hint(bid: str) -> str | None:
    """``Provider · model`` for the openai-compat row, None for everything else."""
    if bid != "openai-compat":
        return None
    try:
        from services import llm_providers
        p = llm_providers.active_provider()
        if p is None:
            return None
        model = llm_providers.configured_model(p)
        return f"{p.display_name} · {model}" if model else p.display_name
    except Exception:
        # The hint is decoration; a provider-registry hiccup must not take
        # down the whole engines listing.
        return None


def active_backend_id() -> str:
    explicit = os.environ.get("OMNIVOICE_LLM_BACKEND")
    if explicit:
        return explicit
    from core import prefs
    picked = prefs.get("llm_backend")
    if picked:
        return picked
    ok, _ = OpenAICompatBackend.is_available()
    return "openai-compat" if ok else "off"


def get_active_llm_backend() -> LLMBackend:
    bid = active_backend_id()
    if bid not in _REGISTRY:
        raise ValueError(f"Unknown LLM backend: {bid!r}. Known: {list(_REGISTRY)}")
    return _REGISTRY[bid]()
