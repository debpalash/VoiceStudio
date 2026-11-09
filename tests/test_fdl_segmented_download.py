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
    DownloadCancelled,
    _MAX_SEGMENT_BYTES,
    _plan_segments,
    segmented_download,
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


def test_planner_tiles_a_large_object_with_checkpoint_sized_ranges():
    object_size = 777 * 1024 * 1024 + 23
    ranges = _plan_segments(object_size, 8)

    assert len(ranges) > 8
    assert max(end - start + 1 for start, end in ranges) <= _MAX_SEGMENT_BYTES
    cursor = 0
    for start, end in ranges:
        assert start == cursor
        cursor = end + 1
    assert cursor == object_size


def test_planner_does_not_fragment_a_subminimum_object():
    object_size = 913_777
    assert _plan_segments(object_size, 32) == [(0, object_size - 1)]


def test_range_pool_never_exceeds_requested_width(monkeypatch, tmp_path):
    payload = bytes(range(91))
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 13)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 13)
    activity = {"now": 0, "high_water": 0}
    all_workers_arrived = asyncio.Event()

    async def handler(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(payload)), "accept-ranges": "bytes"})
        lo, hi = map(int, request.headers["range"].replace("bytes=", "").split("-"))
        activity["now"] += 1
        activity["high_water"] = max(activity["high_water"], activity["now"])
        if activity["now"] == 4:
            all_workers_arrived.set()
        await all_workers_arrived.wait()
        activity["now"] -= 1
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {lo}-{hi}/{len(payload)}"},
            content=payload[lo:hi + 1],
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await segmented_download(
                "https://origin.invalid/weights", str(tmp_path / "pooled.bin"),
                expected_size=len(payload), num_connections=4, client=client,
            )

    asyncio.run(run())
    assert activity["high_water"] == 4
    assert (tmp_path / "pooled.bin").read_bytes() == payload


def test_second_invocation_uses_ranges_committed_before_disconnect(monkeypatch, tmp_path):
    payload = bytes(range(55))
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 11)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 11)
    transferred = 0
    link_is_flaky = True

    async def handler(request):
        nonlocal transferred
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": "55", "accept-ranges": "bytes"})
        lo, hi = map(int, request.headers["range"].replace("bytes=", "").split("-"))
        if link_is_flaky and lo > 0:
            raise httpx.RemoteProtocolError("fixture closed the stream")
        body = payload[lo:hi + 1]
        transferred += len(body)
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {lo}-{hi}/55"},
            content=body,
        )

    async def download():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await segmented_download(
                "https://origin.invalid/archive", str(tmp_path / "resumed.bin"),
                expected_size=55, num_connections=1, client=client,
            )

    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(download())
    assert (tmp_path / "resumed.bin.part.done").is_file()
    link_is_flaky = False
    asyncio.run(download())
    assert (tmp_path / "resumed.bin").read_bytes() == payload
    assert len(payload) <= transferred < 2 * len(payload)


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


def test_http_error_on_one_range_leaves_no_stray_writers(tmp_path):
    """#2642 end to end: a 500 on one range must not leave other ranges running."""
    ranged_gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200, headers={"content-length": str(len(PAYLOAD)), "accept-ranges": "bytes"}
            )
        rng = request.headers.get("range")
        if not rng:
            return httpx.Response(200, content=PAYLOAD)
        ranged_gets["n"] += 1
        if ranged_gets["n"] == 1:
            return httpx.Response(500)
        lo, hi = (int(v) for v in rng.replace("bytes=", "").split("-"))
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {lo}-{hi}/{len(PAYLOAD)}"},
            content=PAYLOAD[lo:hi + 1],
        )

    async def _do():
        async with _client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await segmented_download(
                    "https://cdn.example.com/broken.bin",
                    str(tmp_path / "broken.bin"),
                    client=client,
                    num_connections=4,
                )
        current = asyncio.current_task()
        return [t for t in asyncio.all_tasks() if t is not current]

    leftovers = asyncio.run(_do())
    assert leftovers == []
