"""Runtime-log and crash diagnostics are admin-gated, not bare-server public.

The bare-Docker bootstrap exemption in ``check_admin`` deliberately opens
*read-only* admin requests while no credential is configured, so
``GET /system/logs`` and friends used to be readable by anything that could
reach a published Docker port. The install docs already promised those
diagnostics require the administrator API key, and the routes expose host
paths, log contents and crash records. These tests pin the rule:

* genuine loopback -> allowed (the desktop app's own process);
* non-loopback in server mode with no credential -> 403;
* non-loopback with a valid administrator API key -> allowed;
* a share PIN never satisfies it.

``/system/logs/stream`` is Server-Sent Events and stays open by design, so its
admission and refusal are checked against the dependency directly, alongside
an assertion that the real route declares that dependency. The complete-body
diagnostic routes are exercised end-to-end.
"""

from __future__ import annotations

import asyncio

import pytest


MASTER = "DIAGNOSTICS_MASTER_KEY_4f11"

# Diagnostic GETs that answer with a complete body.
DIAGNOSTIC_JSON_ROUTES = [
    "/system/logs",
    "/system/logs/tauri",
    "/system/errors/recent",
    "/system/last-run-crash",
]

# The Server-Sent Events stream: dependency admission/refusal plus route wiring.
DIAGNOSTIC_STREAM_ROUTE = "/system/logs/stream"

# Bootstrap discovery that must STAY available with no credential configured.
BOOTSTRAP_READ_ROUTES = [
    "/system/info",
    "/stats/usage",
]


def _client(host: str, *, headers: dict[str, str] | None = None):
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=(host, 50000), headers=headers)


def _refusal(client, path: str) -> tuple[int, str]:
    """(status, detail) for one of the complete-body diagnostic routes."""
    with client.stream("GET", path) as response:
        status = response.status_code
        detail = response.read().decode() if status != 200 else ""
    return status, detail


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)


# ── complete-body routes, end to end ────────────────────────────────────────


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_loopback_can_read_diagnostics(path):
    assert _client("127.0.0.1").get(path).status_code == 200, path


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_bare_server_mode_refuses_remote_diagnostics(path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")

    status, detail = _refusal(_client("172.17.0.1"), path)

    assert status == 403, path
    # Same contract as every other admin refusal: the detail names what would
    # actually satisfy the gate (loopback, or the API key when one is set).
    assert "loopback" in detail


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_remote_diagnostics_with_admin_api_key(path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", MASTER)

    client = _client("172.17.0.1", headers={"Authorization": f"Bearer {MASTER}"})

    assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_bare_server_mode_still_refuses_a_share_pin(path, monkeypatch):
    """A 6-digit consumption PIN must never unlock the diagnostics tier."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")

    status, _ = _refusal(_client("172.17.0.1", headers={"X-OmniVoice-Pin": "123456"}), path)

    assert status == 403, path


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_a_valid_consumption_pin_cannot_read_diagnostics(path, monkeypatch):
    from main import app
    from core.db import ensure_schema
    from services.network_share import ShareState

    ensure_schema()
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setattr(app.state, "network_share", ShareState(
        enabled=True, share_port=3901, pin="123456", lan_addresses=["10.0.0.9"],
    ), raising=False)
    client = _client("172.17.0.1")
    assert client.get("/profiles").status_code == 401
    headers = {"X-OmniVoice-Pin": "123456"}
    consumption = client.get("/profiles", headers=headers)
    assert consumption.status_code == 200, consumption.text
    assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_trusted_network_is_consumption_only_for_diagnostics(path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "172.17.0.0/16")
    client = _client("172.17.0.1")
    assert client.get("/stats/usage").status_code == 200
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", DIAGNOSTIC_JSON_ROUTES)
def test_admin_session_reads_diagnostics_without_csrf_header_but_not_cross_site(path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", MASTER)
    client = _client("172.17.0.1")
    from services.admin_sessions import admin_session_store

    session = admin_session_store.issue(MASTER)
    try:
        headers = {"Cookie": f"ov_session={session.token}", "Origin": "http://testserver"}
        assert client.get(path, headers=headers).status_code == 200
        headers["Origin"] = "http://evil.test"
        assert client.get(path, headers=headers).status_code == 403
    finally:
        admin_session_store.clear()


# ── the SSE stream: the gate itself ─────────────────────────────────────────
#
# Opening the stream in a test means reading a body that never ends, and a
# wrongly *admitted* stream also keeps the test client's portal busy on exit, so
# this route is checked at the dependency boundary. The AST test below is what
# makes that equivalent: it proves the route is wired to this exact callable.


def _request(host: str, *, headers: dict[str, str] | None = None):
    from types import SimpleNamespace

    from starlette.requests import Request

    raw_headers = [
        (str(name).lower().encode("latin-1"), str(value).encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": DIAGNOSTIC_STREAM_ROUTE,
            "raw_path": DIAGNOSTIC_STREAM_ROUTE.encode("latin-1"),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": (host, 4321),
            "server": ("127.0.0.1", 3900),
            "app": SimpleNamespace(state=SimpleNamespace(network_share=None)),
            "state": {},
        }
    )


def test_the_stream_route_is_wired_to_the_diagnostics_gate():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "backend/api/routers/system.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wired = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not any(
                isinstance(child, ast.Constant) and child.value == DIAGNOSTIC_STREAM_ROUTE
                for child in ast.walk(decorator)
            ):
                continue
            wired = {
                name.id
                for kw in decorator.keywords
                for name in ast.walk(kw)
                if isinstance(name, ast.Name)
            }
    assert "require_admin_diagnostics" in wired, wired


def test_loopback_passes_the_diagnostics_gate():
    from api.dependencies import require_admin_diagnostics

    asyncio.run(require_admin_diagnostics(_request("127.0.0.1")))  # must not raise


def test_a_keyed_remote_caller_passes_the_diagnostics_gate(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", MASTER)
    from api.dependencies import require_admin_diagnostics

    asyncio.run(
        require_admin_diagnostics(
            _request("172.17.0.1", headers={"Authorization": f"Bearer {MASTER}"})
        )
    )  # must not raise


def test_a_remote_caller_without_a_credential_is_refused_by_the_gate(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    from fastapi import HTTPException

    from api.dependencies import require_admin_diagnostics

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin_diagnostics(_request("172.17.0.1")))
    assert exc.value.status_code == 403
    assert "loopback" in exc.value.detail


def test_a_share_pin_is_refused_by_the_gate(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    from fastapi import HTTPException

    from api.dependencies import require_admin_diagnostics

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_admin_diagnostics(_request("172.17.0.1", headers={"X-OmniVoice-Pin": "123456"})))
    assert exc.value.status_code == 403


# ── bootstrap discovery stays open ──────────────────────────────────────────


@pytest.mark.parametrize("path", BOOTSTRAP_READ_ROUTES)
def test_bare_server_mode_keeps_bootstrap_reads_open(path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")

    assert _client("172.17.0.1").get(path).status_code == 200, path
