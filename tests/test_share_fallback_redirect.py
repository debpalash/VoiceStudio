"""
LAN-share fallback redirect never strands a remote browser (#2680).

When no built SPA is present, ``GET /`` bounces to the UI dev server. That
same route table is served by the LAN share listener on 0.0.0.0, so the
bounce must neither hardcode ``localhost`` (remote client lands on its own
loopback) nor point at the share listener itself (default ports make that
an infinite redirect loop). Only a loopback client aimed elsewhere gets the
bounce; everyone else gets a terminal 404 naming the missing web UI.
"""
import os
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture(scope="module")
def client():
    # Module scope mirrors tests/test_router_smoke.py; these tests are
    # read-only GETs and share no mutable state.
    from fastapi.testclient import TestClient
    from main import app
    import core.db
    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000), follow_redirects=False)


def _get_root(client, host, path="/", *, peer=("127.0.0.1", 50000)):
    if peer != ("127.0.0.1", 50000):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app, client=peer, follow_redirects=False)
    r = client.get(path, headers={"host": host})
    if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
        pytest.skip("built SPA is served; no dev fallback registered")
    return r


def test_fallback_preserves_lan_host_and_pin(client, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    r = _get_root(client, "192.168.1.4:3005", "/?pin=123456")
    assert r.status_code == 307
    assert r.headers["location"] == "http://192.168.1.4:3901/?pin=123456"


def test_fallback_loopback_behavior_unchanged(client, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    r = _get_root(client, "localhost:3900")
    assert r.status_code == 307
    assert r.headers["location"] == "http://localhost:3901/"
    r = _get_root(client, "127.0.0.1:3900")
    assert r.status_code == 307
    assert r.headers["location"] == "http://127.0.0.1:3901/"


def test_fallback_remote_client_gets_terminal_answer(client, monkeypatch):
    # A phone on the LAN has no UI dev server to reach; a redirect would
    # lie (and with default ports, loop onto the share listener itself).
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    r = _get_root(client, "192.168.1.4:3005", "/?pin=123456",
                  peer=("192.168.1.4", 50001))
    assert r.status_code == 404
    assert "location" not in r.headers
    assert "frontend/dist" in r.json()["detail"]


def test_fallback_already_at_ui_port_does_not_loop(client, monkeypatch):
    # Share port == UI port (the defaults): bouncing would return the
    # request to itself forever.
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    r = _get_root(client, "localhost:3901", "/?pin=123456")
    assert r.status_code == 404
    assert "location" not in r.headers


def test_fallback_unit_helper():
    from core.spa_inject import dev_fallback_url
    kw = {"query": "", "client_is_loopback": True, "ui_port": 3901}
    assert dev_fallback_url(host=None, port=3900, **kw) == "http://localhost:3901/"
    assert dev_fallback_url(host="", port=3900, **kw) == "http://localhost:3901/"
    assert dev_fallback_url(
        host="::1", port=3900, query="pin=1", client_is_loopback=True, ui_port=3901
    ) == "http://[::1]:3901/?pin=1"
    assert dev_fallback_url(host="192.168.1.4", port=3005, query="",
                            client_is_loopback=False, ui_port=3901) is None
    assert dev_fallback_url(host="localhost", port=3901, query="",
                            client_is_loopback=True, ui_port=3901) is None
