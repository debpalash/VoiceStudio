"""Every WebSocket endpoint carries the same loopback/credential guard.

The share-PIN gate is HTTP-only (``NetworkAccessMiddleware``) and
``BearerKeyMiddleware`` is inert unless ``OMNIVOICE_API_KEY`` is set, so each
WebSocket route must run ``require_ws_access`` (``api.dependencies``) before it
accepts the handshake. Only dictation did, which left ``/ws/tts`` and
``/ws/events`` reachable from a non-loopback client with no credential at all
— a share-PIN bypass.

The routers are mounted alone in a bare app: the guard, not a middleware, is
what this suite pins. The matrix behind every route is

* loopback client → accepted
* non-loopback client, no credential → closed with 1008
* non-loopback client with the API key → accepted (remote thin-client dictation)
* foreign ``Origin`` on the handshake → closed with 1008
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

pytestmark = pytest.mark.usefixtures("asr_model_installed")

API_KEY = "s3cret-key"

# /v1/audio/transcriptions/stream is the OpenAI-compatible alias of
# /ws/transcribe: the same handler, so the same guard must cover both routes.
WS_ROUTES = (
    "/ws/tts",
    "/ws/events",
    "/ws/transcribe",
    "/v1/audio/transcriptions/stream",
)

LOOPBACK = ("127.0.0.1", 50000)
# Starlette's TestClient default is ("testclient", 50000) — a non-loopback,
# non-trusted peer, i.e. a LAN guest.
NON_LOOPBACK = ("testclient", 50000)


@pytest.fixture(autouse=True)
def _isolated_auth_env(monkeypatch):
    """No credential is configured unless a test asks for one."""
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("OMNIVOICE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    yield
    from services.admin_sessions import admin_session_store

    admin_session_store.clear()


@pytest.fixture(scope="module")
def bare_app():
    """Only the three WebSocket routers, with no middleware in front."""
    from fastapi import FastAPI

    from api.routers import capture_ws, events, tts_stream

    app = FastAPI()
    for module in (capture_ws, events, tts_stream):
        app.include_router(module.router)
    return app


def _connect(bare_app, path, addr, **kwargs):
    """Open one handshake; returns the live context manager (never entered)."""
    from fastapi.testclient import TestClient

    return TestClient(bare_app, client=addr).websocket_connect(path, **kwargs)


def _assert_rejected_with_policy_violation(bare_app, path, addr, **kwargs):
    """The handshake must be closed with 1008, not accepted."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with _connect(bare_app, path, addr, **kwargs):
            pass
    assert exc_info.value.code == 1008


@pytest.mark.parametrize("path", WS_ROUTES)
def test_loopback_client_is_accepted(bare_app, path):
    with _connect(bare_app, path, LOOPBACK) as ws:
        ws.close()


@pytest.mark.parametrize("path", WS_ROUTES)
def test_non_loopback_without_credential_is_rejected(bare_app, path):
    _assert_rejected_with_policy_violation(bare_app, path, NON_LOOPBACK)


@pytest.mark.parametrize("path", WS_ROUTES)
def test_non_loopback_with_api_key_query_is_accepted(bare_app, monkeypatch, path):
    """Remote thin client: the mic is here, the GPU is elsewhere."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", API_KEY)

    with _connect(bare_app, f"{path}?api_key={API_KEY}", NON_LOOPBACK) as ws:
        ws.close()


@pytest.mark.parametrize("path", WS_ROUTES)
def test_non_loopback_with_bearer_key_is_accepted(bare_app, monkeypatch, path):
    monkeypatch.setenv("OMNIVOICE_API_KEY", API_KEY)

    with _connect(
        bare_app,
        path,
        NON_LOOPBACK,
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as ws:
        ws.close()


@pytest.mark.parametrize("path", WS_ROUTES)
def test_foreign_origin_is_rejected_even_from_loopback(bare_app, path):
    """A page on any site can reach 127.0.0.1; its Origin must be refused."""
    _assert_rejected_with_policy_violation(
        bare_app,
        path,
        LOOPBACK,
        headers={"Origin": "http://evil.test"},
    )


@pytest.mark.parametrize("path", WS_ROUTES)
def test_foreign_origin_is_rejected_for_a_keyed_remote_client(bare_app, monkeypatch, path):
    """A credential does not buy a cross-site browser page a handshake."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", API_KEY)

    _assert_rejected_with_policy_violation(
        bare_app,
        f"{path}?api_key={API_KEY}",
        NON_LOOPBACK,
        headers={"Origin": "http://evil.test"},
    )


@pytest.mark.parametrize("path", WS_ROUTES)
def test_trusted_network_client_is_accepted(bare_app, monkeypatch, path):
    """OMNIVOICE_TRUSTED_NETWORKS stays the credential-free consumption path."""
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/24")

    with _connect(bare_app, path, ("10.0.0.5", 40000)) as ws:
        ws.close()
