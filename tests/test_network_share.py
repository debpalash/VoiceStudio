import socket
from types import SimpleNamespace
from unittest.mock import patch
from services import network_share as ns


def _addr(ip):
    class A:  # mimic psutil snicaddr
        family = socket.AF_INET
        address = ip
    return A()


def test_lan_ipv4_filters_loopback_and_linklocal():
    fake = {
        "lo0": [_addr("127.0.0.1")],
        "en0": [_addr("192.168.1.42")],
        "en1": [_addr("169.254.5.5"), _addr("10.0.0.9")],
    }
    with patch("services.network_share.psutil.net_if_addrs", return_value=fake):
        out = ns.lan_ipv4_addresses()
    assert out == ["192.168.1.42", "10.0.0.9"]


def test_gen_pin_is_six_digits():
    pin = ns._gen_pin()
    assert pin.isdigit() and len(pin) == 6


def test_mcp_lan_hosts_exist_only_while_pin_gated_sharing_is_enabled():
    security = SimpleNamespace(
        allowed_hosts=["localhost:*", "10.0.0.9:*"],
        allowed_origins=["http://localhost:*", "http://10.0.0.9:*"],
    )
    app = SimpleNamespace(state=SimpleNamespace(mcp_transport_security=security))
    ns._runtime.mcp_allowed_hosts = []
    try:
        ns._set_mcp_lan_hosts(app, ["192.168.1.42", "10.0.0.9"], enabled=True)
        assert "192.168.1.42:*" in security.allowed_hosts
        assert "http://192.168.1.42:*" in security.allowed_origins
        assert "https://10.0.0.9:*" in security.allowed_origins

        ns._set_mcp_lan_hosts(app, [], enabled=False)
        # Entries supplied independently (for example through
        # OMNIVOICE_MCP_ALLOWED_HOSTS) survive the sharing lifecycle.
        assert security.allowed_hosts == ["localhost:*", "10.0.0.9:*"]
        assert security.allowed_origins == ["http://localhost:*", "http://10.0.0.9:*"]
    finally:
        ns._runtime.mcp_allowed_hosts = []
        ns._runtime.mcp_allowed_origins = []


# ── Configurable ports (issue: user-configurable network ports) ──────────────

def test_backend_port_defaults_to_3900(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    assert ns.backend_port() == 3900


def test_backend_port_honors_env(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PORT", "4000")
    assert ns.backend_port() == 4000


def test_backend_port_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PORT", "not-a-number")
    assert ns.backend_port() == 3900


def test_share_port_base_defaults_to_backend_plus_one(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_SHARE_PORT", raising=False)
    monkeypatch.setenv("OMNIVOICE_PORT", "4000")
    assert ns.share_port_base() == 4001


def test_share_port_base_honors_env(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SHARE_PORT", "5500")
    assert ns.share_port_base() == 5500


def test_share_port_base_bad_env_falls_back(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_PORT", raising=False)
    monkeypatch.setenv("OMNIVOICE_SHARE_PORT", "garbage")
    assert ns.share_port_base() == 3901


from fastapi.testclient import TestClient


def _loopback_client():
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def test_network_state_endpoint_defaults_disabled():
    c = _loopback_client()
    r = c.get("/system/network/state")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_pin_only_remote_discovery_never_returns_share_pin(monkeypatch):
    import importlib

    from main import app

    # Resolve the exact module instance held by the live router. The full suite
    # deliberately replaces app modules in sys.modules, so the module-level
    # ``ns`` test helper may no longer be the endpoint's dependency.
    live_network_share = importlib.import_module("api.routers.system").network_share

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    monkeypatch.setattr(live_network_share._runtime, "state",
                        live_network_share.ShareState(True, 3901, "123456", ["192.168.1.10"]))
    # Keep the consumption middleware inert: this endpoint is testing the
    # intentional admin read-only exception itself, before a PIN is supplied.
    monkeypatch.setattr(app.state, "network_share", None, raising=False)
    response = TestClient(app, client=("172.17.0.1", 50000)).get(
        "/system/network/state"
    )
    assert response.status_code == 200
    assert response.json()["pin"] is None
    assert response.json()["pin_required"] is True
    assert "123456" not in response.text


def test_network_control_rejects_non_loopback():
    from main import app
    c = TestClient(app, client=("10.0.0.5", 9999))
    assert c.post("/system/network/enable").status_code == 403


def test_system_info_has_sharing_fields():
    c = _loopback_client()
    body = c.get("/system/info").json()
    for k in ("share_enabled", "share_port", "lan_addresses", "pin_required"):
        assert k in body


def test_system_info_has_port_fields():
    c = _loopback_client()
    body = c.get("/system/info").json()
    for k in ("backend_port", "share_port_base", "ui_port"):
        assert k in body
    # Defaults when no env override is set.
    assert isinstance(body["backend_port"], int)
    assert isinstance(body["share_port_base"], int)
    assert isinstance(body["ui_port"], int)


def test_set_env_share_port_rejects_non_numeric():
    c = _loopback_client()
    r = c.post("/system/set-env", json={"key": "OMNIVOICE_SHARE_PORT", "value": "abc"})
    assert r.status_code == 400


def test_set_env_share_port_rejects_out_of_range():
    c = _loopback_client()
    r = c.post("/system/set-env", json={"key": "OMNIVOICE_SHARE_PORT", "value": "80"})
    assert r.status_code == 400
    r = c.post("/system/set-env", json={"key": "OMNIVOICE_SHARE_PORT", "value": "70000"})
    assert r.status_code == 400


def test_set_env_share_port_accepts_valid(monkeypatch):
    c = _loopback_client()
    r = c.post("/system/set-env", json={"key": "OMNIVOICE_SHARE_PORT", "value": "5050"})
    assert r.status_code == 200
    assert r.json()["set"] is True
    # Clean up the process-level env mutation so other tests aren't affected.
    import os
    os.environ.pop("OMNIVOICE_SHARE_PORT", None)


def test_system_info_reports_no_ui_port_for_the_packaged_desktop(monkeypatch):
    """The packaged app serves app://voicestudio; there is no UI port to show."""
    for name in ("OMNIVOICE_UI_PORT", "VOICESTUDIO_UI_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    assert _loopback_client().get("/system/info").json()["ui_port"] is None

    # The Electron dev shell passes its renderer port explicitly.
    monkeypatch.setenv("OMNIVOICE_UI_PORT", "3902")
    assert _loopback_client().get("/system/info").json()["ui_port"] == 3902


def test_system_info_reports_the_ui_port_alias(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    monkeypatch.setenv("VOICESTUDIO_UI_PORT", "4100")
    assert _loopback_client().get("/system/info").json()["ui_port"] == 4100


def test_ports_bound_by_other_processes_are_not_persisted():
    """The desktop shell, dev script and Docker bind these from their own
    environment; a saved value only made the backend misreport its ports."""
    c = _loopback_client()
    for key in ("OMNIVOICE_PORT", "OMNIVOICE_UI_PORT"):
        r = c.post("/system/set-env", json={"key": key, "value": "4000"})
        assert r.status_code == 400


def test_stale_saved_bound_ports_are_not_restored(monkeypatch):
    from core import prefs

    for key in ("OMNIVOICE_PORT", "OMNIVOICE_UI_PORT", "OMNIVOICE_SHARE_PORT"):
        monkeypatch.delenv(key, raising=False)
    prefs.restore_env({
        "env.OMNIVOICE_PORT": "4000",
        "env.OMNIVOICE_UI_PORT": "4100",
        "env.OMNIVOICE_SHARE_PORT": "5050",
    })
    import os
    try:
        assert "OMNIVOICE_PORT" not in os.environ
        assert "OMNIVOICE_UI_PORT" not in os.environ
        assert os.environ["OMNIVOICE_SHARE_PORT"] == "5050"
    finally:
        os.environ.pop("OMNIVOICE_SHARE_PORT", None)
