"""OMNIVOICE_TRUST_HOST_LOOPBACK: the Docker host's own browser may skip the
share PIN (#3003), and nothing else may.

Inside a container the host's 127.0.0.1 browser arrives from the default
gateway (the bridge gateway, e.g. 172.17.0.1). The opt-in trusts exactly that
socket address, only in server mode, only on the main listener, and only in
place of the PIN.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services import network_share as ns

GATEWAY = "172.17.0.1"
LAN = "192.168.1.50"
PIN = "654321"

# /proc/net/route: default route via 172.17.0.1 (little-endian 010011AC),
# plus the on-link bridge subnet, which must not count as a gateway.
ROUTE_V4 = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)
# /proc/net/ipv6_route: default route via fd00::1.
ROUTE_V6 = (
    "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
    "fd000000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
)


def _auth_globals():
    from main import is_trusted_host_loopback

    return is_trusted_host_loopback.__globals__


@pytest.fixture
def routes(tmp_path, monkeypatch):
    v4 = tmp_path / "route"
    v4.write_text(ROUTE_V4)
    v6 = tmp_path / "ipv6_route"
    v6.write_text(ROUTE_V6)
    globs = _auth_globals()
    monkeypatch.setitem(globs, "_PROC_ROUTE_V4", str(v4))
    monkeypatch.setitem(globs, "_PROC_ROUTE_V6", str(v6))
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_TRUST_HOST_LOOPBACK", raising=False)
    return v4


@pytest.fixture
def trusted(routes, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUST_HOST_LOOPBACK", "1")
    return routes


def _client(host):
    from main import app

    app.state.network_share = ns.ShareState(
        enabled=True, share_port=3901, pin=PIN, lan_addresses=["10.0.0.9"]
    )
    return TestClient(app, client=(host, 40000))


def teardown_function():
    from main import app
    from services.admin_sessions import admin_session_store

    app.state.network_share = ns.ShareState()
    admin_session_store.clear()


def _pin_required(response) -> bool:
    return response.status_code == 401 and response.json() == {"detail": "PIN required"}


def test_unset_host_gateway_still_needs_pin(routes):
    assert _pin_required(_client(GATEWAY).get("/v1/audio/voices"))


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_falsy_values_keep_the_pin(routes, monkeypatch, value):
    monkeypatch.setenv("OMNIVOICE_TRUST_HOST_LOOPBACK", value)
    assert _pin_required(_client(GATEWAY).get("/v1/audio/voices"))


def test_set_host_gateway_skips_pin(trusted):
    response = _client(GATEWAY).get("/v1/audio/voices")
    assert response.status_code == 200
    assert "ov_pin" not in response.headers.get("set-cookie", "")


def test_set_ipv4_mapped_host_gateway_skips_pin(trusted):
    assert _client(f"::ffff:{GATEWAY}").get("/v1/audio/voices").status_code == 200


def test_set_ipv6_default_gateway_skips_pin(trusted):
    assert _client("fd00::1").get("/v1/audio/voices").status_code == 200


def test_set_lan_client_still_needs_pin(trusted):
    assert _pin_required(_client(LAN).get("/v1/audio/voices"))


def test_set_other_bridge_container_still_needs_pin(trusted):
    # Same subnet as the gateway, but not the gateway itself.
    assert _pin_required(_client("172.17.0.3").get("/v1/audio/voices"))


def test_set_lan_client_with_valid_pin_still_passes(trusted):
    response = _client(LAN).get("/v1/audio/voices", headers={"X-OmniVoice-Pin": PIN})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forwarded-For": GATEWAY},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Forwarded-For": f"{LAN}, {GATEWAY}"},
        {"X-Real-IP": GATEWAY},
        {"Forwarded": f"for={GATEWAY}"},
        {"Forwarded": 'for="[::1]"'},
    ],
)
def test_set_spoofed_forwarded_headers_still_need_pin(trusted, headers):
    assert _pin_required(_client(LAN).get("/v1/audio/voices", headers=headers))


def test_uvicorn_proxy_headers_do_not_trust_the_gateway_by_default(trusted):
    """The real server chain: Uvicorn's ProxyHeadersMiddleware (on by default,
    trusting only 127.0.0.1) must leave a LAN peer's spoofed header unused."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from main import app

    app.state.network_share = ns.ShareState(enabled=True, share_port=3901, pin=PIN)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client = TestClient(wrapped, client=(LAN, 40000))
    response = client.get("/v1/audio/voices", headers={"X-Forwarded-For": GATEWAY})
    assert _pin_required(response)


def test_requires_server_mode(trusted, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    assert _pin_required(_client(GATEWAY).get("/v1/audio/voices"))


def test_missing_route_table_fails_closed(trusted, tmp_path, monkeypatch):
    globs = _auth_globals()
    monkeypatch.setitem(globs, "_PROC_ROUTE_V4", str(tmp_path / "absent"))
    monkeypatch.setitem(globs, "_PROC_ROUTE_V6", str(tmp_path / "absent6"))
    assert _pin_required(_client(GATEWAY).get("/v1/audio/voices"))


def test_does_not_replace_the_api_key(trusted, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret-key")
    response = _client(GATEWAY).get("/v1/audio/voices")
    assert response.status_code == 401
    assert response.json() == {"detail": "API key required"}


def test_never_reaches_admin_routes(trusted):
    response = _client(GATEWAY).post("/system/set-env", json={"key": "X", "value": "1"})
    assert response.status_code in {401, 403}


def test_share_listener_never_qualifies(trusted):
    from types import SimpleNamespace

    check = _auth_globals()["is_trusted_host_loopback"]
    share = SimpleNamespace(pin=PIN, share_port=3901)

    def conn(port):
        return SimpleNamespace(
            client=SimpleNamespace(host=GATEWAY),
            app=SimpleNamespace(state=SimpleNamespace(network_share=share)),
            scope={"type": "http", "client": (GATEWAY, 1), "server": ("172.17.0.2", port)},
        )

    assert check(conn(3900)) is True
    assert check(conn(3901)) is False


def test_principal_is_pin_tier_only(trusted):
    from types import SimpleNamespace

    globs = _auth_globals()
    connection = SimpleNamespace(
        client=SimpleNamespace(host=GATEWAY),
        app=SimpleNamespace(state=SimpleNamespace(network_share=SimpleNamespace(pin=PIN, share_port=3901))),
        headers={},
        query_params={},
        cookies={},
        scope={"type": "http", "path": "/v1/audio/voices", "state": {}, "client": (GATEWAY, 1)},
    )
    principal = globs["resolve_principal"](connection)
    assert principal.kind == globs["PrincipalKind"].PIN
    assert principal.capabilities == globs["CONSUME_CAPABILITIES"]
