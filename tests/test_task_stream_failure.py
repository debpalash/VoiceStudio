import asyncio
import pytest
from core.tasks import TaskManager, _stream_failure

@pytest.mark.parametrize('event', [
    'data: {"type":"error","error":"segment failed"}\n\n',
    'event: error\ndata: {"detail":"segment failed"}\n\n',
])
def test_error_stream_is_terminal(event, monkeypatch):
    from core import job_store
    from core import run_sentinel
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
