"""Model discovery honours the credential-transport gate (#4121).

``discover_model`` used to reach LM Studio's native probe without the
``credential_transport_error`` check that guards every inference call, so a
user-configured plaintext base_url on a remote host received the stored API key
in an ``Authorization: Bearer`` header. These tests are hermetic: the probe's
opener is always stubbed, so an unexpected connection fails the test instead of
touching the network.
"""
from __future__ import annotations

import io
import json
from typing import Optional

import pytest


@pytest.fixture
def lp(monkeypatch, clean_llm_env):
    """llm_providers with settings_store backed by in-memory dicts (no SQLite).

    Mirrors the seam in tests/test_llm_providers.py so discovery sees exactly
    the stored key / base_url / opt-in the test seeds.
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


@pytest.fixture(autouse=True)
def _clear_discovery_cache(lp):
    """The discovery cache is module-global; keep it out of every other file."""
    lp.forget_discovered_models()
    yield
    lp.forget_discovered_models()


class _RecordingOpener:
    """Stands in for ``urllib.request.build_opener``: records, never dials."""

    def __init__(self, payload: Optional[dict] = None):
        self.payload = payload if payload is not None else {
            "data": [{"id": "chat", "type": "llm", "state": "loaded"}]
        }
        self.requests: list[tuple[str, Optional[str]]] = []

    def __call__(self, *handlers):
        return self

    def open(self, request, timeout):
        self.requests.append((request.full_url, request.get_header("Authorization")))
        return io.BytesIO(json.dumps(self.payload).encode())


@pytest.fixture
def no_network(lp, monkeypatch):
    """Any HTTP attempt fails the test — that attempt *is* the key leak."""
    monkeypatch.setattr(
        lp.urllib.request,
        "build_opener",
        lambda *handlers: pytest.fail("discovery opened an HTTP connection"),
    )


# ── (a) plaintext remote host + stored key: no call, no model ──────────────

def test_plaintext_lan_key_is_never_probed(lp, no_network):
    lp.save_key("lmstudio", "stored-secret")
    lp.save_overrides("lmstudio", base_url="http://192.168.1.10:1234/v1")
    provider = lp.get_provider("lmstudio")
    assert lp.credential_transport_error(provider)  # gate really refuses this URL
    assert lp.discover_model(provider) is None
    # The same refusal reaches callers through resolve_model (llm_backend /
    # llm_skills): the placeholder default stands, nothing was discovered.
    assert lp.resolve_model(provider) == provider.default_model


def test_plaintext_lan_refusal_is_not_negative_cached(lp, monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    lp.save_key("lmstudio", "stored-secret")
    lp.save_overrides("lmstudio", base_url="http://192.168.1.10:1234/v1")
    provider = lp.get_provider("lmstudio")

    assert lp.discover_model(provider) is None
    assert opener.requests == []
    # A configuration refusal is not a probe failure: no negative-cache entry,
    # so the very next call after the user opts in probes again.
    assert lp._cached_discovery("lmstudio") == (False, None)

    lp.save_overrides("lmstudio", allow_plain_http=True)
    assert lp.discover_model(lp.get_provider("lmstudio")) == "chat"
    assert opener.requests == [
        ("http://192.168.1.10:1234/api/v0/models", "Bearer stored-secret"),
    ]


# ── (b) opt-in / HTTPS / loopback keep probing exactly as before ───────────

def test_plaintext_lan_opt_in_still_probes(lp, monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    lp.save_key("lmstudio", "stored-secret")
    lp.save_overrides("lmstudio", base_url="http://192.168.1.10:1234/v1",
                      allow_plain_http=True)
    assert lp.credential_transport_error(lp.get_provider("lmstudio")) is None
    assert lp.discover_model(lp.get_provider("lmstudio")) == "chat"
    assert opener.requests == [
        ("http://192.168.1.10:1234/api/v0/models", "Bearer stored-secret"),
    ]


def test_https_remote_discovery_still_probes(lp, monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    lp.save_key("lmstudio", "stored-secret")
    lp.save_overrides("lmstudio", base_url="https://lmstudio.example/v1")
    assert lp.discover_model(lp.get_provider("lmstudio")) == "chat"
    assert opener.requests == [
        ("https://lmstudio.example/api/v0/models", "Bearer stored-secret"),
    ]


def test_loopback_plaintext_discovery_still_probes(lp, monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    lp.save_key("lmstudio", "stored-secret")
    lp.save_overrides("lmstudio", base_url="http://127.0.0.1:1234/v1")
    assert lp.discover_model(lp.get_provider("lmstudio")) == "chat"
    assert opener.requests == [
        ("http://127.0.0.1:1234/api/v0/models", "Bearer stored-secret"),
    ]


# ── defence in depth: the probe itself refuses, whatever the caller ────────

def test_native_probe_refuses_plaintext_remote_key(lp, no_network):
    assert lp._probe_lmstudio_loaded_model("http://192.168.1.10:1234/v1", "stored-secret") is None


def test_native_probe_opted_in_plaintext_still_probes(lp, monkeypatch):
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    lp.save_overrides("lmstudio", allow_plain_http=True)
    assert lp._probe_lmstudio_loaded_model("http://192.168.1.10:1234/v1", "stored-secret") == "chat"
    assert opener.requests == [
        ("http://192.168.1.10:1234/api/v0/models", "Bearer stored-secret"),
    ]


def test_native_probe_needs_no_key_to_probe_a_plaintext_remote(lp, monkeypatch):
    """The 'local' sentinel sends no credential, so nothing can leak."""
    opener = _RecordingOpener()
    monkeypatch.setattr(lp.urllib.request, "build_opener", opener)
    assert lp._probe_lmstudio_loaded_model("http://192.168.1.10:1234/v1", "local") == "chat"
    assert opener.requests == [("http://192.168.1.10:1234/api/v0/models", None)]
