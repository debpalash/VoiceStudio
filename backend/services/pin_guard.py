"""Limits wrong guesses of the Network sharing PIN.

The share PIN is six digits, so without a limit the whole keyspace can be tried
in minutes. Two limits apply to clients that present a PIN which is wrong:

* Per client: after ``CLIENT_FREE_FAILURES`` wrong PINs inside ``WINDOW_S`` the
  client is locked out for ``CLIENT_LOCK_S`` seconds, doubling with each further
  lockout up to ``CLIENT_LOCK_MAX_S``. An IPv6 client is counted by its /64, so
  rotating addresses inside one network does not reset the count.
* Overall: after ``GLOBAL_FAILURES`` wrong PINs from all clients inside
  ``WINDOW_S``, every client that has not already entered the right PIN is
  locked out for ``GLOBAL_LOCK_S`` seconds. That caps a guesser spread over
  many addresses at a few hundred guesses an hour.

A locked-out request is refused before its PIN is compared, so a guess made
while locked out reveals nothing. A request without any PIN is not a guess and
is not counted: the UI's first requests come before the person types the PIN.
The state lives in memory and starts again when the PIN changes or the backend
restarts, which also issues a new PIN.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass, field

WINDOW_S = 15 * 60
CLIENT_FREE_FAILURES = 5
CLIENT_LOCK_S = 60
CLIENT_LOCK_MAX_S = 60 * 60
GLOBAL_FAILURES = 30
GLOBAL_LOCK_S = 15 * 60
# A bound on tracked clients, so a flood of addresses cannot grow memory without end.
MAX_CLIENTS = 4096


@dataclass
class _Client:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    lockouts: int = 0


def client_key(host: str | None) -> str:
    """The key a client is counted under: its IPv4 address, or its IPv6 /64."""
    if not host:
        return "?"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)


class PinGuard:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._pin: str | None = None
        self._clients: dict[str, _Client] = {}
        self._global_failures: list[float] = []
        self._global_locked_until = 0.0
        self._passed: set[str] = set()

    def _for_pin(self, pin: str) -> None:
        if pin != self._pin:
            self._pin = pin
            self._clients.clear()
            self._global_failures.clear()
            self._global_locked_until = 0.0
            self._passed.clear()

    def retry_after(self, host: str | None, pin: str) -> int:
        """Seconds the client must wait before its PIN is even compared, or 0."""
        key = client_key(host)
        with self._lock:
            self._for_pin(pin)
            now = self._clock()
            wait = 0.0
            client = self._clients.get(key)
            if client and client.locked_until > now:
                wait = client.locked_until - now
            if self._global_locked_until > now and key not in self._passed:
                wait = max(wait, self._global_locked_until - now)
            return int(wait) + (1 if wait > int(wait) else 0)

    def failed(self, host: str | None, pin: str) -> None:
        key = client_key(host)
        with self._lock:
            self._for_pin(pin)
            now = self._clock()
            since = now - WINDOW_S
            self._global_failures = [t for t in self._global_failures if t > since]
            self._global_failures.append(now)
            if len(self._global_failures) >= GLOBAL_FAILURES:
                self._global_locked_until = now + GLOBAL_LOCK_S
                self._global_failures.clear()
            client = self._clients.get(key)
            if client is None:
                if len(self._clients) >= MAX_CLIENTS:
                    self._evict(now)
                client = self._clients.setdefault(key, _Client())
            client.failures = [t for t in client.failures if t > since]
            client.failures.append(now)
            if len(client.failures) >= CLIENT_FREE_FAILURES:
                lock = min(CLIENT_LOCK_S * (2 ** client.lockouts), CLIENT_LOCK_MAX_S)
                client.locked_until = now + lock
                client.lockouts += 1
                client.failures.clear()

    def passed(self, host: str | None, pin: str) -> None:
        key = client_key(host)
        with self._lock:
            self._for_pin(pin)
            self._clients.pop(key, None)
            if len(self._passed) < MAX_CLIENTS:
                self._passed.add(key)

    def _evict(self, now: float) -> None:
        # Drop clients that are neither locked nor recently failing; if every one is,
        # drop the oldest half. Eviction only ever forgets, never unlocks the global limit.
        idle = [k for k, c in self._clients.items() if c.locked_until <= now and not c.failures]
        for k in idle:
            del self._clients[k]
        if len(self._clients) >= MAX_CLIENTS:
            oldest = sorted(self._clients, key=lambda k: max(self._clients[k].failures or [0.0]))
            for k in oldest[: len(oldest) // 2]:
                del self._clients[k]

    def reset(self) -> None:
        with self._lock:
            self._pin = None
            self._clients.clear()
            self._global_failures.clear()
            self._global_locked_until = 0.0
            self._passed.clear()


pin_guard = PinGuard()
