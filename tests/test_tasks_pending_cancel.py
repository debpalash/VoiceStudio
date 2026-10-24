"""Cancellation before dispatch must not invoke the queued work."""
import asyncio
import contextlib
import uuid

import pytest


@pytest.mark.parametrize("kind", ["sync", "coroutine", "stream"])
def test_cancelled_pending_task_never_executes_and_persists_cancelled(kind):
    from core import job_store
    from core.db import init_db
    from core.tasks import TaskManager

    init_db()
    manager = TaskManager()
    task_id = uuid.uuid4().hex
    calls = []

    def sync():
        calls.append("executed")

    async def coroutine():
        calls.append("executed")

    async def stream():
        calls.append("executed")
        yield 'data: {"type":"progress"}\n\n'

    async def scenario():
        await manager.add_task(task_id, "test", locals_work[kind])
        listener = asyncio.Queue()
        await manager.add_listener(task_id, listener)
        assert manager.cancel_task(task_id)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 1)
            frames = []
            while not listener.empty():
                frames.append(listener.get_nowait())
            return frames
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    locals_work = {"sync": sync, "coroutine": coroutine, "stream": stream}
    frames = asyncio.run(scenario())
    assert calls == []
    assert manager.active_tasks[task_id]["status"] == "cancelled"
    assert job_store.get(task_id)["status"] == "cancelled"
    assert frames == ['data: {"type": "cancelled"}\n\n', None]


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
def test_cancellation_does_not_change_a_terminal_task(status):
    from core.tasks import TaskManager

    manager = TaskManager()
    manager.active_tasks["finished"] = {"status": status, "cancelled": False}
    assert manager.cancel_task("finished") is False
    assert manager.active_tasks["finished"] == {"status": status, "cancelled": False}


def _drain(manager, task_id):
    async def scenario():
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 1)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    asyncio.run(scenario())


def test_on_cancel_releases_resources_of_a_task_cancelled_before_start():
    from core.db import init_db
    from core.tasks import TaskManager

    init_db()
    manager = TaskManager()
    task_id = uuid.uuid4().hex
    calls = []

    async def scenario():
        await manager.add_task(
            task_id, "test", lambda: calls.append("executed"),
            on_cancel=lambda: calls.append("released"),
        )
        assert manager.cancel_task(task_id)

    asyncio.run(scenario())
    _drain(manager, task_id)
    assert calls == ["released"]
    assert manager.active_tasks[task_id]["status"] == "cancelled"


def test_on_cancel_failure_does_not_wedge_the_worker():
    from core.db import init_db
    from core.tasks import TaskManager

    init_db()
    manager = TaskManager()
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    calls = []

    def broken():
        raise OSError("busy")

    async def scenario():
        await manager.add_task(first, "test", lambda: None, on_cancel=broken)
        await manager.add_task(second, "test", lambda: calls.append("ran"))
        assert manager.cancel_task(first)

    asyncio.run(scenario())
    _drain(manager, first)
    assert calls == ["ran"]
    assert manager.active_tasks[first]["status"] == "cancelled"
    assert manager.active_tasks[second]["status"] == "done"


def test_on_cancel_is_not_called_for_a_task_that_runs():
    from core.db import init_db
    from core.tasks import TaskManager

    init_db()
    manager = TaskManager()
    task_id = uuid.uuid4().hex
    calls = []

    async def scenario():
        await manager.add_task(
            task_id, "test", lambda: calls.append("executed"),
            on_cancel=lambda: calls.append("released"),
        )

    asyncio.run(scenario())
    _drain(manager, task_id)
    assert calls == ["executed"]


def test_abort_cannot_land_between_the_cancel_check_and_dispatch():
    """cancel_task and the worker's check-then-run share one lock."""
    from core.tasks import TaskManager

    manager = TaskManager()
    manager.active_tasks["t"] = {"status": "pending", "cancelled": False}
    with manager._state_lock:
        import threading

        done = threading.Event()
        threading.Thread(
            target=lambda: (manager.cancel_task("t"), done.set()), daemon=True
        ).start()
        assert not done.wait(0.2)  # Blocked while the worker holds the lock.
    assert done.wait(1)
    assert manager.active_tasks["t"]["cancelled"] is True


def test_worker_observes_cancellation_winning_at_dispatch_checkpoint():
    import threading
    from core import job_store
    from core.db import init_db
    from core.tasks import TaskManager

    init_db()
    manager = TaskManager()
    task_id = uuid.uuid4().hex
    calls = []
    checkpoint = threading.Event()
    cancelled = threading.Event()
    owner = threading.get_ident()
    errors = []

    class DispatchLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.waiting = True

        def __enter__(self):
            if threading.get_ident() == owner and self.waiting:
                self.waiting = False
                checkpoint.set()
                assert cancelled.wait(2), 'cancellation did not reach the worker checkpoint'
            self.lock.acquire()
            return self

        def __exit__(self, *_):
            self.lock.release()

    manager._state_lock = DispatchLock()

    def cancel_at_checkpoint():
        if not checkpoint.wait(2):
            errors.append('worker did not acquire its dispatch lock')
            return
        if not manager.cancel_task(task_id):
            errors.append('pending cancellation refused')
        cancelled.set()

    async def scenario():
        await manager.add_task(task_id, 'test', lambda: calls.append('executed'),
                               on_cancel=lambda: calls.append('released'))
        canceller = threading.Thread(target=cancel_at_checkpoint)
        canceller.start()
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 3)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            canceller.join(3)
        assert not canceller.is_alive()

    asyncio.run(scenario())
    assert not errors
    assert calls == ['released']
    assert manager.active_tasks[task_id]['status'] == 'cancelled'
    assert job_store.get(task_id)['status'] == 'cancelled'
