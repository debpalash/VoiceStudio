"""The sync event stream: "something changed" hints over SSE (desktop-app-guide §8).

The stream never carries content. A ``head`` naming a chosen collection runs a
pull; ``revoked`` stops sync; anything else reconnects with backoff while a
60-second poll keeps sync working.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Optional

from services.cloud_sync.client import CloudOff, SyncApiError

POLL_SECONDS = 60.0
STALL_SECONDS = 60.0
TOO_MANY_STREAMS_WAIT = 60.0
_FATAL = {"device_revoked", "account_suspended", "membership_removed"}


@dataclass
class Event:
    event: str
    id: Optional[str] = None
    data: dict = field(default_factory=dict)


class SseParser:
    """Line-by-line SSE parser; a comment line is reported as a ``ping``."""

    def __init__(self) -> None:
        self._event: Optional[str] = None
        self._id: Optional[str] = None
        self._data: list[str] = []

    def feed(self, line: str) -> Optional[Event]:
        line = line.rstrip("\r")
        if line.startswith(":"):
            return Event("ping")
        if line == "":
            if self._event is None and not self._data:
                return None
            event = Event(self._event or "message", self._id, self._decode("\n".join(self._data)))
            self._event, self._id, self._data = None, None, []
            return event
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            self._event = value
        elif name == "id":
            self._id = value
        elif name == "data":
            self._data.append(value)
        return None

    @staticmethod
    def _decode(raw: str) -> dict:
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def backoff_delay(attempt: int, rng: random.Random) -> float:
    nominal = min(60.0, float(2 ** attempt))
    return nominal * rng.uniform(0.8, 1.2)


class EventStream:
    def __init__(self, open_lines: Callable[[Optional[str]], AsyncIterator[str]], *,
                 on_head: Callable[[list[str]], Awaitable[None]],
                 on_revoked: Callable[[str], None],
                 poll: Callable[[], Awaitable[None]],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 stall_seconds: float = STALL_SECONDS,
                 rng: Optional[random.Random] = None) -> None:
        self._open = open_lines
        self._on_head = on_head
        self._on_revoked = on_revoked
        self._poll = poll
        self._sleep = sleep
        self._stall = stall_seconds
        self._rng = rng or random.Random()
        self.last_event_id: Optional[str] = None

    async def run(self) -> None:
        """Run until sync is switched off or the device loses access."""
        attempt = 0
        while True:
            try:
                outcome = await self._session()
            except CloudOff:
                return
            except SyncApiError as error:
                if error.code in _FATAL:
                    self._on_revoked(error.code)
                    return
                outcome = "failed"
            except Exception:  # noqa: BLE001 — any other drop reconnects (cancellation is not an Exception)
                outcome = "failed"
            if outcome == "stop":
                return
            if outcome == "now":
                attempt = 0
                continue
            if outcome in ("wait", "stalled"):
                # No working stream: poll now (it marks the stream down, so the
                # 60-second fallback keeps running) before reconnecting.
                try:
                    await self._poll()
                except CloudOff:
                    return
                if outcome == "wait":
                    await self._sleep(TOO_MANY_STREAMS_WAIT)
                continue
            try:
                await self._poll()
            except CloudOff:
                return
            await self._sleep(backoff_delay(attempt, self._rng))
            attempt = 0 if outcome == "closed" else attempt + 1

    async def _session(self) -> str:
        parser = SseParser()
        lines = self._open(self.last_event_id).__aiter__()
        while True:
            try:
                line = await asyncio.wait_for(lines.__anext__(), timeout=self._stall)
            except StopAsyncIteration:
                return "closed"
            except asyncio.TimeoutError:
                return "stalled"
            event = parser.feed(line)
            if event is None or event.event == "ping":
                continue
            if event.event == "head":
                if event.id:
                    self.last_event_id = event.id
                await self._on_head(list(event.data.get("collections") or []))
            elif event.event == "bye":
                reason = event.data.get("reason")
                return "wait" if reason == "too_many_streams" else "now"
            elif event.event == "revoked":
                self._on_revoked(str(event.data.get("reason") or "revoked"))
                return "stop"
