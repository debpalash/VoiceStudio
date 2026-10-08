"""A published dub must contain every requested spoken segment, without early clipping."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import torch
import soundfile as sf

from schemas.requests import DubRequest


@pytest.fixture
def render_dub(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg
    job = {'duration': 4.0, 'dubbed_tracks': {}, 'segments': [], 'seg_wav_kind_by_lang': {'en': 'natural'}}
    path = tmp_path / 'job'
    path.mkdir()
    events = []
    generated = []
    async def resolve():
        return 'omnivoice', SimpleNamespace(target='local', remote=False), backend
    def generate(**kwargs):
        generated.append(kwargs)
        return output[0]()
    backend = SimpleNamespace(sample_rate=24000, generate=generate, applies_own_mastering=True)
    output = [lambda: torch.ones(1, 24000) * .1]
    class Tasks:
        def is_cancelled(self, *_): return False
        async def add_task(self, tid, kind, func, *args):
            async for event in func(*args):
                if event.startswith('data: '): events.append(json.loads(event[6:]))
    monkeypatch.setattr(dg, '_resolve_dub_execution', resolve)
    monkeypatch.setattr(dg, '_get_job', lambda _: job)
    monkeypatch.setattr(dg, '_save_job', lambda *_: None)
    monkeypatch.setattr(dg, 'DUB_DIR', str(tmp_path))
    monkeypatch.setattr(dg, 'dub_seg_path', lambda _, sid: str(path / f'seg_{sid}.wav'))
    monkeypatch.setattr(dg, 'task_manager', Tasks())
    monkeypatch.setattr(dg, 'rvc_is_enabled', lambda: False)
    monkeypatch.setattr(dg, 'mark_synthetic', lambda a, *args, **kw: a)
    monkeypatch.setattr(dg, 'get_effect_chain', lambda _: None)
    monkeypatch.setattr(dg, 'normalize_audio', lambda a, **kw: a)
    def run(**kwargs):
        body = dict(segments=[dict(start=0, end=1, text='hello')], segment_ids=['a'], language_code='en', num_step=4)
        body.update(kwargs)
        asyncio.run(dg.dub_generate('job', DubRequest(**body)))
        return events
    return SimpleNamespace(run=run, output=output, job=job, path=path, generated=generated)


def test_failed_segment_does_not_publish_complete_track(render_dub):
    def fail(): raise RuntimeError('engine failed')
    render_dub.output[0] = fail
    events = render_dub.run()
    assert any(e['type'] == 'error' for e in events)
    assert not any(e['type'] == 'done' for e in events)
    assert not render_dub.job['dubbed_tracks']


def test_missing_partial_cache_is_regenerated(render_dub):
    events = render_dub.run(regen_only=[])
    assert render_dub.generated
    assert (render_dub.path / 'seg_en_a.wav').exists()
    assert any(e['type'] == 'done' for e in events)


@pytest.mark.parametrize("rvc_enabled", [False, True])
def test_strict_slot_keeps_full_cache_and_fits_the_tail(render_dub, monkeypatch, rvc_enabled):
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.cat((torch.ones(1, 24000)*.1, torch.ones(1, 24000)*.3), dim=-1)
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: rvc_enabled)
    monkeypatch.setattr(dg, "apply_rvc", lambda _: None)
    calls=[]
    async def stretch(wav, target, sr):
        calls.append((wav.shape[-1], target, float(wav[0,-1])))
        return torch.nn.functional.interpolate(wav.unsqueeze(0),size=target,mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events=render_dub.run(timing_strategy='strict_slot')
    assert any(e['type']=='done' for e in events)
    assert sf.info(render_dub.path/'seg_en_a.wav').duration == 2
    assert calls and calls[0][0] == 48000 and calls[0][1] == 24000
    assert calls[0][2] == pytest.approx(.3, abs=1e-4)


def test_concise_overrun_does_not_publish_truncated_track(render_dub):
    render_dub.output[0] = lambda: torch.ones(1, 48000)*.1
    events=render_dub.run()
    assert any(e['type']=='error' for e in events)
    assert not any(e['type']=='done' for e in events)
    assert not render_dub.job['dubbed_tracks']


def test_silent_engine_output_is_not_a_successful_segment(render_dub):
    render_dub.output[0] = lambda: torch.zeros(1, 24000)
    events = render_dub.run()
    assert any(e['type'] == 'error' for e in events)
    assert not render_dub.job['dubbed_tracks']


def test_camera_cut_uses_word_times_instead_of_character_ratio():
    from services.segmentation import Segment, _apply_scene_cuts
    words = [
        {'text': 'A very long early phrase', 'start': 0, 'end': 1.8},
        {'text': 'Short later phrase', 'start': 4.0, 'end': 6.0},
    ]
    segment = Segment(start=0, end=6, text='A very long early phrase Short later phrase', extra={'words': words})
    pieces = _apply_scene_cuts([segment], [4.0])
    assert len(pieces) == 2
    assert pieces[0].end == 1.8
    assert pieces[1].start == 4
    assert pieces[0].extra['words'] == words[:1]
    assert pieces[1].text == 'Short later phrase'


def test_camera_cut_inside_a_word_does_not_reassign_speech():
    from services.segmentation import Segment, _apply_scene_cuts
    words = [
        {'text': 'A lengthy opening phrase', 'start': 0, 'end': 4},
        {'text': 'And the closing phrase', 'start': 4.5, 'end': 7},
    ]
    segment = Segment(start=0, end=7, text='A lengthy opening phrase And the closing phrase', extra={'words': words})
    assert _apply_scene_cuts([segment], [3]) == [segment]


def test_failed_regeneration_preserves_previous_track(render_dub):
    previous = render_dub.path / 'dubbed_en.wav'
    previous.write_bytes(b'previous successful output')
    render_dub.job['dubbed_tracks']['en'] = {'path': str(previous)}
    def fail(): raise RuntimeError('failure')
    render_dub.output[0] = fail
    render_dub.run()
    assert previous.read_bytes() == b'previous successful output'
    assert render_dub.job['dubbed_tracks']['en']['path'] == str(previous)


def test_timing_trims_edge_silence_but_keeps_internal_pauses():
    from services.audio_dsp import trim_speech_padding
    wav = torch.cat((torch.zeros(1, 1000), torch.ones(1, 200)*.1,
                     torch.zeros(1, 200), torch.ones(1, 200)*.1, torch.zeros(1,1000)), dim=-1)
    result = trim_speech_padding(wav, 1000)
    assert result.shape[-1] == 700
    assert torch.equal(result[..., 250:450], torch.zeros(1,200))


def _replace_subtitles(job):
    from services.dub_pipeline import replace_source_segments
    imported = [{'id': 0, 'start': 0.0, 'end': 1.0, 'text': 'imported cue'}]
    replace_source_segments(job, imported)
    return imported


def test_subtitles_replaced_during_synthesis_publish_nothing(render_dub):
    previous = render_dub.path / 'dubbed_en.wav'
    previous.write_bytes(b'previous successful output')
    render_dub.job['dubbed_tracks']['en'] = {'path': str(previous)}
    replaced = []
    def synthesize():
        replaced.append(_replace_subtitles(render_dub.job))
        return torch.ones(1, 24000) * .1
    render_dub.output[0] = synthesize
    events = render_dub.run()
    assert [e.get('error_code') for e in events if e['type'] == 'error'] == ['dub_source_changed']
    assert not any(e['type'] == 'done' for e in events)
    assert previous.read_bytes() == b'previous successful output'
    assert render_dub.job['dubbed_tracks']['en'] == {'path': str(previous)}
    assert render_dub.job['segments'] is replaced[0]
    # Neither the speech nor its fingerprint reaches the segment cache.
    assert not (render_dub.path / 'seg_en_a.wav').exists()
    assert 'seg_hashes' not in render_dub.job
    assert 'seg_order' not in render_dub.job


@pytest.fixture
def assembling(render_dub, monkeypatch):
    """Half-length speech, so strict-slot assembly awaits the underrun stretch."""
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.ones(1, 12000) * .1
    hooks = []
    async def stretch(wav, target, sr):
        for hook in hooks:
            hook()
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    previous = render_dub.path / 'dubbed_en.wav'
    previous.write_bytes(b'previous successful output')
    render_dub.job['dubbed_tracks']['en'] = {'path': str(previous)}
    # The previous track's segment cache, as previews read it.
    cached = render_dub.path / 'seg_en_a.wav'
    cached.write_bytes(b'speech of the previous track')
    render_dub.job['seg_hashes_by_lang'] = {'en': {'a': 'previous-fingerprint'}}
    render_dub.job['seg_hashes'] = {'a': 'previous-fingerprint'}
    render_dub.job['seg_order'] = ['a']
    return SimpleNamespace(hooks=hooks, previous=previous, cached=cached)


def _assert_previous_segment_cache(render_dub, assembling):
    """Previews and partial regeneration still see only the previous track."""
    assert assembling.cached.read_bytes() == b'speech of the previous track'
    assert render_dub.job['seg_hashes_by_lang'] == {'en': {'a': 'previous-fingerprint'}}
    assert render_dub.job['seg_hashes'] == {'a': 'previous-fingerprint'}
    assert render_dub.job['seg_order'] == ['a']
    assert 'seg_num_step' not in render_dub.job


def test_subtitles_replaced_during_assembly_keep_previous_track(render_dub, assembling):
    assembling.hooks.append(lambda: _replace_subtitles(render_dub.job))
    events = render_dub.run(timing_strategy='strict_slot')
    assert [e.get('error_code') for e in events if e['type'] == 'error'] == ['dub_source_changed']
    assert not any(e['type'] == 'done' for e in events)
    assert assembling.previous.read_bytes() == b'previous successful output'
    assert render_dub.job['dubbed_tracks']['en'] == {'path': str(assembling.previous)}
    assert render_dub.job['segments'][0]['text'] == 'imported cue'
    _assert_previous_segment_cache(render_dub, assembling)
    assert not list(render_dub.path.glob('.render-*'))


def test_cancel_during_assembly_keeps_previous_track(render_dub, assembling, monkeypatch):
    import api.routers.dub_generate as dg
    cancelled = []
    monkeypatch.setattr(dg.task_manager, 'is_cancelled', lambda *_: bool(cancelled))
    assembling.hooks.append(lambda: cancelled.append(True))
    events = render_dub.run(timing_strategy='strict_slot')
    assert events[-1]['type'] == 'cancelled'
    assert not any(e['type'] == 'done' for e in events)
    assert assembling.previous.read_bytes() == b'previous successful output'
    assert render_dub.job['dubbed_tracks']['en'] == {'path': str(assembling.previous)}
    _assert_previous_segment_cache(render_dub, assembling)
    assert not list(render_dub.path.glob('.render-*'))


def test_published_render_installs_its_segment_speech(render_dub, assembling):
    events = render_dub.run(timing_strategy='strict_slot')
    assert any(e['type'] == 'done' for e in events)
    assert assembling.cached.read_bytes() != b'speech of the previous track'
    assert sf.info(assembling.cached).frames == 12000
    fingerprint = render_dub.job['seg_hashes_by_lang']['en']['a']
    assert fingerprint != 'previous-fingerprint'
    assert render_dub.job['seg_hashes'] == {'a': fingerprint}
    assert not list(render_dub.path.glob('.render-*'))


def test_failed_render_leaves_cached_speech_and_fingerprints_together(render_dub):
    cached = render_dub.path / 'seg_en_a.wav'
    cached.write_bytes(b'speech the old fingerprint describes')
    render_dub.job['seg_hashes_by_lang'] = {'en': {'a': 'old-fingerprint'}}
    calls = []
    def synthesize():
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError('engine failed')
        return torch.ones(1, 24000) * .1
    render_dub.output[0] = synthesize
    events = render_dub.run(
        segments=[dict(start=0, end=1, text='new words'), dict(start=1, end=2, text='more')],
        segment_ids=['a', 'b'],
    )
    assert any(e['type'] == 'error' for e in events)
    # A later partial run trusts this pair; new speech under the old
    # fingerprint would be spliced in as up to date.
    assert cached.read_bytes() == b'speech the old fingerprint describes'
    assert render_dub.job['seg_hashes_by_lang'] == {'en': {'a': 'old-fingerprint'}}
    assert not list(render_dub.path.glob('.render-*'))


def test_published_segments_drop_qc_marks_of_the_previous_track():
    from api.routers.dub_generate import QC_FIELDS, _sync_job_segments
    job = {'segments': [{
        'id': 'a', 'start': 0.0, 'end': 1.0, 'text': 'hello', 'speaker_id': 'Speaker 1',
        'qc_drift': 0.8, 'qc_flagged': True, 'qc_recognized': 'yellow',
        'qc_measured_start': 0.1, 'qc_measured_end': 0.9,
    }]}
    _sync_job_segments(job, DubRequest(
        segments=[dict(start=0, end=1, text='hola')], segment_ids=['a'], language_code='es',
    ))
    (row,) = job['segments']
    assert row['text'] == 'hola' and row['speaker_id'] == 'Speaker 1'
    assert not set(QC_FIELDS) & set(row)


def test_qc_annotations_during_render_neither_block_nor_survive_publication(render_dub):
    render_dub.job['segments'] = [{'id': 'a', 'start': 0.0, 'end': 1.0, 'text': 'hello'}]
    def synthesize():
        # /dub/qc annotates the live rows in place; that is not a subtitle edit.
        render_dub.job['segments'][0].update(qc_drift=0.9, qc_flagged=True)
        return torch.ones(1, 24000) * .1
    render_dub.output[0] = synthesize
    events = render_dub.run()
    assert any(e['type'] == 'done' for e in events)
    assert 'qc_flagged' not in render_dub.job['segments'][0]
    assert 'qc_drift' not in render_dub.job['segments'][0]
