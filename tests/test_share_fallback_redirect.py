"""
LAN-share fallback redirect keeps the client's host (#2680).

When no built SPA is present, ``GET /`` bounces to the UI dev server. That
same route table is served by the LAN share listener on 0.0.0.0, so a
hardcoded ``localhost`` target bounces a remote client at its own loopback
(``ERR_CONNECTION_REFUSED`` on their machine). The redirect must preserve
the host the client used — and the ``?pin=`` query the PIN gate needs.
"""
import os
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from main import app
    import core.db
    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000), follow_redirects=False)


def _location(client, host, path="/"):
    r = client.get(path, headers={"host": host})
    if r.status_code != 307:
        pytest.skip("built SPA is served; no dev fallback registered")
    return r.headers["location"]


def test_fallback_preserves_lan_host_and_pin(client, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    assert _location(client, "192.168.1.4:3005", "/?pin=123456") == (
        "http://192.168.1.4:3901/?pin=123456"
    )


def test_fallback_loopback_behavior_unchanged(client, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    assert _location(client, "localhost:3900") == "http://localhost:3901/"
    assert _location(client, "127.0.0.1:3900") == "http://127.0.0.1:3901/"


def test_fallback_unit_no_host_no_query():
    from core.spa_inject import dev_fallback_url
    assert dev_fallback_url(None, "", 3901) == "http://localhost:3901/"
    assert dev_fallback_url("", "", 3901) == "http://localhost:3901/"
    assert dev_fallback_url("::1", "pin=1", 3901) == "http://[::1]:3901/?pin=1"
