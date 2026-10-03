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


def test_cancelled_stream_closes_before_worker_waits_for_next_task(monkeypatch):
    job_store = TaskManager.worker.__globals__["job_store"]
    run_sentinel = TaskManager.worker.__globals__["run_sentinel"]
    for name in ['create', 'mark_running', 'mark_cancelled', 'append_event']:
        monkeypatch.setattr(job_store, name, lambda *a, **kw: None)
    monkeypatch.setattr(run_sentinel, 'touch_activity', lambda *a: None)
    closed = []

    async def run():
        manager = TaskManager()
        async def stream():
            try:
                manager.cancel_task('test')
                yield 'data: {"type":"progress"}\n\n'
            finally:
                closed.append(True)
        await manager.add_task('test', 'dub_generate', stream)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 2)
            assert manager.active_tasks['test']['status'] == 'cancelled'
            assert closed == [True]
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
    asyncio.run(run())
