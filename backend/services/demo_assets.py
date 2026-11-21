"""On-demand dubbing demo videos.

The Dub workspace's demo card plays five short clips (an original and four
dubs). They are ~10 MB, so they are not in the app bundle: the bundle carries
only the manifest, the subtitles and one small poster still per clip, and the
videos are fetched from a pinned pre-release of the ``omnivoice-gallery`` repo
(tag ``dub-demo-v1``, one release asset per file, owner-approved 2026-11-21).
``scripts/package_dub_demo.py`` produces those assets and the pinned table
(``backend/assets/samples/demo/dubbing/demo_assets.json``) this module verifies
against.

Consent:
Nothing here runs at import or startup. The only network entry point is
:func:`start_download`, which the user triggers with the Download button
(``POST /demo/dubbing/download``). With it never pressed, or the network down,
the card shows the posters and every other feature is untouched.

Integrity:
Every file is pinned by sha256 and byte size in the committed table. A download
is streamed to a temp file under a hard size cap (the pinned size), then size
and hash are checked; only a file that passes is renamed into the cache. A
wrong hash, a truncated body or an oversized body is deleted and never served.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from core.config import DATA_DIR

logger = logging.getLogger("omnivoice.demo_assets")

_TABLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets" / "samples" / "demo" / "dubbing" / "demo_assets.json"
)

_BASE_URL_TEMPLATE = "https://github.com/debpalash/omnivoice-gallery/releases/download/{tag}"
_HTTP_TIMEOUT_S = 30.0

#: Hard ceiling on any single response regardless of the table.
MAX_FILE_BYTES = 16 << 20


class DemoAssetError(RuntimeError):
    """A download could not be accepted."""


def _load_table() -> dict[str, Any]:
    return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))


_TABLE = _load_table()
TAG: str = _TABLE["tag"]
CACHE_SUBDIR: str = _TABLE["cache_dir"]

#: name -> {"sha256", "bytes"} for the files the Download button fetches.
PINNED: dict[str, dict[str, Any]] = {
    f["name"]: f for f in _TABLE["files"] if f.get("kind") == "video"
}
#: Dev/test fixtures (source audio); pinned but never downloaded by the app.
DEV_PINNED: dict[str, dict[str, Any]] = {
    f["name"]: f for f in _TABLE["files"] if f.get("kind") == "dev"
}


def base_url() -> str:
    """Where the assets are published. Overridable for self-hosting and tests."""
    default = _BASE_URL_TEMPLATE.format(tag=TAG)
    return (os.environ.get("OMNIVOICE_DEMO_ASSETS_URL") or default).rstrip("/")


def cache_dir() -> Path:
    return Path(DATA_DIR) / "demo_cache" / CACHE_SUBDIR


def total_bytes() -> int:
    return sum(int(f["bytes"]) for f in PINNED.values())


def cached_path(name: str) -> Optional[Path]:
    """The cached file for a pinned *name*, or None.

    Only names from the pinned table resolve, so a request can never name a path
    outside the cache. Presence plus exact size is checked here (hash was
    verified before the file was ever renamed into place).
    """
    pin = PINNED.get(name)
    if pin is None:
        return None
    path = cache_dir() / name
    try:
        if path.is_file() and path.stat().st_size == int(pin["bytes"]):
            return path
    except OSError:
        return None
    return None


def _cached_names() -> list[str]:
    return [n for n in PINNED if cached_path(n) is not None]


# ── Status ───────────────────────────────────────────────────────────────────

_state: dict[str, Any] = {"state": "idle", "downloaded_bytes": 0, "error": None}
#: Strong references to background downloads (the loop only keeps weak ones).
_tasks: set["asyncio.Task[Any]"] = set()


def status() -> dict[str, Any]:
    """Offline-safe: reads local state only, never touches the network."""
    cached = _cached_names()
    complete = len(cached) == len(PINNED)
    state = _state["state"]
    if state != "downloading":
        state = "ready" if complete else ("error" if _state["error"] else "idle")
    done = _state["downloaded_bytes"] if state == "downloading" else (
        total_bytes() if complete else sum(int(PINNED[n]["bytes"]) for n in cached)
    )
    return {
        "state": state,
        "total_bytes": total_bytes(),
        "downloaded_bytes": done,
        "files": {name: name in cached for name in PINNED},
        "error": _state["error"] if state == "error" else None,
    }


# ── Download ─────────────────────────────────────────────────────────────────

def _client(client=None):
    if client is not None:
        return client
    from api.http_client import get_http_client

    return get_http_client()


async def _download_one(client, name: str, pin: dict[str, Any], on_chunk) -> None:
    """Stream *name* to a temp file, verify, then atomically publish it."""
    import httpx

    expected = int(pin["bytes"])
    limit = min(expected, MAX_FILE_BYTES)
    dest_dir = cache_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest_dir), prefix=".demo-", suffix=".part")
    digest = hashlib.sha256()
    total = 0
    published = False
    try:
        with os.fdopen(fd, "wb") as fh:
            try:
                async with client.stream(
                    "GET", f"{base_url()}/{name}", timeout=_HTTP_TIMEOUT_S,
                    follow_redirects=True,
                ) as response:
                    if response.status_code != 200:
                        raise DemoAssetError(f"HTTP {response.status_code}")
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise DemoAssetError("response exceeded its size cap")
                        digest.update(chunk)
                        fh.write(chunk)
                        on_chunk(len(chunk))
            except httpx.HTTPError as exc:
                raise DemoAssetError(f"transfer failed: {type(exc).__name__}") from exc
            fh.flush()
            os.fsync(fh.fileno())
        if total != expected:
            raise DemoAssetError("download was truncated")
        if digest.hexdigest() != str(pin["sha256"]).lower():
            raise DemoAssetError("sha256 mismatch")
        os.replace(tmp, dest_dir / name)
        published = True
    finally:
        # Runs for failures and cancellation alike; a half file is never kept.
        if not published:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


async def download_all(client=None) -> dict[str, Any]:
    """Fetch every missing pinned file. All-or-nothing per file; resumable by rerun."""
    _state.update(state="downloading", error=None)
    try:
        client = _client(client)
    except Exception as exc:  # noqa: BLE001 - e.g. ValueError for an unsupported proxy scheme
        logger.info("Demo video download client unavailable: %s", type(exc).__name__)
        _state.update(state="error", error="download client unavailable (check proxy settings)")
        return status()
    base = sum(int(PINNED[n]["bytes"]) for n in _cached_names())
    _state["downloaded_bytes"] = base

    def on_chunk(n: int) -> None:
        _state["downloaded_bytes"] += n

    try:
        for name, pin in PINNED.items():
            if cached_path(name) is not None:
                continue
            before = _state["downloaded_bytes"]
            try:
                await _download_one(client, name, pin, on_chunk)
            except (Exception, asyncio.CancelledError):
                _state["downloaded_bytes"] = before
                raise
    except asyncio.CancelledError:
        _state.update(state="idle")
        raise
    except Exception as exc:  # noqa: BLE001 - reported through status()
        # Only fixed messages or the exception type: OS errors carry absolute paths.
        detail = str(exc) if isinstance(exc, DemoAssetError) else type(exc).__name__
        logger.info("Demo video download failed: %s", detail)
        _state.update(state="error", error=detail)
    else:
        _state.update(state="ready", error=None)
    return status()


async def start_download() -> dict[str, Any]:
    """Begin the user-requested download in the background (idempotent)."""
    if status()["state"] == "ready":
        return status()
    if not any(not t.done() for t in _tasks):
        _state.update(state="downloading", error=None)
        task = asyncio.create_task(download_all())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return status()


# ── Serving ──────────────────────────────────────────────────────────────────

def make_demo_mount(directory: str):
    """``/demo_audio`` mount: bundled files, with cached demo videos as fallback."""
    from core.safe_static import SafeMediaFiles

    class DemoMediaFiles(SafeMediaFiles):
        async def get_response(self, path: str, scope):
            # Windows hands the static mount backslash-separated paths.
            norm = path.replace("\\", "/")
            prefix = "demo/dubbing/"
            if norm.startswith(prefix):
                # Only exact pinned names resolve, so no separator or ".." survives.
                name = norm[len(prefix):]
                cached = cached_path(name)
                if cached is not None:
                    inner = SafeMediaFiles(directory=str(cached.parent), check_dir=False)
                    return await inner.get_response(name, scope)
            return await super().get_response(path, scope)

    return DemoMediaFiles(directory=directory)
