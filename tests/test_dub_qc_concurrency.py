"""A QC result belongs to exactly the track and transcript that ASR read."""
import asyncio
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('change', ['regenerate', 'text', 'timing', 'audio', 'delete', 'replace', 'publication_wait', None])
def test_qc_does_not_publish_after_selected_render_changes(monkeypatch, tmp_path, change):
    from api.routers import dub_export, dub_generate
    from fastapi import HTTPException
    from schemas.requests import DubRequest
    from services import asr_backend, dub_pipeline, model_manager

    audio = tmp_path / 'dubbed_es.wav'
    audio.write_bytes(b'old rendered track')
    job = {
        'segments': [{'id': 'a', 'start': 0., 'end': 2., 'text': 'hola mundo'}],
        'segments_i18n': {'es': {'a': 'hola mundo'}},
        'dubbed_tracks': {'es': {'path': str(audio), 'timing_strategy': 'strict_slot',
                                'source_segments': [{'id': 'a', 'start': 0., 'end': 2.}]}},
        'seg_order': ['a'],
    }
    monkeypatch.setattr(dub_pipeline, '_dub_jobs', {'qc-race': job})
    monkeypatch.setattr(dub_pipeline, '_withdrawn_jobs', {})
    saved = Mock()
    monkeypatch.setattr(dub_pipeline, 'save_job', saved)
    monkeypatch.setattr(dub_export, '_job_dir_or_400', lambda _: None)
    monkeypatch.setattr(dub_export, '_get_job', lambda _: dub_pipeline._dub_jobs.get('qc-race'))
    monkeypatch.setattr(dub_export, '_dub_artifact', lambda path, *a, **kw: path)
    monkeypatch.setattr(asr_backend, 'asr_model_missing_error', lambda: None)
    monkeypatch.setattr(model_manager, '_get_gpu_pool', lambda: None)

    class Backend:
        id = 'test-local-asr'
        def transcribe(self, path, **kwargs):
            assert audio.read_bytes() == b'old rendered track'
            return {'segments': [{'start': 0., 'end': 2., 'text': 'hola mundo'}]}
    monkeypatch.setattr(asr_backend, 'load_active_asr_backend', Backend)

    held_worker = []
    release_worker = []
    async def guarded(pool, fn, **kwargs):
        recognition = fn()
        if change == 'publication_wait':
            import threading
            loop = asyncio.get_running_loop()
            held, contended = asyncio.Event(), asyncio.Event()
            release = threading.Event()
            release_worker.append(release)
            original = dub_pipeline._dub_jobs_lock
            class Lock:
                def __enter__(self):
                    if not original.acquire(blocking=False):
                        loop.call_soon_threadsafe(contended.set)
                        original.acquire()
                    return self
                def __exit__(self, *_):
                    original.release()
            monkeypatch.setattr(dub_export, '_dub_jobs_lock', Lock())
            def publisher():
                with original:
                    loop.call_soon_threadsafe(held.set)
                    assert release.wait(2), 'QC lock wait blocked the event loop'
            held_worker.append(loop.run_in_executor(None, publisher))
            await held.wait()
            async def release_from_loop():
                await asyncio.wait_for(contended.wait(), 10)
                assert not held_worker[0].done()
                release.set()
            held_worker.append(asyncio.create_task(release_from_loop()))
        # These edits happen while an actual ASR worker releases the event loop.
        if change == 'regenerate':
            dub_generate._sync_job_segments(job, DubRequest(
                segments=[{'start': 0., 'end': 2., 'text': 'buenos dias'}],
                segment_ids=['a'], language_code='es'))
            job['dubbed_tracks']['es'] = dict(job['dubbed_tracks']['es'])
        elif change == 'text':
            job['segments_i18n']['es']['a'] = 'buenos dias'
        elif change == 'timing':
            job['dubbed_tracks']['es']['source_segments'][0]['end'] = 20.
        elif change == 'audio':
            # A render overwrites the same path before publishing new metadata.
            audio.write_bytes(b'new rendered audio with different bytes')
        elif change == 'delete':
            dub_pipeline.purge_jobs(['qc-race'], delete_rows=lambda: None)
        elif change == 'replace':
            dub_pipeline.put_job('qc-race', dict(job))
        return recognition
    monkeypatch.setattr(asr_backend, 'run_transcribe_guarded', guarded)

    async def exercise():
        try:
            result = await dub_export.dub_qc_pass('qc-race', lang='es', drift_threshold=.5)
            if held_worker:
                await asyncio.gather(*held_worker)
            return result
        finally:
            for release in release_worker:
                release.set()
            if held_worker:
                await asyncio.gather(*held_worker, return_exceptions=True)
    run = lambda: asyncio.run(exercise())
    if change in {None, 'publication_wait'}:
        assert run()['flagged_count'] == 0
        assert job['segments'][0]['qc_drift'] == 0
        saved.assert_called_once()
    else:
        with pytest.raises(HTTPException) as error:
            run()
        assert error.value.status_code == 409
        assert error.value.detail['code'] == 'dub_qc_track_changed'
        assert all('qc_drift' not in segment for segment in job['segments'])
        saved.assert_not_called()
        if change == 'delete':
            assert 'qc-race' not in dub_pipeline._dub_jobs
