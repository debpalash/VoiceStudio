"""Actual failure-owner diagnostics, independent consent and strict persisted data."""
import asyncio
import importlib
import json
import threading
import time
from pathlib import Path

import pytest



@pytest.fixture
def reporter(monkeypatch,tmp_path):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs,config,analytics
    monkeypatch.delenv('OMNIVOICE_DIAGNOSTICS_DIR',raising=False)
    monkeypatch.delenv('OMNIVOICE_DIAGNOSTICS_DISABLED',raising=False)
    monkeypatch.delenv('OMNIVOICE_ANALYTICS_DISABLED',raising=False)
    monkeypatch.setattr(prefs,'_PREFS_PATH',str(tmp_path/'prefs.json'))
    monkeypatch.setattr(config,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(analytics,'_destination',lambda:{})
    instance=reports.Reporter()
    instance.schema=reports._schema()
    monkeypatch.setattr(reports,'_reporter',instance)
    yield instance
    instance.shutdown()


def records(reporter):
    reporter._tick()
    return [json.loads(path.read_text(encoding='utf-8')) for path in reporter.directory.glob('*.json')]


def test_analytics_consent_does_not_enable_diagnostics(reporter):
    from core import prefs
    prefs.set_('analytics_enabled',True)
    reporter._tick()
    assert not reporter.capture('backend_crash',error=RuntimeError('secret'))
    assert not reporter.root.exists()
    assert records(reporter)==[]


def test_capture_never_waits_for_worker_io(reporter):
    reporter.consent(True)
    entered=threading.Event(); release=threading.Event()
    def hold_lock():
        with reporter.lock:
            entered.set();release.wait(2)
    thread=threading.Thread(target=hold_lock);thread.start();assert entered.wait(1)
    try:
        started=time.monotonic()
        assert reporter.capture('generation_failed',error=RuntimeError('secret'))
        assert time.monotonic()-started < .1
    finally:
        release.set();thread.join(2)


def test_private_metadata_only_and_coalescing(reporter):
    reporter.consent(True)
    secret='hf_PRIVATESECRET /home/customer/My Voice https://secret.invalid/?token=secret transcript'
    for _ in range(3):
        reporter.capture('generation_failed',stage='generation',error=ValueError(secret),engine_id=secret,message=secret,frames=secret,reason=secret)
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['occurrences']==3
    assert saved[0]['props']['error_type']=='ValueError'
    assert saved[0]['props']['engine_id']=='unknown'
    assert secret not in json.dumps(saved)
    assert not any(word in json.dumps(saved) for word in ['PRIVATESECRET','customer','transcript','secret.invalid'])
    assert len(saved[0]['props']['fingerprint'])==64


def test_same_causal_exception_reported_once(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    error=RuntimeError('secret')
    reports.capture_failure('model_load',error=error)
    reports.capture_failure('generation',error=error)
    assert len(records(reporter))==1
    assert records(reporter)[0]['props']['occurrences']==1


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
@pytest.mark.parametrize('kill_value',['1','true','yes',' ON '])
def test_kill_and_withdrawal_purge_and_new_epoch(reporter,monkeypatch,kill,kill_value):
    reporter.consent(True);old=reporter.epoch
    reporter.capture('generation_failed');assert len(records(reporter))==1
    reporter.capture('download_failed')
    monkeypatch.setenv(kill,kill_value)
    assert not reporter.capture('backend_crash')
    assert records(reporter)==[]
    monkeypatch.delenv(kill)
    reporter.consent(False)
    assert records(reporter)==[]
    reporter.consent(True)
    assert reporter.epoch!=old
    assert records(reporter)==[]


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_killed_launch_revokes_saved_grant_and_cannot_replay_after_unkill(reporter,monkeypatch,kill):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs,run_sentinel
    monkeypatch.setattr(run_sentinel,'_previous_run_alive',lambda *args:False)
    prefs.set_('analytics_enabled',True)
    prefs.set_('language','fr')
    reporter.consent(True)
    old_epoch,old_profile=reporter.epoch,reporter.profile
    reporter.capture('generation_failed')
    assert records(reporter)
    reporter.shutdown()
    monkeypatch.setenv(kill,'1')
    killed_run=reports.Reporter();killed_run.schema=reports._schema()
    killed_run._tick()
    killed_run.consent(True)
    assert not killed_run.status()['opted_in']
    assert list(killed_run.directory.glob('*.json'))==[]
    killed_run.shutdown()
    (reporter.root/'session.json').write_text(json.dumps({'pid':42,'started_at':1,'epoch':old_epoch}),encoding='utf-8')
    monkeypatch.delenv(kill)
    next_run=reports.Reporter();next_run.schema=reports._schema()
    try:
        assert records(next_run)==[]
        assert not next_run.capture('backend_crash')
        assert next_run.status()['prompted'] and not next_run.status()['opted_in']
        assert prefs._load()['analytics_enabled'] is True and prefs._load()['language']=='fr'
        next_run.consent(True)
        assert next_run.epoch!=old_epoch and next_run.profile!=old_profile
        assert records(next_run)==[]
        next_run.capture('operation_failed')
        assert [row['props']['category'] for row in records(next_run)]==['operation_failed']
    finally:
        next_run.shutdown()


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_capture_observed_kill_cannot_resume_old_epoch_before_worker_refresh(reporter,monkeypatch,kill):
    reporter.consent(True);old=reporter.epoch
    reporter.capture('generation_failed')
    monkeypatch.setenv(kill,'1')
    assert not reporter.capture('backend_crash')
    monkeypatch.delenv(kill)
    assert records(reporter)==[]
    assert not reporter.status()['opted_in']
    reporter.consent(True)
    assert reporter.epoch!=old
    assert records(reporter)==[]


def test_managed_only_spools_same_enabled_epoch(reporter,monkeypatch,tmp_path):
    reports = importlib.import_module("core.diagnostic_reports")
    root=tmp_path/'managed';root.mkdir()
    monkeypatch.setenv('OMNIVOICE_DIAGNOSTICS_DIR',str(root))
    managed=reports.Reporter();managed.schema=reports._schema()
    assert records(managed)==[] and not (root/'inbox').exists()
    consent={'version':1,'enabled':True,'epoch':'a'*32,'profile_id':'install_'+'b'*32}
    (root/'consent.json').write_text(json.dumps(consent), encoding='utf-8')
    managed._tick()
    assert managed.capture('engine_failed',stage='engine')
    assert records(managed)[0]['epoch']=='a'*32
    assert managed.transport is None
    with pytest.raises(PermissionError):managed.consent(True)
    managed.capture('generation_failed')
    consent.update(enabled=False,epoch='c'*32)
    (root/'consent.json').write_text(json.dumps(consent), encoding='utf-8')
    assert records(managed)==[]
    consent.update(enabled=True,epoch='d'*32)
    (root/'consent.json').write_text(json.dumps(consent), encoding='utf-8')
    assert records(managed)==[]
    managed.shutdown()


def test_offline_queue_survives_restart_and_revalidates(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    reporter.capture('download_failed',stage='download')
    saved=records(reporter)[0]
    reporter.shutdown()
    next_run=reports.Reporter();next_run.schema=reports._schema()
    replayed=records(next_run)
    assert replayed==[saved]
    corrupt=next_run.directory/('f'*32+'.json');corrupt.write_text('{broken', encoding='utf-8')
    stale={**saved,'id':'e'*32,'epoch':'0'*32}
    (next_run.directory/('e'*32+'.json')).write_text(json.dumps(stale), encoding='utf-8')
    assert records(next_run)==[saved]
    assert not corrupt.exists()
    next_run.shutdown()


def test_unclean_marker_requires_same_consent_and_dead_owner(reporter,monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    from core import run_sentinel
    monkeypatch.setattr(run_sentinel,'_previous_run_alive',lambda *args:False)
    reporter.root.mkdir(exist_ok=True)
    marker=reporter.root/'session.json'
    marker.write_text(json.dumps({'pid':42,'started_at':1,'epoch':reporter.epoch}), encoding='utf-8')
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['category']=='backend_crash'
    reporter.shutdown()
    assert not marker.exists()
    next_run=reports.Reporter();next_run.schema=reports._schema()
    marker.write_text(json.dumps({'pid':42,'started_at':1,'epoch':'0'*32}), encoding='utf-8')
    assert len(records(next_run))==1  # Existing report only, no pre-consent crash.
    next_run.shutdown()


def test_corrupt_consent_fails_closed_and_purges(reporter):
    from core import prefs
    reporter.consent(True);reporter.capture('generation_failed');assert records(reporter)
    Path(prefs._PREFS_PATH).write_text('{broken', encoding='utf-8')
    assert records(reporter)==[]
    assert not reporter.capture('backend_crash')


def test_queue_bound_and_invalid_frame_controls(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    for _ in range(100):reporter.capture('generation_failed')
    assert reporter.intake.qsize()==64 and reporter.dropped==36
    assert records(reporter)[0]['props']['occurrences']==64
    schema=reports._schema()
    assert reports._frames('backend/core/tasks.py:10',schema)=='backend/core/tasks.py:10'
    assert reports._frames('backend/user-data.py:10\n/home/name.py:10\nassets/user-secret.js:10',schema)==''


def test_journal_http500_owner_collects_without_uploading_journal(reporter,monkeypatch,tmp_path):
    from core import error_journal
    monkeypatch.setattr(error_journal,'JOURNAL_PATH',str(tmp_path/'local.jsonl'),raising=False)
    reporter.consent(True)
    error_journal.record(RuntimeError('my private voice'),route='/api/generate',trace='private source line')
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['category']=='generation_failed'
    assert 'private' not in json.dumps(saved)


@pytest.mark.parametrize('outcome',['error_frame','exception','cancelled'])
def test_actual_dub_task_terminal_owner(reporter,monkeypatch,outcome):
    from core.tasks import TaskManager
    globals_=TaskManager.worker.__globals__
    for name in ['create','mark_running','append_event','mark_failed','mark_done','mark_cancelled']:
        monkeypatch.setattr(globals_['job_store'],name,lambda *a,**kw:None)
    monkeypatch.setattr(globals_['run_sentinel'],'touch_activity',lambda *a:None)
    reporter.consent(True)
    async def run():
        manager=TaskManager()
        async def stream():
            if outcome=='exception':raise ValueError('private transcript')
            if outcome=='cancelled':manager.cancel_task('private-project-id')
            yield 'data: {"type":"error","error":"private transcript"}\n\n'
        await manager.add_task('private-project-id','dub_generate',stream)
        worker=asyncio.create_task(manager.worker())
        try:await asyncio.wait_for(manager.queue.join(),2)
        finally:
            worker.cancel()
            try:await worker
            except asyncio.CancelledError:pass
    asyncio.run(run())
    saved=records(reporter)
    assert len(saved)==(0 if outcome=='cancelled' else 1)
    if saved:
        assert saved[0]['props']['stage']=='dub'
        assert 'private' not in json.dumps(saved)


def test_actual_progressive_openai_sse_failure(reporter):
    from api.routers.openai_compat import _stream_speech_pcm
    reporter.consent(True)
    async def render(_):raise RuntimeError('private sentence')
    async def run():return [frame async for frame in _stream_speech_pcm(b'first',['secret'],render,sse=True)]
    assert any('speech_generation_failed' in frame for frame in asyncio.run(run()))
    assert records(reporter)[0]['props']['category']=='generation_failed'


def test_actual_gateway_model_and_engine_failures(reporter,monkeypatch):
    from services import gpu_gateway
    reporter.consent(True)
    async def fail(*a,**kw):raise ValueError('private checkpoint')
    monkeypatch.setattr(gpu_gateway,'_run_local',fail)
    class Backend:
        def ensure_ready(self):pass
    async def run():
        with pytest.raises(ValueError):await gpu_gateway.prewarm(backend=Backend(),engine='kittentts',timeout=1)
        with pytest.raises(ValueError):await gpu_gateway.run(local=gpu_gateway.LocalCall(lambda:None))
    asyncio.run(run())
    saved=records(reporter)
    assert {r['props']['category'] for r in saved}=={'engine_failed','model_load_failed'}
    assert 'private' not in json.dumps(saved)


def test_diagnostics_routes_strict_and_admin_guarded(reporter):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.settings import router
    from api.dependencies import require_admin,require_admin_action
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[require_admin]=lambda:None
    app.dependency_overrides[require_admin_action]=lambda:None
    client=TestClient(app)
    assert client.put('/api/settings/diagnostics',json={'enabled':'true'}).status_code==422
    assert client.put('/api/settings/diagnostics',json={'enabled':True}).status_code==200
    good={'category':'generation_failed','source':'renderer','stage':'generation','error_type':'TypeError','expected_epoch':reporter.epoch}
    assert client.post('/api/settings/diagnostics/event',json=good).status_code==200
    for bad in [{**good,'message':'secret'},{**good,'error_type':'secret'},{**good,'frames':'/home/private.py:2'},{**good,'source':'backend'}]:
        assert client.post('/api/settings/diagnostics/event',json=bad).status_code==422
    for path in ['/api/settings/diagnostics','/api/settings/diagnostics/event']:
        route=next(r for r in app.routes if getattr(r,'path','')==path and 'POST' in r.methods or getattr(r,'path','')==path and 'PUT' in r.methods)
        assert any(dep.call is require_admin_action for dep in route.dependant.dependencies)
    assert records(reporter)[0]['props']['source']=='renderer'


def test_standalone_consent_routes_are_available_before_opt_in_and_after_withdrawal(reporter,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.settings import router
    from api.dependencies import require_admin,require_admin_action
    from core import analytics,analytics_transport,prefs

    monkeypatch.setattr(analytics,'_destination',lambda:{
        'site':'localtest123','host':'https://collector.invalid','appUrl':'https://app.invalid',
    })
    def no_transport(*args,**kwargs):
        pytest.fail('Availability/consent queries must not start delivery')
    monkeypatch.setattr(analytics_transport,'SupermetryTransport',no_transport)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[require_admin]=lambda:None
    app.dependency_overrides[require_admin_action]=lambda:None
    with TestClient(app) as client:
        # Check the first response without waiting for a background worker tick.
        response=client.get('/api/settings/diagnostics')
        assert response.status_code==200
        first=response.json()
        assert first['available'] is True
        assert first['enabled'] is False and first['opted_in'] is False
        assert first['prompted'] is False and first['externally_managed'] is False
        assert 'diagnostics_epoch' not in prefs._load()
        reporter._tick()
        assert reporter.transport is None and not reporter.root.exists()

        allowed=client.put('/api/settings/diagnostics',json={'enabled':True})
        assert allowed.status_code==200
        assert allowed.json()['available'] is True and allowed.json()['opted_in'] is True
        assert client.put('/api/settings/diagnostics',json={'enabled':False}).status_code==200
        reporter._tick()
        withdrawn=client.get('/api/settings/diagnostics').json()
        assert withdrawn['available'] is True and withdrawn['prompted'] is True
        assert withdrawn['enabled'] is False and withdrawn['opted_in'] is False
        assert reporter.transport is None


@pytest.mark.parametrize('kill',[None,'OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_standalone_availability_still_requires_destination_and_no_hard_kill(reporter,monkeypatch,kill):
    from core import analytics
    assert reporter.status()['available'] is False  # No destination in this fixture.
    monkeypatch.setattr(analytics,'_destination',lambda:{'site':'localtest123','host':'https://collector.example','appUrl':'https://app.example'})
    if kill:
        monkeypatch.setenv(kill,'1')
        assert reporter.status()['available'] is False
        assert reporter.consent(True)['enabled'] is False
        assert not reporter.root.exists()
    else:
        assert reporter.status()['available'] is True
    assert reporter.transport is None


def test_managed_availability_uses_desktop_authority_without_local_destination(reporter,monkeypatch,tmp_path):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import analytics
    monkeypatch.setenv('OMNIVOICE_DIAGNOSTICS_DIR',str(tmp_path/'managed'))
    def no_destination():
        pytest.fail('Managed Python must not resolve a direct transport destination')
    monkeypatch.setattr(analytics,'_destination',no_destination)
    managed=reports.Reporter();managed.schema=reports._schema()
    assert managed.status()['available'] is True
    assert managed.status()['enabled'] is False
    assert managed.transport is None
    with pytest.raises(PermissionError):managed.consent(True)


def test_capture_releases_exception_frames_and_large_inputs(reporter):
    import gc
    import weakref
    reporter.consent(True)
    class Tensor:
        pass
    class MessageMustNotBeRead(ValueError):
        def __str__(self):raise AssertionError('message read')
    def fail():
        tensor=Tensor()
        ref=weakref.ref(tensor)
        try:
            raise MessageMustNotBeRead('secret transcript')
        except Exception as error:
            assert reporter.capture('generation_failed',error=error)
        return ref
    ref=fail()
    gc.collect()
    assert ref() is None  # Queued data cannot retain the traceback's tensors.
    item=reporter.intake.queue[0]
    assert item[1]['error_type']=='ValueError'
    assert 'secret' not in json.dumps(item)
    assert records(reporter)[0]['props']['error_type']=='ValueError'


from test_download_install_integrity import install_origin


def test_actual_model_install_failed_digest_reports_once(reporter,install_origin):
    from test_download_install_integrity import GIT_BLOB_OID,_install
    reporter.consent(True)
    install_origin.etag=GIT_BLOB_OID
    _install(install_origin)
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['category']=='download_failed'
    assert install_origin.repo not in json.dumps(saved)


def test_actual_optional_engine_installer_failure(reporter,monkeypatch):
    from services import sidecar_install as install
    reporter.consent(True)
    spec=install.SPECS['indextts2']
    monkeypatch.setattr(install,'_user_managed_dir',lambda spec:None)
    monkeypatch.setattr(install,'_healthy',lambda *a,**kw:False)
    def fail(*a):raise install._StepError('private machine path','private remediation')
    monkeypatch.setattr(install,'_step_preflight',fail)
    job=install._new_job(spec.engine_id)
    install._run_install(spec,job)
    assert job['state']=='failed'
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['category']=='install_failed'
    assert saved[0]['props']['engine_id']=='indextts2'
    assert 'private' not in json.dumps(saved)


def test_actual_asr_guard_failure_and_cancel_control(reporter):
    from concurrent.futures import ThreadPoolExecutor
    from services.asr_backend import run_transcribe_guarded
    reporter.consent(True)
    def fail():raise ValueError('private recording.wav')
    async def run():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ValueError):
                await run_transcribe_guarded(pool,fail,timeout=2)
    asyncio.run(run())
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['stage']=='transcription'
    assert 'private' not in json.dumps(saved)


@pytest.mark.parametrize('clean',[False,True])
def test_real_process_exit_marker_is_consent_stamped(reporter,clean):
    reports = importlib.import_module("core.diagnostic_reports")
    import os
    import subprocess
    import sys
    reporter.consent(True)
    script='''
from core import diagnostic_reports as d
r=d.Reporter();r.schema=d._schema();r._tick()
assert r.marker_owned
'''+('r.shutdown()\n' if clean else 'import os;os._exit(19)\n')
    env={**os.environ,'OMNIVOICE_DATA_DIR':str(reporter.root.parent),'SUPERMETRY_SITE_ID':'','PYTHONPATH':str(reports._ROOT/'backend')}
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,timeout=10)
    assert result.returncode==(0 if clean else 19)
    saved=records(reporter)
    assert len(saved)==(0 if clean else 1)
    if saved:assert saved[0]['props']['reason']=='unclean_exit'


def test_withdrawal_cleanup_denial_never_reenables_old_prefs(reporter,monkeypatch):
    reporter.consent(True);reporter.capture('generation_failed');records(reporter)
    old=reporter.epoch
    original=Path.unlink
    def deny(path,*args,**kw):
        if path.parent==reporter.directory:raise PermissionError('denied')
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'unlink',deny)
    # Consent persists before best-effort deletion; a locked record remains
    # stale and cannot become eligible when permission returns or app restarts.
    reporter.consent(False)
    assert not reporter.enabled and reporter.epoch!=old
    assert not reporter.capture('generation_failed')
    from core import prefs
    assert prefs.get('diagnostics_enabled') is False
    monkeypatch.setattr(Path,'unlink',original)
    assert records(reporter)==[]


def test_withdrawal_prefs_write_failure_latches_closed(reporter,monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    original=prefs._save
    monkeypatch.setattr(prefs,'_save',lambda _:(_ for _ in ()).throw(PermissionError('denied')))
    with pytest.raises(PermissionError):reporter.consent(False)
    reporter._tick()
    assert not reporter.enabled and not reporter.capture('generation_failed')
    restarted=reports.Reporter()
    restarted.schema=reports._schema()
    restarted._tick()
    try:
        assert not restarted.enabled
        assert not restarted.capture('generation_failed')
    finally:
        restarted.shutdown()
    monkeypatch.setattr(prefs,'_save',original)
    reporter.consent(False)


def test_real_preload_failure_is_reported_not_silent(reporter,monkeypatch):
    from types import SimpleNamespace
    from services import model_manager as manager,tts_backend
    from core import device_caps
    reporter.consent(True)
    monkeypatch.setattr(manager,'model',None)
    monkeypatch.setattr(manager,'_model_lock',asyncio.Lock())
    monkeypatch.setattr(tts_backend,'active_backend_id',lambda:'omnivoice')
    monkeypatch.setattr(device_caps,'detect_host_caps',lambda:SimpleNamespace(family='cpu'))
    monkeypatch.setattr(manager,'_ensure_omnivoice_licence',lambda:None)
    monkeypatch.setattr(manager,'_checkpoint_in_local_cache',lambda _:True)
    async def fail():raise ImportError('private installed path')
    monkeypatch.setattr(manager,'_load_model_with_timeout',fail)
    asyncio.run(manager.preload_model())
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['category']=='model_load_failed'
    assert saved[0]['props']['error_type']=='ImportError'
    assert 'private' not in json.dumps(saved)


def test_stale_transport_callback_cannot_fill_new_epoch_ack_queue(reporter,monkeypatch):
    from core import analytics,analytics_transport
    callbacks=[]
    class Transport:
        def __init__(self,destination,should_send,*,on_result):callbacks.append(on_result)
        def shutdown(self):pass
    monkeypatch.setattr(analytics,'_destination',lambda:{'site':'testdestination','host':'https://collector.example','appUrl':'https://app.example'})
    monkeypatch.setattr(analytics_transport,'SupermetryTransport',Transport)
    reporter.consent(True);reporter._tick();old_callback=callbacks[-1]
    reporter.consent(False);reporter.consent(True);reporter._tick()
    for _ in range(100):old_callback('f'*32,201)
    assert reporter.acks.empty()
    for i in range(64):callbacks[-1](f'{i:032x}',201)
    assert reporter.acks.qsize()==64
    reporter.consent(False)
    assert reporter.acks.empty()


def test_canonical_frame_and_queue_limits_can_tighten(reporter):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True)
    reporter.schema['limits']={**reporter.schema['limits'],'queue_count':2,'frame_bytes':25,'queue_bytes':1024}
    assert reports._frames('backend/core/tasks.py:10000001',reporter.schema)==''
    assert reports._frames('backend/core/tasks.py:10:10000001',reporter.schema)==''
    assert reports._frames('backend/core/tasks.py:100',reporter.schema)=='backend/core/tasks.py:100'
    for category in ['generation_failed','engine_failed','model_load_failed']:
        reporter.capture(category)
    saved=records(reporter)
    assert len(saved)<=2
    assert sum(p.stat().st_size for p in reporter.directory.glob('*.json'))<=1024


def test_clean_shutdown_locked_marker_does_not_become_crash(reporter,monkeypatch):
    reports = importlib.import_module("core.diagnostic_reports")
    reporter.consent(True);reporter._tick()
    marker=reporter.root/'session.json'
    original=Path.unlink
    def deny(path,*args,**kw):
        if path==marker:raise PermissionError('locked')
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'unlink',deny)
    reporter.shutdown()
    assert json.loads(marker.read_text(encoding='utf-8'))['clean'] is True
    monkeypatch.setattr(Path,'unlink',original)
    from core import run_sentinel
    monkeypatch.setattr(run_sentinel,'_previous_run_alive',lambda *a:False)
    restarted=reports.Reporter();restarted.schema=reports._schema()
    try:assert records(restarted)==[]
    finally:restarted.shutdown()


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_killed_launch_retains_revocation_tombstone_when_shared_prefs_cannot_write(reporter,monkeypatch,kill):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True);old=reporter.epoch
    original=prefs._save
    monkeypatch.setattr(prefs,'_save',lambda _:(_ for _ in ()).throw(PermissionError('denied')))
    monkeypatch.setenv(kill,'1')
    assert records(reporter)==[]
    assert (reporter.root/'revoked.json').exists()
    assert prefs._load()['diagnostics_enabled'] is True  # Old shared prefs could not change.
    monkeypatch.delenv(kill)
    restarted=reports.Reporter();restarted.schema=reports._schema()
    try:
        assert records(restarted)==[] and not restarted.capture('backend_crash')
        monkeypatch.setattr(prefs,'_save',original)
        restarted.consent(True)
        assert restarted.epoch!=old
        assert not (reporter.root/'revoked.json').exists()
    finally:
        restarted.shutdown()


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_enable_while_killed_creates_no_new_grant_profile_or_directory(reporter,monkeypatch,kill):
    from core import prefs
    monkeypatch.setenv(kill,'1')
    assert reporter.consent(True)['opted_in'] is False
    assert reporter.status()['available'] is False
    assert not reporter.root.exists()
    assert not Path(prefs._PREFS_PATH).exists()


@pytest.mark.parametrize('kill',['OMNIVOICE_DIAGNOSTICS_DISABLED','OMNIVOICE_ANALYTICS_DISABLED'])
def test_killed_startup_revokes_before_a_missing_policy_stops_the_worker(reporter,monkeypatch,kill):
    reports = importlib.import_module("core.diagnostic_reports")
    from core import prefs
    reporter.consent(True)
    monkeypatch.setenv(kill,'1')
    monkeypatch.setattr(reports,'_schema',lambda:(_ for _ in ()).throw(ValueError('policy unavailable')))
    restarted=reports.Reporter()
    restarted._run()
    assert (restarted.root/'revoked.json').exists() or prefs._load()['diagnostics_enabled'] is False
    assert not restarted.capture('backend_crash')


@pytest.mark.parametrize('cancelled',[False,True])
def test_batch_empty_transcript_keeps_cancellation_out_of_diagnostics(reporter,monkeypatch,tmp_path,cancelled):
    from types import SimpleNamespace
    from api.routers import batch
    from services import asr_backend,ffmpeg_utils
    import subprocess
    reporter.consent(True)
    monkeypatch.setattr(batch,'DATA_DIR',str(tmp_path))
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(stderr=b'Duration: 00:00:02.00',stdout=b''))
    monkeypatch.setattr(ffmpeg_utils,'find_ffmpeg',lambda:'ffmpeg')
    monkeypatch.setattr(ffmpeg_utils,'validate_media_source',lambda *_:None)
    monkeypatch.setattr(ffmpeg_utils,'require_audio_stream',lambda *_:None)
    job={'status':'running','video_path':str(tmp_path/'input.mp4'),'langs':['en']}
    async def transcribe(*a,**kw):
        if cancelled:job['status']='cancelled'
        return [],'en'
    monkeypatch.setattr(asr_backend,'run_transcribe_guarded',transcribe)
    asyncio.run(batch._run_batch_pipeline('diagnostic-empty',job))
    assert job['status']==('cancelled' if cancelled else 'failed')
    assert len(records(reporter))==(0 if cancelled else 1)


def test_argos_terminal_error_row_is_reported_without_losing_partial_results(reporter,monkeypatch):
    from test_dub_translate import _install_fake_argos
    from api.routers.dub_translate import dub_translate,TranslateRequest
    import importlib
    _install_fake_argos(monkeypatch)
    def translate(text,*_):
        if text=='private failure':raise RuntimeError('private provider response')
        return '[es]'+text
    monkeypatch.setattr(importlib.import_module('argostranslate.translate'),'translate',translate)
    reporter.consent(True)
    result=asyncio.run(dub_translate(TranslateRequest(
        provider='argos',source_lang='en',target_lang='es',quality='fast',
        segments=[{'id':'bad','text':'private failure'},{'id':'good','text':'Hello'}],
    )))
    rows=result['translated']
    assert len(rows)==2 and rows[0]['error'] and rows[1]['text']=='[es]Hello'
    saved=records(reporter)
    assert len(saved)==1 and saved[0]['props']['stage']=='translation'
    assert saved[0]['props']['error_type']=='RuntimeError'
    assert 'private' not in json.dumps(saved)


def test_asr_compute_deadline_is_not_saved_as_network_error(reporter,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from services import asr_backend
    entered=threading.Event();release=threading.Event()
    reporter.consent(True)
    def blocked():
        entered.set()
        assert release.wait(2)
    class ObservedAsyncio:
        def __getattr__(self,name):return getattr(asyncio,name)
        async def wait_for(self,future,timeout):
            # Start the real wait_for deadline only after native compute began.
            # Scheduler load must not turn this into a queued-work timeout.
            assert await asyncio.to_thread(entered.wait,1)
            return await asyncio.wait_for(future,timeout=.01)
    monkeypatch.setattr(asr_backend,'asyncio',ObservedAsyncio())
    async def run():
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with pytest.raises(asr_backend.ASRTimeoutError):
                    await asr_backend.run_transcribe_guarded(pool,blocked,timeout=60)
            finally:release.set()
    asyncio.run(run())
    assert entered.is_set()
    props=records(reporter)[0]['props']
    assert props['stage']=='transcription' and props['error_type']=='TimeoutError'
    assert props['error_code']=='UNKNOWN'


@pytest.mark.parametrize('failure_stage',['eager','diagnostic_initialization'])
def test_startup_failure_restores_loop_handler_and_stops_reporter(reporter,monkeypatch,failure_stage):
    reports = importlib.import_module("core.diagnostic_reports")
    from types import SimpleNamespace
    import main
    from core import run_sentinel
    monkeypatch.setenv('OMNIVOICE_STARTUP_WATCHDOG_S','0')
    monkeypatch.setattr(main,'_EAGER',True)
    monkeypatch.setattr(main,'_phase_a_build',lambda:None)
    monkeypatch.setattr(main,'_phase_a_finalize',lambda:None)
    monkeypatch.setattr(main,'_start_analytics_lifecycle',lambda *_:None)
    monkeypatch.setattr(run_sentinel,'detect_unclean_shutdown',lambda:None)
    monkeypatch.setattr(run_sentinel,'write_sentinel',lambda:None)
    async def fail(_):raise RuntimeError('controlled startup failure')
    monkeypatch.setattr(main,'_phase_b',fail)
    if failure_stage=='diagnostic_initialization':
        initialize=reports.initialize
        def fail_initialize():
            initialize()
            raise RuntimeError('controlled startup failure')
        monkeypatch.setattr(reports,'initialize',fail_initialize)
    captures=[]
    monkeypatch.setattr(reports,'capture',lambda *a,**kw:captures.append(a))
    async def run():
        loop=asyncio.get_running_loop()
        previous=lambda *_:None
        loop.set_exception_handler(previous)
        for _ in range(2):
            with pytest.raises(RuntimeError,match='controlled startup'):
                async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                    pytest.fail('failed startup must not yield')
            assert loop.get_exception_handler() is previous
            assert reports._reporter is None
            loop.call_exception_handler({'message':'controlled loop error','exception':RuntimeError('private')})
        assert captures==([('backend_crash',)] * 2 if failure_stage=='eager' else [])
    asyncio.run(run())


def test_reporter_fixture_tracks_runtime_module_replacement(monkeypatch,tmp_path):
    import importlib.util
    import sys
    import core
    current=__import__('importlib').import_module('core.diagnostic_reports')
    spec=importlib.util.spec_from_file_location('core.diagnostic_reports',current.__file__)
    fresh=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,'core.diagnostic_reports',fresh)
    monkeypatch.setattr(core,'diagnostic_reports',fresh)
    spec.loader.exec_module(fresh)
    fixture=globals()['reporter'].__wrapped__(monkeypatch,tmp_path)
    instance=next(fixture)
    try:
        assert type(instance) is fresh.Reporter
        instance.consent(True)
        assert fresh.capture_failure('translation',error=RuntimeError('private'))
        assert len(records(instance))==1
    finally:fixture.close()


def test_cancelled_argos_request_does_not_report_late_worker_failure(reporter,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from test_dub_translate import _install_fake_argos
    from api.routers import dub_translate
    _install_fake_argos(monkeypatch)
    entered=threading.Event();release=threading.Event()
    def translate(*_):
        entered.set()
        assert release.wait(2)
        raise RuntimeError('private provider response')
    monkeypatch.setattr(importlib.import_module('argostranslate.translate'),'translate',translate)
    reporter.consent(True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(dub_translate,'_cpu_pool',pool)
        async def run():
            task=asyncio.create_task(dub_translate.dub_translate(dub_translate.TranslateRequest(
                provider='argos',source_lang='en',target_lang='es',quality='fast',
                segments=[{'id':'bad','text':'private recording'}],
            )))
            try:
                assert await asyncio.to_thread(entered.wait,1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            finally:release.set()
        asyncio.run(run())
    assert records(reporter)==[]



def test_network_delivery_timeout_retains_network_classification(reporter):
    reporter.consent(True)
    reporter.capture('download_failed',stage='download',error=TimeoutError('private endpoint'))
    props=records(reporter)[0]['props']
    assert props['error_type']=='TimeoutError' and props['error_code']=='NETWORK_ERROR'
