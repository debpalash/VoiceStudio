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

from types import SimpleNamespace

import pytest


class _RestartedManager:
    """A task manager after a restart: no task is live."""

    active_tasks: dict = {}


@pytest.fixture(autouse=True)
def app(monkeypatch):
    # Resolved per test: the suite purges and re-imports app modules.
    from api.routers import dub_export
    from core import job_store
    from core.db import init_db

    init_db()
    monkeypatch.setattr(dub_export, "task_manager", _RestartedManager())
    return SimpleNamespace(router=dub_export, job_store=job_store)


def _job(job_store, prefix: str) -> str:
    job_id = f"{prefix}_{time.time_ns()}_{os.getpid()}"
    job_store.create(job_id, type="dub_prep")
    job_store.mark_running(job_id)
    return job_id


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _replay(router, job_id: str, after_seq: int = 0) -> list[str]:
    async def run():
        response = await router.stream_task(job_id, after_seq=after_seq)
        return [frame async for frame in response.body_iterator]

    return asyncio.run(asyncio.wait_for(run(), 5))


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: "):])


def test_a_job_swept_after_a_crash_replays_its_failure(app):
    job_id = _job(app.job_store, "orphan")
    progress = _frame({"type": "demucs_retry", "attempt": "cpu"})
    app.job_store.append_event(job_id, progress)
    app.job_store.sweep_orphans_on_startup()

    frames = _replay(app.router, job_id)

    assert frames[0] == progress
    assert len(frames) == 2
    closing = _payload(frames[1])
    assert closing["type"] == "error"
    assert closing["reason"] == app.job_store.INTERRUPTED_BY_RESTART
    assert closing["stage"] == "task"


def test_a_reconnect_past_the_last_event_still_gets_the_outcome(app):
    job_id = _job(app.job_store, "caught_up")
    seq = app.job_store.append_event(job_id, _frame({"type": "extract_done"}))
    app.job_store.sweep_orphans_on_startup()

    [closing] = _replay(app.router, job_id, after_seq=seq)

    assert _payload(closing)["type"] == "error"


def test_a_job_that_died_before_any_event_still_ends(app):
    job_id = _job(app.job_store, "silent")
    app.job_store.sweep_orphans_on_startup()

    [closing] = _replay(app.router, job_id)

    assert _payload(closing)["reason"] == app.job_store.INTERRUPTED_BY_RESTART


def test_the_recorded_error_is_kept_when_the_failure_event_was_lost(app):
    job_id = _job(app.job_store, "unpersisted")
    app.job_store.append_event(job_id, _frame({"type": "progress"}))
    app.job_store.mark_failed(job_id, "Separation failed: out of memory")

    closing = _payload(_replay(app.router, job_id)[-1])

    assert closing["type"] == "error"
    assert "out of memory" in closing["reason"]


def test_a_cancelled_job_without_its_event_replays_cancelled(app):
    job_id = _job(app.job_store, "cancelled")
    app.job_store.append_event(job_id, _frame({"type": "progress"}))
    app.job_store.mark_cancelled(job_id)

    assert _payload(_replay(app.router, job_id)[-1]) == {"type": "cancelled"}


@pytest.mark.parametrize(
    "terminal",
    [
        _frame({"type": "error", "reason": "fixture failure"}),
        'event: error\ndata: {"reason": "named fixture failure"}\n\n',
        _frame({"type": "cancelled"}),
    ],
)
def test_a_tail_that_already_ends_is_replayed_unchanged(terminal, app):
    job_id = _job(app.job_store, "ended")
    app.job_store.append_event(job_id, _frame({"type": "progress"}))
    app.job_store.append_event(job_id, terminal)
    app.job_store.mark_failed(job_id, "fixture failure")

    assert _replay(app.router, job_id)[-1] == terminal
    assert len(_replay(app.router, job_id)) == 2


def test_a_finished_job_gets_no_invented_event(app):
    # Prep ends with ``ready``, not ``done``: a completed job must not get an
    # error appended, and a ``done`` event's results cannot be rebuilt.
    job_id = _job(app.job_store, "finished")
    ready = _frame({"type": "ready", "job_id": "fixture"})
    app.job_store.append_event(job_id, ready)
    app.job_store.mark_done(job_id)

    assert _replay(app.router, job_id) == [ready]


def test_an_unknown_task_is_still_not_found(app):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(app.router.stream_task(f"missing_{time.time_ns()}"))
    assert caught.value.status_code == 404
