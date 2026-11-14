"""Request cancellation visible to killable sidecars on executor threads.

In-process native calls remain accounted for until they return; only sidecars
can safely terminate early. A scope belongs to one job, never to a pool thread.
"""
from contextlib import contextmanager
import threading

_local = threading.local()


class InferenceCancellation:
    def __init__(self, *, deadline: float | None = None):
        self.cancelled = threading.Event()
        self.deadline = deadline

    def cancel(self):
        self.cancelled.set()

    @contextmanager
    def activate(self):
        previous = getattr(_local, "scope", None)
        _local.scope = self
        try:
            if self.cancelled.is_set():
                raise RuntimeError("Inference request was cancelled before execution")
            yield
        finally:
            _local.scope = previous


def current_cancellation():
    return getattr(_local, "scope", None)
