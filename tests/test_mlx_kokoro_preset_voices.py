"""#3802: mlx-audio Kokoro must expose its preset voices and speak the
requested language's voice.

Before this fix `MLXAudioBackend` declared no `PRESET_VOICES`, so
`GET /engines` listed `[]` for `mlx-audio` and `/generate` dropped any
`preset_voice` ("engine has no presets: ignore"). Kokoro then fell back
to `af_heart`, so Portuguese text was spoken with an American-English
voice even though `pf_dora` / `pm_alex` / `pm_santa` exist.
"""
import types

import numpy
import pytest

from services import tts_backend


def test_mlx_audio_lists_kokoro_preset_voices():
    voices = tts_backend.preset_voices_of(tts_backend.MLXAudioBackend)
    # The 54 voice packs shipped by hexgrad/Kokoro-82M (voices/*.pt).
    assert len(voices) == 54
    assert len(set(voices)) == len(voices)
    assert "pf_dora" in voices
    assert "pm_alex" in voices
    assert "pm_santa" in voices
    # Every id is "<lang><gender>_<name>" with Kokoro's own lang letter.
    assert all(v[0] in "abefhijpz" and v[1] in "fm" for v in voices)
    assert tts_backend.MLXAudioBackend.DEFAULT_VOICE == "af_heart"


def test_mlx_audio_catalogue_entry_lists_presets_and_default():
    entries = {e["id"]: e for e in tts_backend.list_backends()}
    entry = entries["mlx-audio"]
    assert entry["preset_voices"] == tts_backend.MLXAudioBackend.PRESET_VOICES
    assert entry["default_voice"] == "af_heart"


def _kokoro_backend(monkeypatch, lang_code):
    """A Kokoro backend whose model is a fake that captures its kwargs."""
    monkeypatch.setattr(
        tts_backend, "resolve_kokoro_lang_code", lambda language: lang_code
    )
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["kokoro"]
    backend._ensure_loaded = lambda: None
    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=numpy.zeros(4))])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    return backend, captured


def test_kokoro_defaults_to_the_requested_languages_voice(monkeypatch):
    backend, captured = _kokoro_backend(monkeypatch, "p")
    backend.generate("Com o Bora Orçar, você envia pelo WhatsApp.", language="Portuguese")
    assert captured["lang_code"] == "p"
    assert captured["voice"] == "pf_dora"


@pytest.mark.parametrize("lang_code,expected", [
    ("a", "af_heart"), ("b", "bf_alice"), ("e", "ef_dora"), ("f", "ff_siwis"),
    ("h", "hf_alpha"), ("i", "if_sara"), ("j", "jf_alpha"), ("z", "zf_xiaobei"),
])
def test_kokoro_default_voice_per_language(monkeypatch, lang_code, expected):
    backend, captured = _kokoro_backend(monkeypatch, lang_code)
    backend.generate("hello", language="English")
    assert captured["voice"] == expected


def test_kokoro_explicit_preset_voice_wins_over_the_default(monkeypatch):
    backend, captured = _kokoro_backend(monkeypatch, "p")
    backend.generate("hello", language="Portuguese", voice="pm_alex")
    assert captured["voice"] == "pm_alex"


def test_kokoro_auto_language_derives_lang_code_from_preset(monkeypatch):
    backend, captured = _kokoro_backend(monkeypatch, "a")
    backend.generate("Olá", language="Auto", voice="pf_dora")
    assert captured["lang_code"] == "p"
    assert captured["voice"] == "pf_dora"
