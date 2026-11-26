"""A published dub must contain every requested spoken segment, without early clipping."""
import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import torch
import soundfile as sf

from schemas.requests import DubRequest

# Trimming eases the last 25 ms to zero (#4229); probe the take's real tail
# 50 ms (at 24 kHz) before the end to prove no words were cut.
_BEFORE_TAIL_FADE = -1200

@pytest.fixture
def render_dub(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg
    job = {'duration': 4.0, 'dubbed_tracks': {}, 'segments': [], 'seg_wav_kind_by_lang': {'en': 'natural'}}
    path = tmp_path / 'job'
    path.mkdir()
    checkpoint = tmp_path / 'fixture-model'
    checkpoint.mkdir()
    (checkpoint / 'model.safetensors').write_bytes(b'fixture weights')
    monkeypatch.setenv('OMNIVOICE_MODEL', str(checkpoint))
    events = []
    generated = []
    async def resolve():
        return 'omnivoice', backend
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
    monkeypatch.setattr(dg, "mark_synthetic_async", AsyncMock(side_effect=lambda a, *args, **kw: a), raising=False)
    monkeypatch.setattr(dg, 'get_effect_chain', lambda _: None)
    monkeypatch.setattr(dg, 'normalize_audio', lambda a, **kw: a)

    async def run_async(**kwargs: Any) -> list[dict[str, Any]]:
        body = dict(segments=[dict(start=0, end=1, text='hello')], segment_ids=['a'], language_code='en', num_step=4)
        body.update(kwargs)
        await dg.dub_generate("job", DubRequest(**body))
        return events

    def run(**kwargs: Any) -> list[dict[str, Any]]:
        return asyncio.run(run_async(**kwargs))

    return SimpleNamespace(run=run, run_async=run_async, output=output, job=job, path=path, generated=generated)


def test_segment_watermark_does_not_block_the_event_loop(
    render_dub: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.routers.dub_generate as dg
    from services import watermark
    from services.model_manager import begin_watermark_pool_lifecycle

    begin_watermark_pool_lifecycle()
    callbacks = []
    marks = []

    async def scenario() -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        ticked = threading.Event()

        def mark(audio: torch.Tensor, sr: int, *, context: str, **_kwargs: Any) -> torch.Tensor:
            marks.append((sr, context))
            loop.call_soon_threadsafe(ticked.set)
            # An event-loop caller cannot service this callback while blocked.
            callbacks.append(ticked.wait(timeout=1.0))
            return audio + 0.02

        monkeypatch.setattr(dg, "mark_synthetic", mark)
        monkeypatch.setattr(watermark, "mark_synthetic", mark)
        monkeypatch.setattr(dg, "mark_synthetic_async", watermark.mark_synthetic_async)
        return await render_dub.run_async()

    events = asyncio.run(scenario())
    assert callbacks == [True]
    assert marks == [(24000, "dub_generate.segment")]
    assert any(event["type"] == "done" for event in events)
    audio, sr = sf.read(render_dub.path / "seg_en_a.wav")
    assert sr == 24000
    assert audio.mean() == pytest.approx(0.12, abs=1 / 32768)


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
        # Probe just before the 25 ms tail fade (#4229): the loud tail must arrive uncut.
        calls.append((wav.shape[-1], target, float(wav[0,-2000])))
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


def test_subtitle_only_render_keeps_all_speech_without_any_media(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.job['subtitle_only'] = True
    render_dub.job['duration'] = 2
    render_dub.output[0] = lambda: torch.ones(1, 120000) * .1
    async def stretch(wav, target, sr):
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(
        segments=[dict(start=0, end=1, text='hello'), dict(start=1, end=2, text='goodbye')],
        segment_ids=['a', 'b'], auto_fit_overflow=True,
    )
    assert any(event['type'] == 'done' for event in events), events
    assert not any(event['type'] == 'error' for event in events)
    track = render_dub.job['dubbed_tracks']['en']['path']
    assert sf.info(track).duration == pytest.approx(2, abs=.01)
    assert sf.info(render_dub.path / 'seg_en_a.wav').duration == 5
    assert sf.info(render_dub.path / 'seg_en_b.wav').duration == 5


def test_subtitle_only_tiny_cue_still_speaks_its_words(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.job['subtitle_only'] = True
    async def stretch(wav, target, sr):
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(segments=[dict(start=0, end=.02, text='hello')], auto_fit_overflow=True)
    assert render_dub.generated
    assert any(event['type'] == 'done' for event in events), events


def test_subtitle_only_default_keeps_its_requested_timing(render_dub, monkeypatch):
    """Fixed-window compression for SRT-only projects is part of the opt-in rescue."""
    import api.routers.dub_generate as dg
    render_dub.job['subtitle_only'] = True
    render_dub.job['duration'] = 2
    render_dub.output[0] = lambda: torch.ones(1, 120000) * .1
    async def stretch(wav, target, sr):
        pytest.fail('default SRT-only rendering must not compress speech')
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(
        segments=[dict(start=0, end=1, text='hello'), dict(start=1, end=2, text='goodbye')],
        segment_ids=['a', 'b'],
    )
    assert events[-1]['error_code'] == 'dub_timing_overflow'
    assert not render_dub.job['dubbed_tracks']


def test_failed_fit_can_retry_without_tts_and_survives_restart(render_dub, monkeypatch):
    """The user's failure: short slot, valid speech, then change timing and retry."""
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.ones(1, 48000) * .1
    first = render_dub.run(timing_strategy='concise')
    assert first[-1]['error_code'] == 'dub_timing_overflow'
    assert first[-1]['speech_saved'] == 1
    assert not render_dub.job['dubbed_tracks']
    assert not (render_dub.path / 'seg_en_a.wav').exists()
    assert list((render_dub.path / '.speech-checkpoints').glob('*.wav'))
    assert not list(render_dub.path.glob('.render-*'))
    # The next HTTP request creates a fresh cache object; nothing kept in RAM.
    render_dub.output[0] = lambda: pytest.fail('Valid speech must not be synthesized again')
    async def stretch(wav, target, sr):
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(timing_strategy='strict_slot')
    assert events[-1]['type'] == 'done', events[-1]
    assert events[-1]['speech_reused'] == 1
    assert sf.info(render_dub.path / 'seg_en_a.wav').duration == 2


def test_long_phrase_stops_before_generating_the_rest(render_dub):
    render_dub.output[0] = lambda: torch.ones(1, 72000) * .1
    events = render_dub.run(segments=[dict(start=0, end=1, text='first'), dict(start=1, end=2, text='later')], segment_ids=['a', 'b'])
    assert events[-1]['error_code'] == 'dub_timing_overflow'
    assert events[-1]['segment_id'] == 'a'
    assert len(render_dub.generated) == 1


def test_synthesis_failure_reuses_completed_rows_on_retry(render_dub):
    calls=[]
    def synthesize():
        calls.append(True)
        if len(calls)==2: raise RuntimeError('engine unavailable')
        return torch.ones(1, 12000)*.1
    render_dub.output[0]=synthesize
    kwargs=dict(segments=[dict(start=0, end=1, text='first'),dict(start=1, end=2, text='second')],segment_ids=['a','b'])
    events=render_dub.run(**kwargs)
    assert events[-1]['type']=='error'
    assert events[-1]['speech_saved']==1
    events=render_dub.run(**kwargs)
    assert events[-1]['type']=='done'
    assert events[-1]['speech_reused']==1
    assert len(calls)==3


def test_explicit_regenerate_still_creates_a_new_take(render_dub):
    assert render_dub.run()[-1]['type']=='done'
    before=len(render_dub.generated)
    assert render_dub.run(regen_only=['a'])[-1]['type']=='done'
    assert len(render_dub.generated)==before+1


def test_edit_only_regenerates_the_changed_text_after_failure(render_dub):
    calls=[]
    def synthesize():
        calls.append(True)
        if len(calls)==2:raise RuntimeError('engine unavailable')
        return torch.ones(1,12000)*.1
    render_dub.output[0]=synthesize
    kwargs=dict(segments=[dict(start=0,end=1,text='first'),dict(start=1,end=2,text='second')],segment_ids=['a','b'])
    render_dub.run(**kwargs)
    kwargs['segments'][1]['text']='edited second'
    assert render_dub.run(**kwargs)[-1]['type']=='done'
    assert len(calls)==3


def test_recovery_checkpoint_is_scoped_to_track_language(render_dub):
    render_dub.run(language_code='en')
    before=len(render_dub.generated)
    render_dub.run(language_code='fr')
    assert len(render_dub.generated)==before+1


def test_automatic_overflow_fits_whole_phrase_and_announces_playable_take(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.cat((torch.ones(1, 24000)*.1, torch.ones(1, 24000)*.3), dim=-1)
    calls = []
    async def stretch(wav, target, sr):
        calls.append((wav.shape[-1], target, float(wav[0, _BEFORE_TAIL_FADE])))
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(auto_fit_overflow=True)
    assert events[-1]['type'] == 'done'
    assert events[-1]['fit_status'][0]['auto_adjusted'] is True
    assert calls[0] == pytest.approx((48000, 24000, .3), abs=1e-4)
    ready = next(e for e in events if e['type'] == 'segment_ready')
    assert ready['segment_id'] == 'a' and ready['language_code'] == 'en'
    assert events.index(ready) < next(i for i,e in enumerate(events) if e['type'] == 'assembling')
    # The audition survives staging cleanup and retains the complete natural take.
    response = dg.dub_speech_preview('job', *ready['preview_url'].split('/')[-2:])
    assert sf.info(response.path).duration == 2
    assert sf.info(render_dub.path / 'seg_en_a.wav').duration == 2


def test_automatic_fitting_is_opt_in():
    assert DubRequest(segments=[dict(start=0, end=1, text="hello")]).auto_fit_overflow is False


def test_default_concise_overflow_still_blocks_without_compression(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.ones(1, 48000) * .1
    calls = []
    async def stretch(wav, target, sr):
        calls.append(target)
        return wav[..., :target]
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run()
    assert events[-1]['error_code'] == 'dub_timing_overflow'
    assert not calls
    assert not render_dub.job['dubbed_tracks']


def test_default_smart_fit_overflow_still_blocks_without_rescue(render_dub):
    render_dub.job['duration'] = 1.0
    render_dub.output[0] = lambda: torch.ones(1, 48000) * .1
    events = render_dub.run(timing_strategy='smart_fit',
        fit_options={'allow_video_retime': False, 'audio_rate_cap': 1.2, 'max_audio_only_rate': 1.2})
    assert events[-1]['error_code'] == 'dub_timing_overflow'
    assert not any(e['type'] == 'done' for e in events)
    assert not render_dub.job['dubbed_tracks']


def test_playable_first_take_survives_a_later_engine_failure(render_dub):
    import api.routers.dub_generate as dg
    def synthesize():
        if len(render_dub.generated) == 2:
            raise RuntimeError('second line failed')
        return torch.ones(1, 24000)*.1
    render_dub.output[0] = synthesize
    events = render_dub.run(segments=[dict(start=0,end=1,text='first'),dict(start=2,end=3,text='second')], segment_ids=['a','b'])
    ready = [e for e in events if e['type'] == 'segment_ready']
    assert len(ready) == 1 and ready[0]['segment_id'] == 'a'
    assert events[-1]['type'] == 'error'
    response = dg.dub_speech_preview('job', *ready[0]['preview_url'].split('/')[-2:])
    assert sf.info(response.path).duration == 1
    assert not render_dub.job['dubbed_tracks']


def test_smart_fit_overflow_is_rescued_in_one_pitch_preserving_pass(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.job['duration'] = 1.0
    render_dub.output[0] = lambda: torch.cat((torch.ones(1,24000)*.1, torch.ones(1,24000)*.3), dim=-1)
    calls=[]
    async def stretch(wav, target, sr):
        calls.append((wav.shape[-1], target, float(wav[0, _BEFORE_TAIL_FADE])))
        return torch.nn.functional.interpolate(wav.unsqueeze(0),size=target,mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events=render_dub.run(timing_strategy='smart_fit',auto_fit_overflow=True,
        fit_options={'allow_video_retime':False, 'audio_rate_cap':1.2,'max_audio_only_rate':1.2})
    assert events[-1]['type']=='done'
    assert len(calls)==1 and calls[0] == pytest.approx((48000,24000,.3),abs=1e-4)
    assert events[-1]['fit_status'][0]['audio_rate']==2
    assert events[-1]['fit_status'][0]['auto_adjusted'] is True


@pytest.mark.parametrize('render_id,token', [('../escape','a'*64),('a'*32,'../escape'),('a'*32,'b'*63)])
def test_speech_preview_rejects_invalid_paths(render_dub, render_id, token):
    import api.routers.dub_generate as dg
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        dg.dub_speech_preview('job',render_id,token)
    assert error.value.status_code==404


def _generation_plan(**overrides):
    import api.routers.dub_generate as dg
    body=dict(segments=[dict(start=0,end=1,text='hello')],segment_ids=['a'],language_code='en',num_step=4)
    body.update(overrides)
    return asyncio.run(dg.dub_generation_plan('job',DubRequest(**body)))


def test_preflight_reports_exact_reuse_without_synthesizing_or_publishing(render_dub):
    first=_generation_plan()
    assert first['synthesize']==1 and first['reuse']==0
    assert not render_dub.generated and not render_dub.job['dubbed_tracks']
    assert not list(render_dub.path.iterdir())
    render_dub.run()
    calls=len(render_dub.generated)
    before=dict(render_dub.job)
    second=_generation_plan()
    assert second['synthesize']==0 and second['reuse']==1
    assert len(render_dub.generated)==calls and render_dub.job==before
    assert _generation_plan(guidance_scale=3)['synthesize']==1
    assert _generation_plan(regen_only=['a'])['synthesize']==1


def test_preflight_detects_a_corrupt_checkpoint_and_preserves_old_track(render_dub):
    render_dub.run()
    checkpoint=next((render_dub.path/'.speech-checkpoints').glob('*.wav'))
    checkpoint.write_bytes(b'broken')
    track=(render_dub.path/'dubbed_en.wav').read_bytes()
    plan=_generation_plan()
    assert plan['synthesize']==1 and plan['reuse']==0
    assert (render_dub.path/'dubbed_en.wav').read_bytes()==track


def test_tiny_spoken_cue_is_identified_before_any_synthesis(render_dub):
    from fastapi import HTTPException
    body=dict(segments=[dict(start=0,end=1,text='Hello'),dict(start=1,end=1.049,text='Right?')],segment_ids=['first','short'])
    plan=_generation_plan(**body)
    assert plan['issues'][0]['segment_id']=='short'
    assert plan['issues'][0]['segment']==1
    assert plan['issues'][0]['code']=='dub_cue_too_short'
    with pytest.raises(HTTPException) as error:
        render_dub.run(**body)
    assert error.value.status_code==422
    assert not render_dub.generated
    assert not render_dub.job['dubbed_tracks']


def test_combining_a_tiny_cue_only_generates_the_combined_speech(render_dub):
    old=[dict(start=0,end=1,text='Hello'),dict(start=1,end=2,text='For it to have attributes')]
    render_dub.run(segments=old,segment_ids=['first','next'])
    calls=len(render_dub.generated)
    merged=[old[0],dict(start=1,end=3,text='Right? For it to have attributes')]
    plan=_generation_plan(segments=merged,segment_ids=['first','short'])
    assert plan['issues']==[] and plan['synthesize']==1 and plan['reuse']==1
    events=render_dub.run(segments=merged,segment_ids=['first','short'])
    assert events[-1]['type']=='done'
    assert len(render_dub.generated)==calls+1
    assert events[-1]['speech_reused']==1


def test_narration_keeps_original_captions_and_fits_without_delay(render_dub):
    render_dub.output[0] = lambda: torch.ones(1, 24000 * 5) * .1
    captions = [dict(start=0, end=.03, text='Hello'), dict(start=.03, end=1, text='world.')]
    events = render_dub.run(timing_strategy='narration', segments=[dict(start=0,end=1,text='Hello world.')], segment_ids=['block_ab'], caption_segments=captions, caption_segment_ids=['a','b'])
    done = next(e for e in events if e['type'] == 'done')
    assert done['fit_status'][0]['audio_rate'] == pytest.approx(5)
    assert done['fit_status'][0]['drift_s'] == 0
    assert done['fit_status'][0]['placed_end'] <= 1
    assert render_dub.generated[0]['duration'] is None
    assert [s['text'] for s in render_dub.job['segments']] == ['Hello','world.']
    assert render_dub.job['segments'][0]['end'] == .03
    assert render_dub.job['narration_scripts']['en']['blocks'][0]['text'] == 'Hello world.'
    assert render_dub.job['dubbed_tracks']['en']['duration'] == 4
    assert render_dub.job['narration_scripts']['en']['blocks'][0]['start'] == 0
    assert render_dub.job['narration_scripts']['en']['blocks'][0]['end'] == 1


def test_narration_export_keeps_caption_cues_and_uses_speech_blocks_for_clips(render_dub):
    from api.routers import dub_export as de
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DubRequest(segments=[dict(start=0,end=1,text='Hello')],timing_strategy='narration')
    render_dub.run(timing_strategy='narration',segments=[dict(start=0,end=1,text='Hello world.',source_ids=['a','b'])],segment_ids=['block_ab'],caption_segments=[dict(start=0,end=.4,text='Hello'),dict(start=.4,end=1,text='world.')],caption_segment_ids=['a','b'])
    job=render_dub.job
    assert len(de._segments_for_lang(job,'en')) == 2
    assert len(de._speech_segments(job,'en')) == 1
    assert de._speech_order(job,'en') == ['block_ab']
    assert de._has_narration_audio(job)


def test_bounded_narration_does_not_move_the_following_block_and_reuses_full_takes(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.output[0] = lambda: torch.cat((torch.ones(1, 24000)*.1, torch.ones(1, 24000)*.3),dim=-1)
    calls=[]
    async def stretch(wav,target,sr):
        calls.append((wav.shape[-1],target,float(wav[0, _BEFORE_TAIL_FADE])))
        return torch.nn.functional.interpolate(wav.unsqueeze(0),size=target,mode='linear').squeeze(0)
    monkeypatch.setattr(dg,'_pitch_preserving_stretch',stretch)
    kwargs=dict(timing_strategy='narration', segments=[dict(start=0,end=1,text='Hello',source_ids=['a']),dict(start=2,end=3,text='World',source_ids=['b'])],segment_ids=['one','two'],caption_segments=[dict(start=0,end=1,text='Hello'),dict(start=2,end=3,text='World')],caption_segment_ids=['a','b'])
    events=render_dub.run(**kwargs)
    done=next(e for e in events if e['type']=='done')
    assert [(s['placed_start'],s['placed_end'],s['drift_s']) for s in done['fit_status']]==[(0,1,0),(2,3,0)]
    assert all(c[0]==48000 and c[1]==24000 and c[2]==pytest.approx(.3,abs=1e-4) for c in calls)
    assert [s['end'] for s in render_dub.job['narration_scripts']['en']['blocks']]==[1,3]
    audio,_=sf.read(render_dub.job['dubbed_tracks']['en']['path'])
    assert max(abs(audio[24000:48000]))==0
    generated=len(render_dub.generated)
    repeated=render_dub.run(**kwargs,regen_only=[])
    assert next(e for e in reversed(repeated) if e['type']=='done')['speech_reused']==2
    assert len(render_dub.generated)==generated
    assert sf.info(render_dub.job['dubbed_tracks']['en']['path']).duration==4


def test_narration_rejects_shifted_source_windows_before_synthesis():
    from pydantic import ValidationError
    with pytest.raises(ValidationError,match='original SRT'):
        DubRequest(timing_strategy='narration',segments=[dict(start=1,end=3,text='Hello',source_ids=['a'])],segment_ids=['one'],caption_segments=[dict(start=0,end=2,text='Hello')],caption_segment_ids=['a'])


def test_plan_verifies_interrupted_speech_without_mutating_recovery_files(render_dub):
    render_dub.output[0] = lambda: torch.ones(1, 48000) * .1
    assert render_dub.run()[-1]['error_code'] == 'dub_timing_overflow'
    before = {str(p.relative_to(render_dub.path)): p.read_bytes()
              for p in render_dub.path.rglob('*') if p.is_file()}
    calls = len(render_dub.generated)
    assert _generation_plan()['reuse'] == 1
    assert _generation_plan(guidance_scale=3)['synthesize'] == 1
    assert len(render_dub.generated) == calls
    assert {str(p.relative_to(render_dub.path)): p.read_bytes()
            for p in render_dub.path.rglob('*') if p.is_file()} == before
    recovery = render_dub.path / '.resume' / 'en' / 'seg_a.wav'
    recovery.write_bytes(b'broken')
    assert _generation_plan()['synthesize'] == 1
    assert recovery.read_bytes() == b'broken'


def test_checkpointed_take_is_marked_once_through_the_watermark_pool(render_dub, monkeypatch):
    import shutil
    import api.routers.dub_generate as dg
    from services import dub_resume
    marks = []
    async def mark(audio, sr, *, context):
        marks.append(context)
        return audio + .02
    # Fresh speech goes through main's single async mark, never a second
    # synchronous mark inside the synthesis thread.
    monkeypatch.setattr(dg, 'mark_synthetic_async', mark)
    monkeypatch.setattr(dg, 'mark_synthetic', lambda *a, **k: pytest.fail('marked off the watermark pool'))
    calls = []
    def synthesize():
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError('engine unavailable')
        return torch.ones(1, 24000) * .1
    render_dub.output[0] = synthesize
    kwargs = dict(segments=[dict(start=0, end=1, text='first'), dict(start=2, end=3, text='second')], segment_ids=['a', 'b'])
    assert render_dub.run(**kwargs)[-1]['type'] == 'error'
    assert marks == ['dub_generate.segment']
    # Only the private checkpoint remains (no interrupted-render journal).
    shutil.rmtree(render_dub.path / dub_resume.JOURNAL_DIRNAME, ignore_errors=True)
    done = render_dub.run(**kwargs)[-1]
    assert done['type'] == 'done' and done['speech_reused'] == 1
    assert marks == ['dub_generate.segment'] * 2
    published, _ = sf.read(render_dub.path / 'seg_en_a.wav', dtype='float32')
    assert published.max() == pytest.approx(.12, abs=1e-3)


def test_rerenders_keep_only_recent_speech_previews(render_dub):
    import api.routers.dub_generate as dg
    urls = []
    for _ in range(7):
        events = render_dub.run()
        assert events[-1]['type'] == 'done'
        urls.append([e for e in events if e['type'] == 'segment_ready'][-1]['preview_url'])
    renders = list((render_dub.path / '.speech-previews').iterdir())
    # The current render plus the four before it; older takes are released.
    assert len(renders) == 5
    assert dg.dub_speech_preview('job', *urls[-1].split('/')[-2:]).path
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        dg.dub_speech_preview('job', *urls[0].split('/')[-2:])


@pytest.mark.parametrize('duration,segments', [
    # A job without a recorded source duration.
    (0.0, [dict(start=0, end=1, text='hello')]),
    # Overlapping cues: the next line starts with this one.
    (4.0, [dict(start=0, end=1, text='hello'), dict(start=0, end=1, text='again')]),
])
def test_concise_rescue_never_lets_overflow_through_unfitted(render_dub, monkeypatch, duration, segments):
    import api.routers.dub_generate as dg
    render_dub.job['duration'] = duration
    render_dub.output[0] = lambda: torch.ones(1, 48000) * .1
    calls = []
    async def stretch(wav, target, sr):
        calls.append(target)
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    events = render_dub.run(segments=segments, segment_ids=[str(i) for i in range(len(segments))], auto_fit_overflow=True)
    assert events[-1]['type'] == 'done'
    # The take is fitted into at least its own cue, never mixed over the next.
    assert events[-1]['fit_status'][0]['auto_adjusted'] is True
    assert calls[0] == 24000


def test_fixed_window_tiny_cue_language_is_validated_before_synthesis(render_dub, monkeypatch):
    import api.routers.dub_generate as dg
    render_dub.job['subtitle_only'] = True
    _, backend = asyncio.run(dg._resolve_dub_execution())
    checked = []
    def check(language):
        checked.append(language)
        raise ValueError('unsupported language')
    backend._check_language = check
    async def stretch(wav, target, sr):
        return torch.nn.functional.interpolate(wav.unsqueeze(0), size=target, mode='linear').squeeze(0)
    monkeypatch.setattr(dg, '_pitch_preserving_stretch', stretch)
    # The tiny cue is spoken under fixed windows, so it is validated too.
    with pytest.raises(ValueError, match='unsupported language'):
        render_dub.run(segments=[dict(start=0, end=.02, text='hello')], language='Klingon', auto_fit_overflow=True)
    assert checked == ['Klingon']
    assert not render_dub.generated
