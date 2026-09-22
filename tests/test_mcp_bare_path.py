"""The published MCP URL (`/mcp`, no slash) works on the real app with a built SPA.

Docker images and source builds serve `frontend/dist` from a StaticFiles mount
at "/". The MCP transport is mounted at "/mcp", which only matches "/mcp/...",
so a bare "/mcp" used to fall through to the SPA and answer POST with 405
(without an SPA: a 307 most MCP clients never re-POST). Every exported client
config and doc publishes "/mcp", so both spellings must serve MCP directly.
"""
import importlib
import json

import pytest

pytest.importorskip("mcp")

HEADERS = {"Accept": "application/json, text/event-stream", "X-OmniVoice-Client-Id": "codex-cli"}


def _result(response):
    assert response.status_code == 200, (response.status_code, response.text[:200])
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return json.loads(next(
            line[6:] for line in response.text.splitlines() if line.startswith("data: ")
        ))
    return response.json()


@pytest.fixture
def app_with_spa(monkeypatch, tmp_path):
    from core import spa_inject

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa</title>")
    monkeypatch.setattr(spa_inject, "frontend_dist_dir", lambda: str(dist))
    monkeypatch.delenv("OMNIVOICE_MCP_DISABLE", raising=False)
    import main

    importlib.reload(main)
    try:
        yield main.app
    finally:
        monkeypatch.undo()
        importlib.reload(main)  # restore the default app for later tests


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_mcp_streamable_http_on_real_app_with_spa_mount(app_with_spa, path):
    from starlette.testclient import TestClient

    with TestClient(
        app_with_spa, base_url="http://127.0.0.1:3900", follow_redirects=False
    ) as client:
        assert "<title>spa</title>" in client.get("/").text  # SPA really mounted
        headers = dict(HEADERS)
        init = client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "codex-cli", "version": "test"},
            },
        })
        assert "serverInfo" in _result(init)["result"]
        headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
        assert client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }).status_code == 202
        tools = _result(client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        }))
        assert "generate_speech" in {t["name"] for t in tools["result"]["tools"]}
        # Session teardown (DELETE) reaches the transport too — not 405/307.
        assert client.delete(path, headers=headers).status_code == 200


def test_bare_mcp_route_accepts_every_method(app_with_spa):
    """GET opens the SSE stream; the bare route must not restrict methods."""
    from starlette.routing import Route

    bare = [r for r in app_with_spa.router.routes if isinstance(r, Route) and r.path == "/mcp"]
    assert len(bare) == 1 and bare[0].methods is None
    names = [getattr(r, "path", None) for r in app_with_spa.router.routes]
    assert names.index("/mcp") < names.index("")  # ahead of the SPA mount at "/"


def test_tools_reach_the_api_when_bound_to_a_lan_address_behind_a_key(monkeypatch, app_with_spa):
    """A concrete OMNIVOICE_BIND_HOST plus an API key must not break the tools.

    Tool calls go in-process as a loopback caller, so neither the bind address
    nor the API-key / share-PIN gates apply to them.
    """
    from starlette.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_BIND_HOST", "192.0.2.10")  # not a local address
    monkeypatch.setenv("OMNIVOICE_PORT", "1")  # nothing listens here
    monkeypatch.setenv("OMNIVOICE_API_KEY", "k" * 40)
    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    with TestClient(
        app_with_spa, base_url="http://127.0.0.1:3900", follow_redirects=False,
        client=("127.0.0.1", 50000),  # the agent itself is local
    ) as client:
        headers = dict(HEADERS)
        init = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "codex-cli", "version": "test"},
            },
        })
        _result(init)
        headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
        client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        called = _result(client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "check_health", "arguments": {}},
        }))
    assert not called["result"].get("isError"), called
    assert "status" in called["result"]["content"][0]["text"]
