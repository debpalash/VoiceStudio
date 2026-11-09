"""Keepalive behavior for quiet generic and long-form response streams."""

import asyncio
import contextvars

import pytest

from core import stream_keepalive
from core.stream_keepalive import SSE_KEEPALIVE, with_keepalive


def test_slow_source_gets_repeated_pings_without_reordering_items():
    async def source():
        await asyncio.sleep(0.035)
        yield "first"
        await asyncio.sleep(0.025)
        yield "last"

    async def collect():
        return [item async for item in with_keepalive(source(), "ping", 0.01)]

    output = asyncio.run(collect())
    assert output[0] == "ping"
    assert output.count("ping") >= 3
    assert [item for item in output if item != "ping"] == ["first", "last"]
    assert output[-1] == "last"


def test_fast_source_has_no_pings():
    async def source():
        for item in ("north", "south", "west"):
            yield item

    async def collect():
        return [item async for item in with_keepalive(source(), "ping", 60)]

    assert asyncio.run(collect()) == ["north", "south", "west"]


def test_source_failure_is_raised_after_earlier_items():
    class StreamFailure(RuntimeError):
        pass

    async def source():
        yield "before"
        raise StreamFailure("broken source")

    async def consume():
        iterator = with_keepalive(source(), "ping", 60)
        assert await anext(iterator) == "before"
        with pytest.raises(StreamFailure, match="^broken source$"):
            await anext(iterator)

    asyncio.run(consume())


def test_source_steps_share_context_across_a_ping():
    marker = contextvars.ContextVar("stream_marker", default="unset")

    async def source():
        marker.set("carried")
        yield "set"
        await asyncio.sleep(0.025)
        yield marker.get()

    async def collect():
        return [item async for item in with_keepalive(source(), "ping", 0.01)]

    output = asyncio.run(collect())
    assert output[0] == "set"
    assert "ping" in output[1:-1]
    assert output[-1] == "carried"


def test_close_cancels_blocked_step_then_finalizes_source():
    events = []

    async def source():
        try:
            yield "ready"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("cancelled")
                raise
        finally:
            events.append("finalized")

    async def close_during_ping():
        iterator = with_keepalive(source(), "ping", 0.01)
        assert await anext(iterator) == "ready"
        assert await anext(iterator) == "ping"
        await iterator.aclose()
        assert events == ["cancelled", "finalized"]

    asyncio.run(close_during_ping())


def test_cancelled_consumer_finalizes_blocked_source():
    finalized = asyncio.Event()

    async def source():
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            finalized.set()

    async def cancel_consumer():
        iterator = with_keepalive(source(), "ping", 60)
        consumer = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await asyncio.wait_for(finalized.wait(), 0.5)

    asyncio.run(cancel_consumer())


def test_longform_render_response_keeps_silent_sse_alive(monkeypatch):
    from api.routers import audiobook

    started = "data: {\"type\": \"started\"}\n\n"
    finished = "data: {\"type\": \"done\"}\n\n"

    async def fake_render(*_args, **_kwargs):
        yield started
        await asyncio.sleep(0.035)
        yield finished

    monkeypatch.setattr(audiobook, "_ensure_active_engine_licence", lambda: None)
    monkeypatch.setattr(audiobook, "_public_longform_stream", fake_render)
    monkeypatch.setattr(stream_keepalive, "SILENCE_INTERVAL_SECONDS", 0.01)

    async def render():
        response = await audiobook.longform_render(
            audiobook.LongformRenderRequest()
        )
        body = "".join([frame async for frame in response.body_iterator])
        return response, body

    response, body = asyncio.run(render())
    assert response.headers["x-accel-buffering"] == "no"
    assert SSE_KEEPALIVE in body
    assert body.replace(SSE_KEEPALIVE, "") == started + finished
