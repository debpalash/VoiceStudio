"""Quiet-but-alive tasks keep the SSE connection warm via comment frames."""
import asyncio

import pytest

from api.routers import dub_export as export_router

COMMENT = ": keepalive\n\n"


class _FakeManager:
    """Just enough of the task manager for the stream endpoint."""

    def __init__(self, task_id):
        self.task_id = task_id
        self.queues = []
        self.active_tasks = {task_id: {"status": "running"}}

    async def add_listener(self, task_id, queue):
        assert task_id == self.task_id
        self.queues.append(queue)
        return True

    async def remove_listener(self, task_id, queue):
        self.queues.remove(queue)


@pytest.fixture
def quiet_stream(monkeypatch):
    from core import job_store

    manager = _FakeManager("job-1")
    monkeypatch.setattr(export_router, "task_manager", manager)
    monkeypatch.setattr(export_router, "TASK_STREAM_KEEPALIVE_S", 0.01)
    monkeypatch.setattr(job_store, "get", lambda _id: None)
    monkeypatch.setattr(job_store, "events_since", lambda _id, after_seq=0: [])
    return manager


async def _next_frame(iterator, limit=2.0):
    return await asyncio.wait_for(iterator.__anext__(), limit)


def test_idle_stream_emits_only_comment_frames_and_cleans_up(quiet_stream):
    async def run():
        response = await export_router.stream_task("job-1")
        headers = {k.lower(): v for k, v in response.headers.items()}
        frames = [await _next_frame(response.body_iterator) for _ in range(4)]
        await response.body_iterator.aclose()
        return headers, frames

    headers, frames = asyncio.run(run())
    assert frames == [COMMENT] * 4
    assert "no-transform" in headers["cache-control"]
    assert headers["x-accel-buffering"] == "no"
    assert quiet_stream.queues == []


def test_queued_events_survive_keepalives_and_none_ends_stream(quiet_stream):
    async def run():
        response = await export_router.stream_task("job-1")
        it = response.body_iterator
        assert await _next_frame(it) == COMMENT
        queue = quiet_stream.queues[0]
        for item in ("data: one\n\n", "data: two\n\n", None):
            queue.put_nowait(item)
        seen = []
        async for frame in it:
            if frame != COMMENT:
                seen.append(frame)
        return seen

    assert asyncio.run(asyncio.wait_for(run(), 5)) == ["data: one\n\n", "data: two\n\n"]
    assert quiet_stream.queues == []
