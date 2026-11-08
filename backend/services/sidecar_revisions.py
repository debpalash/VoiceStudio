"""Track successful sidecar environment changes without interrupting requests."""
import threading

_lock = threading.Lock()
_revisions: dict[str, int] = {}


def current(engine_id: str) -> int:
    with _lock:
        return _revisions.get(engine_id, 0)


def advance(engine_id: str) -> None:
    with _lock:
        _revisions[engine_id] = _revisions.get(engine_id, 0) + 1
