"""The telephony gateway: a separate loopback listener for provider webhooks.

Why not the main backend port: a tunnel (cloudflared, ngrok) on this machine
connects to its target from 127.0.0.1, and the main API trusts loopback
callers as the desktop user. Pointing a public tunnel at the main port would
publish the whole API. The gateway is a second, minimal ASGI app in the same
process (so it shares the loaded TTS model) that serves ONLY the telephony
webhook and media-stream routes, each authenticated by the provider's request
signature and a per-call token. The tunnel must forward to this port.

Nothing listens unless the user enabled the integration. This module only
manages the listener; the app it serves is built by
``api.routers.telephony_twilio.build_gateway_app`` (keeping services free of
router imports).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.telephony")

_DEFAULT_PORT = 3950
_PORT_TRIES = 20


_LOOPBACK = "127.0.0.1"


def gateway_host() -> str:
    """Loopback by default. A Docker deployment whose tunnel runs in another
    container may set OMNIVOICE_TWILIO_HOST to a specific IP deliberately
    (docs/integrations/twilio.md). Only IP literals are accepted; anything
    else — including an empty value — falls back to loopback, never to
    "all interfaces"."""
    raw = (os.environ.get("OMNIVOICE_TWILIO_HOST") or _LOOPBACK).strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        logger.warning("Ignoring invalid OMNIVOICE_TWILIO_HOST; using loopback")
        return _LOOPBACK


def gateway_port_base() -> int:
    raw = os.environ.get("OMNIVOICE_TWILIO_PORT")
    try:
        port = int(raw) if raw else _DEFAULT_PORT
    except ValueError:
        return _DEFAULT_PORT
    return port if 0 < port < 65536 else _DEFAULT_PORT


@dataclass
class _Runtime:
    server: Optional[object] = None
    task: Optional[asyncio.Task] = None
    host: str = ""
    port: Optional[int] = None


_runtime = _Runtime()
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _free_port(host: str, base: int) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for port in range(base, min(base + _PORT_TRIES, 65536)):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free telephony port in {base}-{base + _PORT_TRIES - 1}")


def _next_port(host: str) -> Optional[int]:
    try:
        return _free_port(host, gateway_port_base())
    except (OSError, RuntimeError):
        return None


def state() -> dict:
    running = _runtime.server is not None and bool(getattr(_runtime.server, "started", False))
    host = _runtime.host or gateway_host()
    port = _runtime.port if running else None
    # A wildcard bind is reachable locally through loopback; show that.
    display_host = _LOOPBACK if ipaddress.ip_address(host).is_unspecified else host
    if ":" in display_host:
        display_host = f"[{display_host}]"
    return {
        "running": running,
        "host": host,
        "port": port,
        # The port the gateway would bind now (the same probe start() uses),
        # so setup shows the exact tunnel command while calls are still off.
        "preferred_port": port if running else _next_port(host),
        "tunnel_target": f"http://{display_host}:{port}" if running else None,
    }


async def start(app) -> dict:
    """Serve ``app`` (the telephony-only ASGI app) on the gateway port."""
    import uvicorn

    async with _get_lock():
        if _runtime.server is not None:
            return state()
        host = gateway_host()
        port = _free_port(host, gateway_port_base())
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            # Twilio media messages are ~0.3 KB; nothing legitimate is large.
            ws_max_size=64 * 1024,
            limit_concurrency=32,
            # The public hop is the tunnel; never trust forwarded headers.
            proxy_headers=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # never hijack signals in-process
        task = asyncio.create_task(server.serve())
        for _ in range(100):  # ~5 s for the socket to bind
            if getattr(server, "started", False) or task.done():
                break
            await asyncio.sleep(0.05)
        if not getattr(server, "started", False):
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=2)
            except Exception:  # noqa: BLE001 — startup already failed
                pass
            raise RuntimeError("telephony listener failed to start")
        _runtime.server, _runtime.task, _runtime.host, _runtime.port = server, task, host, port
        logger.info("Telephony gateway listening on %s:%s", host, port)
        return state()


async def stop() -> dict:
    async with _get_lock():
        server, task = _runtime.server, _runtime.task
        if server is not None:
            server.should_exit = True
            if task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5)
                except Exception:  # noqa: BLE001
                    logger.warning("Telephony gateway did not stop cleanly")
                    if not task.done():
                        task.cancel()
            logger.info("Telephony gateway stopped")
        _runtime.server = _runtime.task = None
        _runtime.port = None
        return state()
