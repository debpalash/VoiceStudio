"""Desktop language names reach the Supertonic SDK as ISO language codes."""
import importlib.util
import io
from pathlib import Path

import pytest

LANGUAGES = [
    ('English', 'en'), ('Korean', 'ko'), ('Japanese', 'ja'), ('Arabic', 'ar'),
    ('Bulgarian', 'bg'), ('Czech', 'cs'), ('Danish', 'da'), ('German', 'de'),
    ('Greek', 'el'), ('Spanish', 'es'), ('Estonian', 'et'), ('Finnish', 'fi'),
    ('French', 'fr'), ('Hindi', 'hi'), ('Croatian', 'hr'), ('Hungarian', 'hu'),
    ('Indonesian', 'id'), ('Italian', 'it'), ('Lithuanian', 'lt'), ('Latvian', 'lv'),
    ('Dutch', 'nl'), ('Polish', 'pl'), ('Portuguese', 'pt'), ('Romanian', 'ro'),
    ('Russian', 'ru'), ('Slovak', 'sk'), ('Slovenian', 'sl'), ('Swedish', 'sv'),
    ('Turkish', 'tr'), ('Ukrainian', 'uk'), ('Vietnamese', 'vi'),
]


@pytest.fixture
def sidecar():
    path = Path(__file__).resolve().parents[1] / 'backend/engines/supertonic3/sidecar.py'
    spec = importlib.util.spec_from_file_location('supertonic_language_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name,code', LANGUAGES)
def test_synthesis_dispatches_full_language_names_to_sdk(sidecar, monkeypatch, name, code):
    calls = []

    class SDK:
        sample_rate = 44100

        def get_voice_style(self, voice_name):
            assert voice_name == 'M1'
            return 'test-style'

        def synthesize(self, **kwargs):
            calls.append(kwargs)
            assert kwargs['lang'] == code
            return object(), 0.1

    monkeypatch.setattr(sidecar, '_load_tts', lambda stdout: SDK())
    monkeypatch.setattr(sidecar, '_wav_float_to_pcm_b64', lambda wav, sr: ('AAA=', sr, 1))
    frames = []
    monkeypatch.setattr(sidecar, '_send', lambda stdout, frame: frames.append(frame))
    sidecar._handle_synthesize({'text': 'A language test.', 'lang': name}, io.BytesIO())
    assert len(calls) == 1
    assert frames[0]['op'] == 'audio'


@pytest.mark.parametrize('name,code', LANGUAGES)
def test_language_codes_remain_accepted(sidecar, name, code):
    assert sidecar._normalize_lang(code.upper()) == code


@pytest.mark.parametrize('raw,expected', [
    (None, 'na'), ('', 'na'), ('  AUTO ', 'na'), (42, 'na'), ('na', 'na'),
    ('pt-BR', 'pt'), ('PT_pt', 'pt'), ('en-US', 'en'),
    ('  Portuguese  ', 'pt'), ('eStOnIaN', 'et'),
    ('Englishish', 'englishish'), ('Polishish', 'polishish'),
    ('English-unknown', 'english-unknown'), ('en-unknown', 'en-unknown'),
    ('zz-US', 'zz-us'), ('es-419', 'es'),
])
def test_language_normalization_controls(sidecar, raw, expected):
    assert sidecar._normalize_lang(raw) == expected
