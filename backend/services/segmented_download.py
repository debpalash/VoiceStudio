"""Opt-in multi-connection segmented downloader (FDL-08).

An IDM/uGet-style downloader for a single file: it fetches many byte-ranges in
parallel over HTTP, resumes a partial download, verifies the result, and can be
cancelled. It exists for the **legacy-LFS** download path — the app forces that
path by default (``HF_HUB_DISABLE_XET=1``) because Xet's progress is opaque, and
classic LFS is single-stream, so this restores parallel speed *and* keeps live
byte progress (it reports every received chunk to the aggregator).

Auth safety (critical): the Hugging Face ``Authorization`` header is sent **only**
to ``huggingface.co``/``hf.co`` hosts. When a resolve URL redirects to a CDN
(CloudFront/etc.), the presigned URL already carries auth, so the token is
**never** forwarded to the CDN host. Redirects are followed manually to enforce
this per-hop.

This module is deliberately framework-free and unit-tested with
``httpx.MockTransport``; the HF-cache integration lives in the setup router.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Callable, Optional

import httpx

# Hosts the HF token may be sent to. Anything else (CDN) gets no auth header.
_HF_AUTH_HOSTS = ("huggingface.co", "hf.co")
_DEFAULT_CONNECTIONS = 8
_MIN_SEGMENT_BYTES = 4 * 1024 * 1024   # don't split below this — overhead > gain
# Cap on a single segment. Progress is committed to the manifest only when a
# whole segment lands, so the segment size is also the MOST bytes a dropped
# connection can throw away. Sizing segments as size/num_connections made that
# ~100 MB on an 800 MB blob: on a link that drops every ~50 MB no segment ever
# completed, the manifest was never written, and every retry restarted from
# zero (#1224 follow-up). Bounded segments turn the same flaky link into steady
# forward progress.
_MAX_SEGMENT_BYTES = 16 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


class DownloadCancelled(Exception):
    """Raised when ``cancel_check()`` returns True mid-download."""


def _host_gets_auth(url: str) -> bool:
    host = (httpx.URL(url).host or "").lower()
    return host in _HF_AUTH_HOSTS or host.endswith(".huggingface.co")


def _auth_headers(url: str, token: Optional[str]) -> dict:
    if token and _host_gets_auth(url):
        return {"Authorization": f"Bearer {token}"}
    return {}


async def _resolve(client: httpx.AsyncClient, url: str, token: Optional[str], max_hops: int = 10):
    """Follow redirects manually, dropping the auth header on any cross-host hop.

    Returns (final_url, size_or_None, accepts_ranges_bool).
    """
    cur = url
    for _ in range(max_hops):
        r = await client.head(cur, headers=_auth_headers(cur, token))
        if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
            cur = str(httpx.URL(cur).join(r.headers["location"]))
            continue
        r.raise_for_status()
        size = r.headers.get("content-length")
        size = int(size) if size and size.isdigit() else None
        accepts = r.headers.get("accept-ranges", "").lower() == "bytes"
        return cur, size, accepts
    raise httpx.TooManyRedirects(f"exceeded {max_hops} redirects for {url}")


def _plan_segments(size: int, num_connections: int) -> list[tuple[int, int]]:
    """Byte ranges to fetch, each at most ``_MAX_SEGMENT_BYTES``.

    ``num_connections`` controls how many run at once (see the semaphore in
    :func:`segmented_download`), NOT how many segments exist — a large file is
    split into many bounded segments so each one commits to the manifest
    quickly and a dropped connection costs at most one segment.
    """
    n = max(1, min(num_connections, max(1, size // _MIN_SEGMENT_BYTES)))
    step = -(-size // n)  # ceil
    step = max(_MIN_SEGMENT_BYTES, min(step, _MAX_SEGMENT_BYTES))
    segs = []
    start = 0
    while start < size:
        end = min(start + step, size) - 1
        segs.append((start, end))
        start = end + 1
    return segs


def _manifest_path(part: str) -> str:
    return part + ".done"


def _load_done(part: str, size: int) -> set[tuple[int, int]]:
    try:
        if os.path.getsize(part) != size:
            return set()  # missing/truncated bytes cannot be certified by a sidecar
        with open(_manifest_path(part)) as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("size") != size:
            return set()
        ranges = data.get("done", [])
        if not isinstance(ranges, list):
            return set()
        done = set()
        for segment in ranges:
            if (not isinstance(segment, list) or len(segment) != 2
                    or any(type(value) is not int for value in segment)
                    or not 0 <= segment[0] <= segment[1] < size):
                return set()
            done.add(tuple(segment))
        return done
    except (OSError, ValueError):
        return set()


def _save_done(part: str, size: int, done: set) -> None:
    try:
        tmp = _manifest_path(part) + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"size": size, "done": sorted(list(s) for s in done)}, f)
        os.replace(tmp, _manifest_path(part))
    except OSError:
        pass


async def segmented_download(
    url: str,
    dest: str,
    *,
    token: Optional[str] = None,
    expected_size: Optional[int] = None,
    expected_etag: Optional[str] = None,
    num_connections: int = _DEFAULT_CONNECTIONS,
    on_bytes: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0,
) -> str:
    """Download ``url`` to ``dest`` using parallel byte-ranges with resume.

    - ``on_bytes(delta)`` is called as bytes land (feeds the aggregator).
    - ``cancel_check()`` is polled between chunks; returning True raises
      :class:`DownloadCancelled` and leaves the ``.part`` for a later resume.
    - On success the size (and ``expected_etag`` if given) is verified, then the
      ``.part`` is atomically renamed to ``dest``.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=timeout)
    part = dest + ".part"

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    try:
        final_url, probed_size, accepts_ranges = await _resolve(client, url, token)
        size = expected_size or probed_size

        # Single-stream fallback: server won't range, or we don't know the size.
        if not accepts_ranges or not size:
            await _stream_single(client, final_url, token, part, on_bytes, _cancelled)
        else:
            done = _load_done(part, size)
            _preallocate(part, size)
            segments = [s for s in _plan_segments(size, num_connections) if s not in done]
            lock = asyncio.Lock()
            # Segments are bounded, so a big file yields many more of them than
            # there are connections. The semaphore — not the segment count — is
            # what keeps concurrency at num_connections.
            sem = asyncio.Semaphore(max(1, num_connections))

            async def _fetch(seg: tuple[int, int]):
                start, end = seg
                want = end - start + 1
                headers = {**_auth_headers(final_url, token), "Range": f"bytes={start}-{end}"}
                async with client.stream("GET", final_url, headers=headers) as r:
                    r.raise_for_status()
                    match = re.fullmatch(r"bytes\s+([0-9]+)-([0-9]+)/([0-9]+|\*)",
                                         r.headers.get("content-range", "").strip(), re.IGNORECASE)
                    if (r.status_code != 206 or match is None
                            or (int(match[1]), int(match[2])) != (start, end)
                            or (match[3] != "*" and int(match[3]) != size)):
                        raise ValueError(f"invalid response range for bytes {start}-{end}/{size}")
                    got = 0
                    with open(part, "r+b") as fh:
                        fh.seek(start)
                        async for chunk in r.aiter_bytes(_READ_CHUNK):
                            if _cancelled():
                                raise DownloadCancelled()
                            fh.write(chunk)
                            got += len(chunk)
                            if on_bytes:
                                on_bytes(len(chunk))
                # Truncation guard: preallocation makes the file `size` bytes
                # regardless of what arrived, so the per-segment received count
                # — not the file size — is what proves the bytes are real.
                if got != want:
                    raise ValueError(f"segment {start}-{end} short read: got {got}, want {want}")
                async with lock:
                    done.add(seg)
                    _save_done(part, size, done)

            async def _fetch_limited(seg: tuple[int, int]):
                async with sem:
                    await _fetch(seg)

            if segments:
                tasks = [asyncio.create_task(_fetch_limited(s)) for s in segments]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    # gather propagates one failed range without stopping its
                    # siblings. Retire every writer before releasing the client
                    # or letting a retry reuse the partial file and manifest.
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise

        # ── verify ──────────────────────────────────────────────────────
        actual = os.path.getsize(part)
        if size and actual != size:
            raise ValueError(f"size mismatch: got {actual}, expected {size}")
        # etag is typically the sha256 (LFS) — verify when it looks like a hash
        if expected_etag:
            tag = expected_etag.strip('"')
            if len(tag) == 64 and all(c in "0123456789abcdef" for c in tag.lower()):
                if _sha256(part) != tag.lower():
                    raise ValueError("sha256 mismatch — download corrupt")

        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        os.replace(part, dest)
        try:
            os.remove(_manifest_path(part))
        except OSError:
            pass
        return dest
    finally:
        if own_client:
            await client.aclose()


async def _stream_single(client, url, token, part, on_bytes, cancelled) -> None:
    async with client.stream("GET", url, headers=_auth_headers(url, token)) as r:
        r.raise_for_status()
        with open(part, "wb") as fh:
            async for chunk in r.aiter_bytes(_READ_CHUNK):
                if cancelled():
                    raise DownloadCancelled()
                fh.write(chunk)
                if on_bytes:
                    on_bytes(len(chunk))


def _invalidate_done(part: str) -> None:
    try:
        os.remove(_manifest_path(part))
    except FileNotFoundError:
        pass  # already absent: the goal of invalidation is met


def _preallocate(part: str, size: int) -> None:
    # A checkpoint only certifies the original bytes, not a resized replacement.
    try:
        fh = open(part, "r+b")
    except FileNotFoundError:
        # Invalidate before creating a missing partial, too: an unlink failure
        # must leave both the original partial state and destination untouched.
        _invalidate_done(part)
        fh = open(part, "a+b")
    with fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() != size:
            _invalidate_done(part)
            fh.truncate(size)


def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
