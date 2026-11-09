"""HuggingFace download progress — one monkey-patch, every `hf_hub_download`
reports bytes downloaded through a central callback.

`huggingface_hub` uses tqdm for progress bars; we subclass it, intercept
`update()` calls, and forward (filename, downloaded_bytes, total_bytes) to
whatever callback is registered. No changes to calling sites across
transformers / mlx_whisper / diffusers / accelerate — they all route through
`hf_hub_download`, which uses the patched tqdm.

Usage:
    from utils.hf_progress import install, register_listener, unregister_listener

    install()  # once at app startup
    listener_id = register_listener(lambda ev: print(ev))
    # …models download, listener fires…
    unregister_listener(listener_id)
"""
from __future__ import annotations

import contextvars
import itertools
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("omnivoice.hf_progress")

# Context-scoped active repo_id. Set in the install/delete handler so every
# tqdm event fired while a snapshot_download runs can be stamped with the
# originating repo, letting the frontend route per-file events to the right
# row instead of heuristically matching filename substrings.
current_repo_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnivoice_hf_progress_repo_id", default=None,
)
current_target: contextvars.ContextVar[str] = contextvars.ContextVar(
    "omnivoice_hf_progress_target", default="local",
)

# Event shape forwarded to listeners. Typed loosely on purpose — SSE encodes
# it as JSON so consumers read the dict directly.
#   {
#     "filename": str,        # desc on the tqdm bar, usually the HF file path
#     "downloaded": int,      # bytes pulled so far
#     "total": int | None,    # total bytes or None if unknown
#     "pct": float,           # 0.0-1.0 (or 0.0 if total unknown)
#     "phase": "start"|"progress"|"done",
#   }
ProgressEvent = dict
Listener = Callable[[ProgressEvent], None]

_listeners: dict[int, Listener] = {}
_listener_lock = threading.Lock()
_listener_counter = itertools.count(1)
# Per-bar key for the overall aggregator. A monotonic counter, not id(self):
# CPython recycles a freed object's address for the next same-shaped object, so
# consecutive per-file bars routinely shared an id and the next bar's first
# update overwrote bytes the finished file had already earned (#2936).
_bar_key_counter = itertools.count(1)
_installed = False
_install_lock = threading.Lock()

# Set by install() to the TrackedTqdm subclass so call sites can drive it
# explicitly via snapshot_download(tqdm_class=...) instead of relying solely on
# the global monkey-patch. Xet feeds bytes into whatever tqdm_class is passed,
# so this is also the xet-aware progress hook (FDL-02).
_tracked_tqdm_class: Optional[type] = None


def tracked_tqdm_class() -> Optional[type]:
    """Return the progress-emitting tqdm subclass (or None if install() hasn't
    run / huggingface_hub's tqdm couldn't be patched). Pass it as
    ``snapshot_download(tqdm_class=...)`` to drive progress deterministically."""
    return _tracked_tqdm_class


# Optional sink fed every per-file (repo_id, filename, downloaded, total) byte
# update — used by utils.download_aggregator to build the overall aggregate bar
# (FDL-06). Kept as a setter to avoid a circular import (this module must not
# import the aggregator). Signature: fn(repo_id, filename, downloaded, total).
_byte_sink: Optional[Callable] = None


def set_byte_sink(fn: Optional[Callable]) -> None:
    global _byte_sink
    _byte_sink = fn


def register_listener(cb: Listener) -> int:
    """Register a callback that receives progress events. Returns an id that
    can be passed to `unregister_listener` when the listener is done."""
    with _listener_lock:
        lid = next(_listener_counter)
        _listeners[lid] = cb
        return lid


def unregister_listener(lid: int) -> None:
    with _listener_lock:
        _listeners.pop(lid, None)


def _emit(event: ProgressEvent) -> None:
    """Fan out to all registered listeners. Never raise — a bad listener
    shouldn't break a download."""
    # Stamp the active repo_id so frontends can route events to the right
    # row. Only set when this emit is happening inside an install handler.
    rid = current_repo_id.get()
    if rid is not None and "repo_id" not in event:
        event = {**event, "repo_id": rid}
    if "target" not in event:
        event = {**event, "target": current_target.get()}
    with _listener_lock:
        listeners = list(_listeners.values())
    for cb in listeners:
        try:
            cb(event)
        except Exception as e:  # noqa: BLE001
            logger.debug("hf_progress listener raised: %s", e)


def emit(event: ProgressEvent) -> None:
    """Public emit — lets non-tqdm operations (delete, verify, etc.) push
    lifecycle events onto the same SSE stream."""
    _emit(event)


class SafeFileWrapper:
    """File proxy that prevents display failures from aborting downloads."""

    _is_safe_wrapper = True

    def __init__(self, fp):
        object.__setattr__(self, "_target_stream", fp)

    def write(self, s):
        try:
            return self._target_stream.write(s)
        except (OSError, UnicodeError):
            return None

    def flush(self):
        flush = getattr(self._target_stream, "flush", None)
        if flush is None:
            return None
        try:
            return flush()
        except OSError:
            return None

    def __getattr__(self, name):
        from operator import attrgetter

        destination = vars(self)["_target_stream"]
        return attrgetter(name)(destination)


def _safe_progress_stream(candidate):
    if candidate is None:
        return None
    if getattr(candidate, "_is_safe_wrapper", False):
        return candidate
    return SafeFileWrapper(candidate)

def install() -> None:
    """Monkey-patch `huggingface_hub`'s tqdm so every download reports to our
    listeners. Safe to call multiple times — second call is a no-op."""
    global _installed
    with _install_lock:
        if _installed:
            return
        # `huggingface_hub.utils.__init__` does `from .tqdm import tqdm`,
        # which shadows the `tqdm` SUBMODULE with the CLASS of the same name
        # when accessed via attribute lookup. Pull the real module out of
        # sys.modules after an explicit import so we patch the right thing.
        try:
            import sys
            import huggingface_hub.utils.tqdm  # noqa: F401
            hf_tqdm_module = sys.modules.get("huggingface_hub.utils.tqdm")
            if hf_tqdm_module is None:
                raise ImportError("huggingface_hub.utils.tqdm not in sys.modules after import")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "hf_progress.install: huggingface_hub.utils.tqdm missing (%s); "
                "progress tracking disabled.", e,
            )
            return

        original = getattr(hf_tqdm_module, "tqdm", None)
        if original is None or not isinstance(original, type):
            logger.warning("hf_progress.install: no `tqdm` class on the module; aborting")
            return

        class TrackedTqdm(original):  # type: ignore[misc,valid-type]
            """tqdm subclass that emits a progress event on every update."""

            _last_emit_time: float = 0.0

            @staticmethod
            def status_printer(file):
                output_stream = _safe_progress_stream(file)
                try:
                    printer = original.status_printer(output_stream)
                except Exception:
                    def printer(_message):
                        return None
                return printer

            def __init__(self, *args, **kwargs):
                # Allocate the aggregator key FIRST: tqdm's own __init__ calls
                # display(), which reaches _feed_sink() before any attribute
                # set after super().__init__() would exist. Stable for this
                # bar's whole life and never reused by a later bar (#2936).
                self._bar_key = next(_bar_key_counter)

                if "file" in kwargs:
                    kwargs["file"] = _safe_progress_stream(kwargs["file"])
                try:
                    super().__init__(*args, **kwargs)
                except OSError:
                    pass
                inherited_stream = getattr(self, "fp", None)
                protected_stream = _safe_progress_stream(inherited_stream)
                if protected_stream is not inherited_stream:
                    self.fp = protected_stream
                self._last_emit_time = time.monotonic()
                try:
                    desc = getattr(self, "desc", None)
                    total = int(getattr(self, "total", 0) or 0)
                    _emit({
                        "filename": str(desc or "download"),
                        "downloaded": 0,
                        "total": total,
                        "pct": 0.0,
                        "phase": "start",
                    })
                except Exception:
                    pass

            def _emit_progress(self):
                """Emit current state as a progress event."""
                try:
                    desc = getattr(self, "desc", None)
                    total = int(getattr(self, "total", 0) or 0)
                    done = int(getattr(self, "n", 0) or 0)
                    pct = (done / total) if total > 0 else 0.0
                    # Pull rate from tqdm's own calculations if available
                    rate = None
                    try:
                        rate = self.format_dict.get("rate")
                    except Exception:
                        pass
                    event = {
                        "filename": str(desc or "download"),
                        "downloaded": done,
                        "total": total,
                        "pct": pct,
                        "phase": "done" if (total > 0 and done >= total) else "progress",
                    }
                    if rate and rate > 0:
                        event["rate"] = rate  # bytes/sec from tqdm
                    _emit(event)
                    # Feed the overall aggregator (FDL-06), if wired.
                    self._feed_sink(done, total, complete=False)
                except Exception:
                    pass

            def _feed_sink(self, done, total, *, complete: bool):
                """Forward a byte/count update to the overall aggregator sink.

                Passes the tqdm `unit` so the aggregator can tell a byte bar
                (unit 'B') from the "Fetching N files" count bar, and a stable
                per-bar key because every per-file byte bar shares the default
                desc 'download' under Xet — keying by desc would collapse them
                into one. The key is a counter allocated per bar, not id(self):
                CPython reuses a freed bar's address, which made two files
                share a key and erase each other's bytes (#2936).
                """
                sink = _byte_sink
                if sink is None:
                    return
                rid = current_repo_id.get()
                if not rid:
                    return
                try:
                    unit = getattr(self, "unit", None)
                    sink(rid, self._bar_key, unit, int(done or 0), int(total or 0), complete)
                except Exception:
                    pass

            def update(self, n=1):
                try:
                    result = super().update(n)
                except OSError:
                    result = None
                now = time.monotonic()
                if now - self._last_emit_time >= 0.3:
                    self._last_emit_time = now
                    try:
                        self._emit_progress()
                    except Exception:
                        pass
                return result

            def display(self, msg=None, pos=None):
                now = time.monotonic()
                if now - self._last_emit_time >= 0.5:
                    self._last_emit_time = now
                    try:
                        self._emit_progress()
                    except Exception:
                        pass
                try:
                    return super().display(msg, pos)
                except OSError:
                    return None

            def close(self):
                # tqdm totals can be floats or numpy numbers; coerce like _emit_progress.
                try:
                    total = int(getattr(self, "total", 0) or 0)
                except (TypeError, ValueError):
                    total = 0
                if total > 0:
                    try:
                        self._feed_sink(total, total, complete=True)
                    except Exception:
                        pass
                try:
                    return super().close()
                except OSError:
                    return None

        # Stash the original for inspection / uninstall, then swap.
        hf_tqdm_module._omnivoice_original_tqdm = original  # type: ignore[attr-defined]
        hf_tqdm_module.tqdm = TrackedTqdm  # type: ignore[assignment]
        global _tracked_tqdm_class
        _tracked_tqdm_class = TrackedTqdm
        _installed = True
        logger.info("hf_progress: installed tqdm patch on huggingface_hub.utils.tqdm")
