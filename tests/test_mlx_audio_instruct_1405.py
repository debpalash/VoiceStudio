"""#1405 — the VoiceDesign model never received the description it requires.

`MLXAudioBackend.generate` builds its kwargs by hand and forwards `voice`,
`ref_audio`, `ref_text` and `lang_code`. It did **not** forward `instruct`,
even though the comment directly above the block said it did:

    # Different engines accept different kwargs (voice for Kokoro, ref_audio
    # for CSM, instruct for Qwen3) — we pass them all …

The curated `qwen3-tts` model IS the VoiceDesign variant
(`Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit`), and mlx-audio raises outright when
that variant is generated without an `instruct`. So the engine could not
produce audio no matter what the user typed, and the report was a bare
`400 Bad Request` quoting a library message the app never explains.

Two things are pinned here: the description actually reaches the library, and
its absence produces an error a user can act on rather than the raw internal
one.
"""
import importlib

import pytest


@pytest.fixture
def tts_backend():
    """Resolve the app module per test rather than binding it at collection.

    A module-level `from services import tts_backend` keeps whatever object
    was in `sys.modules` when this file was imported. Other suites in this
    repo rebind that name, so the binding can go stale and leave these tests
    exercising a different implementation than the one under test — passing
    in isolation and quietly proving nothing in the full run.
    """
    return importlib.import_module("services.tts_backend")


class _FakeConfig:
    def __init__(self, kind):
        self.tts_model_type = kind


class _FakeResult:
    def __init__(self):
        import numpy as np

        self.audio = np.zeros(16, dtype="float32")


class _FakeModel:
    """Stands in for a loaded mlx-audio model, recording its call kwargs."""

    def __init__(self, kind="voice_design"):
        self.config = _FakeConfig(kind)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield _FakeResult()


@pytest.fixture
def backend(monkeypatch, tts_backend):
    """An MLXAudioBackend with the model pre-loaded, so no mlx import happens."""
    be = tts_backend.MLXAudioBackend.__new__(tts_backend.MLXAudioBackend)
    be._model_id = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit"
    be._sr = 24000
    be._model = _FakeModel()
    monkeypatch.setattr(be, "_ensure_loaded", lambda: None)
    return be


def test_the_description_reaches_the_model(backend):
    """Fail-before: `instruct` was silently dropped, so the library saw None."""
    backend.generate("hello", instruct="a warm, low-pitched British narrator")
    assert backend._model.calls, "the model was never called"
    assert backend._model.calls[0].get("instruct") == "a warm, low-pitched British narrator"


def test_a_voice_design_model_without_a_description_says_what_to_do(backend):
    with pytest.raises(ValueError) as excinfo:
        backend.generate("hello")
    msg = str(excinfo.value)
    # The user's next step must be in the message — the library's own wording
    # ("VoiceDesign model requires 'instruct' …") names an internal parameter
    # and no action, which is what reached the reporter as a raw 400.
    assert "description" in msg.lower()
    assert "instruct" not in msg.lower(), "don't surface the internal parameter name"
    assert "designed voice" in msg.lower() or "cloning" in msg.lower()


def test_a_normal_model_without_a_description_is_untouched(backend):
    """The guard must not block every other mlx-audio model, none of which
    take an instruct at all."""
    backend._model = _FakeModel(kind="base")
    backend.generate("hello")
    assert backend._model.calls
    assert "instruct" not in backend._model.calls[0]


def test_voice_design_is_detected_from_the_id_when_config_is_silent(backend):
    """Configs that don't expose `tts_model_type` still have to be caught —
    the id carries `VoiceDesign` by naming convention."""
    backend._model = _FakeModel(kind=None)
    assert backend._is_voice_design() is True

    backend._model_id = "mlx-community/Kokoro-82M-4bit"
    assert backend._is_voice_design() is False


def test_detection_prefers_the_models_own_config_over_the_name(backend):
    """The config field is the one mlx-audio itself branches on, so it wins —
    otherwise a renamed or re-tagged checkpoint would drift from the library."""
    backend._model = _FakeModel(kind="base")
    backend._model_id = "some/Thing-VoiceDesign-4bit"  # name says design
    assert backend._is_voice_design() is False
