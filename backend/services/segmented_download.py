"""Resumable multi-connection segmented downloader (FDL-08).

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

This module is framework-free, with real loopback HTTP and MockTransport tests;
the HF-cache integration lives in the setup router.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
from typing import Callable, Optional

import httpx

from services.hf_auth import host_gets_auth as _host_gets_auth
from core.url_safety import UnsafeURLError, check_public_url
from services.public_download_transport import PublicDownloadTransport
from services import _download_policy

_DEFAULT_CONNECTIONS = 8
_MIN_SEGMENT_BYTES = 4 * 1024 * 1024   # don't split below this — overhead > gain
_MAX_SEGMENT_BYTES = 16 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


class DownloadCancelled(Exception):
    """Raised when ``cancel_check()`` returns True mid-download."""


class DownloadIntegrityError(ValueError):
    """Verified bytes were corrupt; never install them through a fallback."""


def _auth_headers(url: str, token: Optional[str]) -> dict:
    if token and _host_gets_auth(url):
        return {"Authorization": f"Bearer {token}"}
    return {}


def _hop_request(client, method, url, token, origin_url, *, headers=None):
    """Build once, then bound client credentials to the initial logical origin."""
    request = client.build_request(method, url, headers=headers)
    origin, target = httpx.URL(origin_url), httpx.URL(url)
    def identity(value):
        return value.scheme, value.raw_host, value.port or (443 if value.scheme == "https" else 80)
    same_origin = identity(origin) == identity(target)
    secure_upgrade = (
        origin.scheme == "http" and target.scheme == "https"
        and origin.raw_host == target.raw_host
        and origin.port in (None, 80) and target.port in (None, 443)
    )
    auth = httpx.USE_CLIENT_DEFAULT
    if not (same_origin or secure_upgrade):
        request.headers.pop("Authorization", None)
        request.headers.pop("Cookie", None)
        auth = None
    explicit = _auth_headers(url, token)
    if explicit:
        # An explicit HF token is authoritative; client.auth must not replace it.
        request.headers.update(explicit)
        auth = None
    return request, auth


@asynccontextmanager
async def _hop_stream(client, url, token, origin_url, *, headers=None):
    request, auth = _hop_request(client, "GET", url, token, origin_url, headers=headers)
    response = await client.send(request, auth=auth, stream=True, follow_redirects=False)
    try:
        yield response
    finally:
        await response.aclose()


async def _resolve(client: httpx.AsyncClient, url: str, token: Optional[str], max_hops: int = 10):
    """Follow redirects manually, dropping the auth header on any cross-host hop.

    Redirect targets receive an early public-destination check. The guarded
    transport also resolves and pins every HEAD/body/range request to validated
    addresses, including proxy targets and requests that might reuse a pool.
    Private mirrors require the existing explicit private-URL opt-in.

    Returns (final_url, size_or_None, accepts_ranges_bool).
    """
    cur = url
    for _ in range(max_hops):
        _require_safe_client(client, cur)
        if isinstance(client, httpx.AsyncClient):
            request, auth = _hop_request(client, "HEAD", cur, token, url)
            r = await client.send(request, auth=auth, follow_redirects=False)
        else:
            r = await client.head(cur, headers=_auth_headers(cur, token), follow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
            target = str(httpx.URL(cur).join(r.headers["location"]))
            await _download_policy.run_check(
                check_public_url, target,
                timeout=getattr(getattr(client, "timeout", None), "connect", None),
            )
            cur = target
            continue
        r.raise_for_status()
        size = r.headers.get("content-length")
        size = int(size) if size and size.isdigit() else None
        accepts = r.headers.get("accept-ranges", "").lower() == "bytes"
        return cur, size, accepts
    raise httpx.TooManyRedirects(f"exceeded {max_hops} redirects for {url}")


def _require_safe_client(client, url):
    """Network clients must not silently bypass the download-only transport.

    The injected client seam remains available for network-free MockTransport
    fixtures and explicitly opted-in private mirrors. An ordinary shared client
    must be given PublicDownloadTransport rather than weakening another user's
    pool or mutating a process-wide resolver.
    """
    from core.url_safety import private_url_imports_allowed
    if isinstance(client, httpx.AsyncClient) and not private_url_imports_allowed():
        transport = client._transport_for_url(httpx.URL(url))
        if not isinstance(transport, (PublicDownloadTransport, httpx.MockTransport)):
            raise UnsafeURLError('Model download client must use the address-pinned transport')


@asynccontextmanager
async def _stream_get(client, url, token, cancelled, *, headers=None, max_hops=10, origin_url=None):
    """Keep GET-only redirects inside the pinned transfer and policy boundary.

    Some CDNs answer HEAD directly but redirect the body or a range request.
    A refused Location must remain UnsafeURLError, rather than a generic HTTP
    failure that the installer may retry through the legacy Hub transfer.
    """
    cur = url
    for _ in range(max_hops):
        if cancelled():
            raise DownloadCancelled()
        _require_safe_client(client, cur)
        async with _hop_stream(client, cur, token, origin_url or url, headers=headers) as response:
            if response.status_code in (301, 302, 303, 307, 308) and 'location' in response.headers:
                if cancelled():
                    raise DownloadCancelled()
                target = str(httpx.URL(cur).join(response.headers['location']))
                await _download_policy.run_check(
                    check_public_url, target,
                    timeout=getattr(getattr(client, "timeout", None), "connect", None),
                )
                cur = target
                continue
            response.raise_for_status()
            yield response
            return
    raise httpx.TooManyRedirects(f'exceeded {max_hops} redirects for {url}')


def _plan_segments(size: int, num_connections: int) -> list[tuple[int, int]]:
    """Plan contiguous ranges, limiting resumable work independently of concurrency."""
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
    - ``cancel_check()`` is polled between transfer/hash chunks and before
      publication; returning True raises
      :class:`DownloadCancelled` and leaves the ``.part`` for a later resume.
    - On success the size (and ``expected_etag`` if given) is verified, then the
      ``.part`` is atomically renamed to ``dest``.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(
        transport=PublicDownloadTransport(), follow_redirects=False, timeout=timeout,
    )
    part = dest + ".part"

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    def _check_cancelled():
        if _cancelled():
            raise DownloadCancelled()

    policy_token = _download_policy.set_cancellation_check(_check_cancelled)
    try:
        _check_cancelled()
        final_url, probed_size, accepts_ranges = await _resolve(client, url, token)
        _require_safe_client(client, final_url)
        size = expected_size or probed_size

        # Single-stream fallback: server won't range, or we don't know the size.
        if not accepts_ranges or not size:
            await _stream_single(client, final_url, token, part, on_bytes, _cancelled, origin_url=url)
        else:
            done = _load_done(part, size)
            _preallocate(part, size)
            segments = [s for s in _plan_segments(size, num_connections) if s not in done]
            lock = asyncio.Lock()
            semaphore = asyncio.Semaphore(max(1, num_connections))

            async def _fetch(seg: tuple[int, int]):
                start, end = seg
                want = end - start + 1
                headers = {"Range": f"bytes={start}-{end}"}
                async with _stream_get(client, final_url, token, _cancelled, headers=headers, origin_url=url) as r:
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

            async def _limited_fetch(seg: tuple[int, int]):
                async with semaphore:
                    await _fetch(seg)

            if segments:
                await _gather_or_cancel([_limited_fetch(s) for s in segments])

        # ── verify ──────────────────────────────────────────────────────
        if _cancelled():
            raise DownloadCancelled()
        actual = os.path.getsize(part)
        if size and actual != size:
            raise ValueError(f"size mismatch: got {actual}, expected {size}")
        # Hub metadata uses a Git blob object ID for regular files and a
        # SHA-256 digest for LFS files. Neither is just a size check.
        if expected_etag:
            tag = expected_etag.strip('"').lower()
            if len(tag) in (40, 64) and all(c in "0123456789abcdef" for c in tag):
                if _content_hash(part, git_blob=len(tag) == 40, cancel_check=_cancelled) != tag:
                    # Completed ranges only certify transferred bytes, not
                    # integrity. Retrying must not trust rejected bytes again.
                    algorithm = "git blob sha1" if len(tag) == 40 else "sha256"
                    message = f"{algorithm} mismatch — download corrupt"
                    try:
                        _invalidate_done(part)
                    except OSError as error:
                        raise DownloadIntegrityError(
                            f"{message}; completed-range checkpoint could not be cleared"
                        ) from error
                    raise DownloadIntegrityError(message)

        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        if _cancelled():
            raise DownloadCancelled()
        os.replace(part, dest)
        try:
            os.remove(_manifest_path(part))
        except OSError:
            pass
        return dest
    finally:
        _download_policy.reset_cancellation_check(policy_token)
        if own_client:
            await client.aclose()


async def _gather_or_cancel(coros) -> None:
    """Run ``coros`` concurrently; on the first failure (or outer cancellation)
    cancel the siblings and *await* them, so no writer is still touching the
    ``.part`` file once this returns or raises. ``asyncio.gather`` alone
    re-raises immediately and leaves the siblings running.
    """
    with _download_policy.range_policy_scope() as policy:
        tasks = [asyncio.ensure_future(c) for c in coros]
        cancelled_during_cleanup = False
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            drained = asyncio.gather(*tasks, return_exceptions=True)
            while not drained.done():
                try:
                    await asyncio.shield(drained)
                except asyncio.CancelledError:
                    cancelled_during_cleanup = True
            if cancelled_during_cleanup:
                raise asyncio.CancelledError()
        failures = [task.exception() for task in tasks if not task.cancelled() and task.exception() is not None]
        # Known refusals beat cancellation; an interrupted check cannot become
        # an ordinary range error eligible for the unpinned legacy fallback.
        for terminal in (UnsafeURLError, DownloadCancelled):
            for error in failures:
                if isinstance(error, terminal):
                    raise error
        if policy.interrupted:
            raise UnsafeURLError("Model download destination validation was interrupted")
        if failures:
            raise failures[0]


async def _stream_single(client, url, token, part, on_bytes, cancelled, *, origin_url=None) -> None:
    async with _stream_get(client, url, token, cancelled, origin_url=origin_url) as r:
        if cancelled():
            raise DownloadCancelled()
        # A fallback replaces the partial's bytes; invalidate their old proof
        # before truncation, so cleanup failure preserves the resumable file.
        _invalidate_done(part)
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


def _content_hash(
    path: str, *, git_blob: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> str:
    import hashlib
    h = hashlib.sha1(usedforsecurity=False) if git_blob else hashlib.sha256()
    if git_blob:
        h.update(f"blob {os.path.getsize(path)}\0".encode("ascii"))
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            if cancel_check and cancel_check():
                raise DownloadCancelled()
            h.update(block)
    return h.hexdigest()
