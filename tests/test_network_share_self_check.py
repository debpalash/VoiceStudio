"""LAN share address ranking and self-check (#3415).

Users scan the first QR code the share panel shows. When it encoded a
container bridge, VM host-only or VPN address, the phone on the same Wi-Fi
got "could not connect".
"""
import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient



def _ns():
    """Resolve at call time: suites that purge and re-import ``services.*``
    mid-run would otherwise leave a module-level alias pointing at a stale
    copy, so a patch on ``sys.modules`` misses the code under test."""
    import importlib

    return importlib.import_module("services.network_share")


ns = None


@pytest.fixture(autouse=True)
def _current_network_share():
    """Every test sees the module the code under test actually runs."""
    global ns
    ns = _ns()
    yield


def _addr(ip):
    return SimpleNamespace(family=socket.AF_INET, address=ip)


def _lan(fake, stats=None, primary=None):
    with patch.object(ns.psutil, "net_if_addrs", return_value=fake), patch.object(
        ns.psutil, "net_if_stats", return_value=stats or {}
    ), patch.object(ns, "_default_route_ipv4", return_value=primary):
        return ns.lan_ipv4_addresses()


def test_real_lan_address_comes_before_virtual_adapters():
    fake = {
        "vEthernet (WSL)": [_addr("172.28.16.1")],
        "VirtualBox Host-Only Network": [_addr("192.168.56.1")],
        "docker0": [_addr("172.17.0.1")],
        "Tailscale": [_addr("100.101.102.103")],
        "utun4": [_addr("10.8.0.2")],
        "Wi-Fi": [_addr("192.168.1.42")],
    }
    out = _lan(fake)
    assert out[0] == "192.168.1.42"
    # Nothing is dropped: a bridged setup may carry the real LAN under such a name.
    assert sorted(out) == sorted(a.address for addrs in fake.values() for a in addrs)


def test_default_route_address_ranks_first_even_on_a_bridge():
    fake = {
        "en0": [_addr("10.0.0.9")],
        "vEthernet (External Switch)": [_addr("192.168.1.42")],
    }
    assert _lan(fake, primary="192.168.1.42") == ["192.168.1.42", "10.0.0.9"]


def test_full_tunnel_vpn_on_the_default_route_never_ranks_first():
    """A VPN owning the default route must not push the Wi-Fi address down."""
    fake = {
        "utun4": [_addr("10.8.0.2")],
        "Wi-Fi": [_addr("192.168.1.42")],
        "docker0": [_addr("172.17.0.1")],
    }
    assert _lan(fake, primary="10.8.0.2") == ["192.168.1.42", "172.17.0.1", "10.8.0.2"]


def test_windows_vpn_adapters_and_cgnat_rank_after_the_lan():
    fake = {
        "Cisco AnyConnect Secure Mobility Client": [_addr("10.20.30.40")],
        "OpenVPN TAP-Windows6": [_addr("10.9.0.6")],
        "Ethernet 3": [_addr("100.72.1.9")],
        "Wi-Fi": [_addr("192.168.1.42")],
    }
    for primary in ("10.20.30.40", "10.9.0.6", "100.72.1.9"):
        assert _lan(fake, primary=primary)[0] == "192.168.1.42"


def test_point_to_point_links_are_tunnels_whatever_their_name():
    fake = {"corpnet0": [_addr("10.1.2.3")], "en0": [_addr("192.168.1.42")]}
    stats = {
        "corpnet0": SimpleNamespace(isup=True, flags="up,pointopoint,running"),
        "en0": SimpleNamespace(isup=True, flags="up,broadcast,running"),
    }
    assert _lan(fake, stats=stats, primary="10.1.2.3") == ["192.168.1.42", "10.1.2.3"]


def test_private_addresses_come_before_public_ones_on_physical_adapters():
    fake = {"eth0": [_addr("81.2.69.160")], "eth1": [_addr("192.168.1.42")]}
    assert _lan(fake) == ["192.168.1.42", "81.2.69.160"]


def test_adapters_that_are_down_are_not_listed():
    fake = {"Ethernet": [_addr("192.168.0.20")], "Wi-Fi": [_addr("192.168.1.42")]}
    stats = {"Ethernet": SimpleNamespace(isup=False), "Wi-Fi": SimpleNamespace(isup=True)}
    assert _lan(fake, stats=stats) == ["192.168.1.42"]


def test_unreadable_interface_stats_never_hide_addresses():
    fake = {"en0": [_addr("192.168.1.42")]}
    with patch.object(ns.psutil, "net_if_addrs", return_value=fake), patch.object(
        ns.psutil, "net_if_stats", side_effect=OSError("denied")
    ), patch.object(ns, "_default_route_ipv4", return_value=None):
        assert ns.lan_ipv4_addresses() == ["192.168.1.42"]


def test_default_route_probe_returns_none_offline():
    class Offline:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def connect(self, _target):
            raise OSError("Network is unreachable")

    with patch.object(ns.socket, "socket", Offline):
        assert ns._default_route_ipv4() is None


def test_self_check_reports_bind_port_and_listener_per_address(monkeypatch):
    real_probe = ns._accepts_connections

    async def probe(address, port):
        if address == "127.0.0.1":
            return await real_probe(address, port)
        return False

    async def exercise():
        server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        state = ns.ShareState(True, port, "123456", ["127.0.0.1"])
        monkeypatch.setattr(ns, "_runtime", ns._ShareRuntime(state=state))
        monkeypatch.setattr(ns, "lan_ipv4_addresses", lambda: ["127.0.0.1", "192.0.2.1"])
        monkeypatch.setattr(ns, "_accepts_connections", probe)
        try:
            return port, await ns.self_check(SimpleNamespace(state=SimpleNamespace()))
        finally:
            server.close()
            await server.wait_closed()

    port, result = asyncio.run(exercise())
    assert result == {
        "enabled": True,
        "bind_host": "0.0.0.0",
        "share_port": port,
        "addresses": [
            {"address": "127.0.0.1", "listening": True},
            {"address": "192.0.2.1", "listening": False},
        ],
    }
    # A network change since enabling replaces the stale address list.
    assert ns.get_state().lan_addresses == ["127.0.0.1", "192.0.2.1"]


def test_overlapping_check_cannot_mix_addresses_and_results(monkeypatch):
    """Results are paired with the address list that was probed."""
    state = ns.ShareState(True, 3901, "123456", ["192.168.1.42", "172.17.0.1"])
    monkeypatch.setattr(ns, "_runtime", ns._ShareRuntime(state=state))
    monkeypatch.setattr(ns, "lan_ipv4_addresses", lambda: ["192.168.1.42", "172.17.0.1"])

    async def probe(address, _port):
        # Another check replaces the list while this one is probing.
        state.lan_addresses = ["10.0.0.7"]
        await asyncio.sleep(0)
        return address == "192.168.1.42"

    monkeypatch.setattr(ns, "_accepts_connections", probe)
    result = asyncio.run(ns.self_check(SimpleNamespace(state=SimpleNamespace())))
    assert result["addresses"] == [
        {"address": "192.168.1.42", "listening": True},
        {"address": "172.17.0.1", "listening": False},
    ]


def test_closed_port_is_reported_as_not_listening():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert asyncio.run(ns._accepts_connections("127.0.0.1", port)) is False


def test_self_check_when_sharing_is_off(monkeypatch):
    monkeypatch.setattr(ns, "_runtime", ns._ShareRuntime())
    result = asyncio.run(ns.self_check(SimpleNamespace(state=SimpleNamespace())))
    assert result == {"enabled": False, "bind_host": None, "share_port": None, "addresses": []}


def test_network_check_endpoint_is_loopback_only(monkeypatch):
    from main import app

    monkeypatch.setattr(ns, "_runtime", ns._ShareRuntime())
    remote = TestClient(app, client=("10.0.0.5", 9999))
    assert remote.get("/system/network/check").status_code == 403
    assert TestClient(app, client=("127.0.0.1", 50000)).get("/system/network/check").json()["enabled"] is False
