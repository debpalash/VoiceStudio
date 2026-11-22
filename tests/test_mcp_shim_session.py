"""stdio shim must carry Mcp-Session-Id and recover from expiry (#4345)."""

import asyncio
import importlib
import json

import httpx
import pytest

URL = "http://x/mcp/"


@pytest.fixture
def shim():
    return importlib.import_module("mcp_shim.__main__")


class _Server:
    """Minimal session-enforcing streamable-HTTP server."""

    def __init__(self):
        self.valid = set()
        self.n = 0
        self.log = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sid = request.headers.get("mcp-session-id")
        self.log.append((body.get("method"), sid))
        if body.get("method") == "initialize":
            self.n += 1
            new = f"s{self.n}"
            self.valid.add(new)
            return httpx.Response(
                200,
                headers={"mcp-session-id": new, "content-type": "application/json"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
            )
        if not sid:
            return httpx.Response(400, text="Bad Request: Missing session ID")
        if sid not in self.valid:
            return httpx.Response(404, text="Session not found")
        if "id" not in body:
            return httpx.Response(202)
        reply = {"jsonrpc": "2.0", "id": body["id"], "result": {"ok": 1}}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"event: message\ndata: {json.dumps(reply)}\n\n",
        )


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
INITED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _send(shim, monkeypatch, srv, session, messages):
    out = []
    monkeypatch.setattr(shim, "_write_stdout", out.append)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(srv)) as client:
            for m in messages:
                await shim._handle_request(client, URL, json.dumps(m), {}, session)

    asyncio.run(go())
    return out


def test_session_id_forwarded_after_initialize(shim, monkeypatch):
    srv = _Server()
    out = _send(shim, monkeypatch, srv, shim._Session(), [INIT, INITED, LIST])
    assert out[-1]["result"] == {"ok": 1}
    assert srv.log[1:] == [("notifications/initialized", "s1"), ("tools/list", "s1")]


def test_expired_session_reinitializes_and_retries(shim, monkeypatch):
    srv = _Server()
    session = shim._Session()
    out = _send(shim, monkeypatch, srv, session, [INIT, INITED])
    srv.valid.clear()  # backend restart / expiry
    out += _send(shim, monkeypatch, srv, session, [{**LIST, "id": 3}])
    assert out[-1] == {"jsonrpc": "2.0", "id": 3, "result": {"ok": 1}}
    assert session.id == "s2"
    assert sum(1 for m, _ in srv.log if m == "initialize") == 2
    assert not any("error" in o for o in out)


def test_unrecoverable_404_returns_error(shim, monkeypatch):
    session = shim._Session()
    session.id = "gone"  # no cached initialize to replay
    out = _send(shim, monkeypatch, _Server(), session, [LIST])
    assert out[-1]["error"]["code"] == -32000


def test_400_with_new_id_after_failed_handshake_still_recovers(shim, monkeypatch):
    """A session-less request that the server answers 400 *with* a fresh id
    must still trigger the replay: judge the request sent, not session.id."""
    srv = _Server()
    session = shim._Session()
    session.initialize = json.dumps(INIT)
    calls = {"n": 0}
    inner = srv.__call__

    def handler(request):
        body = json.loads(request.content)
        if body.get("method") != "initialize" and not request.headers.get("mcp-session-id"):
            calls["n"] += 1
            srv.n += 1
            new = f"s{srv.n}"
            srv.valid.add(new)
            return httpx.Response(400, headers={"mcp-session-id": new}, text="Missing session ID")
        return inner(request)

    out = _send(shim, monkeypatch, handler, session, [LIST])
    assert out[-1]["result"] == {"ok": 1}
    assert sum(1 for m, _ in srv.log if m == "initialize") == 1
