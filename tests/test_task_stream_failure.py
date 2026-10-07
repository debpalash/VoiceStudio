import asyncio
import pytest
from core.tasks import TaskManager, _stream_failure

@pytest.mark.parametrize('event', [
    'data: {"type":"error","error":"segment failed"}\n\n',
    'event: error\ndata: {"detail":"segment failed"}\n\n',
])
def test_error_stream_is_terminal(event, monkeypatch):
    # Patch the modules actually held by TaskManager despite suite reloads.
    job_store = TaskManager.worker.__globals__["job_store"]
    run_sentinel = TaskManager.worker.__globals__["run_sentinel"]
    states=[]
    for name in ['create','mark_running','append_event']:
        monkeypatch.setattr(job_store,name,lambda *a,**kw: None)
    monkeypatch.setattr(job_store,'mark_failed',lambda *a: states.append('failed'))
    monkeypatch.setattr(job_store,'mark_done',lambda *a: states.append('done'))
    monkeypatch.setattr(run_sentinel,'touch_activity',lambda *a: None)
    closed=[]
    async def stream():
        try:
            yield event
            yield 'data: {"type":"done"}\n\n'
        finally: closed.append(True)
    async def run():
        manager=TaskManager()
        await manager.add_task('test','dub_generate',stream)
        worker=asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(),2)
            assert manager.active_tasks['test']['status']=='failed'
            assert len(manager.active_tasks['test']['history'])==1
        finally:
            worker.cancel()
            try: await worker
            except asyncio.CancelledError: pass
    asyncio.run(run())
    assert states==['failed']
    assert closed==[True]

def test_warnings_remain_non_terminal():
    assert _stream_failure('data: {"type":"warning","error":"retrying"}\n\n') is None


@pytest.mark.parametrize('outcome', ['cancelled', 'raised'])
def test_stream_is_closed_however_the_task_ends(outcome, monkeypatch):
    # A render holds resources (voice-file leases) until its generator closes;
    # cancellation and errors must close it, not leave it suspended (#2535).
    job_store = TaskManager.worker.__globals__["job_store"]
    run_sentinel = TaskManager.worker.__globals__["run_sentinel"]
    for name in ['create', 'mark_running', 'append_event', 'mark_failed', 'mark_done', 'mark_cancelled']:
        monkeypatch.setattr(job_store, name, lambda *a, **kw: None)
    monkeypatch.setattr(run_sentinel, 'touch_activity', lambda *a: None)
    closed = []

    async def run():
        manager = TaskManager()

        async def stream():
            try:
                yield 'data: {"type":"progress"}\n\n'
                if outcome == 'cancelled':
                    manager.cancel_task('test')
                    yield 'data: {"type":"progress"}\n\n'
                else:
                    raise RuntimeError('boom')
                yield 'data: {"type":"done"}\n\n'
            finally:
                closed.append(True)

        await manager.add_task('test', 'dub_generate', stream)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 2)
            assert manager.active_tasks['test']['status'] == ('cancelled' if outcome == 'cancelled' else 'failed')
            # Closed while the worker is still alive and waiting for the next task.
            assert closed == [True]
        finally:
            worker.cancel()
            try: await worker
            except asyncio.CancelledError: pass

    asyncio.run(run())
    assert closed == [True]


def test_completion_after_a_late_cancel_is_reported_done(monkeypatch):
    """A render that committed before seeing the cancel must not read as cancelled."""
    job_store = TaskManager.worker.__globals__["job_store"]
    run_sentinel = TaskManager.worker.__globals__["run_sentinel"]
    states = []
    for name in ['create', 'mark_running', 'append_event']:
        monkeypatch.setattr(job_store, name, lambda *a, **kw: None)
    monkeypatch.setattr(job_store, 'mark_cancelled', lambda *a: states.append('cancelled'))
    monkeypatch.setattr(job_store, 'mark_done', lambda *a: states.append('done'))
    monkeypatch.setattr(run_sentinel, 'touch_activity', lambda *a: None)

    async def run():
        manager = TaskManager()

        async def stream():
            yield 'data: {"type":"assembling"}\n\n'
            manager.cancel_task('test')  # arrives while the result is committed
            yield 'data: {"type":"done"}\n\n'

        await manager.add_task('test', 'dub_generate', stream)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 2)
            task = manager.active_tasks['test']
            assert task['status'] == 'done'
            assert task['history'][-1] == 'data: {"type":"done"}\n\n'
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    assert states == ['done']
