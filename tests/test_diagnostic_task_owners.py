"""Delayed completed faults keep their pre-await authority; new units can opt in."""
import asyncio
import importlib
import json
import pytest
from test_diagnostic_reports import reporter  # noqa: F401
from test_download_install_integrity import install_origin  # noqa: F401
from test_dub_batch_engine_selection import dub_job_env, fake_registry  # noqa: F401


@pytest.mark.parametrize("kind", ["memory", "compute", "subclass"])
def test_actual_dub_terminal_error_keeps_one_causal_report(reporter, dub_job_env, fake_registry, monkeypatch, kind):
    from test_dub_batch_engine_selection import _one_seg_request
    tasks = importlib.import_module('core.tasks')
    dg, _job = dub_job_env
    engine = fake_registry('fake-diagnostic-oom', supports_cloning=True)
    monkeypatch.setenv('OMNIVOICE_TTS_BACKEND', 'fake-diagnostic-oom')
    for name in ('create', 'mark_running', 'mark_failed', 'mark_done', 'mark_cancelled'):
        monkeypatch.setattr(tasks.job_store, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.run_sentinel, 'touch_activity', lambda *args, **kwargs: None)
    published = []
    monkeypatch.setattr(tasks.job_store, 'append_event', lambda _identity, event: published.append(str(event)) or len(published))
    reporter.consent(True)

    from services.model_manager import GpuJobTimeoutError

    class ComputeDeadline(GpuJobTimeoutError):
        pass

    error = {"memory": MemoryError, "compute": GpuJobTimeoutError, "subclass": ComputeDeadline}[kind]

    def fail(self, text, **kwargs):
        raise error('private failing allocation')

    monkeypatch.setattr(engine, 'generate', fail)

    async def run():
        manager = tasks.TaskManager()
        monkeypatch.setattr(dg, 'task_manager', manager)
        result = await dg.dub_generate('jobX', _one_seg_request())
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 5)
            assert manager.active_tasks[result['task_id']]['status'] == 'failed'
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    reporter._tick()
    rows = reporter._files()
    assert len(rows) == 1
    assert rows[0]['props']['error_type'] == ('MemoryError' if kind == 'memory' else 'TimeoutError')
    assert rows[0]['props']['error_code'] == ('HOST_MEMORY_EXHAUSTED' if kind == 'memory' else 'GPU_JOB_TIMEOUT')
    assert rows[0]['props']['occurrences'] == 1
    assert any('"type": "error"' in event for event in published)
    assert all('diagnostic' not in event and 'private' not in event for event in published)

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
            entered.set()
            await completed_failure
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
            if choice=='replacement':
                reporter.consent(False)
                reporter.consent(True)
            elif choice=='later_opt_in':
                reporter.consent(True)
            release.set()
            while await asyncio.wait_for(events.get(),2) is not None:
                pass
            assert manager.active_tasks['isolated-diagnostic-review']['status']=='failed'
        finally:
            release.set()
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
    reporter._tick()
    rows=reporter._files()
    assert len(rows)==int(choice=='unchanged'), 'Awaited runner failure borrowed a later grant'

@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in','consent_during_operation'])
def test_progressive_openai_fault_completed_before_new_grant_cannot_borrow_it(reporter, choice):
    openai=importlib.import_module('api.routers.openai_compat')
    reporter.consent(choice not in ('later_opt_in','consent_during_operation'))
    async def run():
        entered=asyncio.Event()
        failure=asyncio.get_running_loop().create_future()
        async def render(sentence):
            if choice == 'consent_during_operation':
                assert importlib.import_module('core.diagnostic_reports').observe_grant() is not None
            entered.set();return await failure
        stream=openai._stream_speech_pcm(b'\0\0',['first','second'],render,sse=True)
        await stream.__anext__()
        if choice=='consent_during_operation':reporter.consent(True)
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
    assert len(reporter._files())==int(choice == 'unchanged'), 'Completed progressive fault borrowed a later grant'


def test_same_task_iteration_after_opt_in_does_not_borrow_consent(reporter, monkeypatch):
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
    assert len(reporter._files())==0


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


@pytest.mark.parametrize('initial_owner', ['off', 'replaced'])
def test_reused_batch_worker_observes_each_new_job_grant(reporter, monkeypatch, initial_owner):
    batch = importlib.import_module('api.routers.batch')
    reports = importlib.import_module('core.diagnostic_reports')
    reporter.consent(initial_owner == 'replaced')

    async def run():
        queue = asyncio.Queue()
        jobs = {identity: {'status': 'pending', 'filename': 'fixture.wav'}
                for identity in ('first-owner', 'fresh-owner')}
        monkeypatch.setattr(batch, '_queue', queue)
        monkeypatch.setattr(batch, '_jobs', jobs)
        monkeypatch.setattr(batch, '_processing_job_ids', set())
        observed = []

        async def pipeline(job_id, _job):
            if job_id == 'fresh-owner':
                observed.append(reports.inherited_grant())
                reports.capture('engine_failed', stage='engine')
                raise RuntimeError('controlled fresh job failure')

        monkeypatch.setattr(batch, '_run_batch_pipeline', pipeline)
        await queue.put('first-owner')
        # The production long-lived worker inherits its creating request's
        # context. Finishing that request must not pin all later jobs to it.
        with reports.grant_scope(reports.observe_grant()):
            worker = asyncio.create_task(batch._worker())
        try:
            await asyncio.wait_for(queue.join(), 2)
            assert jobs['first-owner']['status'] == 'done'
            if initial_owner == 'replaced':
                reporter.consent(False)
            reporter.consent(True)
            fresh = reports.observe_grant()
            await queue.put('fresh-owner')
            await asyncio.wait_for(queue.join(), 2)
            assert jobs['fresh-owner']['status'] == 'failed'
            assert observed == [fresh]
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    reporter._tick()
    assert {row['props']['category'] for row in reporter._files()} == {
        'engine_failed', 'generation_failed',
    }


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


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in'])
def test_install_background_failure_keeps_admitted_grant(reporter,install_origin,monkeypatch,choice):
    import threading
    from services import token_resolver
    state=install_origin
    reporter.consent(choice!='later_opt_in')
    entered=threading.Event();release=threading.Event()
    def failed_token_resolution():
        try:raise RuntimeError('controlled install failure already observed')
        except RuntimeError:
            entered.set();assert release.wait(3);raise
    monkeypatch.setattr(token_resolver,'resolve',failed_token_resolution)
    async def run():
        await state.download.install_model(state.download.InstallModelRequest(repo_id=state.repo))
        task=state.download._install_tasks_by_repo[state.repo]
        try:
            assert await asyncio.to_thread(entered.wait,2)
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            release.set();await asyncio.wait_for(task,2)
            assert state.repo in state.download._install_failures
        finally:
            release.set()
            if not task.done():await asyncio.wait_for(task,2)
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged')


@pytest.mark.parametrize('cancelled',[False,True])
def test_cancelled_task_late_exception_does_not_report_failure(reporter,monkeypatch,cancelled):
    tasks=importlib.import_module('core.tasks')
    for name in ('create','mark_running','mark_failed','mark_done','mark_cancelled'):
        monkeypatch.setattr(tasks.job_store,name,lambda *args,**kwargs:None)
    monkeypatch.setattr(tasks.job_store,'append_event',lambda *args,**kwargs:1)
    monkeypatch.setattr(tasks.run_sentinel,'touch_activity',lambda *args,**kwargs:None)
    reporter.consent(True)
    async def run():
        entered=asyncio.Event();events=asyncio.Queue();failure=asyncio.get_running_loop().create_future()
        async def work():entered.set();await failure
        manager=tasks.TaskManager()
        await manager.add_task('cancelled-late-failure','batch',work)
        await manager.add_listener('cancelled-late-failure',events)
        worker=asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(entered.wait(),2)
            if cancelled:assert manager.cancel_task('cancelled-late-failure')
            failure.set_exception(RuntimeError('transfer interrupted after user cancel'))
            while await asyncio.wait_for(events.get(),2) is not None:pass
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==int(not cancelled)


@pytest.mark.parametrize('choice',['unchanged','replacement','later_opt_in','consent_during_operation'])
def test_translation_outer_failure_keeps_awaited_unit_grant(reporter,monkeypatch,choice):
    from test_dub_translate import _install_fake_argos
    translation=importlib.import_module('api.routers.dub_translate')
    _install_fake_argos(monkeypatch)
    reporter.consent(choice not in ('later_opt_in','consent_during_operation'))
    async def run():
        entered=asyncio.Event();failure=asyncio.get_running_loop().create_future()
        async def cinematic(*args,**kwargs):
            if choice == 'consent_during_operation':
                assert importlib.import_module('core.diagnostic_reports').observe_grant() is not None
            entered.set();return await failure
        monkeypatch.setattr(translation,'_maybe_cinematic',cinematic)
        if choice=='consent_during_operation':
            # Cinematic refinement is still part of the OFF-started
            # translation operation, even if consent changes between units.
            real_translate=importlib.import_module('argostranslate.translate').translate
            def translate(*args):
                reporter.consent(True)
                return real_translate(*args)
            monkeypatch.setattr(importlib.import_module('argostranslate.translate'),'translate',translate)
        task=asyncio.create_task(translation.dub_translate(translation.TranslateRequest(
            provider='argos',source_lang='en',target_lang='es',quality='fast',
            segments=[{'id':'fixture','text':'synthetic fixture'}])))
        try:
            await asyncio.wait_for(entered.wait(),2)
            failure.set_exception(RuntimeError('cinematic fault completed before consent transition'))
            if choice=='replacement':reporter.consent(False);reporter.consent(True)
            elif choice=='later_opt_in':reporter.consent(True)
            response=await asyncio.wait_for(task,2)
            assert response.status_code==500
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run());reporter._tick()
    assert len(reporter._files())==int(choice=='unchanged')


@pytest.mark.parametrize('initially_enabled', [False, True])
def test_queued_task_keeps_submission_owner_before_worker_starts(reporter, monkeypatch, initially_enabled):
    tasks = importlib.import_module('core.tasks')
    reports = importlib.import_module('core.diagnostic_reports')
    for name in ('create', 'mark_running', 'mark_failed', 'mark_done', 'mark_cancelled'):
        monkeypatch.setattr(tasks.job_store, name, lambda *a, **kw: None)
    monkeypatch.setattr(tasks.job_store, 'append_event', lambda *a, **kw: 1)
    monkeypatch.setattr(tasks.run_sentinel, 'touch_activity', lambda *a, **kw: None)
    reporter.consent(initially_enabled)
    async def run():
        async def work():
            raise RuntimeError('synthetic queued failure')
        manager = tasks.TaskManager()
        await manager.add_task('queued-owner', 'batch', work)
        reporter.consent(False); reporter.consent(True)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 2)
            assert reporter.intake.empty(), 'queued work borrowed a later grant'
            await manager.add_task('fresh-owner', 'batch', work)
            await asyncio.wait_for(manager.queue.join(), 2)
            assert reporter.intake.qsize() == 1
        finally:
            worker.cancel(); await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(run())


def test_known_off_expected_epoch_differs_from_omitted_epoch(reporter):
    reports = importlib.import_module('core.diagnostic_reports')
    off = reports.observe_grant()
    reporter.consent(True)
    assert off is None
    assert not reports.capture_failure('generation', expected_epoch=off)
    assert not reporter.capture('engine_failed', _expected_epoch=off)
    assert reports.capture_failure('generation')


@pytest.mark.parametrize('old_grant', [False, True])
def test_openai_upload_keeps_request_owner_for_nested_asr(reporter, monkeypatch, old_grant):
    reports = importlib.import_module('core.diagnostic_reports')
    openai = importlib.import_module('api.routers.openai_compat')
    asr = importlib.import_module('services.asr_backend')
    ffmpeg = importlib.import_module('services.ffmpeg_utils')
    monkeypatch.setattr(asr, 'asr_model_missing_error', lambda: None)
    monkeypatch.setattr(ffmpeg, 'raise_for_audio_extract_failure', lambda *a, **kw: None)
    reporter.consent(old_grant)
    owner = reports.observe_grant()
    calls = []
    class Upload:
        filename = 'fixture.wav'
        sent = False
        async def read(self, size=-1):
            # The route reads the upload in chunks; the first chunk flips consent.
            if self.sent:
                return b''
            self.sent = True
            reporter.consent(False); reporter.consent(True)
            calls.append('upload_changed_consent')
            return b'RIFF-fixture'
    async def guarded(*args, **kwargs):
        assert calls == ['upload_changed_consent']
        calls.append('guarded_asr')
        reports.capture_failure('transcription', error=RuntimeError('synthetic nested ASR fault'))
        raise RuntimeError('synthetic ASR fault')
    monkeypatch.setattr(asr, 'run_transcribe_guarded', guarded)
    async def run():
        with reports.grant_scope(owner):
            with pytest.raises(openai.HTTPException) as raised:
                await openai._transcribe_request(task='transcribe', file=Upload(), language=None, prompt=None, response_format='json', temperature=None)
            assert raised.value.status_code == 500
    asyncio.run(run())
    assert calls == ['upload_changed_consent', 'guarded_asr']
    assert reporter.intake.empty()


from test_dub_crash_resume import dub  # noqa: E402,F401


@pytest.mark.parametrize('cancelled', [False, True])
def test_actual_dub_native_exception_after_cancel_stays_local(reporter, dub, cancelled):
    reporter.consent(True)
    dub.state['fail_on_call'] = 1
    dub.state['cancel_after'] = 1 if cancelled else None
    assert any(event['type'] == 'error' for event in dub.run())
    reporter._tick()
    assert len(reporter._files()) == int(not cancelled)


@pytest.mark.parametrize("code", ["dub_source_changed", "dub_timing_overflow", "UNKNOWN", "NETWORK_ERROR", ["dub_source_changed"], {"code": "dub_source_changed"}])
@pytest.mark.parametrize("choice", ["current", "off", "regrant"])
def test_expected_dub_refusal_frames_stay_local_without_changing_task_failure(reporter, monkeypatch, code, choice):
    tasks = importlib.import_module("core.tasks")
    for name in ("create", "mark_running", "mark_failed", "mark_done", "mark_cancelled", "append_event"):
        monkeypatch.setattr(tasks.job_store, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.run_sentinel, "touch_activity", lambda *args, **kwargs: None)
    reporter.consent(choice != "off")
    frame = 'data: ' + json.dumps({"type": "error", "error_code": code, "reason": "private user-actionable detail"}) + '\n\n'

    async def run():
        async def stream():
            if choice == "regrant":
                reporter.consent(False)
                reporter.consent(True)
            yield frame
        manager = tasks.TaskManager()
        await manager.add_task("refusal", "dub_generate", stream)
        worker = asyncio.create_task(manager.worker())
        try:
            await asyncio.wait_for(manager.queue.join(), 2)
            task = manager.active_tasks["refusal"]
            assert task["status"] == "failed"
            assert task["history"] == [frame]
            assert task["error"] == "private user-actionable detail"
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(run())
    reporter._tick()
    expected = choice == "current" and code not in ("dub_source_changed", "dub_timing_overflow")
    assert len(reporter._files()) == int(expected)
