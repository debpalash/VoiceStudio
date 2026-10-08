"""FDL-08: segmented (multi-connection) downloader — correctness + auth safety.

Each test runs a single self-contained coroutine via ``asyncio.run`` (fresh
event loop per call). Do NOT use ``asyncio.get_event_loop()`` here: in the full
suite an earlier async test can leave the global loop closed, which would make
these RuntimeError even though they pass standalone.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

import services.segmented_download as sd
from services.segmented_download import (
    segmented_download,
    DownloadCancelled,
    _plan_segments,
    _MAX_SEGMENT_BYTES,
)


PAYLOAD = bytes((i % 256) for i in range(1_000_000))  # 1 MB deterministic body


def _ranged_handler(payload=PAYLOAD, *, accept_ranges=True, record=None):
    """A mock origin that honours Range requests over `payload`."""
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        if request.method == "HEAD":
            h = {"content-length": str(len(payload))}
            if accept_ranges:
                h["accept-ranges"] = "bytes"
            return httpx.Response(200, headers=h)
        rng = request.headers.get("range")
        if rng and accept_ranges:
            lo, hi = rng.replace("bytes=", "").split("-")
            lo, hi = int(lo), int(hi)
            return httpx.Response(206, headers={"Content-Range": f"bytes {lo}-{hi}/{len(payload)}"}, content=payload[lo:hi + 1])
        return httpx.Response(200, content=payload)
    return handler


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _download(handler, dest, **kw):
    """Run one segmented_download against a mock origin in a fresh event loop."""
    url = kw.pop("url", "https://cdn.example.com/f.bin")

    async def _do():
        async with _client(handler) as client:
            return await segmented_download(url, dest, client=client, **kw)

    return asyncio.run(_do())


def test_parallel_ranges_reassemble_exactly(tmp_path):
    dest = str(tmp_path / "model.bin")
    _download(_ranged_handler(), dest, expected_size=len(PAYLOAD), num_connections=8)
    with open(dest, "rb") as f:
        assert f.read() == PAYLOAD
    assert not os.path.exists(dest + ".part")
    assert not os.path.exists(dest + ".part.done")


def test_single_stream_fallback_when_no_range(tmp_path):
    dest = str(tmp_path / "f.bin")
    _download(_ranged_handler(accept_ranges=False), dest, expected_size=len(PAYLOAD))
    with open(dest, "rb") as f:
        assert f.read() == PAYLOAD


def test_auth_header_never_sent_to_cdn_host(tmp_path):
    record = []
    dest = str(tmp_path / "f.bin")
    _download(_ranged_handler(record=record), dest,
              url="https://cdn.cloudfront.net/blob",  # NOT a huggingface.co host
              token="hf_secrettoken", expected_size=len(PAYLOAD))
    assert record
    assert all("authorization" not in {k.lower() for k in r.headers} for r in record), \
        "Authorization must never be sent to a non-huggingface.co host"


def test_auth_header_sent_to_hf_host(tmp_path):
    record = []
    dest = str(tmp_path / "f.bin")
    _download(_ranged_handler(record=record), dest,
              url="https://huggingface.co/api/x/resolve/main/f",
              token="hf_tok", expected_size=len(PAYLOAD))
    assert any(r.headers.get("authorization") == "Bearer hf_tok" for r in record)


def test_size_mismatch_raises(tmp_path):
    dest = str(tmp_path / "f.bin")
    with pytest.raises(ValueError):
        _download(_ranged_handler(), dest, expected_size=len(PAYLOAD) + 999)
    assert not os.path.exists(dest)


def test_cancel_raises_and_leaves_no_commit(tmp_path):
    dest = str(tmp_path / "f.bin")
    with pytest.raises(DownloadCancelled):
        _download(_ranged_handler(), dest, expected_size=len(PAYLOAD), cancel_check=lambda: True)
    assert not os.path.exists(dest)


def test_on_bytes_reports_total(tmp_path):
    dest = str(tmp_path / "f.bin")
    seen = []
    _download(_ranged_handler(), dest, expected_size=len(PAYLOAD), on_bytes=lambda d: seen.append(d))
    assert sum(seen) == len(PAYLOAD)


# ── #1224 follow-up: bounded segments so a flaky link makes progress ────────
#
# Progress is committed to the manifest only when a WHOLE segment lands, so the
# segment size is also the most bytes a dropped connection can throw away.
# Sizing segments as size/num_connections made that ~100 MB on an 800 MB blob:
# on a link dropping every ~50 MB no segment ever completed, the manifest was
# never written, and every retry restarted from zero.

def test_large_file_is_split_into_bounded_segments():
    """Fail-before: the old plan returned 8 segments of ~100 MB for this size."""
    size = 805_665_628  # the k2-fsa/OmniVoice blob that reproduced the stall
    segs = _plan_segments(size, 8)

    assert max(e - s + 1 for s, e in segs) <= _MAX_SEGMENT_BYTES
    assert len(segs) > 8, "segment count must not be capped by num_connections"
    # Exact cover: no gap, no overlap, no byte past the end.
    assert segs[0][0] == 0 and segs[-1][1] == size - 1
    assert all(segs[i][1] + 1 == segs[i + 1][0] for i in range(len(segs) - 1))


def test_small_file_still_single_segment():
    """The cap must not shard tiny files into per-request overhead."""
    assert len(_plan_segments(1_000_000, 8)) == 1


def test_concurrency_stays_at_num_connections(tmp_path, monkeypatch):
    """Many bounded segments must not all fire at once.

    The handler has to HOLD requests open: a synchronous mock returns before any
    other task is scheduled, so nothing ever overlaps and the assertion passes
    without exercising the semaphore at all.
    """
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 16 * 1024)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 32 * 1024)
    connections = 4
    assert len(_plan_segments(len(PAYLOAD), connections)) > connections, (
        "test needs more segments than connections"
    )
    dest = str(tmp_path / "m.bin")
    state = {"inflight": 0, "peak": 0}

    async def _run():
        saturated = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(200, headers={
                    "content-length": str(len(PAYLOAD)), "accept-ranges": "bytes"})
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            try:
                if state["inflight"] >= connections:
                    saturated.set()
                # Hold the range open until the pool fills, so overlap is
                # observable without sleeping. The timeout keeps an
                # over-restrictive semaphore a failure instead of a hang.
                try:
                    await asyncio.wait_for(saturated.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                lo, hi = request.headers["range"].replace("bytes=", "").split("-")
                return httpx.Response(206, headers={"Content-Range": f"bytes {lo}-{hi}/{len(PAYLOAD)}"}, content=PAYLOAD[int(lo):int(hi) + 1])
            finally:
                state["inflight"] -= 1

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client:
            await segmented_download(
                "https://cdn.example.com/f.bin", dest, client=client,
                expected_size=len(PAYLOAD), num_connections=connections,
            )

    asyncio.run(_run())
    assert state["peak"] == connections, (
        f"expected exactly {connections} ranges in flight, saw {state['peak']}"
    )
    with open(dest, "rb") as f:
        assert f.read() == PAYLOAD


def test_dropped_connection_resumes_from_manifest(tmp_path, monkeypatch):
    """A drop must cost one segment, not the whole file."""
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 16 * 1024)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 32 * 1024)
    dest = str(tmp_path / "m.bin")
    served = []
    fail_after = {"n": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={
                "content-length": str(len(PAYLOAD)), "accept-ranges": "bytes"})
        if fail_after["n"] > 0:
            fail_after["n"] -= 1
            if fail_after["n"] == 0:
                raise httpx.RemoteProtocolError("peer closed connection", request=request)
        lo, hi = request.headers["range"].replace("bytes=", "").split("-")
        served.append(int(hi) - int(lo) + 1)
        return httpx.Response(206, headers={"Content-Range": f"bytes {lo}-{hi}/{len(PAYLOAD)}"}, content=PAYLOAD[int(lo):int(hi) + 1])

    with pytest.raises(httpx.RemoteProtocolError):
        _download(handler, dest, expected_size=len(PAYLOAD), num_connections=2)

    assert os.path.exists(dest + ".part.done"), "completed segments must be committed"
    before = sum(served)
    assert before > 0

    _download(handler, dest, expected_size=len(PAYLOAD), num_connections=2)
    with open(dest, "rb") as f:
        assert f.read() == PAYLOAD
    # Resumed, not restarted: total bytes served stay below two full copies.
    assert sum(served) < 2 * len(PAYLOAD)
    assert sum(served) >= len(PAYLOAD)


def test_failed_segment_cancels_and_awaits_sibling_writers():
    """#2642: after one range fails, no sibling may still be running/writing."""
    state = {"ticks": 0, "cancelled": False}

    async def writer():
        try:
            while True:
                state["ticks"] += 1
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            state["cancelled"] = True
            await asyncio.sleep(0.01)  # cleanup that must be awaited
            state["cleaned"] = True
            raise

    async def failing():
        await asyncio.sleep(0.02)
        raise ValueError("boom")

    async def _do():
        with pytest.raises(ValueError, match="boom"):
            await sd._gather_or_cancel([writer(), writer(), failing()])
        seen = state["ticks"]
        await asyncio.sleep(0.05)
        return seen

    seen = asyncio.run(_do())
    assert state["cancelled"] and state.get("cleaned")
    assert state["ticks"] == seen


def test_segment_http_error_stops_other_segments(tmp_path):
    """#2642 end to end: a 500 on one range leaves no stray writers."""
    calls = []

    def handler(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(PAYLOAD)), "accept-ranges": "bytes"})
        calls.append(request.headers["range"])
        if len(calls) == 1:
            return httpx.Response(500)
        lo, hi = map(int, request.headers["range"].replace("bytes=", "").split("-"))
        return httpx.Response(206, headers={"Content-Range": f"bytes {lo}-{hi}/{len(PAYLOAD)}"}, content=PAYLOAD[lo:hi + 1])

    async def _do():
        with pytest.raises(httpx.HTTPStatusError):
            async with _client(handler) as client:
                await segmented_download("https://cdn.example.com/f.bin", str(tmp_path / "f.bin"),
                                         client=client, num_connections=4)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    assert asyncio.run(_do()) == []
