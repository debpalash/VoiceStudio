"""Unknown native ASR language must not become an English translation source."""
import asyncio
from types import SimpleNamespace

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]


@pytest.mark.parametrize('data', [{}, {'result': {}}, {'result': {'language': None}}])
def test_missing_language_stays_unknown(data):
    from engines.whisper_cpp.output import parse_transcript
    assert parse_transcript(data, word_timestamps=True)['language'] is None


@pytest.mark.parametrize('result', ['nl', ['nl'], True, 1, None, '', []])
def test_non_mapping_result_keeps_transcript_and_unknown_language(result):
    from engines.whisper_cpp.output import parse_transcript

    data = {'result': result, 'transcription': [
        {'text': ' Goedemorgen', 'offsets': {'from': 0, 'to': 2000}},
    ]}
    transcript = parse_transcript(data, word_timestamps=False)
    assert transcript['language'] is None
    assert transcript['text'] == 'Goedemorgen'
    assert transcript['chunks'] == [{'text': ' Goedemorgen', 'timestamp': (0.0, 2.0)}]


def test_mapping_result_language_is_preserved():
    from collections import UserDict

    from engines.whisper_cpp.output import parse_transcript

    assert parse_transcript({'result': UserDict(language='nl')}, False)['language'] == 'nl'


@pytest.mark.parametrize('language', [True, 1, 1.5, ['nl'], {'code': 'nl'}, False, 0, [], {}])
def test_non_string_language_retains_transcript_as_unknown(language):
    from engines.whisper_cpp.output import parse_transcript
    from services.dub_language import detected_source_language

    transcript = parse_transcript({
        'result': {'language': language},
        'transcription': [{'text': 'Goedemorgen', 'offsets': {'from': 0, 'to': 2000}}],
    }, word_timestamps=False)
    assert transcript['language'] is None
    assert transcript['text'] == 'Goedemorgen'
    assert transcript['chunks'] == [{'text': 'Goedemorgen', 'timestamp': (0.0, 2.0)}]
    assert detected_source_language(transcript['language']) is None


def test_reported_language_is_preserved():
    from engines.whisper_cpp.output import parse_transcript
    assert parse_transcript({'result': {'language': 'nl'}}, word_timestamps=False)['language'] == 'nl'


@pytest.mark.parametrize('language', [None, 'unknown', 'und', ' '])
def test_batch_stops_before_translation_when_language_unknown(tmp_path, monkeypatch, m, language):
    from api.routers import batch
    from services import ffmpeg_utils
    monkeypatch.setattr(batch, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ffmpeg_utils, 'find_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(ffmpeg_utils, 'validate_media_source', lambda _: None)
    monkeypatch.setattr(ffmpeg_utils, 'require_audio_stream', lambda _: None)
    monkeypatch.setattr('subprocess.run', lambda *a, **k: SimpleNamespace(stderr=b'Duration: 00:00:02.00', stdout=b'', returncode=0))
    result = {'chunks': [{'text': 'Goedemorgen', 'timestamp': (0, 2)}], 'language': language}
    backend = SimpleNamespace(transcribe=lambda *a, **kw: result, unload=lambda: None)
    monkeypatch.setattr(m.ab, 'load_active_asr_backend', lambda: backend)
    async def transcribe(_pool, fn, **_kwargs):
        return fn()
    monkeypatch.setattr(m.ab, 'run_transcribe_guarded', transcribe)
    def no_tts(_):
        raise AssertionError('Unknown source reached TTS resolution')
    monkeypatch.setattr(batch, '_batch_voice', no_tts)
    job = {'video_path': str(tmp_path / 'input.mp4'), 'langs': ['en'], 'status': 'running'}
    with pytest.raises(RuntimeError, match='detected source language'):
        asyncio.run(batch._run_batch_pipeline('unknown-language', job))
    assert job['source_lang'] is None
    assert job['segments'][0]['text'] == 'Goedemorgen'

@pytest.mark.parametrize('override', [None, 'fr'])
def test_whole_file_dub_preserves_unknown_or_explicit_source(tmp_path, monkeypatch, m, override):
    import numpy as np
    from api.routers import dub_core as dc
    job = {'audio_path': str(tmp_path / 'input.wav'), 'duration': 2, 'source_lang_override': override}
    monkeypatch.setattr(dc, '_get_job', lambda _: job)
    monkeypatch.setattr(dc, '_save_job', lambda *args: None)
    monkeypatch.setattr(dc, 'should_preload_tts_asr', lambda: False)
    monkeypatch.setattr(dc, '_ensure_diarisation_accepted', lambda: None)
    monkeypatch.setattr(dc, 'get_diarization_pipeline', lambda: None)
    monkeypatch.setattr(dc, 'release_device_cache', lambda: None)
    monkeypatch.setattr(dc, 'snap_segment_starts', lambda *a, **k: None)
    monkeypatch.setattr(dc.sf, 'read', lambda *a, **k: (np.zeros(32000), 16000))
    monkeypatch.setattr(m.ab, 'asr_model_missing_error', lambda *a, **k: None)
    transcript = {'chunks': [{'text': 'Goedemorgen', 'timestamp': (0, 2)}], 'language': None}
    backend = SimpleNamespace(id='whisper-cpp', transcribe=lambda *a, **k: transcript, unload=lambda: None)
    monkeypatch.setattr(m.ab, 'load_active_asr_backend', lambda **k: backend)
    async def transcribe(_pool, fn, **_kwargs):
        return fn()
    monkeypatch.setattr(dc, 'run_transcribe_guarded', transcribe)
    result = asyncio.run(dc.dub_transcribe('unknown-language'))
    assert result['source_lang'] == override
    assert job['source_lang_detection_missing'] is (override is None)


@pytest.mark.parametrize('override', [None, 'fr'])
def test_sse_replay_preserves_unknown_or_explicit_source(monkeypatch, override):
    import json

    from api.routers import dub_core as dc
    job = {'transcription_complete': True, 'segments': [], 'source_lang': None, 'source_lang_override': override}
    monkeypatch.setattr(dc, '_get_job', lambda _: job)
    async def replay():
        response = await dc.dub_transcribe_stream('unknown-language')
        return ''.join([part.decode() if isinstance(part, bytes) else str(part) async for part in response.body_iterator])
    body = asyncio.run(replay())
    final = next(line[5:].strip() for line in body.splitlines() if line.startswith('data:'))
    assert json.loads(final)['source_lang'] == override


def test_unknown_asr_translation_requires_source_but_text_only_fallback_remains(monkeypatch):
    import json

    from api.routers import dub_translate as dt
    from services import translation_engines
    job = {'source_lang': None, 'source_lang_detection_missing': True}
    monkeypatch.setattr(dt, '_get_job', lambda _: job)
    monkeypatch.setattr(translation_engines, 'get_engine', lambda _: object())
    request = SimpleNamespace(provider='argos', target_lang='fr', source_lang=None, job_id='j1', segments=[])
    response = asyncio.run(dt.dub_translate(request))
    assert response.status_code == 400
    assert json.loads(response.body)['code'] == 'source_language_required'
    request.source_lang = 'nl'
    assert dt._resolve_source_lang(request) == 'nl'
    request.source_lang = None
    job['source_lang_override'] = 'de'
    assert dt._resolve_source_lang(request) == 'de'
    request.job_id = None
    assert dt._resolve_source_lang(request) == 'en'


@pytest.mark.parametrize('value', [None, '', ' ', 'auto', 'und', 'unknown', 'none', 'unknown-language'])
def test_unknown_dub_detection_never_becomes_english(value):
    from api.routers.dub_core import _detected_source_lang
    assert _detected_source_lang(value) is None


@pytest.mark.parametrize('value, expected', [('en', 'en'), ('nl_NL', 'nl'), ('yue', 'yue')])
def test_known_source_language_codes_are_retained(value, expected):
    from api.routers.dub_core import _detected_source_lang
    assert _detected_source_lang(value) == expected


@pytest.mark.parametrize('kind, expected', [('srt', 'en'), ('captions', 'nl')])
def test_imported_subtitles_clear_stale_asr_unknown_marker(monkeypatch, kind, expected):
    import io

    from api.routers import dub_core as dc
    from api.routers import dub_translate as dt
    from fastapi import UploadFile
    job = {
        'duration': 2, 'source_lang': None, 'source_lang_detection_missing': True,
        'transcription_complete': True, 'segments': [],
        'youtube_subs': {'nl': [{'start': 0, 'end': 2, 'text': 'Goedemorgen'}]},
    }
    monkeypatch.setattr(dc, '_get_job', lambda _: job)
    monkeypatch.setattr(dt, '_get_job', lambda _: job)
    monkeypatch.setattr(dc, '_save_job', lambda *args: None)
    if kind == 'srt':
        upload = UploadFile(filename='source.srt', file=io.BytesIO(b'1\n00:00:00,000 --> 00:00:02,000\nGoedemorgen\n'))
        asyncio.run(dc.dub_import_srt('job', upload))
    else:
        dc.dub_use_downloaded_captions('job')
    assert 'source_lang_detection_missing' not in job
    request = SimpleNamespace(source_lang=None, job_id='job', segments=[])
    assert dt._resolve_source_lang(request) == expected
