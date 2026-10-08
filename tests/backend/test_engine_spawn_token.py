"""Subprocess env injection + Settings API endpoint tests (Task 3).

Covers AUTH-04 (subprocess HF_TOKEN injection) and AUTH-03 backend half
(Settings API endpoints — POST/DELETE/GET, all loopback-gated).
"""
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


SAMPLE_TOKEN = "hf_subsubsubsubsubsubsubsubsubsubsubsubsub01"


@pytest.fixture
def fresh_app(monkeypatch, tmp_path):
    """Build a fresh FastAPI app instance with isolated DB + cleared HF env.
    The Settings router is mounted manually because the full main.py app
    factory imports the entire backend stack (torch, whisperx, demucs, …)
    which is too heavy for a unit test."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(tmp_path / "stored_tokens"))
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
            or mod == "api" or mod.startswith("api.")
        ):
            del sys.modules[mod]

    from core import db as _db
    _db.init_db()

    from fastapi import FastAPI
    from api.routers import settings as settings_router

    app = FastAPI()
    app.include_router(settings_router.router)
    return app


def _client(app):
    """TestClient anchored to a loopback client tuple so require_admin
    treats requests as local. The default TestClient client tuple is
    ('testclient', 50000), which the dep rejects."""
    from fastapi.testclient import TestClient
    return TestClient(app, client=("127.0.0.1", 12345))


def test_post_hf_token_loopback_succeeds(fresh_app, monkeypatch):
    """A loopback-origin POST persists the token and returns the updated
    cascade state."""
    import huggingface_hub
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "whoami",
        lambda self, token=None, **kw: {"name": "alice"},
    )
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)

    c = _client(fresh_app)
    r = c.post("/api/settings/hf-token", json={"token": SAMPLE_TOKEN})
    assert r.status_code == 200, r.text

    # Round-trip via settings_store.
    from services import settings_store
    assert settings_store.get_hf_token() == SAMPLE_TOKEN

    # Response reports masked local presence without asserting remote validity.
    body = r.json()
    assert body["active"] is None
    assert any(s["source"] == "app" and s["set"] for s in body["sources"])


def test_post_hf_token_non_loopback_returns_403(fresh_app):
    """A non-loopback origin (simulated via TestClient client tuple) is
    rejected with 403 per the require_admin dep."""
    from fastapi.testclient import TestClient
    with TestClient(fresh_app, client=("10.0.0.5", 12345)) as c:
        r = c.post("/api/settings/hf-token", json={"token": SAMPLE_TOKEN})
    assert r.status_code == 403
    assert "loopback" in r.json().get("detail", "").lower()


def test_delete_hf_token_clears_store(fresh_app, monkeypatch):
    import huggingface_hub
    from services import token_resolver
    # Only the app source is under test; keep the Hub token file untouched.
    monkeypatch.setattr(token_resolver, "persist_hub_token", lambda token: None)
    monkeypatch.setattr(huggingface_hub, "logout", lambda: None)
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "whoami",
        lambda self, token=None, **kw: {"name": "alice"},
    )
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)

    c = _client(fresh_app)
    c.post("/api/settings/hf-token", json={"token": SAMPLE_TOKEN})
    from services import settings_store
    assert settings_store.get_hf_token() == SAMPLE_TOKEN

    r = c.delete("/api/settings/hf-token")
    assert r.status_code == 200
    assert settings_store.get_hf_token() is None
    body = r.json()
    assert body["active"] is None
    assert all(not s["set"] for s in body["sources"])


def test_get_hf_token_state_returns_three_rows(fresh_app, monkeypatch):
    """GET state returns the same shape as token_resolver.state(): three
    SourceState rows in priority order, plus an `active` field."""
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "whoami",
        lambda self, token=None, **kw: {"name": "alice"},
    )

    c = _client(fresh_app)
    r = c.get("/api/settings/hf-token/state")
    assert r.status_code == 200
    body = r.json()
    assert "active" in body
    assert "sources" in body
    assert [s["source"] for s in body["sources"]] == ["app", "env", "hf-cli"]


def test_get_hf_token_state_fresh_busts_whoami_cache(fresh_app, monkeypatch):
    """`?fresh=1` (the panel's "Test now") re-runs whoami instead of serving
    the resolver's 300s validation cache. Fail-before/pass-after for the bug
    where "Test now" showed a stale 'whoami failed' (or a stale success on a
    revoked token) for up to 5 minutes: the cached NEGATIVE result below kept
    being served by plain GETs even after whoami started succeeding."""
    import huggingface_hub
    monkeypatch.setenv("HF_TOKEN", SAMPLE_TOKEN)
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)

    calls = {"n": 0}
    verdict = {"ok": False}

    def fake_whoami(self, token=None, **kw):
        calls["n"] += 1
        if not verdict["ok"]:
            raise RuntimeError("simulated network failure")
        return {"name": "alice"}

    monkeypatch.setattr(huggingface_hub.HfApi, "whoami", fake_whoami)

    c = _client(fresh_app)

    # An explicit test fails; ordinary reads must never revalidate.
    r = c.get("/api/settings/hf-token/state?fresh=1")
    env_row = next(s for s in r.json()["sources"] if s["source"] == "env")
    assert env_row["set"] and env_row["whoami_ok"] is False
    first_calls = calls["n"]

    # Network recovers, but a plain GET only reports unvalidated local presence.
    verdict["ok"] = True
    r = c.get("/api/settings/hf-token/state")
    env_row = next(s for s in r.json()["sources"] if s["source"] == "env")
    assert env_row["whoami_ok"] is None, "plain GET must not validate"
    assert calls["n"] == first_calls

    # "Test now" (fresh=1) drops the cache and re-runs whoami → verified.
    r = c.get("/api/settings/hf-token/state?fresh=1")
    assert r.status_code == 200
    env_row = next(s for s in r.json()["sources"] if s["source"] == "env")
    assert env_row["whoami_ok"] and env_row["whoami_user"] == "alice"
    assert calls["n"] > first_calls


def test_get_hf_token_state_loopback_only(fresh_app):
    """GET state is on the same admin router; non-loopback desktop → 403."""
    from fastapi.testclient import TestClient
    with TestClient(fresh_app, client=("10.0.0.5", 12345)) as c:
        r = c.get("/api/settings/hf-token/state")
    assert r.status_code == 403


def _build_subprocess_env(resolved_token: Optional[str]) -> dict:
    """Reproduce the env-injection logic that lives in
    backend/services/sonitranslate.py:start(). We extract it here so the
    test exercises the canonical pattern without standing up the whole
    SoniTranslate machinery (which assumes an installed Gradio app on
    disk). The pattern matches the one documented in 01-01-PLAN.md Task 3
    Step 1."""
    import os
    from services import token_resolver

    env = os.environ.copy()
    resolved = token_resolver.resolve()
    hf_token = resolved.token if resolved else ""
    if hf_token:
        env["HF_TOKEN"] = hf_token
        env["YOUR_HF_TOKEN"] = hf_token
    return env


def test_subprocess_env_includes_hf_token_when_resolved(monkeypatch, tmp_path):
    """When token_resolver.resolve() returns a token, the env block passed
    to a Popen-style subprocess launcher contains HF_TOKEN=<that token>.
    This is the AUTH-04 invariant; the SoniTranslate launcher (and any
    future subprocess launcher) MUST follow this exact pattern."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(tmp_path / "stored_tokens"))
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
        ):
            del sys.modules[mod]
    from core import db as _db
    _db.init_db()

    from services import token_resolver
    fake = token_resolver.ResolvedToken(
        token=SAMPLE_TOKEN, source="app", username="alice"
    )
    monkeypatch.setattr(token_resolver, "resolve", lambda **kw: fake)

    env = _build_subprocess_env(SAMPLE_TOKEN)
    assert env["HF_TOKEN"] == SAMPLE_TOKEN
    assert env["YOUR_HF_TOKEN"] == SAMPLE_TOKEN


def test_subprocess_env_unchanged_when_no_token(monkeypatch, tmp_path):
    """When token_resolver.resolve() returns None, the env block does NOT
    contain an injected HF_TOKEN. (If the parent had one in os.environ
    it would still be in the copy — but the launcher does not _add_ an
    empty string, which would clobber any child-set default.)"""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(tmp_path / "stored_tokens"))
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
        ):
            del sys.modules[mod]
    from core import db as _db
    _db.init_db()

    from services import token_resolver
    monkeypatch.setattr(token_resolver, "resolve", lambda **kw: None)

    env = _build_subprocess_env(None)
    # HF_TOKEN must not have been injected by the launcher.
    assert "HF_TOKEN" not in env or env.get("HF_TOKEN") == ""
    assert "YOUR_HF_TOKEN" not in env


def test_sonitranslate_module_uses_resolver(monkeypatch, tmp_path):
    """Source-level check: the SoniTranslate module's subprocess.Popen
    launcher block contains the canonical resolver import. This guards
    against future refactors silently reverting the AUTH-04 wiring."""
    import inspect
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(tmp_path / "stored_tokens"))
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
        ):
            del sys.modules[mod]
    from services import sonitranslate
    src = inspect.getsource(sonitranslate)
    # The launcher must read from the resolver, not from os.environ.
    assert "token_resolver.resolve" in src
    # And the env injection assigns HF_TOKEN explicitly.
    assert 'env["HF_TOKEN"]' in src or "env['HF_TOKEN']" in src


@pytest.mark.parametrize("endpoint", ["/system/hf-token/state", "/api/settings/hf-token/state"])
def test_token_state_reads_never_contact_hugging_face(fresh_app, monkeypatch, endpoint):
    import huggingface_hub
    monkeypatch.setenv("HF_TOKEN", SAMPLE_TOKEN)
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)
    whoami = MagicMock(side_effect=AssertionError("unexpected outbound validation"))
    monkeypatch.setattr(huggingface_hub.HfApi, "whoami", whoami)
    if endpoint == "/system/hf-token/state":
        from api.routers.system import router
        fresh_app.include_router(router)
    response = _client(fresh_app).get(endpoint)
    assert response.status_code == 200
    row = next(row for row in response.json()["sources"] if row["source"] == "env")
    assert row["set"] and row["whoami_ok"] is None
    whoami.assert_not_called()


def test_canonical_save_replaces_the_existing_app_source(fresh_app, monkeypatch):
    import huggingface_hub
    from services import settings_store, token_resolver
    settings_store.set_hf_token("hf_old_app")
    monkeypatch.setenv("HF_TOKEN", "hf_old_env")
    monkeypatch.setattr(huggingface_hub.HfApi, "whoami", lambda self, token=None, **kw: {"name": token})
    response = _client(fresh_app).post("/api/settings/hf-token", json={"token": SAMPLE_TOKEN})
    assert response.status_code == 200
    assert settings_store.get_hf_token() == SAMPLE_TOKEN
    resolved = token_resolver.resolve()
    assert resolved.source == "app"
    assert resolved.token == SAMPLE_TOKEN
    app_row = next(row for row in response.json()["sources"] if row["source"] == "app")
    assert app_row["set"] is True
    assert app_row["whoami_ok"] is None
