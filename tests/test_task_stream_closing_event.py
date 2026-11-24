"""A replayed task stream always ends with a terminal event (#4463).

When the process running a task dies (an OOM kill mid-separation, a crash),
the restarted backend marks the job ``failed`` but its event tail stops at the
last progress event. ``/tasks/stream`` must still end the replay with that
outcome, or a resuming client waits on a task that no longer exists.
"""
import asyncio
import json
import os
import time

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from api.routers import dub_export as export_router
from core import job_store
from core.db import init_db


class _RestartedManager:
    """A task manager after a restart: no task is live."""

    active_tasks: dict = {}


@pytest.fixture(autouse=True)
def restarted(monkeypatch):
    init_db()
    monkeypatch.setattr(export_router, "task_manager", _RestartedManager())


def _job(prefix: str) -> str:
    job_id = f"{prefix}_{time.time_ns()}_{os.getpid()}"
    job_store.create(job_id, type="dub_prep")
    job_store.mark_running(job_id)
    return job_id


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _replay(job_id: str, after_seq: int = 0) -> list[str]:
    async def run():
        response = await export_router.stream_task(job_id, after_seq=after_seq)
        return [frame async for frame in response.body_iterator]

    return asyncio.run(asyncio.wait_for(run(), 5))


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: "):])


def test_a_job_swept_after_a_crash_replays_its_failure():
    job_id = _job("orphan")
    progress = _frame({"type": "demucs_retry", "attempt": "cpu"})
    job_store.append_event(job_id, progress)
    job_store.sweep_orphans_on_startup()

    frames = _replay(job_id)

    assert frames[0] == progress
    assert len(frames) == 2
    closing = _payload(frames[1])
    assert closing["type"] == "error"
    assert closing["reason"] == job_store.INTERRUPTED_BY_RESTART
    assert closing["stage"] == "task"


def test_a_reconnect_past_the_last_event_still_gets_the_outcome():
    job_id = _job("caught_up")
    seq = job_store.append_event(job_id, _frame({"type": "extract_done"}))
    job_store.sweep_orphans_on_startup()

    [closing] = _replay(job_id, after_seq=seq)

    assert _payload(closing)["type"] == "error"


def test_a_job_that_died_before_any_event_still_ends():
    job_id = _job("silent")
    job_store.sweep_orphans_on_startup()

    [closing] = _replay(job_id)

    assert _payload(closing)["reason"] == job_store.INTERRUPTED_BY_RESTART


def test_the_recorded_error_is_kept_when_the_failure_event_was_lost():
    job_id = _job("unpersisted")
    job_store.append_event(job_id, _frame({"type": "progress"}))
    job_store.mark_failed(job_id, "Separation failed: out of memory")

    closing = _payload(_replay(job_id)[-1])

    assert closing["type"] == "error"
    assert "out of memory" in closing["reason"]


def test_a_cancelled_job_without_its_event_replays_cancelled():
    job_id = _job("cancelled")
    job_store.append_event(job_id, _frame({"type": "progress"}))
    job_store.mark_cancelled(job_id)

    assert _payload(_replay(job_id)[-1]) == {"type": "cancelled"}


@pytest.mark.parametrize(
    "terminal",
    [
        _frame({"type": "error", "reason": "fixture failure"}),
        'event: error\ndata: {"reason": "named fixture failure"}\n\n',
        _frame({"type": "cancelled"}),
    ],
)
def test_a_tail_that_already_ends_is_replayed_unchanged(terminal):
    job_id = _job("ended")
    job_store.append_event(job_id, _frame({"type": "progress"}))
    job_store.append_event(job_id, terminal)
    job_store.mark_failed(job_id, "fixture failure")

    assert _replay(job_id)[-1] == terminal
    assert len(_replay(job_id)) == 2


def test_a_finished_job_gets_no_invented_event():
    # Prep ends with ``ready``, not ``done``: a completed job must not get an
    # error appended, and a ``done`` event's results cannot be rebuilt.
    job_id = _job("finished")
    ready = _frame({"type": "ready", "job_id": "fixture"})
    job_store.append_event(job_id, ready)
    job_store.mark_done(job_id)

    assert _replay(job_id) == [ready]


def test_an_unknown_task_is_still_not_found():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(export_router.stream_task(f"missing_{time.time_ns()}"))
    assert caught.value.status_code == 404
