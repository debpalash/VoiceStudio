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

_DEFAULT_BACKEND_PORT = 3900  # must match backend/main.py uvicorn.run(port=...)
logger = logging.getLogger("omnivoice.network_share")


def backend_port() -> int:
    """The port the main backend listens on.

    Single source of truth is the ``OMNIVOICE_PORT`` env var (read by the Rust
    sidecar at startup and passed through to uvicorn's ``--port``). LAN-share
    and Tailscale derive their target ports from this so a user who runs the
    backend on a custom port gets a consistent share/proxy port. Falls back to
    the default on a missing or malformed value — never throws.
    """
    raw = os.environ.get("OMNIVOICE_PORT")
    if raw is None:
        return _DEFAULT_BACKEND_PORT
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BACKEND_PORT


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
        return backend_port() + 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return backend_port() + 1


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


_runtime = _ShareRuntime()


def lan_ipv4_addresses() -> list:
    out, seen = [], set()
    for _name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AF_INET:
                ip = a.address
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                if ip not in seen:
                    seen.add(ip)
                    out.append(ip)
    return out


def _gen_pin() -> str:
    return f"{secrets.randbelow(900000) + 100000}"  # 100000-999999


def _find_free_port(base: int, tries: int = 20) -> int:
    for p in range(base, base + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free share port available")


def get_state() -> ShareState:
    return _runtime.state


async def enable(app) -> ShareState:
    if _runtime.state.enabled:
        return _runtime.state
    port = _find_free_port(share_port_base())
    pin = _gen_pin()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
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
            logger.warning("Failed LAN listener startup could not be cleaned up")
            raise RuntimeError(
                "LAN share listener could not be stopped. Retry Disable before enabling again."
            ) from exc
        _runtime.server = _runtime.task = None
        raise RuntimeError("share listener failed to start")
    _runtime.server = server
    _runtime.state = ShareState(True, port, pin, lan_ipv4_addresses())
    app.state.network_share = _runtime.state
    return _runtime.state


async def disable(app) -> ShareState:
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
    _runtime.server = _runtime.task = None
    _runtime.state = ShareState()
    app.state.network_share = _runtime.state
    return _runtime.state
