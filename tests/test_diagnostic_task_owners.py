"""Delayed completed faults keep their pre-await authority; new units can opt in."""
import asyncio
import importlib
import pytest
from test_diagnostic_reports import reporter  # noqa: F401

@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
@pytest.mark.parametrize('mode',['raise','terminal_frame'])
def test_actual_task_runner_failure_stays_with_its_initial_grant(reporter, monkeypatch, choice, mode):
    tasks=importlib.import_module('core.tasks')
    for name in ('create','mark_running','mark_failed','mark_done','mark_cancelled'):
        monkeypatch.setattr(tasks.job_store,name,lambda *args,**kwargs:None)
    monkeypatch.setattr(tasks.run_sentinel,'touch_activity',lambda *args,**kwargs:None)
    reporter.consent(choice not in ('later_opt_in','fresh_later_unit'))
    async def run():
        entered=asyncio.Event(); release=asyncio.Event(); events=asyncio.Queue(maxsize=1)
        completed_failure=asyncio.get_running_loop().create_future()
        if mode=='terminal_frame':events.put_nowait('occupied listener queue')
        def append_event(*args,**kwargs):
            # The real dispatcher has already received and persisted the
            # terminal failure before listener backpressure suspends reporting.
            entered.set()
            return 1
        monkeypatch.setattr(tasks.job_store,'append_event',append_event)
        async def raised():
            entered.set();await completed_failure
        async def terminal():
            yield 'data: {"type":"error","reason":"controlled technical failure"}\n\n'
        manager=tasks.TaskManager()
        await manager.add_task('isolated-diagnostic-review','batch',raised if mode=='raise' else terminal)
        await manager.add_listener('isolated-diagnostic-review',events)
        worker=asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(entered.wait(),2)
            if mode=='raise':
                # Completion is already a fault under A/OFF; the dispatcher
                # cannot process its scheduled continuation until we yield.
                completed_failure.set_exception(RuntimeError('controlled technical failure'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            release.set()
            while await asyncio.wait_for(events.get(),2) is not None:pass
            assert manager.active_tasks['isolated-diagnostic-review']['status']=='failed'
        finally:
            release.set();worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
    reporter._tick()
    rows=reporter._files()
    assert len(rows)==int(choice=='unchanged'), 'Awaited runner failure borrowed a later grant'

@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in','fresh_later_unit'])
def test_progressive_openai_fault_completed_before_new_grant_cannot_borrow_it(reporter, choice):
    openai=importlib.import_module('api.routers.openai_compat')
    reporter.consent(choice not in ('later_opt_in','fresh_later_unit'))
    async def run():
        entered=asyncio.Event()
        failure=asyncio.get_running_loop().create_future()
        async def render(sentence):
            entered.set();return await failure
        stream=openai._stream_speech_pcm(b'\0\0',['first','second'],render,sse=True)
        await stream.__anext__()
        if choice=='fresh_later_unit':reporter.consent(True)
        pending=asyncio.create_task(stream.__anext__())
        try:
            await asyncio.wait_for(entered.wait(),2)
            failure.set_exception(RuntimeError('completed technical failure'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            frame=await asyncio.wait_for(pending,2)
            assert 'speech_generation_failed' in frame
        finally:
            if not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
            await stream.aclose()
    asyncio.run(run())
    reporter._tick()
    assert len(reporter._files())==int(choice in ('unchanged','fresh_later_unit')), 'Completed progressive fault borrowed a later grant'


def test_fresh_task_iteration_after_opt_in_keeps_reporting(reporter, monkeypatch):
    tasks=importlib.import_module('core.tasks')
    for name in ('create','mark_running','mark_failed','mark_done','mark_cancelled'):
        monkeypatch.setattr(tasks.job_store,name,lambda *args,**kwargs:None)
    monkeypatch.setattr(tasks.run_sentinel,'touch_activity',lambda *args,**kwargs:None)
    reporter.consent(False)
    async def run():
        progress_observed=asyncio.Event()
        events=asyncio.Queue(maxsize=1)
        events.put_nowait('occupied listener queue')
        def append_event(*args,**kwargs):
            progress_observed.set()
            return 1
        monkeypatch.setattr(tasks.job_store,'append_event',append_event)
        async def work():
            yield 'data: {"type":"progress","progress":0}\n\n'
            raise RuntimeError('fresh iteration failure')
        manager=tasks.TaskManager()
        await manager.add_task('isolated-fresh-diagnostic-review','batch',work)
        await manager.add_listener('isolated-fresh-diagnostic-review',events)
        worker=asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(progress_observed.wait(),2)
            reporter.consent(True)
            while await asyncio.wait_for(events.get(),2) is not None:pass
            assert manager.active_tasks['isolated-fresh-diagnostic-review']['status']=='failed'
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
    reporter._tick()
    assert len(reporter._files())==1


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
@pytest.mark.parametrize('nested',['gpu_run','prewarm'])
def test_nested_gpu_capture_cannot_escape_outer_task_grant(reporter, monkeypatch, choice, nested):
    from types import SimpleNamespace
    tasks=importlib.import_module('core.tasks')
    gateway=importlib.import_module('services.gpu_gateway')
    manager_module=importlib.import_module('services.model_manager')
    for name in ('create','mark_running','mark_failed','mark_done','mark_cancelled'):
        monkeypatch.setattr(tasks.job_store,name,lambda *args,**kwargs:None)
    monkeypatch.setattr(tasks.job_store,'append_event',lambda *args,**kwargs:1)
    monkeypatch.setattr(tasks.run_sentinel,'touch_activity',lambda *args,**kwargs:None)
    reporter.consent(choice!='later_opt_in')
    async def run():
        entered=asyncio.Event(); events=asyncio.Queue()
        fault=asyncio.get_running_loop().create_future()
        async def guarded(*args,**kwargs):
            entered.set();return await fault
        monkeypatch.setattr(manager_module,'run_on_gpu_pool_guarded',guarded)
        async def work():
            if nested=='gpu_run':
                await gateway.run(local=gateway.LocalCall(fn=lambda:None))
            else:
                await gateway.prewarm(backend=SimpleNamespace(ensure_ready=lambda:None),engine='omnivoice',timeout=1)
        manager=tasks.TaskManager()
        await manager.add_task('nested-diagnostic-review','batch',work)
        await manager.add_listener('nested-diagnostic-review',events)
        worker=asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(entered.wait(),2)
            fault.set_exception(RuntimeError('old completed GPU fault'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            while await asyncio.wait_for(events.get(),2) is not None:pass
            assert manager.active_tasks['nested-diagnostic-review']['status']=='failed'
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
    reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged'), 'Inner GPU capture bypassed the outer task grant'


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
def test_real_executor_inner_capture_cannot_escape_task_grant(reporter, monkeypatch, choice):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    tasks=importlib.import_module('core.tasks')
    mm=importlib.import_module('services.model_manager')
    for name in ('create','mark_running','mark_failed','mark_done','mark_cancelled'):
        monkeypatch.setattr(tasks.job_store,name,lambda *args,**kwargs:None)
    monkeypatch.setattr(tasks.job_store,'append_event',lambda *args,**kwargs:1)
    monkeypatch.setattr(tasks.run_sentinel,'touch_activity',lambda *args,**kwargs:None)
    monkeypatch.setattr(mm,'_note_gpu_pool_idle',lambda:None)
    reports=importlib.import_module('core.diagnostic_reports')
    reporter.consent(choice!='later_opt_in')
    pool=mm._ResilientGpuPool();pool._pool=ThreadPoolExecutor(max_workers=1)
    entered=threading.Event();release=threading.Event()
    def compute():
        try:raise RuntimeError('already observed executor failure')
        except RuntimeError as error:
            entered.set();assert release.wait(3)
            reports.capture_failure('model_load',error=error,engine_id='omnivoice')
            raise
    async def run():
        events=asyncio.Queue()
        async def work():await asyncio.get_running_loop().run_in_executor(pool,compute)
        manager=tasks.TaskManager()
        await manager.add_task('thread-diagnostic-review','batch',work)
        await manager.add_listener('thread-diagnostic-review',events)
        worker=asyncio.create_task(manager.worker())
        try:
            assert await asyncio.to_thread(entered.wait,2)
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            release.set()
            while await asyncio.wait_for(events.get(),2) is not None:pass
        finally:
            release.set();worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    try:asyncio.run(run())
    finally:release.set();pool._pool.shutdown(wait=True,cancel_futures=True)
    reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged'), 'Executor-side old failure borrowed replacement authority'


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
def test_actual_openai_transcription_caught_fault_cannot_borrow_grant_during_classification(reporter, monkeypatch, choice):
    import io
    import threading
    from starlette.datastructures import UploadFile
    from fastapi import HTTPException
    openai=importlib.import_module('api.routers.openai_compat')
    asr=importlib.import_module('services.asr_backend')
    ffmpeg=importlib.import_module('services.ffmpeg_utils')
    monkeypatch.setattr(asr,'asr_model_missing_error',lambda:None)
    reporter.consent(choice!='later_opt_in')
    async def run():
        entered=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        async def failed(*args,**kwargs):raise RuntimeError('fault already caught before await')
        def classify(*args,**kwargs):
            loop.call_soon_threadsafe(entered.set);assert release.wait(3)
        monkeypatch.setattr(asr,'run_transcribe_guarded',failed)
        monkeypatch.setattr(ffmpeg,'raise_for_audio_extract_failure',classify)
        upload=UploadFile(file=io.BytesIO(b'RIFF-fixture'),filename='fixture.wav')
        task=asyncio.create_task(openai._transcribe_request(task='transcribe',file=upload,language=None,prompt=None,response_format='json',temperature=None))
        try:
            await asyncio.wait_for(entered.wait(),2)
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            release.set()
            with pytest.raises(HTTPException) as caught:await asyncio.wait_for(task,2)
            assert caught.value.status_code==500
        finally:
            release.set()
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            await upload.close()
    asyncio.run(run())
    reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged'), 'Caught ASR fault borrowed a new grant during awaited error classification'



def test_known_off_scope_is_inherited_and_explicit_new_unit_can_observe_on(reporter):
    reports=importlib.import_module('core.diagnostic_reports')
    reporter.consent(True)
    with reports.grant_scope(None):
        assert reports.inherited_grant() is None
        work=reports.bind_callable(lambda:reporter.capture('engine_failed'))
        assert not work()
        with reports.grant_scope(reports.observe_grant()):
            assert reporter.capture('generation_failed')
        assert not reporter.capture('engine_failed')
    assert reporter.capture('download_failed')
    reporter._tick()
    assert len(reporter._files())==2
    assert all('expected_epoch' not in row['props'] and 'grant_epoch' not in row['props'] for row in reporter._files())


def test_sibling_async_units_do_not_share_or_leave_authority(reporter):
    reports=importlib.import_module('core.diagnostic_reports')
    reporter.consent(True);old=reporter.epoch
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def old_unit():
            with reports.grant_scope(old):
                entered.set();await release.wait()
                assert not reporter.capture('engine_failed')
        task=asyncio.create_task(old_unit())
        await entered.wait()
        reporter.consent(False);reporter.consent(True)
        with reports.grant_scope(reports.observe_grant()):
            assert reporter.capture('generation_failed')
        release.set();await task
        assert reporter.capture('download_failed')
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==2


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
def test_actual_batch_worker_completed_fault_cannot_borrow_new_grant(reporter, monkeypatch, choice):
    batch=importlib.import_module('api.routers.batch')
    reporter.consent(choice!='later_opt_in')
    async def run():
        queue=asyncio.Queue();entered=asyncio.Event();fault=asyncio.get_running_loop().create_future()
        jobs={'owned-fixture':{'status':'pending','filename':'fixture.wav'}}
        monkeypatch.setattr(batch,'_queue',queue);monkeypatch.setattr(batch,'_jobs',jobs);monkeypatch.setattr(batch,'_processing_job_ids',set())
        async def pipeline(*args):entered.set();await fault
        monkeypatch.setattr(batch,'_run_batch_pipeline',pipeline)
        await queue.put('owned-fixture');worker=asyncio.create_task(batch._worker())
        try:
            await asyncio.wait_for(entered.wait(),2)
            fault.set_exception(RuntimeError('old completed batch fault'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            await asyncio.wait_for(queue.join(),2)
            assert jobs['owned-fixture']['status']=='failed'
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged'), 'Old batch completion borrowed a later grant'


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in','fresh_asr'])
def test_batch_empty_asr_result_uses_its_unit_grant_not_job_lifetime(reporter,monkeypatch,tmp_path,choice):
    import subprocess
    import threading
    from types import SimpleNamespace
    batch=importlib.import_module('api.routers.batch')
    asr=importlib.import_module('services.asr_backend')
    ffmpeg=importlib.import_module('services.ffmpeg_utils')
    reporter.consent(choice not in ('later_opt_in','fresh_asr'))
    monkeypatch.setattr(batch,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(ffmpeg,'find_ffmpeg',lambda:'ffmpeg')
    monkeypatch.setattr(ffmpeg,'validate_media_source',lambda *_:None)
    monkeypatch.setattr(ffmpeg,'require_audio_stream',lambda *_:None)
    async def run():
        entered=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        result=loop.create_future()
        def extract(*args,**kwargs):
            if choice=='fresh_asr':
                loop.call_soon_threadsafe(entered.set);assert release.wait(3)
            return SimpleNamespace(stderr=b'Duration: 00:00:02.00',stdout=b'')
        monkeypatch.setattr(subprocess,'run',extract)
        async def transcribe(*args,**kwargs):
            if choice=='fresh_asr':return [],'en'
            entered.set();return await result
        monkeypatch.setattr(asr,'run_transcribe_guarded',transcribe)
        job={'status':'running','video_path':str(tmp_path/'fixture.mp4'),'langs':['en']}
        task=asyncio.create_task(batch._run_batch_pipeline('isolated-asr-unit',job))
        try:
            await asyncio.wait_for(entered.wait(),2)
            if choice!='fresh_asr':result.set_result(([],'en'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice in ('later_opt_in','fresh_asr'):reporter.consent(True)
            release.set();await asyncio.wait_for(task,2)
            assert job['status']=='failed'
        finally:
            release.set()
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==int(choice in ('unchanged','fresh_asr'))


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
def test_argos_worker_row_failure_keeps_submission_grant(reporter,monkeypatch,choice):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from test_dub_translate import _install_fake_argos
    argos=importlib.import_module('api.routers.dub_translate')
    _install_fake_argos(monkeypatch)
    reporter.consent(choice!='later_opt_in')
    entered=threading.Event();release=threading.Event()
    def translate(*args):
        try:raise RuntimeError('already observed Argos row failure')
        except RuntimeError:
            entered.set();assert release.wait(3);raise
    monkeypatch.setattr(importlib.import_module('argostranslate.translate'),'translate',translate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(argos,'_cpu_pool',pool)
        async def run():
            task=asyncio.create_task(argos.dub_translate(argos.TranslateRequest(
                provider='argos',source_lang='en',target_lang='es',quality='fast',segments=[{'id':'fixture','text':'synthetic fixture'}])))
            try:
                assert await asyncio.to_thread(entered.wait,2)
                if choice=='replacement':reporter.consent(False);reporter.consent(True)
                elif choice=='later_opt_in':reporter.consent(True)
                release.set();response=await asyncio.wait_for(task,2)
                assert response['translated'][0]['error']
            finally:
                release.set()
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        asyncio.run(run())
    reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged')
