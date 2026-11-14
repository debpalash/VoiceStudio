"""WebSocket endpoint for real-time sidebar events.

A single ``/ws/events`` connection replaces all sidebar polling. The
frontend connects once and receives JSON messages like:

    {"kind": "projects", "ts": 1714200000.0}
    {"kind": "profiles", "ts": 1714200001.2, "id": "abc123"}

On each message the frontend invalidates the matching TanStack Query
cache key, which triggers a single targeted refetch.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import require_ws_access
from core import event_bus

router = APIRouter()
logger = logging.getLogger("omnivoice.events")


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Fan-out event stream for sidebar reactivity.

    Protocol:
    - Server → Client: JSON event dicts (``kind``, ``ts``, optional fields)
    - Client → Server: ping/pong only (no app-level messages expected)
    - Server sends ``{"kind": "ping"}`` every 25 s as a keepalive
    """
    if not await require_ws_access(ws):
        return

    await ws.accept()
    q = await event_bus.subscribe()
    logger.info("WS client connected (%d total)", len(event_bus._listeners))
    try:
        while True:
            # Wait for an event or send a keepalive ping every 25s
            try:
                event_str = await asyncio.wait_for(q.get(), timeout=25.0)
                await ws.send_text(event_str)
            except asyncio.TimeoutError:
                # Keepalive — prevents proxies/firewalls from killing idle connections
                await ws.send_text('{"kind":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS client error: %s", e)
    finally:
        await event_bus.unsubscribe(q)
        logger.info("WS client disconnected (%d remaining)", len(event_bus._listeners))
