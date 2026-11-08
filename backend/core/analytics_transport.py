"""Bounded Supermetry delivery for opted-in opaque profiles; no user content."""
from __future__ import annotations

import http.client
import json
import platform
import queue
import re
import socket
import threading
import time
import uuid
from typing import Callable
from urllib.parse import urlsplit


def valid_url(value: str, *, origin: bool = False) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment
            and (not origin or parsed.path in {"", "/"})
            and parsed.port != 0
            and not any(ord(c) < 33 for c in value)
        )
    except (ValueError, TypeError):
        return False


def user_agent() -> str:
    os_name = platform.system().lower()
    device = {"darwin": "Macintosh; Intel Mac OS X 10_15_7", "windows": "Windows NT 10.0; Win64; x64"}.get(os_name, "X11; Linux x86_64")
    return f"Mozilla/5.0 ({device}) AppleWebKit/537.36 (KHTML, like Gecko) VoiceStudio/1.0 Chrome/130.0.0.0 Safari/537.36"


class _CancellationMixin:
    """Check cancellation both before reconnecting and before writing bytes."""
    def __init__(self, *args, cancelled: threading.Event, **kwargs):
        self._cancelled = cancelled
        super().__init__(*args, **kwargs)

    def _check_cancelled(self) -> None:
        if self._cancelled.is_set():
            self.close()
            raise OSError("analytics delivery cancelled")

    def connect(self) -> None:
        self._check_cancelled()
        super().connect()
        # Withdrawal may have happened while DNS/TCP/TLS was in progress.
        self._check_cancelled()

    def send(self, data) -> None:
        self._check_cancelled()
        super().send(data)


class _HTTPConnection(_CancellationMixin, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_CancellationMixin, http.client.HTTPSConnection):
    pass


class SupermetryTransport:
    """One daemon worker, at most 64 pending events, three attempts per event.

    Shutdown discards work and closes the active socket. Requests already sent
    cannot be recalled, but no queued event or retry survives withdrawal.
    """
    def __init__(self, destination: dict, should_send: Callable[[], bool]):
        self.destination = destination
        self.should_send = should_send
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._connection = None
        self._thread = threading.Thread(target=self._run, name="supermetry", daemon=True)
        self._thread.start()

    def capture(self, event: str, properties: dict, profile_id: str | None = None, traits: dict | None = None,
                path: str = "/backend") -> bool:
        if (not isinstance(event, str) or not event or len(event.encode()) > 300
                or self._stopped.is_set() or not self.should_send()):
            return False
        if not isinstance(path, str) or not re.fullmatch(r"/[a-z0-9_/-]{0,80}", path):
            return False
        payload = {
            "v": 1, "id": uuid.uuid4().hex, "ts": int(time.time() * 1000),
            "site": self.destination["site"], "name": event,
            "url": self.destination["appUrl"].rstrip("/") + path,
            "props": properties,
        }
        if profile_id is not None:
            if not isinstance(profile_id, str) or not re.fullmatch(r"(?:install_[a-f0-9]{32}|account_[a-f0-9]{64})", profile_id):
                return False
            payload["profile_id"] = profile_id
        if event == "identify" and profile_id is None:
            return False
        if traits is not None:
            if event != "identify" or not profile_id or not isinstance(traits, dict):
                return False
            payload["traits"] = {key: value for key, value in traits.items()
                                 if key in {"platform", "app_version", "profile_kind"}
                                 and isinstance(value, str) and len(value) <= 256}
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(body) > 8192:
            return False
        with self._lock:
            if self._stopped.is_set():
                return False
            try:
                self._queue.put_nowait(body)
                return True
            except queue.Full:
                return False

    def _send(self, body: bytes) -> int:
        parsed = urlsplit(self.destination["host"])
        factory = _HTTPSConnection if parsed.scheme == "https" else _HTTPConnection
        connection = factory(parsed.hostname, parsed.port, timeout=2, cancelled=self._stopped)
        with self._lock:
            if self._stopped.is_set() or not self.should_send():
                return 0
            self._connection = connection
        try:
            origin = urlsplit(self.destination["appUrl"])
            connection.request("POST", "/api/public/v1/events", body, {
                "Content-Type": "application/json", "User-Agent": user_agent(),
                "Origin": f"{origin.scheme}://{origin.netloc}",
            })
            return connection.getresponse().status
        finally:
            connection.close()
            with self._lock:
                self._connection = None

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                body = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                for attempt in range(3):
                    if self._stopped.is_set() or not self.should_send():
                        break
                    try:
                        status = self._send(body)
                        if status != 429 and status < 500:
                            break  # Includes 4xx and redirects; never follow them.
                    except Exception:
                        pass  # Telemetry must never interrupt a feature.
                    if attempt == 2 or self._stopped.wait(0.5 * (2 ** attempt)):
                        break
            finally:
                self._queue.task_done()

    def shutdown(self) -> None:
        self._stopped.set()
        with self._lock:
            if self._connection is not None:
                self._connection.auto_open = 0
                active_socket = self._connection.sock
                if active_socket is not None:
                    try:
                        active_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        # Already closed or reset by the peer: nothing left to shut down.
                        pass
                self._connection.close()
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
