"""Same-process LAN share listener + access PIN.

Enabling starts a SECOND uvicorn.Server bound to 0.0.0.0 on a dedicated port,
serving the SAME FastAPI app object — so the loaded model and in-flight jobs
are untouched (no restart). Disabling stops it, closing the 0.0.0.0 socket.
Loopback-only by default: nothing binds 0.0.0.0 until enable() is called.
"""
import asyncio
import ipaddress
import logging
import os
import secrets
import socket
from dataclasses import dataclass, field
from typing import Optional

import psutil
import uvicorn

from core.app_config import DEFAULT_BACKEND_PORT, DEFAULT_WEB_PORT

logger = logging.getLogger("omnivoice.network_share")


def backend_port() -> int:
    """The port the main backend listens on.

    Single source of truth is the ``OMNIVOICE_PORT`` env var (the Electron
    shell sets it and passes the same value to uvicorn's ``--port``). LAN-share
    and Tailscale derive their target ports from this so a user who runs the
    backend on a custom port gets a consistent share/proxy port. Falls back to
    the default on a missing or malformed value — never throws.
    """
    raw = os.environ.get("OMNIVOICE_PORT")
    if raw is None:
        return DEFAULT_BACKEND_PORT
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BACKEND_PORT


def backend_self_url() -> str:
    """Base URL for in-process callers that reach this backend over HTTP.

    ``OMNIVOICE_API_URL`` wins when set (reverse proxy, remote worker).
    Otherwise the URL follows the host and port the backend actually binds —
    ``OMNIVOICE_BIND_HOST`` + ``OMNIVOICE_PORT``, the same variables uvicorn
    and the desktop shells use — so a backend moved off 3900 never calls back
    into a stale default port. Wildcard binds are reached over loopback,
    which the auth gates never challenge.
    """
    override = os.environ.get("OMNIVOICE_API_URL", "").strip().rstrip("/")
    if override:
        return override
    host = os.environ.get("OMNIVOICE_BIND_HOST", "127.0.0.1").strip()
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None  # a hostname ("localhost", a LAN name): use it as given
    if not host or host == "localhost":
        host = "127.0.0.1"
    elif ip is not None and ip.is_unspecified:
        # A wildcard bind also listens on loopback; reach it there.
        host = "::1" if ip.version == 6 else "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{backend_port()}"


def backend_auth_headers(base_url: str) -> dict:
    """Bearer header for an HTTP caller of this backend, if one is warranted.

    ``OMNIVOICE_API_KEY`` is sent only over https or to a loopback host: a
    remote plain-http target would expose the master key on the wire (the
    same rule ``backend.speech_client`` enforces). Loopback never needs it,
    but sending it there is harmless.
    """
    from urllib.parse import urlsplit

    key = os.environ.get("OMNIVOICE_API_KEY", "").strip()
    if not key:
        return {}
    target = urlsplit(base_url)
    host = (target.hostname or "").strip("[]")
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if target.scheme.lower() != "https" and not loopback:
        logger.warning(
            "OMNIVOICE_API_KEY not sent to %s: remote API keys require https://",
            f"{target.scheme}://{target.hostname}",
        )
        return {}
    return {"Authorization": f"Bearer {key}"}


def share_port_base() -> int:
    """The first port LAN sharing tries to bind on 0.0.0.0.

    Defaults to ``backend_port() + 1`` (e.g. 3901 for the default 3900), but
    can be overridden with ``OMNIVOICE_SHARE_PORT``. ``enable()`` probes
    upward from here for a free port. Falls back to the default on a missing
    or malformed value — never throws.
    """
    raw = os.environ.get("OMNIVOICE_SHARE_PORT")
    if raw is None:
        return DEFAULT_WEB_PORT if backend_port() == DEFAULT_BACKEND_PORT else backend_port() + 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WEB_PORT if backend_port() == DEFAULT_BACKEND_PORT else backend_port() + 1


@dataclass
class ShareState:
    enabled: bool = False
    share_port: Optional[int] = None
    pin: Optional[str] = None
    lan_addresses: list = field(default_factory=list)


@dataclass
class _ShareRuntime:
    state: ShareState = field(default_factory=ShareState)
    server: Optional["uvicorn.Server"] = None
    task: Optional["asyncio.Task"] = None
    mcp_allowed_hosts: list[str] = field(default_factory=list)
    mcp_allowed_origins: list[str] = field(default_factory=list)
    lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_runtime = _ShareRuntime()


def _set_mcp_lan_hosts(app, addresses: list[str], *, enabled: bool) -> None:
    """Open MCP's DNS-rebinding allowlist only while PIN-gated sharing runs."""
    security = getattr(app.state, "mcp_transport_security", None)
    if security is None:
        if not enabled:
            _runtime.mcp_allowed_hosts = []
            _runtime.mcp_allowed_origins = []
        return
    if enabled:
        hosts = [f"{address}:*" for address in addresses]
        added_hosts = []
        added_origins = []
        for host in hosts:
            if host not in security.allowed_hosts:
                security.allowed_hosts.append(host)
                added_hosts.append(host)
            for scheme in ("http", "https"):
                origin = f"{scheme}://{host}"
                if origin not in security.allowed_origins:
                    security.allowed_origins.append(origin)
                    added_origins.append(origin)
        _runtime.mcp_allowed_hosts = added_hosts
        _runtime.mcp_allowed_origins = added_origins
        return
    for host in _runtime.mcp_allowed_hosts:
        while host in security.allowed_hosts:
            security.allowed_hosts.remove(host)
    for origin in _runtime.mcp_allowed_origins:
        while origin in security.allowed_origins:
            security.allowed_origins.remove(origin)
    _runtime.mcp_allowed_hosts = []
    _runtime.mcp_allowed_origins = []


# Adapters that exist only on this machine (container bridges, VM host-only
# networks). Their addresses are listed after the real Wi-Fi/Ethernet
# address: a phone on the same Wi-Fi cannot reach 172.17.0.1 or 192.168.56.1
# (#3415). They are kept rather than dropped because a bridged setup
# (Hyper-V external switch, a Linux br0) can carry the real LAN address under
# such a name, so the default-route address on one still ranks first.
_VIRTUAL_ADAPTER_PREFIXES = (
    "docker", "br-", "veth", "virbr", "vboxnet", "vmnet", "vnic", "lxc", "lxd",
    "cni", "flannel", "podman", "awdl", "llw", "anpi", "gif", "stf", "bridge",
)
_VIRTUAL_ADAPTER_WORDS = (
    "virtual", "vethernet", "hyper-v", "wsl", "vmware", "virtualbox", "host-only",
    "npcap", "loopback", "docker",
)
# Tunnels (VPNs, overlays) lead somewhere else, never to the local Wi-Fi.
# A full-tunnel VPN owns the default route, so its address must not win the
# default-route rank: it is always listed last.
_TUNNEL_ADAPTER_PREFIXES = ("utun", "tun", "tap", "wg", "ppp", "ipsec", "zt", "ham")
_TUNNEL_ADAPTER_WORDS = (
    "tailscale", "zerotier", "hamachi", "radmin", "wireguard", "openvpn", "vpn",
    "tap-windows", "anyconnect", "cisco", "fortinet", "forticlient", "globalprotect",
    "pangp", "juniper", "pulse", "nordlynx", "proton", "mullvad", "tunnel",
)
_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")  # Tailscale and carrier NAT
# Any address works as the probe target: connect() on a UDP socket only asks
# the kernel which source address its route would use. No packet is sent.
_ROUTE_PROBE_TARGET = ("192.0.2.1", 9)  # TEST-NET-1 (RFC 5737)


def _is_virtual_adapter(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.startswith(_VIRTUAL_ADAPTER_PREFIXES) or any(
        word in lowered for word in _VIRTUAL_ADAPTER_WORDS
    )


def _is_tunnel(name: str, address: ipaddress.IPv4Address, stats: dict) -> bool:
    """Whether an address sits on a VPN/overlay tunnel rather than the LAN.

    Uses the adapter name, the CGNAT range Tailscale and carrier NAT use, and
    the point-to-point flag psutil reports on macOS and Linux (every
    utun/tun/ppp/WireGuard link has it; Windows reports no flags).
    """
    lowered = name.strip().lower()
    if lowered.startswith(_TUNNEL_ADAPTER_PREFIXES) or any(
        word in lowered for word in _TUNNEL_ADAPTER_WORDS
    ):
        return True
    if address in _CGNAT:
        return True
    flags = str(getattr(stats.get(name), "flags", "") or "")
    return "pointopoint" in flags.split(",")


def _default_route_ipv4() -> Optional[str]:
    """The IPv4 source address of this machine's default route, if any.

    That is the address on the network the user is actually connected to,
    which is the one other devices on that Wi-Fi/Ethernet can reach. Offline
    or with no default route it returns None and ranking falls back to
    adapter kind.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(_ROUTE_PROBE_TARGET)
            address = probe.getsockname()[0]
    except OSError:
        return None
    if not address or address.startswith(("0.", "127.", "169.254.")):
        return None
    return address


def _interface_is_up(stats: dict, name: str) -> bool:
    entry = stats.get(name)
    # Unknown stats (psutil could not read them) never hide an address.
    return entry is None or bool(getattr(entry, "isup", True))


def lan_ipv4_addresses() -> list:
    """IPv4 addresses another device may use to reach this machine, best first.

    The share panel shows one QR code per address and users scan the first
    one, so order matters (#3415): the default-route address (unless a VPN
    owns the route), then private (RFC 1918) addresses on physical adapters,
    then other physical addresses, then container/VM adapters, then VPN and
    overlay tunnels. Loopback, link-local and addresses on
    adapters that are down are never listed.
    """
    try:
        stats = psutil.net_if_stats()
    except Exception:  # noqa: BLE001 — stats are a ranking hint only
        stats = {}
    primary = _default_route_ipv4()
    ranked, seen = [], set()
    for order, (name, addrs) in enumerate(psutil.net_if_addrs().items()):
        if not _interface_is_up(stats, name):
            continue
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            ip = a.address
            try:
                parsed = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
                continue
            if ip in seen:
                continue
            seen.add(ip)
            if _is_tunnel(name, parsed, stats):
                rank = 4
            elif ip == primary:
                rank = 0
            elif _is_virtual_adapter(name):
                rank = 3
            elif parsed.is_private:
                rank = 1
            else:
                rank = 2
            ranked.append((rank, order, ip))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [ip for _rank, _order, ip in ranked]


async def _accepts_connections(address: str, port: int, timeout: float = 1.0) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def self_check(app) -> dict:
    """Where the share listener is bound and whether each address answers.

    Probes come from this machine, so they prove the listener is up on that
    address but cannot see an inbound firewall or Wi-Fi client isolation;
    the UI pairs the result with that hint. Addresses are re-read so a
    network change since enabling (new Wi-Fi, VPN up) shows the current
    URLs instead of stale ones.
    """
    state = _runtime.state
    if not state.enabled or not state.share_port:
        return {"enabled": False, "bind_host": None, "share_port": None, "addresses": []}
    current = lan_ipv4_addresses()
    if current != state.lan_addresses:
        async with _runtime.lifecycle_lock:
            if _runtime.state.enabled and _runtime.state is state:
                _set_mcp_lan_hosts(app, [], enabled=False)
                state.lan_addresses = current
                _set_mcp_lan_hosts(app, current, enabled=True)
    port = state.share_port
    # Probe and report one snapshot: an overlapping check may replace
    # state.lan_addresses while these probes are awaited.
    addresses = list(state.lan_addresses)
    results = await asyncio.gather(
        *(_accepts_connections(address, port) for address in addresses)
    )
    return {
        "enabled": True,
        "bind_host": SHARE_BIND_HOST,
        "share_port": port,
        "addresses": [
            {"address": address, "listening": ok} for address, ok in zip(addresses, results)
        ],
    }


def _gen_pin() -> str:
    return f"{secrets.randbelow(900000) + 100000}"  # 100000-999999


SHARE_BIND_HOST = "0.0.0.0"


def _find_free_port(base: int, tries: int = 20) -> int:
    for p in range(base, base + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((SHARE_BIND_HOST, p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free share port available")


def get_state() -> ShareState:
    return _runtime.state


async def enable(app) -> ShareState:
    async with _runtime.lifecycle_lock:
        return await _enable(app)


async def _enable(app) -> ShareState:
    if _runtime.state.enabled:
        return _runtime.state
    port = _find_free_port(share_port_base())
    pin = _gen_pin()
    config = uvicorn.Config(app, host=SHARE_BIND_HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # never hijack signals in-process
    _runtime.task = asyncio.create_task(server.serve())
    for _ in range(100):  # ~5s for the socket to bind
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.05)
    if not getattr(server, "started", False):
        # Bind failed (e.g. the port was taken in the race after the
        # free-port probe). Tear down and stay Local — never report enabled
        # with a listener that isn't actually up (spec §7).
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(_runtime.task), timeout=2)
        except asyncio.CancelledError:
            if _runtime.task.done():
                _runtime.server = _runtime.task = None
                _runtime.state = ShareState()
            else:
                _runtime.server = server
                _runtime.state = ShareState(True, port, pin, lan_ipv4_addresses())
            app.state.network_share = _runtime.state
            if _runtime.state.enabled:
                _set_mcp_lan_hosts(app, _runtime.state.lan_addresses, enabled=True)
            raise
        except Exception as exc:
            if _runtime.task.done():
                _runtime.server = _runtime.task = None
                _runtime.state = ShareState()
                app.state.network_share = _runtime.state
                raise RuntimeError("share listener failed to start") from exc
            _runtime.server = server
            _runtime.state = ShareState(True, port, pin, lan_ipv4_addresses())
            app.state.network_share = _runtime.state
            _set_mcp_lan_hosts(app, _runtime.state.lan_addresses, enabled=True)
            logger.warning("Failed LAN listener startup could not be cleaned up")
            raise RuntimeError(
                "LAN share listener could not be stopped. Retry Disable before enabling again."
            ) from exc
        _runtime.server = _runtime.task = None
        raise RuntimeError("share listener failed to start")
    _runtime.server = server
    _runtime.state = ShareState(True, port, pin, lan_ipv4_addresses())
    app.state.network_share = _runtime.state
    _set_mcp_lan_hosts(app, _runtime.state.lan_addresses, enabled=True)
    return _runtime.state


async def disable(app) -> ShareState:
    async with _runtime.lifecycle_lock:
        return await _disable(app)


async def _disable(app) -> ShareState:
    if _runtime.server is not None:
        _runtime.server.should_exit = True
        if _runtime.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(_runtime.task), timeout=5)
            except Exception as exc:
                logger.warning("LAN share listener did not stop; retaining enabled state")
                raise RuntimeError(
                    "LAN sharing could not be disabled. Retry after active connections close."
                ) from exc
    _set_mcp_lan_hosts(app, [], enabled=False)
    _runtime.server = _runtime.task = None
    _runtime.state = ShareState()
    app.state.network_share = _runtime.state
    return _runtime.state
