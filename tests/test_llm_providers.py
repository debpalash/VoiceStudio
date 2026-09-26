"""LLM provider registry — resolution precedence + no-key-leak (Settings → LLM
Providers, v0.3.8).

Covers the field-resolution logic (env override → encrypted store → default),
active-provider selection, local-provider handling, and the client-safe
descriptor that must never carry key material. The encrypted round-trip itself
(settings_store.set_secret/get_secret) reuses the proven HF-token Fernet path.
"""
from __future__ import annotations

import json
import os

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
    # 14 cloud + 2 local + custom + openai
    for expected in ("openai", "openrouter", "orcarouter", "cheaperinference",
                     "groq", "cerebras", "google-ai",
                     "mistral", "cohere", "nvidia", "github-models", "cloudflare",
                     "huggingface", "sambanova", "siliconflow", "ollama",
                     "lmstudio", "custom"):
        assert expected in ids, expected


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


def test_cheaperinference_provider_contract(lp, monkeypatch):
    p = lp.get_provider("cheaperinference")
    assert p.display_name == "Cheaper Inference"
    assert p.default_base_url == "https://api.cheaperinference.com/v1"
    assert p.default_model == "gpt-5.4-mini"
    assert p.key_envs == ("CHEAPER_INFERENCE_API_KEY",)
    assert p.base_url_env == "CHEAPER_INFERENCE_BASE_URL"
    assert p.model_env == "CHEAPER_INFERENCE_MODEL"

    monkeypatch.setenv("CHEAPER_INFERENCE_API_KEY", "sk-ci-test")
    monkeypatch.setenv("CHEAPER_INFERENCE_BASE_URL", "https://cheaperinference.example/v1")
    monkeypatch.setenv("CHEAPER_INFERENCE_MODEL", "gpt-5.4")
    assert lp.resolve_api_key(p) == "sk-ci-test"
    assert lp.resolve_base_url(p) == "https://cheaperinference.example/v1"
    assert lp.resolve_model(p) == "gpt-5.4"


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
    # The legacy endpoint keeps working, now via the store.
    p = lp.get_provider("custom")
    assert lp.resolve_base_url(p) == "http://legacy:11434/v1"
    assert lp.is_configured(p) is True


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


def test_discover_model_probes_loaded_and_filters_embeddings(lp, monkeypatch):
    lp.forget_discovered_models()
    p = lp.get_provider("lmstudio")

    # When LM Studio native API reports loaded model, it picks it
    monkeypatch.setattr(
        lp, "_probe_lmstudio_loaded_model",
        lambda url, api_key="local": "qwen/qwen3.6-35b-a3b"
    )
    assert lp.discover_model(p) == "qwen/qwen3.6-35b-a3b"

    # When fallback OpenAI models list runs, filters embeddings and picks preferred model
    lp.forget_discovered_models()
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda url, api_key="local": None)

    class _FakeModel:
        def __init__(self, id):
            self.id = id

    class _FakeModels:
        def list(self, timeout=None):
            return [
                _FakeModel("text-embedding-nomic-embed-text-v1.5"),
                _FakeModel("qwen/qwen3.6-35b-a3b"),
                _FakeModel("prism-ml/bonsai-27b"),
            ]

    class _FakeClient:
        models = _FakeModels()

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeClient())
    assert lp.discover_model(lp.get_provider("ollama")) == "qwen/qwen3.6-35b-a3b"



# ── LM Studio loaded-model discovery (regression) ───────────────────────────

def test_lmstudio_loaded_model_never_overrides_the_stored_choice(lp, monkeypatch):
    """The probe must sit BELOW the user's own choice.

    Regression: it was wired into resolve_model above the stored override, so
    picking a model in Settings → LLM Providers changed nothing for LM Studio —
    the exact action the "pick one deliberately" log line tells the user to
    take. It also cost an HTTP round trip on every resolve, which is what the
    discovery cache exists to avoid.
    """
    lp.forget_discovered_models()
    p = lp.get_provider("lmstudio")

    probes: list[str] = []
    monkeypatch.setattr(
        lp, "_probe_lmstudio_loaded_model",
        lambda url, api_key="local": (probes.append(url), "loaded/other-model")[1],
    )
    lp._text[lp._MODEL_KEY + "lmstudio"] = "my/deliberate-choice"

    assert lp.resolve_model(p) == "my/deliberate-choice"
    assert probes == []


def test_lmstudio_discovery_uses_the_loaded_model_when_nothing_is_set(lp, monkeypatch):
    lp.forget_discovered_models()
    p = lp.get_provider("lmstudio")
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda url, api_key="local": "loaded/qwen3")
    assert lp.resolve_model(p) == "loaded/qwen3"


def test_discover_model_is_deterministic_regardless_of_server_order(lp, monkeypatch):
    """Two runs on one machine must pick the same model, or a bug report from
    this path is not reproducible."""
    p = lp.get_provider("ollama")
    monkeypatch.setattr(lp, "_probe_lmstudio_loaded_model", lambda url, api_key="local": None)

    class _FakeModel:
        def __init__(self, mid):
            self.id = mid

    def _fake_openai(order):
        class _FakeModels:
            def list(self, timeout=None):
                return [_FakeModel(m) for m in order]

        class _FakeClient:
            models = _FakeModels()

        return lambda **kw: _FakeClient()

    import openai

    picks = set()
    for order in (["qwen/b-8b", "qwen/a-8b"], ["qwen/a-8b", "qwen/b-8b"]):
        lp.forget_discovered_models()
        monkeypatch.setattr(openai, "OpenAI", _fake_openai(order))
        picks.add(lp.discover_model(p))
    assert picks == {"qwen/a-8b"}


def test_discovery_does_not_select_embedding_only_models(lp, monkeypatch):
    import openai
    lp.forget_discovered_models()
    monkeypatch.setattr(lp, '_probe_lmstudio_loaded_model', lambda *args: None)
    from types import SimpleNamespace
    models = [SimpleNamespace(id=name) for name in ['text-embedding-nomic', 'bert-embedding']]
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(models=SimpleNamespace(list=lambda **kw: models)))
    assert lp.discover_model(lp.get_provider('ollama')) is None


def test_loaded_model_probe_uses_the_configured_key(lp, monkeypatch):
    import io
    import urllib.request
    lp.forget_discovered_models()
    lp._secrets['llm_key.lmstudio'] = 'test-only-secret'
    requests = []
    def respond(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps({'data': [{'id': 'loaded-chat', 'type': 'llm', 'state': 'loaded'}]}).encode())
    from types import SimpleNamespace
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=respond))
    assert lp.discover_model(lp.get_provider('lmstudio')) == 'loaded-chat'
    assert requests[0].get_header('Authorization') == 'Bearer test-only-secret'


@pytest.mark.parametrize('url', ['file:///tmp/models', 'ftp://host/models', 'https:///missing-host'])
def test_loaded_model_probe_rejects_non_http_targets(lp, monkeypatch, url):
    import urllib.request
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('invalid URL must not reach transport')
    from types import SimpleNamespace
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=forbidden))
    assert lp._probe_lmstudio_loaded_model(url) is None
    assert not calls


def test_loaded_model_probe_never_forwards_credentials_through_redirect(lp):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get('Authorization')))
            if self.path == '/api/v0/models':
                self.send_response(302)
                self.send_header('Location', f'http://localhost:{self.server.server_port}/other-origin')
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data": [{"id": "chat", "type": "llm", "state": "loaded"}]}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = lp._probe_lmstudio_loaded_model(f'http://127.0.0.1:{server.server_port}/v1', 'test-only-secret')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert seen == [('/api/v0/models', 'Bearer test-only-secret')]
    assert result is None


@pytest.mark.parametrize('model_type', ['embeddings', 'reranker', None])
def test_lmstudio_never_selects_an_opaque_embedding_id(lp, monkeypatch, model_type):
    import io
    import urllib.request
    import openai
    from types import SimpleNamespace
    lp.forget_discovered_models()
    payload = {'data': [{'id': 'opaque-123', 'type': model_type, 'state': 'loaded'}]}
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: SimpleNamespace(
        open=lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode())))
    calls = []
    def fallback(**kwargs):
        calls.append(True)
        return SimpleNamespace(models=SimpleNamespace(list=lambda **kwargs: [SimpleNamespace(id='opaque-123')]))
    monkeypatch.setattr(openai, 'OpenAI', fallback)
    assert lp.discover_model(lp.get_provider('lmstudio')) is None
    assert not calls
