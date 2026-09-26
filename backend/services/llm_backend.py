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

#: Local reasoning models (Qwen3, DeepSeek-R1 derivatives, gpt-oss…) emit their
#: chain of thought in a <think> block ahead of the answer. It is not part of
#: the answer — on the dictation path it would be pasted straight into the
#: user's transcript.
_THINK_TAG_RE = re.compile(r"^\s*<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
#: Output truncated mid-thought (token cap, or a stop that never came) leaves
#: the block unclosed. Still not an answer: drop from the tag to the end.
_OPEN_THINK_RE = re.compile(r"^\s*<(think|thinking|reasoning)>.*\Z", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(raw: str) -> str:
    """Return the answer with any reasoning block removed.

    Returns ``""`` when the response was *only* reasoning. That is deliberate:
    every caller treats an empty completion as "no result" and keeps its own
    input (dictation keeps the raw transcript, translation keeps the source),
    whereas handing back the model's private monologue as if it were the answer
    would silently overwrite the user's words with it.
    """
    while match := _THINK_TAG_RE.match(raw):
        raw = raw[match.end():]
    cleaned = _OPEN_THINK_RE.sub("", raw)
    return cleaned.strip()


def _rejects_reasoning_effort(exc: BaseException) -> bool:
    """True when a failed request looks like "this endpoint has no such param".

    ``reasoning_effort`` is only understood by reasoning models and by newer
    SDKs. An older SDK raises TypeError locally; a server that doesn't know the
    field answers 400 — which is NOT a TypeError, so catching only that left
    refinement permanently broken against such an endpoint.
    """
    # Only when the server names the field: OpenAI ("Unsupported parameter:
    # 'reasoning_effort' …"), Azure ("Unrecognized request argument supplied:
    # reasoning_effort"), pydantic-validated servers (field listed). A generic
    # "unsupported parameter" could be about temperature, and retrying without
    # the wrong field would just fail twice.
    text = str(exc).lower()
    return "reasoning_effort" in text or "reasoning effort" in text


#: ``base_url|model`` pairs that rejected ``reasoning_effort``. Module-level on
#: purpose: get_active_llm_backend() builds a FRESH backend instance per call,
#: so an instance attribute would forget the rejection before the next request
#: and every refinement would pay the rejected round trip again.
_REASONING_EFFORT_REJECTED: set[str] = set()


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
        """

    def chat_messages(self, *, messages: list[dict], timeout: Optional[float] = None,
                      temperature: Optional[float] = None,
                      reasoning_effort: Optional[str] = None) -> str:
        """One-shot completion over a full message list.

        Fallback for backends that only implement the two-message
        :meth:`chat`: it keeps the system prompt and the final user turn.
        Any few-shot turns in between are DROPPED — small local models lean on
        those examples heavily, so a backend that callers pass few-shot turns
        to should override this rather than inherit it. Logged, because a
        silently truncated prompt looks like a model-quality problem.
        """
        sys_msg = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        usr_msg = messages[-1]["content"] if messages else ""
        dropped = max(len(messages) - (2 if sys_msg else 1), 0)
        if dropped:
            logger.warning(
                "%s has no chat_messages(): dropping %d few-shot turn(s) from the prompt.",
                type(self).__name__, dropped,
            )
        return self.chat(system=sys_msg, user=usr_msg, timeout=timeout,
                         temperature=temperature, reasoning_effort=reasoning_effort)

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
        """Endpoint this instance will talk to, or None when unconfigured.
        Never raises — used for bookkeeping keys, not for the request itself."""
        from services import llm_providers
        p = self._resolve_provider()
        if p is not None:
            return llm_providers.resolve_base_url(p)
        return os.environ.get("TRANSLATE_BASE_URL") or None

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
                "No LLM configured. Add a provider key in Settings → LLM Providers "
                "(OpenAI/OpenRouter/OrcaRouter/Cheaper Inference/Groq/… or a local "
                "Ollama), or set TRANSLATE_BASE_URL (+ TRANSLATE_API_KEY)."
            )
        if not llm_providers.resolve_base_url(p):
            return False, f"{p.display_name}: set a Base URL in Settings → LLM Providers."
        if not llm_providers.has_key(p):
            return False, f"{p.display_name}: add an API key in Settings → LLM Providers."
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
        from openai import OpenAI
        from services import llm_providers
        p = self._resolve_provider()
        if p is None:
            raise RuntimeError("LLM not configured. See `is_available()` for the hint.")
        base_url = llm_providers.resolve_base_url(p)
        api_key = llm_providers.resolve_api_key(p)
        if not api_key:
            raise RuntimeError("LLM not configured. See `is_available()` for the hint.")
        kw = {"api_key": api_key}
        if base_url:
            kw["base_url"] = base_url
        # max_retries=0 so a 429 + Retry-After can't make one chat() sleep
        # through the Autofit fit-pass wall-clock budget (speech_rate).
        self._client = OpenAI(max_retries=0, **kw)
        return self._client

    def chat(self, *, system: str, user: str, timeout: Optional[float] = None,
             temperature: Optional[float] = None,
             reasoning_effort: Optional[str] = None) -> str:
        return self.chat_messages(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=timeout,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def chat_messages(self, *, messages: list[dict], timeout: Optional[float] = None,
                      temperature: Optional[float] = None,
                      reasoning_effort: Optional[str] = None) -> str:
        """One-shot completion over a full message list.

        Additive surface for callers that need structured few-shot turns
        (dictation refinement, Wave 2.1) — small local models pattern-match
        and echo inline examples, so examples must arrive as prior chat
        turns, not inside the system prompt.

        ``temperature`` is only forwarded when set (Cinematic/Autofit pin 0.2
        — the provider default of 1.0 makes local models drift and invent);
        every other caller leaves it None and keeps the provider default.

        ``reasoning_effort`` allows latency-critical paths (dictation refinement)
        to explicitly request 'none' to bypass 25+ second thinking pauses on
        reasoning models.
        """
        if timeout is None:
            try:
                timeout = float(os.environ.get("OMNIVOICE_LLM_TIMEOUT", "45"))
            except ValueError:
                timeout = 45.0
        model = self.model_name
        endpoint = f"{self._base_url() or ''}|{model}"
        kw = {}
        if temperature is not None:
            kw["temperature"] = temperature
        if reasoning_effort is not None and endpoint not in _REASONING_EFFORT_REJECTED:
            kw["reasoning_effort"] = reasoning_effort

        def _create(**extra):
            return self._get_client().chat.completions.create(
                model=model,
                timeout=timeout,
                messages=messages,
                **extra,
            )

        try:
            res = _create(**kw)
        except Exception as exc:  # noqa: BLE001 — re-raised unless it's the param
            if "reasoning_effort" not in kw or not _rejects_reasoning_effort(exc):
                raise
            # This SDK or endpoint doesn't take the field. Remember it for the
            # endpoint so the rejected round trip is paid once — not once per
            # request, since callers get a fresh instance each time — and run
            # the request again without it rather than failing the feature.
            logger.debug("reasoning_effort not supported by %s (%s); retrying without it",
                         endpoint, exc)
            _REASONING_EFFORT_REJECTED.add(endpoint)
            kw.pop("reasoning_effort", None)
            res = _create(**kw)

        return _strip_reasoning(res.choices[0].message.content or "")

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
        model = llm_providers.resolve_model(p)
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
