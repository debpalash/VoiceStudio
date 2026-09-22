"""The telephony gateway: a separate loopback listener for provider webhooks.

Why not the main backend port: a tunnel (cloudflared, ngrok) on this machine
connects to its target from 127.0.0.1, and the main API trusts loopback
callers as the desktop user. Pointing a public tunnel at the main port would
publish the whole API. The gateway is a second, minimal ASGI app in the same
process (so it shares the loaded TTS model) that serves ONLY the telephony
webhook and media-stream routes, each authenticated by the provider's request
signature and a per-call token. The tunnel must forward to this port.

Nothing listens unless the user enabled the integration.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.telephony")

_DEFAULT_PORT = 3950
_PORT_TRIES = 20


def gateway_host() -> str:
    """Loopback by default. Docker deployments, where the tunnel runs in
    another container, may set OMNIVOICE_TWILIO_HOST=0.0.0.0 deliberately."""
    return os.environ.get("OMNIVOICE_TWILIO_HOST", "").strip() or "127.0.0.1"


def gateway_port_base() -> int:
    raw = os.environ.get("OMNIVOICE_TWILIO_PORT")
    try:
        port = int(raw) if raw else _DEFAULT_PORT
    except ValueError:
        return _DEFAULT_PORT
    return port if 0 < port < 65536 else _DEFAULT_PORT


def build_app():
    """The gateway ASGI app: telephony routes and nothing else."""
    from fastapi import FastAPI

    from api.routers.telephony_twilio import webhook_router

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(webhook_router)
    return app


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


def state() -> dict:
    running = _runtime.server is not None and bool(getattr(_runtime.server, "started", False))
    host = _runtime.host or gateway_host()
    port = _runtime.port if running else None
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return {
        "running": running,
        "host": host,
        "port": port,
        "tunnel_target": f"http://{display_host}:{port}" if running else None,
    }


async def start() -> dict:
    import uvicorn

    async with _get_lock():
        if _runtime.server is not None:
            return state()
        host = gateway_host()
        port = _free_port(host, gateway_port_base())
        config = uvicorn.Config(
            build_app(),
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


async def start_if_enabled() -> None:
    """Backend startup: resume the listener when the user left it enabled."""
    from services.telephony import config

    try:
        cfg = config.load()
    except Exception:  # noqa: BLE001 — settings unreadable: stay off
        return
    if cfg.enabled:
        try:
            await start()
        except Exception as exc:  # noqa: BLE001 — never block startup
            logger.warning("Telephony gateway not started: %s", exc)
