"""Chapter synthesis must honor inline pronunciation before dictionaries/cache."""

import importlib
import json
import wave

import pytest


@pytest.fixture
def runtime(monkeypatch):
    torch = pytest.importorskip('torch')
    watermark = importlib.import_module('services.watermark')
    monkeypatch.setattr(watermark, 'will_mark', lambda: False)
    return (importlib.import_module('services.audiobook'), torch)


@pytest.mark.parametrize('text,lexicon,expected', [
    ('SQL [[label|SQL]]', {'SQL': 'sequel'}, 'sequel SQL'),
    ('[[SQL]]', {'SQL': 'sequel'}, 'SQL'),
    ('[[label|SQL]]', None, 'SQL'),
])
def test_chapter_synth_receives_the_explicit_inline_respelling(runtime, text, lexicon, expected):
    audiobook, torch = runtime
    seen = []

    def synth(text, voice, speed=None):
        seen.append(text)
        return torch.full((800,), 0.1)

    chapter = audiobook.parse_audiobook_script(text).chapters[0]
    audiobook.synthesize_chapter(chapter.spans, synth, 8000, lexicon=lexicon)
    assert seen == [expected]


@pytest.mark.parametrize('layer', ['chapter', 'segment'])
def test_old_inline_audio_caches_are_invalidated_then_new_audio_is_reused(runtime, tmp_path, layer):
    audiobook, torch = runtime
    router = importlib.import_module('api.routers.audiobook')
    cache = importlib.import_module('services.longform_render')
    text = 'SQL [[label|SQL]]'
    lexicon = {'SQL': 'sequel'}
    span = audiobook.Span(voice_id=None, text=text, pause_ms_after=0)
    chapter = audiobook.Chapter(title='Chapter', spans=[span])
    voice_sig = 'None|None|None|None'
    lex_sig = json.dumps(lexicon, sort_keys=True)
    if layer == 'chapter':
        key = cache.chapter_cache_key([(None, text, 0, None)], sample_rate=8000,
                                      engine_id='eng', voice_sig={'': voice_sig, '\x00lexicon': lex_sig})
        old_path = tmp_path / f'{key}.wav'
    else:
        key = cache.segment_cache_key(text, sample_rate=8000, engine_id='eng',
                                      voice_id=None, voice_sig=voice_sig, extra_sig=lex_sig)
        old_path = tmp_path / cache.SEGMENT_SUBDIR / f'{key}.wav'
        old_path.parent.mkdir()
    # A complete real WAV under the released key, representing old narration.
    with wave.open(str(old_path), 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0' * 800)
    seen = []

    def synth(text, voice, speed=None):
        seen.append(text)
        return torch.full((800,), 0.1)

    def resolve(_voice):
        return {'ref_audio': None, 'ref_text': None, 'instruct': None, 'seed': None}

    result = router._render_chapter_cached(chapter, synth, 8000, 'eng', resolve,
                                            str(tmp_path), lexicon=lexicon)
    assert seen == ['sequel SQL']
    assert result[2] is False
    seen.clear()
    repeated = router._render_chapter_cached(chapter, synth, 8000, 'eng', resolve,
                                              str(tmp_path), lexicon=lexicon)
    assert seen == []
    assert repeated[2] is True
