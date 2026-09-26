"""LLM provider registry — the OpenAI-compatible providers VoiceStudio can use
for Cinematic / Autofit translation (and any future LLM feature).

Every provider here speaks the OpenAI chat-completions shape, so a single
client (`llm_backend.OpenAICompatBackend`) drives all of them — the only
per-provider differences are ``base_url``, ``model``, and the API key. This
module is the one place that knows those defaults and resolves the live value
for the *active* provider.

Resolution precedence for every field (key / base_url / model), highest first:
    1. Environment variable  — power-user / `.env` override, wins always.
    2. Encrypted settings store (UI-entered) — `settings_store.get_secret` for
       keys, `get_text` for base_url/model overrides.
    3. Built-in default from the table below.

Local providers (Ollama, LM Studio) need no key — a "local" sentinel is used
so the OpenAI client is happy. This keeps the local-first path fully offline:
nothing is sent anywhere unless the user picks a remote provider *and* a
feature gate (quality="cinematic"/"autofit") fires.

Keys entered in the UI are stored **encrypted** (never in `.env`, never
returned to the client). `.env` keys remain a valid override for CI / power
users.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.llm_providers")

# Settings-store row names (non-secret overrides live in the plaintext table;
# keys live in the encrypted secret table under ``llm_key.<id>``).
_ACTIVE_PROVIDER_KEY = "llm.active_provider"
_BASE_URL_KEY = "llm.base_url."   # + provider id
_MODEL_KEY = "llm.model."         # + provider id
SECRET_PREFIX = "llm_key."        # + provider id  → settings_store secret name


@dataclass(frozen=True)
class Provider:
    id: str
    display_name: str
    default_base_url: str
    default_model: str
    # Env var names checked (in order) for the API key. First one set wins.
    key_envs: tuple[str, ...] = ()
    base_url_env: Optional[str] = None
    model_env: Optional[str] = None
    local: bool = False           # runs on the user's machine → no key, offline
    # Key optional when a base_url is set (self-hosted OpenAI-compatible servers
    # — vLLM, LM Studio behind a custom URL — often ignore the key). Preserves
    # the pre-registry behaviour where a lone TRANSLATE_BASE_URL was usable
    # keyless.
    key_optional: bool = False
    # True when ``default_model`` is a placeholder rather than a model this
    # provider will accept. LM Studio serves whatever the user has loaded, so
    # there is no name we can ship that is right — see resolve_model.
    model_is_placeholder: bool = False
    needs_account: bool = False   # Cloudflare: base_url needs an account id
    account_env: Optional[str] = None
    signup_url: str = ""
    notes: str = ""


# Order here is the display order in the settings page. OpenAI first (the
# canonical), then the free/fast cloud providers from the shipped .env, then
# the local engines, then Custom.
_PROVIDERS: tuple[Provider, ...] = (
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini",
             key_envs=("OPENAI_API_KEY", "TRANSLATE_API_KEY"),
             base_url_env="OPENAI_BASE_URL", model_env="OPENAI_MODEL",
             signup_url="https://platform.openai.com/api-keys",
             notes="GPT-4o / o-series. Highest quality; paid."),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
             "openai/gpt-4o-mini",
             key_envs=("OPENROUTER_API_KEY",), base_url_env="OPENROUTER_BASE_URL",
             model_env="OPENROUTER_MODEL",
             signup_url="https://openrouter.ai/keys",
             notes="One key, hundreds of models incl. free tiers."),
    Provider("orcarouter", "OrcaRouter", "https://api.orcarouter.ai/v1",
             "openai/gpt-5.5",
             key_envs=("ORCAROUTER_API_KEY",), base_url_env="ORCAROUTER_BASE_URL",
             model_env="ORCAROUTER_MODEL",
             signup_url="https://www.orcarouter.ai"),
    Provider("cheaperinference", "Cheaper Inference",
             "https://api.cheaperinference.com/v1", "gpt-5.4-mini",
             key_envs=("CHEAPER_INFERENCE_API_KEY",),
             base_url_env="CHEAPER_INFERENCE_BASE_URL",
             model_env="CHEAPER_INFERENCE_MODEL",
             signup_url="https://cheaperinference.com/signup"),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1",
             "llama-3.3-70b-versatile",
             key_envs=("GROQ_API_KEY",), base_url_env="GROQ_BASE_URL",
             model_env="GROQ_MODEL", signup_url="https://console.groq.com/keys",
             notes="Very fast Llama/Mixtral inference. Generous free tier."),
    Provider("cerebras", "Cerebras", "https://api.cerebras.ai/v1",
             "llama-3.3-70b",
             key_envs=("CEREBRAS_API_KEY",), base_url_env="CEREBRAS_BASE_URL",
             model_env="CEREBRAS_MODEL", signup_url="https://cloud.cerebras.ai",
             notes="Fastest Llama inference. Free tier."),
    Provider("google-ai", "Google AI (Gemini)",
             "https://generativelanguage.googleapis.com/v1beta/openai",
             "gemini-2.0-flash",
             key_envs=("GOOGLE_AI_API_KEY",), base_url_env="GOOGLE_AI_BASE_URL",
             model_env="GOOGLE_AI_MODEL",
             signup_url="https://aistudio.google.com/app/apikey",
             notes="Gemini via OpenAI-compatible endpoint. Free tier."),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1",
             "mistral-small-latest",
             key_envs=("MISTRAL_API_KEY",), base_url_env="MISTRAL_BASE_URL",
             model_env="MISTRAL_MODEL", signup_url="https://console.mistral.ai/api-keys",
             notes="Strong multilingual models. Free tier."),
    Provider("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1",
             "command-r-08-2024",
             key_envs=("COHERE_API_KEY",), base_url_env="COHERE_BASE_URL",
             model_env="COHERE_MODEL", signup_url="https://dashboard.cohere.com/api-keys",
             notes="Command models; good for RAG/translation. Free trial keys."),
    Provider("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
             "meta/llama-3.3-70b-instruct",
             key_envs=("NVIDIA_API_KEY",), base_url_env="NVIDIA_BASE_URL",
             model_env="NVIDIA_MODEL", signup_url="https://build.nvidia.com",
             notes="NIM-hosted open models. Free credits."),
    Provider("github-models", "GitHub Models",
             "https://models.github.ai/inference", "openai/gpt-4o-mini",
             key_envs=("GITHUB_MODELS_API_KEY",), base_url_env="GITHUB_MODELS_BASE_URL",
             model_env="GITHUB_MODELS_MODEL",
             signup_url="https://github.com/settings/tokens",
             notes="Uses a GitHub PAT. Free for dev, rate-limited."),
    Provider("cloudflare", "Cloudflare Workers AI",
             "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
             "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
             key_envs=("CLOUDFLARE_API_KEY",), base_url_env="CLOUDFLARE_BASE_URL",
             model_env="CLOUDFLARE_MODEL", needs_account=True,
             account_env="CLOUDFLARE_ACCOUNT_ID",
             signup_url="https://dash.cloudflare.com/profile/api-tokens",
             notes="Needs an Account ID. Free tier."),
    Provider("huggingface", "Hugging Face", "https://router.huggingface.co/v1",
             "meta-llama/Llama-3.3-70B-Instruct",
             key_envs=("HUGGINGFACE_API_KEY", "HF_TOKEN"),
             base_url_env="HUGGINGFACE_BASE_URL", model_env="HUGGINGFACE_MODEL",
             signup_url="https://huggingface.co/settings/tokens",
             notes="HF Inference router. Reuses your HF token."),
    Provider("sambanova", "SambaNova", "https://api.sambanova.ai/v1",
             "Meta-Llama-3.3-70B-Instruct",
             key_envs=("SAMBANOVA_API_KEY",), base_url_env="SAMBANOVA_BASE_URL",
             model_env="SAMBANOVA_MODEL", signup_url="https://cloud.sambanova.ai",
             notes="Fast open models. Free tier."),
    Provider("siliconflow", "SiliconFlow", "https://api.siliconflow.com/v1",
             "Qwen/Qwen2.5-7B-Instruct",
             key_envs=("SILICONFLOW_API_KEY",), base_url_env="SILICONFLOW_BASE_URL",
             model_env="SILICONFLOW_MODEL", signup_url="https://siliconflow.com",
             notes="Qwen/DeepSeek and more. Strong for CJK."),
    Provider("ollama", "Ollama (local)", "http://localhost:11434/v1",
             "llama3.1", local=True,
             base_url_env="OLLAMA_BASE_URL", model_env="OLLAMA_MODEL",
             signup_url="https://ollama.com",
             notes="Fully offline. Run `ollama pull llama3.1` first."),
    # `local-model` is a placeholder, NOT a model id — LM Studio serves
    # whatever the user has loaded and rejects a name it does not know, which
    # is why translation failed here while Ollama (whose default `llama3.1` is
    # a real name people actually pull) worked on the same machine (#1332).
    # resolve_model asks the server instead of shipping a guess.
    Provider("lmstudio", "LM Studio (local)", "http://localhost:1234/v1",
             "local-model", local=True, model_is_placeholder=True,
             base_url_env="LMSTUDIO_BASE_URL", model_env="LMSTUDIO_MODEL",
             signup_url="https://lmstudio.ai",
             notes="Fully offline. Start the LM Studio local server and load a model."),
    Provider("custom", "Custom (OpenAI-compatible)", "", "",
             key_envs=("TRANSLATE_API_KEY",), base_url_env="TRANSLATE_BASE_URL",
             model_env="TRANSLATE_MODEL", key_optional=True,
             notes="Any OpenAI-compatible host. Set Base URL + Model (+ key)."),
)

_BY_ID: dict[str, Provider] = {p.id: p for p in _PROVIDERS}


def all_providers() -> tuple[Provider, ...]:
    return _PROVIDERS


def get_provider(pid: str) -> Optional[Provider]:
    return _BY_ID.get(pid)


# ── Field resolution (env → store → default) ──────────────────────────────

def _env_first(names: tuple[str, ...]) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def resolve_account_id(p: Provider) -> str:
    """The Cloudflare-style account id: env override → stored → empty."""
    from services import settings_store
    return (
        (p.account_env and os.environ.get(p.account_env))
        or settings_store.get_text(f"llm.account.{p.id}")
        or ""
    )


def resolve_base_url(p: Provider, *, substitute: bool = True) -> str:
    """Resolve a provider's base URL (env → stored override → default).

    ``substitute`` interpolates ``{account_id}`` for account-scoped providers
    (Cloudflare) so the *client* gets a working URL. The UI passes
    ``substitute=False`` so the field shows/saves the raw template — baking the
    substituted value back into a stored override would freeze the URL and make
    later account-id changes silently no-op (the bug this guards against).
    """
    from services import settings_store
    val = (
        (p.base_url_env and os.environ.get(p.base_url_env))
        or settings_store.get_text(_BASE_URL_KEY + p.id)
        or p.default_base_url
    )
    if substitute and p.needs_account and val and "{account_id}" in val:
        val = val.replace("{account_id}", resolve_account_id(p))
    return val or ""


#: How long a discovered model id is trusted. Bounded rather than permanent
#: because the user can swap the loaded model inside LM Studio without touching
#: VoiceStudio at all — an unbounded cache would keep sending the unloaded name
#: and 404 every translation until a restart (greptile).
DISCOVERY_TTL_S = 300.0

#: How long a FAILED probe is remembered. Without this, a server that is
#: stopped costs a 5s timeout on *every translated segment* — a 200-segment dub
#: would spend 1000s discovering nothing, which is worse than the bug being
#: fixed (greptile / CodeRabbit). Short, so starting the server recovers within
#: seconds rather than needing a restart.
DISCOVERY_FAILURE_TTL_S = 30.0

#: provider id → (model id or None, monotonic expiry). ``None`` is a remembered
#: failure, which is why this cannot be a plain ``dict[str, str]``: "no entry"
#: and "we looked and there was nothing" have to be distinguishable or the
#: negative case cannot be cached at all.
_DISCOVERED_MODEL: dict[str, tuple[Optional[str], float]] = {}


def forget_discovered_models(pid: Optional[str] = None) -> None:
    """Drop the discovery cache (all providers, or one).

    Called whenever the user changes a provider's model or base URL: keeping a
    model discovered from the previous server would silently ignore the edit,
    which is a worse failure than the one this whole path exists to fix. Also
    called when a request is rejected for an unknown model, so a swap made
    inside the local app self-heals on the next attempt rather than at the next
    TTL expiry.
    """
    if pid is None:
        _DISCOVERED_MODEL.clear()
    else:
        _DISCOVERED_MODEL.pop(pid, None)


def _cached_discovery(pid: str) -> tuple[bool, Optional[str]]:
    """``(hit, value)``. ``hit`` is False once the entry has expired, so a
    remembered failure (value ``None``) is still a hit until it ages out."""
    entry = _DISCOVERED_MODEL.get(pid)
    if entry is None:
        return False, None
    value, expires_at = entry
    if time.monotonic() >= expires_at:
        _DISCOVERED_MODEL.pop(pid, None)
        return False, None
    return True, value


#: LM Studio's native endpoint, unlike the OpenAI-compatible ``/v1/models``,
#: reports which checkpoints are actually resident in memory. On a laptop only
#: one usually is, and asking for any other makes LM Studio evict and reload
#: multi-GB weights mid-request (or fail outright when they don't fit), so the
#: loaded one is the only sane discovery answer.
_LMSTUDIO_PROBE_TIMEOUT_S = 3.0


def _probe_lmstudio_loaded_model(base_url: str, api_key: str = "local") -> Optional[str]:
    """Return the id of a model LM Studio currently holds in memory, if any.

    Best-effort and never raises: LM Studio not running, an older build without
    ``/api/v0``, or any other host on that URL simply yields ``None`` and the
    caller keeps its configured/default model without guessing from untyped IDs.
    Only ever called through
    :func:`discover_model`, which caches both outcomes — probing per request
    would cost an HTTP round trip on every translated segment.
    """
    import json
    import urllib.request
    from urllib.parse import urlsplit

    if not base_url:
        return None
    try:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[: -len("/v1")]
        headers = {"User-Agent": "VoiceStudio"}
        if api_key and api_key != "local":
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(f"{clean_base}/api/v0/models", headers=headers)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        # A configured API credential belongs only to the configured origin.
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=_LMSTUDIO_PROBE_TIMEOUT_S) as resp:  # nosec B310 — HTTP(S) validated above
            data = json.loads(resp.read().decode("utf-8"))
        loaded = [
            str(m.get("id"))
            for m in (data.get("data") or [])
            if isinstance(m, dict)
            and m.get("state") == "loaded"
            and m.get("type") in {"llm", "vlm"}
            and m.get("id")
        ]
    except Exception as e:  # noqa: BLE001 — discovery is best-effort by design
        logger.debug("LM Studio loaded-model probe failed: %s", e)
        return None
    # Sorted for the same reason discover_model sorts: two runs on one machine
    # must pick the same model when several are resident.
    return sorted(loaded)[0] if loaded else None


def discover_model(p: Provider) -> Optional[str]:
    """Ask an OpenAI-compatible server which model it is actually serving.

    Only used when we would otherwise send a placeholder. Never raises: a
    server that is down or does not implement ``/v1/models`` leaves the caller
    with the placeholder, which is exactly where it was before.

    Both outcomes are cached — success for :data:`DISCOVERY_TTL_S`, failure for
    :data:`DISCOVERY_FAILURE_TTL_S` — because this runs once per translated
    segment, so an uncached failure costs a 5s timeout per segment.
    """
    hit, cached = _cached_discovery(p.id)
    if hit:
        return cached
    base_url = resolve_base_url(p)
    if not base_url:
        return None

    if p.id == "lmstudio":
        loaded = _probe_lmstudio_loaded_model(base_url, resolve_api_key(p))
        if loaded:
            _DISCOVERED_MODEL[p.id] = (loaded, time.monotonic() + DISCOVERY_TTL_S)
            return loaded
        # /v1/models omits native types; opaque embedding IDs cannot be
        # distinguished safely. Keep the user/default choice instead.
        _DISCOVERED_MODEL[p.id] = (None, time.monotonic() + DISCOVERY_FAILURE_TTL_S)
        return None

    try:
        from openai import OpenAI

        # max_retries=0 + a short timeout: this runs in the request path, and a
        # local server that is not running must fail fast rather than add the
        # SDK's retry ladder to a translation the user is waiting on.
        client = OpenAI(api_key=resolve_api_key(p) or "local",
                        base_url=base_url, max_retries=0)
        ids = [m.id for m in client.models.list(timeout=5)]
    except Exception as e:  # noqa: BLE001 — discovery is best-effort by design
        logger.debug("model discovery failed for %s: %s", p.id, e)
        _DISCOVERED_MODEL[p.id] = (None, time.monotonic() + DISCOVERY_FAILURE_TTL_S)
        return None
    if not ids:
        _DISCOVERED_MODEL[p.id] = (None, time.monotonic() + DISCOVERY_FAILURE_TTL_S)
        return None

    # An embedding checkpoint cannot serve chat — picking one yields a 400 on
    # every request — so never return them as a chat discovery result.
    chat_ids = [mid for mid in ids if not any(k in mid.lower() for k in ("embed", "bert"))]
    if not chat_ids:
        _DISCOVERED_MODEL[p.id] = (None, time.monotonic() + DISCOVERY_FAILURE_TTL_S)
        return None
    candidate_ids = chat_ids

    # Prefer a known instruct family over whatever else is loaded, then sort
    # WITHIN the chosen bucket: deterministic rather than "whatever the server
    # listed first", so two runs on the same machine pick the same model and a
    # bug report is reproducible.
    preferred = [mid for mid in candidate_ids if any(k in mid.lower() for k in ("qwen", "llama"))]
    chosen = sorted(preferred or candidate_ids)[0]

    if len(ids) > 1:
        logger.info(
            "%s has %d models loaded and no model is set in Settings; using %r. "
            "Pick one in Settings → LLM Providers to choose deliberately.",
            p.display_name, len(ids), chosen,
        )
    _DISCOVERED_MODEL[p.id] = (chosen, time.monotonic() + DISCOVERY_TTL_S)
    return chosen


def resolve_model(p: Provider) -> str:
    """Env override → stored override → discovered → default.

    Discovery sits between the user's choice and the built-in default so it can
    never override an explicit setting, and only runs for providers whose
    default is a placeholder — everyone else keeps a pure, offline resolution.
    For LM Studio, "discovered" means the model actually loaded in memory
    (:func:`_probe_lmstudio_loaded_model`) rather than the alphabetically first
    one the server lists — but it stays BELOW the stored override like every
    other discovery: picking a model in Settings → LLM Providers has to win, or
    the "pick one deliberately" line this module logs would be a lie. Probing
    from here would also re-open the per-segment cost the discovery cache
    exists to prevent (a 200-segment dub × one HTTP round trip each).
    """
    from services import settings_store
    explicit = (
        (p.model_env and os.environ.get(p.model_env))
        or settings_store.get_text(_MODEL_KEY + p.id)
    )
    if explicit:
        return explicit
    if p.model_is_placeholder:
        discovered = discover_model(p)
        if discovered:
            return discovered
    return p.default_model


def resolve_api_key(p: Provider) -> Optional[str]:
    """Env key → encrypted stored key → 'local' sentinel for local/keyless."""
    from services import settings_store
    env_key = _env_first(p.key_envs)
    if env_key:
        return env_key
    stored = settings_store.get_secret(SECRET_PREFIX + p.id)
    if stored:
        return stored
    if p.local or (p.key_optional and resolve_base_url(p)):
        return "local"  # self-hosted OpenAI-compatible servers ignore the key
    return None


def has_key(p: Provider) -> bool:
    """True if a usable key is resolvable (local, or keyless-with-base_url)."""
    if p.local:
        return True
    if _env_first(p.key_envs) or _key_in_store(p.id):
        return True
    return bool(p.key_optional and resolve_base_url(p))


def _key_in_store(pid: str) -> bool:
    from services import settings_store
    return (SECRET_PREFIX + pid) in settings_store.list_secret_names()


def is_configured(p: Provider) -> bool:
    """Usable end-to-end: has a base_url (custom needs one set) and a key."""
    if not resolve_base_url(p):
        return False
    return has_key(p)


# ── Active provider selection ─────────────────────────────────────────────

def stored_active_provider_id() -> Optional[str]:
    """The user's explicitly-persisted selection ONLY — no env pin, no legacy
    TRANSLATE_* fallback, no auto-detect.

    ``None`` means the user has never chosen a provider. This is what gates
    save-activates in the settings router (#963): an explicit save may claim
    the *empty* slot, but must never steal it from a made choice.
    """
    from services import settings_store
    stored = settings_store.get_text(_ACTIVE_PROVIDER_KEY)
    return stored if stored and stored in _BY_ID else None


def active_provider_id() -> Optional[str]:
    """The provider Cinematic/Autofit should use.

    Precedence: env ``LLM_DEFAULT_PROVIDER`` → stored selection → first
    configured provider → None. Legacy ``TRANSLATE_BASE_URL`` users with no
    explicit selection resolve to ``custom`` (its envs are TRANSLATE_*).
    """
    env_pick = os.environ.get("LLM_DEFAULT_PROVIDER")
    if env_pick and env_pick in _BY_ID:
        return env_pick
    stored = stored_active_provider_id()
    if stored:
        return stored
    # Legacy: a lone TRANSLATE_BASE_URL means the old single-endpoint setup.
    if os.environ.get("TRANSLATE_BASE_URL"):
        return "custom"
    # Auto-select only a provider with a real key. Local providers (Ollama/
    # LM Studio) are *always* "configured" (no key needed) but we must NOT
    # assume their server is running — they require an explicit selection.
    for p in _PROVIDERS:
        if not p.local and is_configured(p):
            return p.id
    return None


def set_active_provider(pid: str) -> None:
    from services import settings_store
    if pid not in _BY_ID:
        raise ValueError(f"unknown provider {pid!r}")
    settings_store.set_text(_ACTIVE_PROVIDER_KEY, pid)


def active_provider() -> Optional[Provider]:
    pid = active_provider_id()
    return _BY_ID.get(pid) if pid else None


# ── UI + persistence helpers ──────────────────────────────────────────────

def save_key(pid: str, api_key: str) -> None:
    """Persist (encrypted) or clear an API key for a provider."""
    from services import settings_store
    if pid not in _BY_ID:
        raise ValueError(f"unknown provider {pid!r}")
    settings_store.set_secret(SECRET_PREFIX + pid, api_key or "")


def save_overrides(pid: str, *, base_url: Optional[str] = None,
                   model: Optional[str] = None,
                   account_id: Optional[str] = None) -> None:
    from services import settings_store
    if pid not in _BY_ID:
        raise ValueError(f"unknown provider {pid!r}")
    p = _BY_ID[pid]
    if base_url is not None:
        bu = base_url.strip()
        # Never freeze an override that equals the built-in default. Critical
        # for account-templated URLs (Cloudflare): persisting the shown value
        # would pin the base_url and stop later account-id edits from taking
        # effect. Clearing (→ empty) falls the resolver back to the default
        # template so substitution stays live. Also self-heals a stale override
        # if a provider's default URL changes in a future release.
        settings_store.set_text(_BASE_URL_KEY + pid, "" if bu == p.default_base_url else bu)
    if model is not None:
        settings_store.set_text(_MODEL_KEY + pid, model.strip())
    # Any base_url or model edit can invalidate a discovered id — a stale one
    # would make the user's change look like it did nothing.
    if base_url is not None or model is not None:
        forget_discovered_models(pid)
    if account_id is not None:
        settings_store.set_text(f"llm.account.{pid}", account_id.strip())


def _active_env_pin() -> Optional[str]:
    """The provider id pinned by ``LLM_DEFAULT_PROVIDER`` (if set + valid)."""
    pick = os.environ.get("LLM_DEFAULT_PROVIDER")
    return pick if pick and pick in _BY_ID else None


def describe(p: Provider) -> dict:
    """Client-safe provider descriptor — NEVER includes the key material.

    The ``*_from_env`` booleans mirror ``key_from_env`` so the UI can disable an
    env-pinned field (and the make-active button) with an explainer instead of
    letting the user edit a value the resolver will silently override. ``base_url``
    is the RAW template (``substitute=False``) so an account-scoped default shows
    ``{account_id}`` rather than a baked-in value; ``account_id`` is returned
    separately for account-scoped providers so the field can round-trip.
    """
    d = {
        "id": p.id,
        "display_name": p.display_name,
        "local": p.local,
        "needs_account": p.needs_account,
        "signup_url": p.signup_url,
        "notes": p.notes,
        "base_url": resolve_base_url(p, substitute=False),
        "model": resolve_model(p),
        "has_key": has_key(p),
        "key_from_env": bool(_env_first(p.key_envs)),
        "base_url_from_env": bool(p.base_url_env and os.environ.get(p.base_url_env)),
        "model_from_env": bool(p.model_env and os.environ.get(p.model_env)),
        "active_from_env": _active_env_pin() is not None,
        "configured": is_configured(p),
    }
    if p.needs_account:
        d["account_id"] = resolve_account_id(p)
        d["account_from_env"] = bool(p.account_env and os.environ.get(p.account_env))
    return d


# ── Legacy TRANSLATE_* prefs migration (#963) ──────────────────────────────

# prefs.json row → the custom-provider field it becomes.
_LEGACY_TRANSLATE_PREFS: tuple[tuple[str, str], ...] = (
    ("env.TRANSLATE_BASE_URL", "base_url"),
    ("env.TRANSLATE_MODEL", "model"),
    ("env.TRANSLATE_API_KEY", "api_key"),
)


def migrate_legacy_translate_prefs() -> bool:
    """Move the retired (≤v0.3.7) Translation-LLM panel's prefs rows into the
    ``custom`` provider's own settings-store rows, then delete them.

    Those ``env.TRANSLATE_*`` rows in prefs.json are re-imported into
    ``os.environ`` on every launch (main.py), and a live ``TRANSLATE_BASE_URL``
    makes :func:`active_provider_id` resolve to ``custom`` ahead of the stored
    selection fallbacks — silently hijacking the active slot on every restart
    (issue #963, "Ollama works until I restart"). Must run BEFORE main.py's
    prefs→env import so the rows never reach the environment.

    Semantics:
      * Each value is copied only where the store has no value yet — a user's
        later edit of the custom provider always wins over legacy leftovers.
      * The prefs row is deleted afterwards either way, so it can never be
        re-imported as env again (the migration is one-shot per row).
      * Real process env vars are NEVER touched — a shell/.env
        ``TRANSLATE_BASE_URL`` keeps its documented override behavior.
      * A row whose store write fails is kept in prefs (it still works via the
        env import this launch and the migration retries next launch).

    Returns True if any prefs row was migrated/removed.
    """
    from core import prefs
    from services import settings_store

    changed = False
    for prefs_key, field in _LEGACY_TRANSLATE_PREFS:
        try:
            raw = prefs.get(prefs_key)
        except Exception:
            logger.exception("legacy TRANSLATE prefs read failed (%s)", prefs_key)
            return changed
        if raw is None:
            continue
        val = str(raw).strip()
        try:
            if val:
                if field == "base_url":
                    if not settings_store.get_text(_BASE_URL_KEY + "custom"):
                        save_overrides("custom", base_url=val)
                elif field == "model":
                    if not settings_store.get_text(_MODEL_KEY + "custom"):
                        save_overrides("custom", model=val)
                else:  # api_key — encrypted store, never overwrite an existing one
                    if not _key_in_store("custom"):
                        save_key("custom", val)
            prefs.delete(prefs_key)
            changed = True
        except Exception:
            # Store not ready (e.g. settings table missing) — keep the prefs
            # row so the legacy env import still works and we retry next boot.
            logger.exception("legacy TRANSLATE prefs migration failed (%s)", prefs_key)
    return changed
