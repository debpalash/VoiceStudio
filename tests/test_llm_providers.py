"""LLM provider registry — resolution precedence + no-key-leak (Settings → LLM
Providers, v0.3.8).

Covers the field-resolution logic (env override → encrypted store → default),
active-provider selection, local-provider handling, and the client-safe
descriptor that must never carry key material. The encrypted round-trip itself
(settings_store.set_secret/get_secret) reuses the proven HF-token Fernet path.
"""
from __future__ import annotations

import json
import io
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


@pytest.fixture
def lp(monkeypatch, clean_llm_env):
    """llm_providers with settings_store backed by in-memory dicts (no SQLite).

    clean_llm_env (conftest) clears the FULL provider env surface — a partial
    list left other providers' keys standing when an earlier `main` import
    dotenv-loaded them into os.environ, breaking precedence asserts (#878).
    """
    from services import settings_store as ss
    from services import llm_providers as _lp

    text: dict[str, str] = {}
    secrets: dict[str, str] = {}

    monkeypatch.setattr(ss, "get_text", lambda k, default=None: text.get(k, default))
    monkeypatch.setattr(ss, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(ss, "get_secret", lambda n: secrets.get(n))
    monkeypatch.setattr(ss, "set_secret", lambda n, v: secrets.__setitem__(n, v) if v else secrets.pop(n, None))
    monkeypatch.setattr(ss, "list_secret_names", lambda: list(secrets))
    _lp._text, _lp._secrets = text, secrets  # handles for the test to seed
    return _lp


def test_registry_has_all_providers(lp):
    ids = {p.id for p in lp.all_providers()}
    # Named cloud providers, local servers and a custom endpoint.
    for expected in ("openai", "openrouter", "orcarouter", "cheaperinference",
                     "opper", "api-route", "groq", "cerebras", "google-ai",
                     "mistral", "cohere", "nvidia", "github-models", "cloudflare",
                     "huggingface", "sambanova", "siliconflow", "ollama",
                     "lmstudio", "iflytek", "custom"):
        assert expected in ids, expected


def test_lmstudio_catalogue_hides_placeholder_and_tracks_key(lp, monkeypatch):
    provider = lp.get_provider("lmstudio")
    monkeypatch.setattr(lp, "discover_model", lambda p: pytest.fail("catalogue probed"))
    descriptor = lp.describe(provider)
    assert descriptor["model"] == ""
    assert descriptor["has_api_key"] is False
    lp.save_key(provider.id, "stored-key")
    assert lp.describe(provider)["has_api_key"] is True


def test_saved_placeholder_is_treated_as_discoverable(lp, monkeypatch):
    provider = lp.get_provider("lmstudio")
    lp.save_overrides(provider.id, model=provider.default_model)
    monkeypatch.setattr(lp, "discover_model", lambda p: "resident-model")
    assert lp.resolve_model(provider) == "resident-model"
    assert lp.describe(provider)["model"] == ""


@pytest.mark.parametrize("url,model", [
    ("localhost:1234/v1", "chat"), ("file:///tmp/model", "chat"),
    ("http://localhost:bad/v1", "chat"), ("http://localhost:1234/v1", ""),
])
def test_incomplete_custom_provider_is_not_ready(lp, url, model):
    lp.save_overrides("custom", base_url=url, model=model)
    lp.set_active_provider("custom")
    p = lp.get_provider("custom")
    assert not lp.is_configured(p)
    from services.llm_backend import OpenAICompatBackend
    assert not OpenAICompatBackend.is_available()[0]
    from services.llm_skills import resolve_skill
    assert not resolve_skill("dub_translation").ready


def test_account_scoped_provider_requires_account_only_for_templated_url(lp):
    p = lp.get_provider("cloudflare")
    lp.save_key(p.id, "test-key")
    assert not lp.is_configured(p)
    lp.save_overrides(p.id, account_id="account123")
    assert lp.is_configured(p)
    lp.save_overrides(p.id, account_id="", base_url="https://gateway.example/v1")
    assert lp.is_configured(p)


@pytest.mark.parametrize("pid,env", [("ollama", "OLLAMA_API_KEY"), ("lmstudio", "LMSTUDIO_API_KEY")])
def test_local_server_key_env_is_supported_without_discovery(lp, monkeypatch, pid, env):
    p = lp.get_provider(pid)
    lp.save_key(pid, "stored-key")
    monkeypatch.setenv(env, "env-key")
    monkeypatch.setattr(lp, "discover_model", lambda p: pytest.fail("unexpected probe"))
    assert lp.resolve_api_key(p) == "env-key"
    assert lp.describe(p)["key_from_env"] is True
    assert lp.describe(p)["has_api_key"] is True


def test_orcarouter_provider_contract(lp, monkeypatch):
    p = lp.get_provider("orcarouter")
    assert p.default_base_url == "https://api.orcarouter.ai/v1"
    assert p.default_model == "openai/gpt-5.5"
    assert p.key_envs == ("ORCAROUTER_API_KEY",)
    assert p.base_url_env == "ORCAROUTER_BASE_URL"
    assert p.model_env == "ORCAROUTER_MODEL"

    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    monkeypatch.setenv("ORCAROUTER_BASE_URL", "https://orcarouter.example/v1")
    monkeypatch.setenv("ORCAROUTER_MODEL", "orcarouter/test-model")
    assert lp.resolve_api_key(p) == "sk-orca-test"
    assert lp.resolve_base_url(p) == "https://orcarouter.example/v1"
    assert lp.resolve_model(p) == "orcarouter/test-model"


def test_cheaper_inference_catalogue_entry_and_environment_resolution(lp, monkeypatch):
    provider = lp.get_provider("cheaperinference")
    declared = {
        "name": provider.display_name,
        "endpoint": provider.default_base_url,
        "model": provider.default_model,
        "key_names": provider.key_envs,
        "endpoint_env": provider.base_url_env,
        "model_env": provider.model_env,
        "signup": provider.signup_url,
    }
    assert declared == {
        "name": "Cheaper Inference",
        "endpoint": "https://api.cheaperinference.com/v1",
        "model": "gpt-5.4-mini",
        "key_names": ("CHEAPER_INFERENCE_API_KEY",),
        "endpoint_env": "CHEAPER_INFERENCE_BASE_URL",
        "model_env": "CHEAPER_INFERENCE_MODEL",
        "signup": "https://cheaperinference.com/signup",
    }

    overrides = {
        "CHEAPER_INFERENCE_API_KEY": "temporary-secret",
        "CHEAPER_INFERENCE_BASE_URL": "https://router.example/api/v1",
        "CHEAPER_INFERENCE_MODEL": "vendor/small-chat",
    }
    for variable, value in overrides.items():
        monkeypatch.setenv(variable, value)
    assert (
        lp.resolve_api_key(provider),
        lp.resolve_base_url(provider),
        lp.resolve_model(provider),
    ) == ("temporary-secret", "https://router.example/api/v1", "vendor/small-chat")


def test_opper_provider_contract(lp, monkeypatch):
    p = lp.get_provider("opper")
    assert p.display_name == "Opper"
    assert p.default_base_url == "https://api.opper.ai/v3/compat"
    assert p.default_model == "claude-sonnet-4-6"
    assert p.key_envs == ("OPPER_API_KEY",)
    assert p.base_url_env is None
    assert p.model_env == "OPPER_MODEL"

    monkeypatch.setenv("OPPER_API_KEY", "op-test")
    monkeypatch.setenv("OPPER_MODEL", "gpt-5.4-mini")
    # Opper's SDKs read OPPER_BASE_URL as the API root, so it must not
    # replace the OpenAI-compatible endpoint here.
    monkeypatch.setenv("OPPER_BASE_URL", "https://api.opper.ai")
    assert lp.resolve_api_key(p) == "op-test"
    assert lp.resolve_model(p) == "gpt-5.4-mini"
    assert lp.resolve_base_url(p) == "https://api.opper.ai/v3/compat"


def test_api_route_provider_contract(lp, monkeypatch):
    p = lp.get_provider("api-route")
    assert p.display_name == "API Route"
    assert p.default_base_url == "https://global.api-route.com/v1"
    assert p.default_model == "deepseek-v4.1-flash"
    assert p.key_envs == ("API_ROUTE_API_KEY",)
    assert p.base_url_env == "API_ROUTE_BASE_URL"
    assert p.model_env == "API_ROUTE_MODEL"
    assert p.transport == "openai" and not p.local
    assert not lp.is_configured(p)
    assert lp.active_provider_id() != p.id

    lp.set_active_provider("openai")
    lp.save_key(p.id, "stored-api-route-key")
    assert lp.is_configured(p)
    assert "stored-api-route-key" not in json.dumps(lp.describe(p))
    assert lp.active_provider_id() == "openai"

    monkeypatch.setenv("API_ROUTE_API_KEY", "env-api-route-key")
    monkeypatch.setenv("API_ROUTE_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("API_ROUTE_MODEL", "test-model")
    assert lp.resolve_api_key(p) == "env-api-route-key"
    assert lp.resolve_base_url(p) == "https://gateway.example/v1"
    assert lp.resolve_model(p) == "test-model"
    assert "env-api-route-key" not in json.dumps(lp.describe(p))


def test_iflytek_provider_contract(lp, monkeypatch):
    p = lp.get_provider("iflytek")
    assert p.default_base_url == "https://maas-api.cn-huabei-1.xf-yun.com/v2"
    assert p.default_model == ""
    assert p.key_envs == ("IFLYTEK_API_KEY",)
    assert p.base_url_env == "IFLYTEK_BASE_URL"
    assert p.model_env == "IFLYTEK_MODEL"
    assert p.transport == "openai" and not p.local

    # A key alone is not enough: MaaS model IDs are per deployment.
    monkeypatch.setenv("IFLYTEK_API_KEY", "sk-iflytek-test")
    assert lp.configuration_error(p) == "Set the Model in Settings > Models > LLM."

    monkeypatch.setenv("IFLYTEK_BASE_URL", "https://maas-token-api.cn-huabei-1.xf-yun.com/v2")
    monkeypatch.setenv("IFLYTEK_MODEL", "spark-x2.5")
    assert lp.resolve_api_key(p) == "sk-iflytek-test"
    assert lp.resolve_base_url(p) == "https://maas-token-api.cn-huabei-1.xf-yun.com/v2"
    assert lp.resolve_model(p) == "spark-x2.5"
    assert lp.configuration_error(p) is None


def test_default_base_url_and_model(lp):
    d = lp.describe(lp.get_provider("groq"))
    assert d["base_url"] == "https://api.groq.com/openai/v1"
    assert d["model"] == "llama-3.3-70b-versatile"
    assert d["has_key"] is False and d["configured"] is False


def test_env_key_wins_and_is_flagged(lp, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env_value")
    d = lp.describe(lp.get_provider("groq"))
    assert d["has_key"] is True
    assert d["key_from_env"] is True
    assert d["configured"] is True
    assert lp.resolve_api_key(lp.get_provider("groq")) == "gsk_env_value"


def test_stored_key_used_when_no_env(lp):
    lp._secrets["llm_key.groq"] = "gsk_stored"
    p = lp.get_provider("groq")
    assert lp.has_key(p) is True
    assert lp.resolve_api_key(p) == "gsk_stored"
    d = lp.describe(p)
    assert d["has_key"] is True and d["key_from_env"] is False


def test_env_overrides_stored_key(lp, monkeypatch):
    lp._secrets["llm_key.groq"] = "gsk_stored"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env")
    assert lp.resolve_api_key(lp.get_provider("groq")) == "gsk_env"


def test_base_url_and_model_overrides(lp):
    lp._text["llm.base_url.custom"] = "http://localhost:9000/v1"
    lp._text["llm.model.custom"] = "my-model"
    p = lp.get_provider("custom")
    assert lp.resolve_base_url(p) == "http://localhost:9000/v1"
    assert lp.resolve_model(p) == "my-model"


def test_local_provider_needs_no_key(lp):
    p = lp.get_provider("ollama")
    assert lp.has_key(p) is True
    assert lp.resolve_api_key(p) == "local"
    assert lp.is_configured(p) is True  # has default base_url + local


def test_cloudflare_account_interpolation(lp):
    p = lp.get_provider("cloudflare")
    assert p.needs_account
    # No account yet → empty segment
    assert "accounts//ai/v1" in lp.resolve_base_url(p)
    lp.save_overrides("cloudflare", account_id="abc123")
    assert "accounts/abc123/ai/v1" in lp.resolve_base_url(p)


def test_active_provider_precedence(lp, monkeypatch):
    # Nothing configured → None
    assert lp.active_provider_id() is None
    # A configured provider auto-selects
    lp._secrets["llm_key.groq"] = "k"
    assert lp.active_provider_id() == "groq"
    # Stored selection wins over auto
    lp.set_active_provider("mistral")
    lp._secrets["llm_key.mistral"] = "k2"
    assert lp.active_provider_id() == "mistral"
    # Env LLM_DEFAULT_PROVIDER wins over everything
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "openrouter")
    assert lp.active_provider_id() == "openrouter"


def test_legacy_translate_base_url_maps_to_custom(lp, monkeypatch):
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://legacy:11434/v1")
    assert lp.active_provider_id() == "custom"


def test_describe_never_leaks_key(lp, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret")
    d = lp.describe(lp.get_provider("groq"))
    assert "gsk_super_secret" not in repr(d)
    assert "api_key" not in d and "key" not in d  # only boolean flags
    assert set(["has_key", "key_from_env"]).issubset(d)


# ── env-override surfacing (silent-revert / dead make-active traps) ──────────

def test_describe_reports_env_override_flags(lp, monkeypatch):
    p = lp.get_provider("groq")
    d = lp.describe(p)
    assert d["base_url_from_env"] is False
    assert d["model_from_env"] is False
    assert d["active_from_env"] is False
    monkeypatch.setenv("GROQ_BASE_URL", "http://env/v1")
    monkeypatch.setenv("GROQ_MODEL", "env-model")
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "groq")
    d = lp.describe(p)
    assert d["base_url_from_env"] is True
    assert d["model_from_env"] is True
    assert d["active_from_env"] is True
    # active_from_env is a GLOBAL pin (LLM_DEFAULT_PROVIDER) — true for every
    # provider while set, so the UI disables make-active everywhere.
    assert lp.describe(lp.get_provider("openai"))["active_from_env"] is True


def test_active_from_env_ignores_unknown_provider(lp, monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "not-a-provider")
    assert lp.describe(lp.get_provider("groq"))["active_from_env"] is False


# ── Cloudflare account-id round-trip + no frozen base_url override ───────────

def test_describe_returns_account_id_and_raw_template(lp):
    p = lp.get_provider("cloudflare")
    lp.save_overrides("cloudflare", account_id="acct-9")
    d = lp.describe(p)
    # (a) the stored account id round-trips so the field isn't reset to empty
    assert d["account_id"] == "acct-9"
    assert d["account_from_env"] is False
    # describe shows the RAW template, not the {account_id}-baked value, so
    # saving it back can't freeze the URL.
    assert "{account_id}" in d["base_url"]


def test_account_change_takes_effect_not_frozen(lp):
    """Regression: the UI posts the shown base_url back on every save. Saving a
    value equal to the default template must NOT persist a frozen override, so
    later account-id changes keep taking effect (the P2 bug)."""
    p = lp.get_provider("cloudflare")
    template = lp.describe(p)["base_url"]  # what the field shows
    lp.save_overrides("cloudflare", base_url=template, account_id="acct-1")
    assert lp._text.get("llm.base_url.cloudflare", "") == ""  # not frozen
    assert "accounts/acct-1/ai/v1" in lp.resolve_base_url(p)
    # Change ONLY the account later — must be reflected, not stuck on acct-1.
    lp.save_overrides("cloudflare", base_url=template, account_id="acct-2")
    assert "accounts/acct-2/ai/v1" in lp.resolve_base_url(p)


def test_real_base_url_override_still_persists(lp):
    # A genuinely custom URL (≠ default) is still stored as an override.
    lp.save_overrides("groq", base_url="http://my-proxy/v1")
    assert lp._text["llm.base_url.groq"] == "http://my-proxy/v1"
    assert lp.resolve_base_url(lp.get_provider("groq")) == "http://my-proxy/v1"


# ── #963: stale TRANSLATE_* prefs migration + explicit-save activation ──────
# The retired (≤v0.3.7) Translation-LLM panel persisted env.TRANSLATE_* rows
# in prefs.json; main.py re-imported them into os.environ every launch, which
# made active_provider_id() resolve to "custom" ahead of auto-select on every
# restart — hijacking the slot from whatever the user saved.


@pytest.fixture
def legacy_prefs(monkeypatch, tmp_path):
    """core.prefs redirected to a temp prefs.json seeded with the retired
    Translation-LLM panel's persisted rows (plus non-LLM rows that must
    survive the migration untouched)."""
    from core import prefs

    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "env.TRANSLATE_BASE_URL": "http://legacy:11434/v1",
        "env.TRANSLATE_MODEL": "legacy-model",
        "env.TRANSLATE_API_KEY": "sk-legacy",
        "env.HTTP_PROXY": "http://proxy:1",
        "tts_backend": "omnivoice",
    }), encoding="utf-8")
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(path))
    return prefs


def test_migration_moves_prefs_into_custom_store_and_deletes_rows(lp, legacy_prefs):
    assert lp.migrate_legacy_translate_prefs() is True
    # values live in the custom provider's own store rows now…
    assert lp._text["llm.base_url.custom"] == "http://legacy:11434/v1"
    assert lp._text["llm.model.custom"] == "legacy-model"
    assert lp._secrets["llm_key.custom"] == "sk-legacy"
    # …the prefs rows are gone (nothing left to re-import as env)…
    data = legacy_prefs._load()
    assert not any(k.startswith("env.TRANSLATE") for k in data)
    # …and rows the migration doesn't own keep persisting.
    assert data["env.HTTP_PROXY"] == "http://proxy:1"
    assert data["tts_backend"] == "omnivoice"
    # Preserve migrated configuration, but require TLS for its remote secret.
    p = lp.get_provider("custom")
    assert lp.resolve_base_url(p) == "http://legacy:11434/v1"
    assert "HTTPS" in lp.configuration_error(p)


def test_migration_runs_exactly_once_and_never_overwrites(lp, legacy_prefs):
    assert lp.migrate_legacy_translate_prefs() is True
    # The user later edits the custom provider…
    lp.save_overrides("custom", base_url="http://mine/v1", model="my-model")
    lp.save_key("custom", "sk-mine")
    # …a second startup's migration is a no-op and can't resurrect leftovers.
    assert lp.migrate_legacy_translate_prefs() is False
    assert lp._text["llm.base_url.custom"] == "http://mine/v1"
    assert lp._text["llm.model.custom"] == "my-model"
    assert lp._secrets["llm_key.custom"] == "sk-mine"


def test_migration_respects_existing_store_values(lp, legacy_prefs):
    lp._text["llm.base_url.custom"] = "http://already/v1"
    lp._secrets["llm_key.custom"] = "sk-already"
    lp.migrate_legacy_translate_prefs()
    # rows the user already owns are never overwritten…
    assert lp._text["llm.base_url.custom"] == "http://already/v1"
    assert lp._secrets["llm_key.custom"] == "sk-already"
    # …the empty one is filled, and the prefs rows are deleted regardless.
    assert lp._text["llm.model.custom"] == "legacy-model"
    assert not any(k.startswith("env.TRANSLATE") for k in legacy_prefs._load())


def test_migration_never_touches_real_env(lp, legacy_prefs, monkeypatch):
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://real-env/v1")
    lp.migrate_legacy_translate_prefs()
    assert os.environ["TRANSLATE_BASE_URL"] == "http://real-env/v1"


def test_saving_ollama_wins_over_legacy_env_after_migration(lp, legacy_prefs, monkeypatch):
    """#963 end to end: legacy TRANSLATE_BASE_URL in the environment (as a
    pre-migration launch would have imported it) + no stored selection. A
    plain save of Ollama must yield active == 'ollama' — pre-fix it stayed
    'custom' on every restart because nothing persisted the choice."""
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://legacy:11434/v1")
    lp.migrate_legacy_translate_prefs()
    from api.routers import settings as settings_router
    settings_router.save_llm_provider(
        "ollama", settings_router._LLMProviderBody(make_active=False))
    assert lp.active_provider_id() == "ollama"


def test_stored_active_provider_id_ignores_env_and_auto(lp, monkeypatch):
    # Only the persisted row counts — env pin / legacy env / auto-detect don't.
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "openrouter")
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://legacy/v1")
    lp._secrets["llm_key.groq"] = "k"  # would auto-select
    assert lp.stored_active_provider_id() is None
    lp.set_active_provider("mistral")
    assert lp.stored_active_provider_id() == "mistral"


def _install_model_listing(monkeypatch, ids, constructed=None):
    class Models:
        def list(self, timeout=None):
            return [SimpleNamespace(id=model_id) for model_id in ids]

    class Client:
        def __init__(self, **kwargs):
            if constructed is not None:
                constructed.append(kwargs)
            self.models = Models()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))


def test_lmstudio_native_probe_answer_wins(lp, monkeypatch):
    lp.forget_discovered_models()
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda url, api_key="local": "resident")
    assert lp.discover_model(lp.get_provider("lmstudio")) == "resident"


def test_ollama_discovery_filters_embeddings_and_prefers_chat_family(lp, monkeypatch):
    lp.forget_discovered_models()
    _install_model_listing(monkeypatch, ["text-embed", "other-chat", "qwen-chat"])
    assert lp.discover_model(lp.get_provider("ollama")) == "qwen-chat"


def test_stored_lmstudio_model_skips_native_probe(lp, monkeypatch):
    lp.forget_discovered_models()
    lp._text[lp._MODEL_KEY + "lmstudio"] = "chosen"
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda *a, **k: pytest.fail("probed"))
    assert lp.resolve_model(lp.get_provider("lmstudio")) == "chosen"


def test_lmstudio_resolve_uses_resident_model(lp, monkeypatch):
    lp.forget_discovered_models()
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda *a, **k: "resident")
    assert lp.resolve_model(lp.get_provider("lmstudio")) == "resident"


def test_discovery_choice_is_deterministic(lp, monkeypatch):
    provider = lp.get_provider("ollama")
    answers = []
    for ids in (["qwen-z", "qwen-a"], ["qwen-a", "qwen-z"]):
        lp.forget_discovered_models()
        _install_model_listing(monkeypatch, ids)
        answers.append(lp.discover_model(provider))
    assert answers == ["qwen-a", "qwen-a"]


def test_discovery_rejects_embedding_only_listing(lp, monkeypatch):
    lp.forget_discovered_models()
    _install_model_listing(monkeypatch, ["text-embed", "minilm-bert"])
    assert lp.discover_model(lp.get_provider("ollama")) is None


def test_native_probe_sends_stored_bearer_key(lp, monkeypatch):
    lp.forget_discovered_models()
    lp._secrets[lp.SECRET_PREFIX + "lmstudio"] = "stored-secret"
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            return io.BytesIO(b'{"data":[{"id":"chat","type":"llm","state":"loaded"}]}')

    monkeypatch.setattr(lp.urllib.request, "build_opener", lambda *handlers: Opener())
    assert lp.discover_model(lp.get_provider("lmstudio")) == "chat"
    assert captured["authorization"] == "Bearer stored-secret"


@pytest.mark.parametrize("url", ["file:///tmp/model", "ftp://host/model", "https:///missing-host"])
def test_native_probe_rejects_invalid_transport(lp, monkeypatch, url):
    monkeypatch.setattr(
        lp.urllib.request,
        "build_opener",
        lambda *handlers: pytest.fail("transport reached"),
    )
    assert lp._probe_lmstudio_loaded_model(url, "secret") is None


def test_native_probe_does_not_follow_redirect_with_credentials(lp):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/api/v0/models":
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

        def log_message(self, format, *args):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("the test sandbox forbids opening a loopback listener")
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        assert lp._probe_lmstudio_loaded_model(url, "secret") is None
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert requests == [("/api/v0/models", "Bearer secret")]


def test_lmstudio_never_uses_opaque_native_ids(lp, monkeypatch):
    constructed = []
    _install_model_listing(monkeypatch, ["fallback-chat"], constructed)
    payload = {
        "data": [
            {"id": "embed-id", "type": "embeddings", "state": "loaded"},
            {"id": "rerank-id", "type": "reranker", "state": "loaded"},
            {"id": "opaque-id", "state": "loaded"},
        ]
    }

    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(lp.urllib.request, "build_opener", lambda *handlers: Opener())
    lp.forget_discovered_models()
    assert lp.discover_model(lp.get_provider("lmstudio")) is None
    assert constructed == []


@pytest.mark.parametrize("provider", ["anthropic", "groq", "custom"])
def test_credentialed_remote_http_is_rejected_before_transport(lp, provider):
    from services.llm_transport import create_client
    p = lp.get_provider(provider)
    lp.save_key(provider, "fixture-secret")
    lp.save_overrides(provider, base_url="http://example.com/v1", model="chat")
    assert "HTTPS" in lp.configuration_error(p)
    with pytest.raises(ValueError, match="HTTPS"):
        create_client(p)


@pytest.mark.parametrize("url", ["http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1", "https://example.com/v1"])
def test_credentialed_loopback_or_https_remains_supported(lp, url):
    lp.save_key("custom", "fixture-secret")
    lp.save_overrides("custom", base_url=url, model="chat")
    assert lp.configuration_error(lp.get_provider("custom")) is None
