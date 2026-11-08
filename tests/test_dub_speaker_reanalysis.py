"""Explicit speaker analysis refreshes labels without re-transcribing edited cues."""
import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import dub_core as dc


@pytest.fixture
def original_extract():
    from services.speaker_clone import extract_speaker_clones
    return extract_speaker_clones


@pytest.fixture
def reanalysis(tmp_path,monkeypatch,original_extract):
    source = tmp_path/'audio.wav'; source.write_bytes(b'audio')
    job_dir = tmp_path/'job'; job_dir.mkdir()
    job = {'audio_path':str(source),'transcription_complete':True,'source_lang':'en',
           'full_transcript':'Edited source words',
           'segments':[{'id':'a','start':0,'end':.4,'text':'Edited first','speaker_id':'Speaker 9','profile_id':'auto:speaker_9','gain':.8},
                       {'id':'b','start':.5,'end':1,'text':'Edited second','speaker_id':'Speaker 9','profile_id':'saved-voice'}],
           'segments_i18n':{'fr':{'a':'Traduction','b':'Deuxième traduction'}},
           'segment_clones':{'a':{'ref_audio':str(source),'ref_text':'Original words'}},
           'dubbed_tracks':{'fr':{'path':str(source),'custom':'retain'}}}
    saved = []
    monkeypatch.setattr(dc,'_get_job',lambda _:job)
    monkeypatch.setattr(dc,'_save_job',lambda _,value,**kwargs:saved.append(copy.deepcopy(value)))
    monkeypatch.setattr(dc,'_safe_job_dir',lambda _:str(job_dir))
    monkeypatch.setattr(dc,'offload_tts_for_asr',lambda:None)
    monkeypatch.setattr(dc,'restore_tts_after_asr',lambda:None)
    monkeypatch.setattr(dc.task_manager,'is_cancelled',lambda _:False)
    def no_asr(*args,**kwargs): raise AssertionError('Speaker refresh must not load ASR')
    monkeypatch.setattr('services.asr_backend.get_active_asr_backend',no_asr)
    monkeypatch.setattr('services.asr_backend.asr_model_missing_error',no_asr)
    monkeypatch.setattr(dc,'get_model',no_asr)
    calls = []
    class Diarization:
        def itertracks(self,yield_label=True):
            from types import SimpleNamespace
            yield SimpleNamespace(start=0,end=.45),None,'SPEAKER_00'
            yield SimpleNamespace(start=.45,end=1),None,'SPEAKER_01'
    def pipe(path,**kwargs):
        calls.append((path,kwargs))
        return Diarization()
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(pipe,None))
    def extract(path,segments,directory,**kwargs):
        result = {}
        for cue in segments:
            reference = Path(directory)/(cue['id']+'.wav'); reference.write_bytes(b'reference')
            result[cue['speaker_id']] = {'ref_audio':str(reference),'ref_text':cue['text'],'duration':4}
        return result
    monkeypatch.setattr('services.speaker_clone.extract_speaker_clones',extract)
    app = FastAPI(); app.include_router(dc.router)
    return TestClient(app),job,calls,saved,job_dir,pipe


def events(response):
    assert response.status_code == 200,response.text
    result = []
    for block in response.text.split('\n\n'):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith('event: ')),None)
        data = next((line[6:] for line in lines if line.startswith('data: ')),None)
        if event and data: result.append((event,json.loads(data)))
    return result


def test_explicit_analysis_reruns_diarizer_preserving_edited_transcript(reanalysis):
    client,job,calls,saved,_,_ = reanalysis
    old = copy.deepcopy(job)
    first = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true&num_speakers=2'))
    assert len(calls) == 1
    assert calls[0][1]['num_speakers'] == 2
    final = next(data for event,data in first if event=='final')
    assert [row['speaker_id'] for row in final['segments']] == ['Speaker 1','Speaker 2']
    assert final['segments'][0]['profile_id'] == 'auto:speaker_1'
    assert final['segments'][1]['profile_id'] == 'saved-voice'
    for actual,prior in zip(final['segments'],old['segments']):
        assert {k:v for k,v in actual.items() if k not in ('speaker_id','profile_id')} == {k:v for k,v in prior.items() if k not in ('speaker_id','profile_id')}
    assert job['segments_i18n'] == old['segments_i18n']
    assert job['dubbed_tracks'] == old['dubbed_tracks']
    assert job['full_transcript'] == old['full_transcript']
    assert set(job['cast_sources']) == {'Speaker 1','Speaker 2'}
    assert saved
    events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true&num_speakers=3'))
    assert [call[1]['num_speakers'] for call in calls] == [2,3]


def test_normal_reconnect_still_replays_without_models(reanalysis):
    client,job,calls,saved,_,_ = reanalysis
    before = copy.deepcopy(job)
    result = events(client.get('/dub/transcribe-stream/job?num_speakers=2'))
    assert [event for event,_ in result] == ['final','done']
    assert calls == [] and saved == [] and job == before


def test_failed_analysis_keeps_existing_segments_and_references(reanalysis,monkeypatch):
    client,job,calls,saved,job_dir,_ = reanalysis
    before = copy.deepcopy(job)
    def fail(*args,**kwargs): raise RuntimeError('Diarizer failed')
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(fail,None))
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [event for event,_ in result][-2:] == ['error','done']
    assert not any(event=='final' for event,_ in result)
    assert job == before and saved == []
    assert list(job_dir.rglob('*.wav')) == []


def test_concurrent_edit_wins_over_reanalysis(reanalysis,monkeypatch):
    client,job,calls,saved,job_dir,pipe = reanalysis
    newer = [{**job['segments'][0],'text':'New concurrent edit'}]
    def edit_during_analysis(*args,**kwargs):
        result = pipe(*args,**kwargs)
        dc.replace_source_segments(job,newer)
        return result
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(edit_during_analysis,None))
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert next(data for event,data in result if event=='final')['segments'] == newer
    assert job['segments'] == newer and saved == []
    assert list(job_dir.rglob('*.wav')) == []


def test_cancelled_analysis_does_not_publish(reanalysis,monkeypatch):
    client,job,calls,saved,job_dir,pipe = reanalysis
    before = copy.deepcopy(job)
    def abort_during_analysis(*args,**kwargs):
        result = pipe(*args,**kwargs); job['aborted'] = True; return result
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(abort_during_analysis,None))
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert any(event=='aborted' for event,_ in result)
    assert {k:v for k,v in job.items() if k!='aborted'} == before
    assert saved == [] and list(job_dir.rglob('*.wav')) == []


@pytest.mark.parametrize('kind',['missing_model','references','persist'])
def test_reanalysis_failure_is_atomic(reanalysis,monkeypatch,kind):
    client,job,calls,saved,job_dir,_ = reanalysis
    before = copy.deepcopy(job)
    def fail(*args,**kwargs): raise OSError('Operation failed')
    if kind=='missing_model': monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(None,'MISSING'))
    elif kind=='references': monkeypatch.setattr('services.speaker_clone.extract_speaker_clones',fail)
    else: monkeypatch.setattr(dc,'_save_job',fail)
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [event for event,_ in result][-2:] == ['error','done']
    assert job == before and saved == []
    assert list(job_dir.rglob('*.wav')) == []


def test_reanalysis_preserves_explicit_cross_speaker_and_imported_references(reanalysis):
    client,job,_,_,_,_ = reanalysis
    old_reference = dict(job['segment_clones']['a'])
    job['speaker_clones'] = {'Speaker 8':old_reference}
    job['segments'][0]['profile_id'] = 'auto:speaker_8'
    job['segments'][1]['profile_id'] = 'auto-seg:handoff-explicit'
    job['segment_clones']['handoff-explicit'] = old_reference
    events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    cross_binding = job['segments'][0]['profile_id'].removeprefix('auto-seg:')
    assert cross_binding != 'a'
    assert job['segment_clones'][cross_binding] == old_reference
    assert job['segments'][1]['profile_id'] == 'auto-seg:handoff-explicit'
    assert job['segment_clones']['handoff-explicit'] == old_reference


def test_strict_persistence_propagates_database_errors(monkeypatch):
    from services import dub_pipeline
    from contextlib import contextmanager
    @contextmanager
    def failed_db():
        raise OSError('Database write failed')
        yield
    monkeypatch.setattr(dub_pipeline,'db_conn',failed_db)
    with pytest.raises(OSError,match='Database write failed'):
        dub_pipeline.save_job('strict-test',{'segments':[]},strict=True)
    # The opt-in behavior leaves existing best-effort writers unchanged.
    assert dub_pipeline.save_job('legacy-test',{'segments':[]}) is None


@pytest.mark.parametrize('segments',[None,[]])
def test_speaker_only_mode_requires_existing_cues_without_asr(reanalysis,segments):
    client,job,calls,saved,_,_ = reanalysis
    job['segments'] = segments
    before = copy.deepcopy(job)
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [event for event,_ in result] == ['error','done']
    assert result[0][1]['code'] == 'dub_transcript_required'
    assert calls == [] and saved == [] and job == before


def test_source_references_use_original_words_and_skip_mixed_speaker_cues(reanalysis,monkeypatch):
    client,job,_,_,_,_ = reanalysis
    job['segments'][0].update(end=1,text='Edited translated words',text_original='Original words')
    job['segments'][1].update(text='Translated second',text_original='Original second')
    captured = []
    def extract(path,segments,directory,**kwargs):
        captured.extend(segments)
        return {}
    monkeypatch.setattr('services.speaker_clone.extract_speaker_clones',extract)
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [row['id'] for row in captured] == ['b']
    assert captured[0]['text'] == 'Original second'
    assert job['segments'][0]['text'] == 'Edited translated words'
    assert job['segments'][0]['start'] == 0 and job['segments'][0]['end'] == 1
    assert 'a' not in job['segment_clones']
    assert any(event=='warning' for event,_ in result)


def test_completed_reanalysis_prunes_unreferenced_previous_run(reanalysis):
    client,job,_,_,job_dir,_ = reanalysis
    old = job_dir/'refs'/'oldrun'; old.mkdir(parents=True)
    (old/'unused.wav').write_bytes(b'unused')
    job['ref_run'] = old.name
    events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert not old.exists()


def test_disconnect_prevents_late_reanalysis_publication(reanalysis,monkeypatch):
    import asyncio
    import threading
    _,job,_,saved,job_dir,pipe = reanalysis
    before = copy.deepcopy(job)
    entered = threading.Event(); release = threading.Event(); finished = threading.Event()
    def slow(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return pipe(*args,**kwargs)
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(slow,None))
    monkeypatch.setattr(dc,'restore_tts_after_asr',lambda:finished.set())
    monkeypatch.setattr(dc,'POST_ASR_PING_S',.001)
    async def run():
        response = await dc.dub_transcribe_stream('job',reanalyze_speakers=True)
        await anext(response.body_iterator)  # Immediate opening comment.
        await anext(response.body_iterator)  # Worker keepalive.
        # The keepalive can arrive before the worker thread starts; wait for it.
        assert await asyncio.to_thread(entered.wait, 5)
        await response.body_iterator.aclose()
        release.set()
        assert await asyncio.to_thread(finished.wait,5)
    try: asyncio.run(run())
    finally: release.set()
    assert job == before and saved == []
    assert list(job_dir.rglob('*.wav')) == []



def test_native_diarizer_ignores_unsupported_hint_with_warning(reanalysis,monkeypatch):
    client,job,calls,saved,_,pipe = reanalysis
    class Native:
        def __call__(self,path,**kwargs):
            assert 'num_speakers' not in kwargs
            assert kwargs['job_id'] == 'job'
            assert kwargs['cancel_check']() is False
            return pipe(path)
    monkeypatch.setattr('services.diarization_native.NativeSortformer',Native)
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(Native(),None))
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true&num_speakers=4'))
    assert any(event=='final' for event,_ in result)
    warning = next(data for event,data in result if event=='warning')
    assert warning['speaker_hint'] == {'requested':4,'status':'ignored'}


def test_reference_file_write_failure_rolls_back_reanalysis(reanalysis,monkeypatch,original_extract):
    from types import SimpleNamespace
    import numpy as np
    client,job,calls,saved,job_dir,_ = reanalysis
    job['segments'] = [{**job['segments'][0],'end':6}]
    before = copy.deepcopy(job)
    class SingleSpeaker:
        def itertracks(self,yield_label=True):
            yield SimpleNamespace(start=0,end=6),None,'SPEAKER_00'
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(lambda *a,**kw:SingleSpeaker(),None))
    monkeypatch.setattr('services.speaker_clone.extract_speaker_clones',original_extract)
    monkeypatch.setattr('services.speaker_clone.sf.read',lambda *a,**kw:(np.zeros(144000,dtype=np.float32),24000))
    def disk_full(*args,**kwargs): raise OSError('Disk full')
    monkeypatch.setattr('services.speaker_clone.sf.write',disk_full)
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [event for event,_ in result][-2:] == ['error','done']
    assert job == before and saved == []
    assert list(job_dir.rglob('*.wav')) == []



def test_no_diarization_evidence_preserves_previous_analysis(reanalysis,monkeypatch):
    client,job,calls,saved,job_dir,_ = reanalysis
    before = copy.deepcopy(job)
    class Empty:
        def itertracks(self,yield_label=True): return iter(())
    monkeypatch.setattr(dc,'_diarization_pipeline_checked',lambda:(lambda *a,**kw:Empty(),None))
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    assert [event for event,_ in result][-2:] == ['error','done']
    assert job == before and saved == []
    assert list(job_dir.rglob('*.wav')) == []



@pytest.mark.parametrize("legacy_idless",[False,"missing","null"])
def test_mixed_cue_default_is_rebound_but_explicit_cross_cue_selection_survives(reanalysis,legacy_idless):
    client,job,_,_,_,_ = reanalysis
    identity = '0' if legacy_idless else 'a'
    if legacy_idless:
        if legacy_idless == 'missing':
            del job['segments'][0]['id']
        else:
            job['segments'][0]['id'] = None
        job['segment_clones'][identity] = job['segment_clones'].pop('a')
    job['segments'][0].update(end=1,profile_id='auto-seg:'+identity)
    job['segments'][1]['profile_id'] = 'auto-seg:'+identity
    old_reference = copy.deepcopy(job['segment_clones'][identity])
    events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    # Cue a's own clip was a default, not an explicit choice. Its mixed audio
    # must no longer override the new pure pooled source in per-line mode.
    assert identity not in job['segment_clones']
    if legacy_idless == 'missing':
        assert 'id' not in job['segments'][0]
    elif legacy_idless == 'null':
        assert job['segments'][0]['id'] is None
    assert job['segments'][0]['profile_id'] == 'auto:speaker_2'
    assert job['speaker_clones']['Speaker 2']['ref_audio'] != old_reference['ref_audio']
    # Cue b explicitly chose cue a's audio; preserve that choice under a
    # separate key which no other cue can inherit as its automatic default.
    selected = job['segments'][1]['profile_id'].removeprefix('auto-seg:')
    assert selected != identity
    assert job['segment_clones'][selected] == old_reference


@pytest.mark.parametrize(('override', 'expected'), [(None, None), ('fr', 'fr')])
def test_speaker_refresh_keeps_an_unknown_source_language_unknown(reanalysis, override, expected):
    client,job,_,_,_,_ = reanalysis
    job['source_lang'] = None
    job['source_lang_detection_missing'] = True
    job['source_lang_override'] = override
    result = events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    final = next(data for event,data in result if event=='final')
    assert final['source_lang'] == expected
