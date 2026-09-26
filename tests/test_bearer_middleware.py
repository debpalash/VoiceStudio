"""BearerKeyMiddleware — remote-backend API key gate (Wave 2.3).

Mirrors tests/test_network_middleware.py: a TestClient with a chosen client
address exercises the loopback bypass, the SPA-shell exemption, and the
401-without / pass-with-key paths. The env var is the switch.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def key_env(monkeypatch):
    from services.admin_sessions import admin_session_store

    admin_session_store.clear()
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret-key")
    yield "s3cret-key"
    admin_session_store.clear()


def _client(addr=("10.0.0.5", 1)):
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=addr)


def test_inert_without_env(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    c = _client()  # non-loopback
    assert c.get("/health").status_code == 200


def test_whitespace_only_env_is_not_an_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_API_KEY", "   ")
    c = _client()
    response = c.get("/v1/audio/voices")
    assert response.status_code == 200
    assert isinstance(response.json().get("voices"), list)


def test_whitespace_query_does_not_shadow_valid_cookie(key_env):
    c = _client()
    c.cookies.set("ov_key", key_env)

    response = c.get("/v1/audio/voices?api_key=%20%20%20")

    assert response.status_code == 200
    assert isinstance(response.json().get("voices"), list)


def test_loopback_bypasses_key(key_env):
    c = _client(("127.0.0.1", 1))
    assert c.get("/system/info").status_code == 200


def test_non_loopback_without_key_401(key_env):
    c = _client()
    r = c.get("/v1/audio/voices")
    assert r.status_code == 401
    assert r.json()["detail"] == "API key required"


def test_non_loopback_with_bearer_passes(key_env):
    c = _client()
    r = c.get("/v1/audio/voices", headers={"Authorization": "Bearer s3cret-key"})
    assert r.status_code != 401


def test_trusted_network_without_explicit_credential_still_passes(key_env, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/24")

    response = _client().get("/v1/audio/voices")

    assert response.status_code == 200


def test_invalid_explicit_header_is_authoritative_on_trusted_network(key_env, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/24")

    response = _client().get(
        "/v1/audio/voices",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "API key required"}


def test_master_key_is_never_reflected_into_a_cookie(key_env):
    c = _client()

    response = c.get(
        "/v1/audio/voices",
        headers={"Authorization": f"Bearer {key_env}"},
    )

    assert response.status_code == 200
    assert key_env not in response.headers.get("set-cookie", "")
    assert c.cookies.get("ov_key") is None


def test_non_loopback_with_admin_session_cookie_passes(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    c = _client()
    c.cookies.set("ov_session", session.token)

    response = c.get("/v1/audio/voices")

    assert response.status_code == 200


def test_non_loopback_with_admin_session_bearer_passes(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    c = _client()

    response = c.get(
        "/v1/audio/voices",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200


def test_invalid_admin_session_is_401(key_env):
    c = _client()
    c.cookies.set("ov_session", "ovs_admin_session_" + "a" * 43)

    response = c.get("/v1/audio/voices")

    assert response.status_code == 401


def test_key_rotation_invalidates_admin_session(key_env, monkeypatch):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    c = _client()
    c.cookies.set("ov_session", session.token)
    assert c.get("/v1/audio/voices").status_code == 200

    monkeypatch.setenv("OMNIVOICE_API_KEY", "rotated-key")

    assert c.get("/v1/audio/voices").status_code == 401


def test_query_param_key_passes(key_env):
    c = _client()
    r = c.get("/v1/audio/voices?api_key=s3cret-key")
    assert r.status_code != 401


def test_wrong_key_401(key_env):
    c = _client()
    r = c.get("/v1/audio/voices", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_non_ascii_invalid_key_fails_closed_instead_of_raising(key_env):
    response = _client().get(
        "/v1/audio/voices",
        params={"api_key": "clé-incorrecte"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "API key required"}


def test_shell_paths_served_without_key(key_env):
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.get("/early-error-capture.js").status_code != 401


def test_middleware_is_plain_asgi():
    from starlette.middleware.base import BaseHTTPMiddleware
    from main import BearerKeyMiddleware
    assert not issubclass(BearerKeyMiddleware, BaseHTTPMiddleware)
    assert callable(getattr(BearerKeyMiddleware, "__call__", None))


def test_ws_handshake_rejected_without_key(key_env):
    """A non-loopback WS handshake without the key is closed, not accepted."""
    c = _client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with c.websocket_connect("/ws/transcribe"):
            pass
    assert exc_info.value.code == 1008


def test_ws_handshake_accepted_with_query_key(key_env):
    c = _client()
    # ws_remote_authorized reads ?api_key; the capture handler then accepts.
    with c.websocket_connect("/ws/transcribe?api_key=s3cret-key") as ws:
        ws.close()


def test_ws_handshake_accepted_with_session_cookie(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    c = _client()
    c.cookies.set("ov_session", session.token)

    with c.websocket_connect(
        "/ws/transcribe",
        headers={"Origin": "http://testserver"},
    ) as ws:
        ws.close()


def test_ws_session_cookie_rejects_missing_or_wrong_origin(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    for headers in ({}, {"Origin": "http://testserver.evil.test"}, {"Origin": "null"}):
        c = _client()
        c.cookies.set("ov_session", session.token)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with c.websocket_connect("/ws/transcribe", headers=headers):
                pass
        assert exc_info.value.code == 1008


def test_ws_ticket_is_path_bound_single_use_and_origin_checked(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    ticket = admin_session_store.issue_ws_ticket(
        session.token,
        "/ws/transcribe",
        key_env,
    )
    url = f"/ws/transcribe?ws_ticket={ticket.token}"

    with _client().websocket_connect(
        url,
        headers={"Origin": "http://testserver"},
    ) as ws:
        ws.close()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with _client().websocket_connect(
            url,
            headers={"Origin": "http://testserver"},
        ):
            pass
    assert exc_info.value.code == 1008


def test_platform_ws_accepts_path_bound_ticket(key_env):
    from services.admin_sessions import admin_session_store

    path = "/v1/audio/transcriptions/stream"
    session = admin_session_store.issue(key_env)
    ticket = admin_session_store.issue_ws_ticket(session.token, path, key_env)

    with _client().websocket_connect(
        f"{path}?ws_ticket={ticket.token}",
        headers={"Origin": "http://testserver"},
    ) as ws:
        assert ws.receive_json()["type"] == "session.started"
        ws.close()


def test_ws_ticket_wrong_path_consumes_ticket(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    ticket = admin_session_store.issue_ws_ticket(
        session.token,
        "/ws/events",
        key_env,
    )
    query = f"?ws_ticket={ticket.token}"

    with pytest.raises(WebSocketDisconnect) as wrong_path:
        with _client().websocket_connect(
            "/ws/transcribe" + query,
            headers={"Origin": "http://testserver"},
        ):
            pass
    assert wrong_path.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as reused:
        with _client().websocket_connect(
            "/ws/events" + query,
            headers={"Origin": "http://testserver"},
        ):
            pass
    assert reused.value.code == 1008


def test_ws_ticket_rejects_untrusted_origin(key_env):
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(key_env)
    ticket = admin_session_store.issue_ws_ticket(
        session.token,
        "/ws/transcribe",
        key_env,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with _client().websocket_connect(
            f"/ws/transcribe?ws_ticket={ticket.token}",
            headers={"Origin": "http://evil.test"},
        ):
            pass
    assert exc_info.value.code == 1008
