"""LAN devices get the web UI from packaged builds, never a redirect to localhost (#2599).

The share listener serves the same app on 0.0.0.0. Before the fix, a packaged
runtime had no web build beside its backend, so "/" answered every client with
a 307 to ``http://localhost:<ui port>``, which on a phone or second laptop is
that device itself.
"""
import importlib
from pathlib import Path

import pytest

from core.spa_inject import (
    FRONTEND_DIST_ENV,
    dev_ui_redirect,
    frontend_available,
    frontend_dist_dir,
    web_ui_missing_body,
)

ROOT = Path(__file__).resolve().parents[1]


def test_electron_bundle_carries_and_hands_over_the_web_ui() -> None:
    builder = (ROOT / "electron/electron-builder.config.mjs").read_text(encoding="utf-8")
    backend = (ROOT / "electron/src/main/backend.ts").read_text(encoding="utf-8")
    contract = (ROOT / "electron/tests/packaging-contract.mjs").read_text(encoding="utf-8")
    electron_package = (ROOT / "electron/package.json").read_text(encoding="utf-8")

    assert "from: '../frontend/dist'" in builder
    assert "to: 'frontend/dist'" in builder
    assert FRONTEND_DIST_ENV in backend
    assert "'frontend/dist'" in contract
    # Every packaging path builds the web UI before electron-builder runs.
    for script in ("\"dist\":", "\"dist:dir\":"):
        line = next(l for l in electron_package.splitlines() if script in l)
        assert "bun run build:web" in line


def test_frontend_dist_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(FRONTEND_DIST_ENV, str(tmp_path))
    assert frontend_dist_dir() == str(tmp_path)
    monkeypatch.delenv(FRONTEND_DIST_ENV)
    assert Path(frontend_dist_dir()).resolve() == (ROOT / "frontend" / "dist").resolve()


def test_an_empty_build_directory_is_not_a_web_ui(tmp_path):
    assert not frontend_available(str(tmp_path))
    (tmp_path / "index.html").write_text("<!doctype html>")
    assert frontend_available(str(tmp_path))


@pytest.mark.parametrize(
    ("client", "host", "expected"),
    [
        ("127.0.0.1", "localhost:3900", "http://localhost:3901"),
        ("::1", "[::1]:3900", "http://localhost:3901"),
        ("127.0.0.1", "127.0.0.1:3900", "http://localhost:3901"),
        # A LAN device addressing the share port.
        ("192.168.1.20", "192.168.1.10:3901", None),
        # A remote client relayed by a loopback proxy (tailscale serve).
        ("127.0.0.1", "studio.tailnet.ts.net", None),
        (None, "localhost:3900", None),
        ("127.0.0.1", "", None),
    ],
)
def test_only_local_browsers_are_sent_to_the_dev_ui(client, host, expected):
    assert dev_ui_redirect(client, host, 3901) == expected


def test_missing_web_ui_explains_itself_to_browsers_and_api_clients():
    media_type, body = web_ui_missing_body("text/html,application/xhtml+xml")
    assert media_type == "text/html" and "bun run build:web" in body
    media_type, body = web_ui_missing_body("application/json")
    assert media_type == "application/json" and '"detail"' in body


@pytest.fixture
def boot(monkeypatch):
    """Reload the real app with a given web UI directory."""
    import main

    def _boot(dist: Path):
        monkeypatch.setenv(FRONTEND_DIST_ENV, str(dist))
        monkeypatch.setenv("OMNIVOICE_MCP_DISABLE", "1")
        importlib.reload(main)
        return main.app

    try:
        yield _boot
    finally:
        monkeypatch.undo()
        importlib.reload(main)  # restore the default app for later tests


def _get(app, client, host, accept="text/html", path="/"):
    from starlette.testclient import TestClient

    with TestClient(
        app, base_url=f"http://{host}", client=(client, 50000), follow_redirects=False
    ) as http:
        return http.get(path, headers={"accept": accept})


def test_lan_device_gets_the_packaged_web_ui(boot, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>studio</title>")
    app = boot(dist)

    response = _get(app, "192.168.1.20", "192.168.1.10:3901")

    assert response.status_code == 200
    assert "<title>studio</title>" in response.text


def test_lan_device_without_web_ui_is_never_redirected_to_localhost(boot, tmp_path):
    app = boot(tmp_path / "missing")

    page = _get(app, "192.168.1.20", "192.168.1.10:3901")
    api = _get(app, "192.168.1.20", "192.168.1.10:3901", accept="application/json")

    assert page.status_code == 503 and "location" not in page.headers
    assert "Web interface unavailable" in page.text
    assert api.status_code == 503 and "build:web" in api.json()["detail"]


def test_local_dev_browser_still_reaches_the_dev_ui(boot, tmp_path):
    app = boot(tmp_path / "missing")

    response = _get(app, "127.0.0.1", "localhost:3900")

    assert response.status_code == 307
    assert response.headers["location"].startswith("http://localhost:")


def test_local_redirect_keeps_the_pin_query(boot, tmp_path):
    # The PIN gate reads ?pin=; dropping it on the dev-UI bounce would sign
    # the local browser out of the share it just opened (#2680).
    app = boot(tmp_path / "missing")

    response = _get(app, "127.0.0.1", "localhost:3900", path="/?pin=123456")

    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:3901?pin=123456"


def test_no_redirect_loop_when_sharing_from_the_ui_port(boot, tmp_path):
    # Default ports put the share listener on the UI port; bouncing there
    # returns the request to itself forever, so answer terminally (#2680).
    app = boot(tmp_path / "missing")

    response = _get(app, "127.0.0.1", "localhost:3901", path="/?pin=123456")

    assert response.status_code == 503
    assert "location" not in response.headers
