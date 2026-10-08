"""#959: a SOCKS proxy env (ALL_PROXY/HTTPS_PROXY=socks5://) broke synthesis.

httpx raises ImportError AT CLIENT CONSTRUCTION ("Using SOCKS proxy, but the
'socksio' package is not installed") when a socks5:// proxy env var is set and
socksio isn't importable. huggingface_hub's ``get_session()`` builds exactly
that client inside ``snapshot_download``, so ``POST /generate`` 500'd with the
bare message even for a FULLY INSTALLED model — and ``preload_model``'s
``model_info`` probe hit the same error and silently skipped warm-up. Latent
since v0.3.5; unmasked by #947's fresh-process spawning (the parent process no
longer masked the proxy env).

Three layers, each covered here:
  (a) ship socksio — pyproject dependency (httpx imports it lazily in
      try/except, so nothing imports it statically, hence the recurrence guard);
  (b) cache-first model resolution — a complete local cache resolves with
      ``local_files_only=True`` (no HTTP session constructed), so no
      session-construction failure can break synthesis of an installed model;
  (c) the 500 detail carries the actionable class hint instead of the bare
      httpx message.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The exact httpx message from issue #959.
_SOCKS_MSG = (
    "Using SOCKS proxy, but the 'socksio' package is not installed. "
    "Make sure to install httpx using `pip install httpx[socks]`."
)


# ── (a) socksio ships — source install AND frozen installers ────────────────


def test_socksio_declared_in_pyproject_dependencies():
    import tomllib

    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    assert any(re.match(r"socksio\b", d) for d in deps), (
        "#959 regression: socksio must be a [project] dependency — without it "
        "any socks5:// ALL_PROXY/HTTPS_PROXY env breaks every httpx client "
        "construction (model downloads, hub probes, OpenAI-compat LLM clients)."
    )


def test_httpx_client_constructs_under_socks_proxy_env(monkeypatch):
    # Construction only — no network. FAILS (ImportError) in an env without
    # socksio; passes once (a) ships it.
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9")
    import httpx

    httpx.Client().close()
    asyncio.run(httpx.AsyncClient().aclose())


# ── (b) cache-first model resolution ─────────────────────────────────────────


def test_resolve_snapshot_dir_uses_local_dir(tmp_path):
    from omnivoice.models.omnivoice import _resolve_snapshot_dir

    assert _resolve_snapshot_dir(str(tmp_path)) == str(tmp_path)


def test_resolve_snapshot_dir_prefers_complete_cache(monkeypatch, tmp_path):
    # A complete local cache must resolve WITHOUT ever constructing an HTTP
    # session — the network path raising the #959 ImportError proves the
    # cached branch won. FAILS pre-fix (the old code always hit the network
    # snapshot_download for repo ids).
    import huggingface_hub
    from omnivoice.models import omnivoice as ov

    snap = tmp_path / "snap"
    snap.mkdir()
    calls: list[dict] = []

    def fake_snapshot_download(repo_id, *args, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            return str(snap)
        raise ImportError(_SOCKS_MSG)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    assert ov._resolve_snapshot_dir("k2-fsa/OmniVoice") == str(snap)
    assert calls == [{"local_files_only": True}]


def test_resolve_snapshot_dir_falls_back_to_network_on_cache_miss(monkeypatch):
    # Miss/incomplete cache → the original network snapshot_download, so
    # first-install behavior (and its error surface) is unchanged.
    import huggingface_hub
    from omnivoice.models import omnivoice as ov

    def fake_snapshot_download(repo_id, *args, **kwargs):
        if kwargs.get("local_files_only"):
            raise FileNotFoundError(f"{repo_id} not in cache")
        return "/net/snapshot"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    assert ov._resolve_snapshot_dir("k2-fsa/OmniVoice") == "/net/snapshot"


# ── (b) preload probe: network failure ≠ "model not installed" ──────────────


def _fail_model_info(monkeypatch):
    import huggingface_hub

    def boom(*args, **kwargs):
        raise ImportError(_SOCKS_MSG)

    monkeypatch.setattr(huggingface_hub, "model_info", boom)


def test_preload_warms_up_from_cache_when_network_probe_fails(monkeypatch):
    # Pre-fix, ANY model_info failure silently skipped warm-up — under a SOCKS
    # proxy env that meant a fully cached model never preloaded and the first
    # /generate ate the whole load. Now a failed network probe falls back to a
    # cache-only check and warms up anyway.
    import services.model_manager as mm

    _fail_model_info(monkeypatch)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "k2-fsa/OmniVoice")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda cp: True)
    sentinel = object()

    async def fake_load():
        return sentinel

    monkeypatch.setattr(mm, "_load_model_with_timeout", fake_load)
    asyncio.run(mm.preload_model())
    assert mm.model is sentinel


def test_preload_still_skips_when_not_cached(monkeypatch):
    # Probe failed AND nothing in the cache → the historical skip (no heavy
    # load attempt that would fail and pollute startup logs).
    import services.model_manager as mm

    _fail_model_info(monkeypatch)
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "k2-fsa/OmniVoice")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda cp: False)

    async def must_not_load():  # pragma: no cover - failure path
        raise AssertionError("warm-up must not run for an uncached model")

    monkeypatch.setattr(mm, "_load_model_with_timeout", must_not_load)
    asyncio.run(mm.preload_model())
    assert mm.model is None


def test_checkpoint_in_local_cache_probe(monkeypatch, tmp_path):
    import huggingface_hub
    import services.model_manager as mm

    # A local dir needs no hub at all.
    assert mm._checkpoint_in_local_cache(str(tmp_path)) is True

    def fake_snapshot_download(repo_id, *args, **kwargs):
        assert kwargs.get("local_files_only") is True  # never a network probe
        if repo_id == "org/cached":
            return "/cache/snap"
        raise FileNotFoundError(repo_id)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    assert mm._checkpoint_in_local_cache("org/cached") is True
    assert mm._checkpoint_in_local_cache("org/missing") is False


# ── (c) /generate 500 detail carries the actionable hint ────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from main import app
    import core.db

    core.db.init_db()
    # raise_server_exceptions=False: the unhandled ImportError must flow
    # through the global 500 handler (main.py) — the surface under test —
    # instead of re-raising into the test.
    return TestClient(
        app, client=("127.0.0.1", 50000), raise_server_exceptions=False
    )


def test_generate_500_detail_carries_socks_hint(client, monkeypatch):
    # get_model() is called OUTSIDE /generate's try block, so the ImportError
    # reaches the global handler bare. Pre-fix the 500 detail was the raw
    # httpx message with no next step; it must now carry the class hint.
    # generation.py binds get_model at import (`from services.model_manager
    # import get_model`), so the module-local binding is the effective seam.
    import api.routers.generation as gen

    async def boom():
        raise ImportError(_SOCKS_MSG)

    monkeypatch.setattr(gen, "get_model", boom)
    r = client.post("/generate", data={"text": "hello", "engine": "omnivoice"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert _SOCKS_MSG not in detail
    assert detail.startswith(
        "VoiceStudio hit an internal error; check the backend log for details."
    )
    assert "unset ALL_PROXY/HTTPS_PROXY" in detail
    assert r.json()["docs_topic"] == "SOCKS_PROXY_SUPPORT_MISSING"
