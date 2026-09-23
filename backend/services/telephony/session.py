"""Provider-agnostic phone-call media session.

A provider adapter (today only :mod:`services.telephony.twilio`) translates its
wire protocol into :class:`StreamEvent` and back; this module owns everything
else: per-call WebSocket tokens, concurrency and rate limits, the recent-call
log shown in the app, the rendered-greeting cache, and the call loop itself.

Nothing here stores phone numbers or audio from the caller. Call identifiers
are kept only as a masked suffix for the status list.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

logger = logging.getLogger("omnivoice.telephony")

END_MARK = "voicestudio-end"
START_TIMEOUT_S = 10.0
TOKEN_TTL_S = 60.0


@dataclass(frozen=True)
class StreamEvent:
    kind: str  # connected | start | media | mark | stop
    stream_id: str = ""
    call_id: str = ""
    account_id: str = ""
    params: dict = field(default_factory=dict)
    payload: bytes = b""
    track: str = ""
    mark: str = ""


class MediaStreamProvider(Protocol):
    """What a telephony provider adapter must supply (see ``twilio.py``)."""

    NAME: str

    def parse_event(self, message: dict) -> StreamEvent | None:
        """Normalize one inbound provider message; None for unknown events."""

    def media_message(self, stream_id: str, payload: bytes) -> dict:
        """An outbound frame carrying 8 kHz μ-law ``payload``."""

    def mark_message(self, stream_id: str, name: str) -> dict:
        """An outbound marker the provider echoes once playback reaches it."""

    def clear_message(self, stream_id: str) -> dict:
        """Drop audio the provider has buffered but not played (barge-in)."""


def mask_call_id(call_id: str) -> str:
    return f"…{call_id[-4:]}" if len(call_id) >= 4 else "…"


def token_subject(call_id: str, session_id: str = "") -> str:
    """What a stream token is bound to: the provider's call id, plus the call
    agent's session id when the TwiML named one (so a token issued for one
    agent call can never start another session)."""
    return f"{call_id}|{session_id}" if session_id else call_id


# ── Per-call WebSocket tokens ───────────────────────────────────────────────
# Twilio signs the webhook but not the WebSocket upgrade, so the TwiML carries
# a random single-use token as a <Stream> custom parameter. The stream's
# `start` event must present it for the same CallSid within TOKEN_TTL_S.


class CallTokens:
    def __init__(self, ttl_s: float = TOKEN_TTL_S, max_pending: int = 64, clock=time.monotonic):
        self._ttl = ttl_s
        self._max = max_pending
        self._clock = clock
        self._pending: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        #: Bumped by reset(). A webhook captures it on arrival and may only
        #: issue a token if no disable happened while it was in flight.
        self.epoch = 0

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _prune(self, now: float) -> None:
        for key in [k for k, (_c, exp) in self._pending.items() if exp <= now]:
            del self._pending[key]
        while len(self._pending) > self._max:
            self._pending.popitem(last=False)

    def issue(self, call_id: str, epoch: int | None = None) -> str | None:
        """New single-use token for ``call_id``; None if ``epoch`` is stale
        (the integration was turned off after the request arrived)."""
        token = secrets.token_urlsafe(24)
        with self._lock:
            if epoch is not None and epoch != self.epoch:
                return None
            now = self._clock()
            self._pending[self._digest(token)] = (call_id, now + self._ttl)
            self._prune(now)
        return token

    def pending(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._pending)

    def redeem(self, token: str, call_id: str) -> bool:
        """Single use: a token is consumed by its first presentation."""
        if not token or not call_id:
            return False
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._pending.pop(self._digest(token), None)
        if entry is None:
            return False
        return secrets.compare_digest(entry[0].encode(), call_id.encode())

    def reset(self) -> None:
        with self._lock:
            self._pending.clear()
            self.epoch += 1


# ── Limits + recent-call log ────────────────────────────────────────────────


class SlidingWindow:
    def __init__(self, window_s: float = 60.0, clock=time.monotonic):
        self._window = window_s
        self._clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self) -> None:
        now = self._clock()
        while self._hits and self._hits[0] <= now - self._window:
            self._hits.popleft()

    def exceeded(self, limit: int) -> bool:
        with self._lock:
            self._prune()
            return len(self._hits) >= limit

    def hit(self) -> None:
        with self._lock:
            self._hits.append(self._clock())

    def allow(self, limit: int) -> bool:
        """Record a hit unless the window is already full."""
        with self._lock:
            self._prune()
            if len(self._hits) >= limit:
                return False
            self._hits.append(self._clock())
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


@dataclass
class CallRecord:
    call: str
    started_at: float
    ended_at: float | None = None
    outcome: str = "in_progress"
    audio_seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "call": self.call,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "audio_seconds": round(self.audio_seconds, 2),
        }


class CallRegistry:
    """Active-call slots plus the last ``keep`` outcomes (in memory only)."""

    def __init__(self, keep: int = 20):
        self._active: dict[int, CallRecord] = {}
        self._recent: deque[CallRecord] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def active(self) -> int:
        with self._lock:
            return len(self._active)

    def has_capacity(self, limit: int, pending: int = 0) -> bool:
        with self._lock:
            return len(self._active) + pending < limit

    def begin(self, call_id: str, limit: int) -> CallRecord | None:
        with self._lock:
            if len(self._active) >= limit:
                return None
            record = CallRecord(call=mask_call_id(call_id), started_at=time.time())
            self._active[id(record)] = record
            return record

    def end(self, record: CallRecord, outcome: str) -> None:
        with self._lock:
            self._active.pop(id(record), None)
            record.outcome = outcome
            record.ended_at = time.time()
            self._recent.appendleft(record)

    def note(self, outcome: str, call_id: str = "") -> None:
        """Log a call that never reached the media stage (busy, rejected…)."""
        now = time.time()
        with self._lock:
            self._recent.appendleft(
                CallRecord(call=mask_call_id(call_id), started_at=now, ended_at=now, outcome=outcome)
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active": len(self._active),
                "recent": [r.as_dict() for r in list(self._active.values()) + list(self._recent)][:20],
            }

    def reset(self) -> None:
        with self._lock:
            self._active.clear()
            self._recent.clear()


# ── Rendered greeting cache ─────────────────────────────────────────────────


class UlawCache:
    """Small LRU of fully rendered μ-law greetings: repeat calls (and the call
    after a local test) answer instantly instead of waiting on synthesis."""

    def __init__(self, max_entries: int = 4, max_bytes: int = 8 * 1024 * 1024):
        self._entries: OrderedDict[tuple, bytes] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    def get(self, key: tuple) -> bytes | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple, value: bytes) -> None:
        if len(value) > self._max_bytes:
            return
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries or (
                sum(len(v) for v in self._entries.values()) > self._max_bytes
            ):
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


tokens = CallTokens()
registry = CallRegistry()
webhook_window = SlidingWindow()
failure_window = SlidingWindow()
ulaw_cache = UlawCache()
_FAILED_SIGNATURES_PER_MINUTE = 10


def reset_state() -> None:
    """Forget pending tokens, limits, cache and history (tests, disable)."""
    tokens.reset()
    registry.reset()
    webhook_window.reset()
    failure_window.reset()
    ulaw_cache.clear()
    _inflight.clear()


def signature_failures_exceeded() -> bool:
    return failure_window.exceeded(_FAILED_SIGNATURES_PER_MINUTE)


def note_signature_failure() -> None:
    failure_window.hit()
    registry.note("rejected_signature")


# ── Speech ─────────────────────────────────────────────────────────────────


def _speech_key(text: str, voice: str, engine: str, language: str) -> tuple:
    """Everything the rendered audio depends on, resolved at call time.

    Keyed on what synthesis would actually use rather than on the saved
    settings: a blank engine resolves to the *current* active engine, and the
    voice resolves through the same profile lookup as synthesis (reference
    clip, lock state, transcript, style) plus the clip's size/mtime — so a
    re-recorded, locked/unlocked or edited profile, or an engine switch,
    renders fresh instead of replaying the old voice.
    """
    from api.routers.tts_stream import build_stream_kwargs
    from services.tts_backend import active_backend_id

    kw = build_stream_kwargs({"voice": voice or None, "language": language or None})
    ref = kw.get("ref_audio")
    try:
        stat = os.stat(ref) if ref else None
        clip = (stat.st_size, stat.st_mtime_ns) if stat else None
    except OSError:
        clip = None
    return (
        text,
        engine or f"active:{active_backend_id()}",
        language or "",
        repr(sorted(kw.items())),
        clip,
    )


class _Render:
    """One in-flight synthesis, shared by every concurrent caller of the same
    greeting (single flight): followers stream the chunks the leader's render
    has produced so far instead of starting a duplicate synthesis."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.done = False
        self.error: Exception | None = None
        self.cond = asyncio.Condition()
        self.task: asyncio.Task | None = None
        #: Callers currently streaming this render. When the last one leaves
        #: before it finishes (hang-up, time limit), synthesis is cancelled.
        self.consumers = 0

    async def publish(self, chunk: bytes | None = None, *, done: bool = False) -> None:
        async with self.cond:
            if chunk is not None:
                self.chunks.append(chunk)
            self.done = self.done or done
            self.cond.notify_all()


_inflight: dict[tuple, _Render] = {}


async def _produce(key: tuple, render: _Render, text: str, voice: str, engine: str, language: str) -> None:
    from api.routers.tts_stream import synthesize_stream
    from services.telephony.audio import to_phone_ulaw

    try:
        async for wav, sr in synthesize_stream(
            text, voice=voice or None, engine=engine or None, language=language or None
        ):
            await render.publish(await asyncio.to_thread(to_phone_ulaw, wav, sr))
        ulaw_cache.put(key, b"".join(render.chunks))
    except asyncio.CancelledError:
        render.error = RuntimeError("speech synthesis was cancelled")
        raise
    except Exception as exc:  # noqa: BLE001 — handed to every consumer
        render.error = exc
    finally:
        if _inflight.get(key) is render:
            del _inflight[key]
        render.done = True
        try:
            await render.publish(done=True)
        except RuntimeError:  # loop closing; nobody left to wake
            pass


async def render_ulaw(
    text: str, *, voice: str = "", engine: str = "", language: str = ""
) -> AsyncIterator[bytes]:
    """Yield 8 kHz μ-law audio per sentence as the TTS pipeline finishes it.

    Reuses the streaming-TTS pipeline (``/ws/tts``): the first sentence is on
    the line while later ones are still synthesizing. Concurrent calls for the
    same greeting share one synthesis, and a complete render is cached, so
    later calls start immediately.
    """
    key = _speech_key(text, voice, engine, language)
    cached = ulaw_cache.get(key)
    if cached is not None:
        yield cached
        return
    render = _inflight.get(key)
    if render is None:
        render = _inflight[key] = _Render()
        render.task = asyncio.create_task(_produce(key, render, text, voice, engine, language))
    render.consumers += 1
    sent = 0
    try:
        while True:
            async with render.cond:
                await render.cond.wait_for(lambda: sent < len(render.chunks) or render.done)
                ready = render.chunks[sent:]
                finished = render.done
            for chunk in ready:
                yield chunk
            sent += len(ready)
            if finished and sent >= len(render.chunks):
                error = render.error
                if isinstance(error, Exception):
                    raise error
                return
    finally:
        render.consumers -= 1
        if render.consumers == 0 and not render.done and render.task is not None:
            # Everyone hung up mid-render: a greeting can be up to
            # MAX_GREETING_CHARS (1,000) characters — a minute or more of
            # speech, far longer on CPU — so don't keep holding the GPU pool
            # for nobody. The partial render is never cached (only a
            # completed render is), and it leaves the in-flight map now so
            # the next call starts a fresh render instead of joining this one.
            if _inflight.get(key) is render:
                del _inflight[key]
            render.task.cancel()


async def render_ulaw_all(text: str, **kw) -> bytes:
    return b"".join([chunk async for chunk in render_ulaw(text, **kw)])


# ── The call loop ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Clear:
    """Yielded by a responder: drop everything queued for playback (barge-in)."""


@dataclass(frozen=True)
class Mark:
    """Yielded by a responder: ask the provider to report (``on_mark``) once
    playback reaches this point, i.e. when the audio before it was heard."""

    name: str


class Responder(Protocol):
    """What the caller hears. The announcement responder speaks a fixed text;
    the call agent (``services.telephony.agent``) listens through
    ``on_inbound_audio`` and yields replies, :class:`Clear` and :class:`Mark`
    items from ``speak``. Optional extras: ``async on_mark(name)`` and a
    ``max_seconds`` attribute overriding the call-length limit."""

    def speak(self) -> AsyncIterator[bytes | Clear | Mark]:
        """8 kHz μ-law audio (and playback controls) for the caller, in order."""

    async def on_inbound_audio(self, ulaw: bytes) -> None:
        """Caller audio (8 kHz μ-law) as it arrives."""


class AnnouncementResponder:
    def __init__(self, text: str, *, voice: str = "", engine: str = "", language: str = ""):
        self._text = text
        self._kw = {"voice": voice, "engine": engine, "language": language}

    def speak(self) -> AsyncIterator[bytes]:
        return render_ulaw(self._text, **self._kw)

    async def on_inbound_audio(self, ulaw: bytes) -> None:
        return None  # announcement mode does not listen


async def _receive_event(websocket, provider: MediaStreamProvider) -> StreamEvent | None:
    """Next recognised event; None when the socket closed."""
    from starlette.websockets import WebSocketDisconnect

    while True:
        try:
            raw = await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return None
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if isinstance(message, dict):
            event = provider.parse_event(message)
            if event is not None:
                return event


async def _close(websocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except Exception:  # noqa: BLE001 — already closed by the peer
        pass


async def run_call(
    websocket,
    provider: MediaStreamProvider,
    *,
    account_id: str,
    responder_factory,
    max_calls: int,
    max_seconds: float,
) -> str:
    """Drive one media stream from handshake to hang-up; returns the outcome.

    ``responder_factory(start)`` receives the verified ``start`` event and
    returns the responder, or None to refuse the stream. The stream token is
    bound to the CallSid and, for agent calls, to the ``call`` session id the
    TwiML carried (see :func:`token_subject`).
    """
    from services.telephony.audio import FRAME_BYTES, PHONE_SAMPLE_RATE, ULAW_SILENCE

    await websocket.accept()

    async def _await_start() -> StreamEvent | None:
        while True:
            event = await _receive_event(websocket, provider)
            if event is None or event.kind == "start":
                return event
            if event.kind == "stop":
                return None

    try:
        start = await asyncio.wait_for(_await_start(), timeout=START_TIMEOUT_S)
    except asyncio.TimeoutError:
        start = None
    if (
        start is None
        or not tokens.redeem(
            start.params.get("token", ""),
            token_subject(start.call_id, start.params.get("call", "")) if start.call_id else "",
        )
        or not secrets.compare_digest(start.account_id.encode(), account_id.encode())
    ):
        registry.note("rejected_stream", start.call_id if start else "")
        await _close(websocket, 1008)
        return "rejected_stream"

    record = registry.begin(start.call_id, max_calls)
    if record is None:
        registry.note("busy", start.call_id)
        await _close(websocket, 1013)
        return "busy"

    responder = responder_factory(start)
    if responder is None:
        registry.end(record, "rejected_stream")
        await _close(websocket, 1008)
        return "rejected_stream"
    stream_id = start.stream_id
    max_seconds = getattr(responder, "max_seconds", None) or max_seconds

    async def _send() -> None:
        pending = b""
        sent = 0
        async for chunk in responder.speak():
            if isinstance(chunk, Clear):
                pending = b""
                await websocket.send_json(provider.clear_message(stream_id))
                continue
            if isinstance(chunk, Mark):
                if pending:
                    frame = pending + bytes([ULAW_SILENCE]) * (FRAME_BYTES - len(pending))
                    await websocket.send_json(provider.media_message(stream_id, frame))
                    sent += FRAME_BYTES
                    pending = b""
                await websocket.send_json(provider.mark_message(stream_id, chunk.name))
                continue
            pending += chunk
            whole = len(pending) - len(pending) % FRAME_BYTES
            for i in range(0, whole, FRAME_BYTES):
                await websocket.send_json(provider.media_message(stream_id, pending[i:i + FRAME_BYTES]))
                sent += FRAME_BYTES
                if (sent // FRAME_BYTES) % 50 == 0:
                    await asyncio.sleep(0)  # keep the receive side responsive
            pending = pending[whole:]
            record.audio_seconds = sent / PHONE_SAMPLE_RATE
        if pending:
            frame = pending + bytes([ULAW_SILENCE]) * (FRAME_BYTES - len(pending))
            await websocket.send_json(provider.media_message(stream_id, frame))
            sent += FRAME_BYTES
        record.audio_seconds = sent / PHONE_SAMPLE_RATE
        # Twilio echoes the mark once everything before it has played.
        await websocket.send_json(provider.mark_message(stream_id, END_MARK))

    on_mark = getattr(responder, "on_mark", None)

    async def _receive() -> str:
        while True:
            event = await _receive_event(websocket, provider)
            if event is None or event.kind == "stop":
                return "caller_hung_up"
            if event.kind == "mark" and event.mark == END_MARK:
                return "completed"
            if event.kind == "mark" and on_mark is not None:
                await on_mark(event.mark)
            if event.kind == "media" and event.payload:
                await responder.on_inbound_audio(event.payload)

    outcome = "error"
    send_task = asyncio.create_task(_send())
    recv_task = asyncio.create_task(_receive())
    deadline = time.monotonic() + max_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "time_limit"
                break
            done, _ = await asyncio.wait(
                {t for t in (send_task, recv_task) if not t.done()} or {recv_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if recv_task in done:
                outcome = recv_task.result()
                break
            if send_task in done and send_task.exception() is not None:
                exc = send_task.exception()
                from api.routers.tts_stream import StreamUnavailableError

                outcome = "engine_unavailable" if isinstance(exc, StreamUnavailableError) else "synthesis_failed"
                # Class name only: the message may quote the greeting text.
                logger.warning("Phone call audio failed (%s)", type(exc).__name__)
                break
    finally:
        for task in (send_task, recv_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(send_task, recv_task, return_exceptions=True)
        registry.end(record, outcome)
        await _close(websocket, 1000)
    logger.info("Phone call %s ended: %s (%.1fs audio)", record.call, outcome, record.audio_seconds)
    return outcome
