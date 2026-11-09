"""MLX model failures must not turn into successful unconditioned synthesis."""
import importlib
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def backend(monkeypatch):
    cls = importlib.import_module("services.tts_backend").MLXAudioBackend
    be = cls.__new__(cls)
    be._model_id = cls.CURATED_MODELS["csm"]
    be._sr = 24000
    be._model = SimpleNamespace()
    monkeypatch.setattr(be, "_ensure_loaded", lambda: None)
    return be


def _result(audio):
    return SimpleNamespace(audio=audio, sample_rate=24000)


@pytest.mark.parametrize("failure_stage", ["call", "first_chunk", "after_chunk"])
def test_internal_type_error_does_not_restart_unconditioned_synthesis(backend, failure_stage):
    calls = 0
    error = TypeError("invalid speaker embedding")

    def results():
        if failure_stage == "after_chunk":
            yield _result(np.ones(16, dtype=np.float32))
        raise error

    def generate(**kwargs):
        nonlocal calls
        calls += 1
        # The old fallback reported this default-voice output as success.
        if "ref_audio" not in kwargs:
            return iter([_result(np.zeros(16, dtype=np.float32))])
        if failure_stage == "call":
            raise error
        return results()

    backend._model.generate = generate
    with pytest.raises(TypeError) as excinfo:
        backend.generate("hello", ref_audio="reference.wav", ref_text="reference words")
    assert excinfo.value is error
    assert calls == 1


def test_limited_model_synthesizes_with_its_supported_conditioning(backend):
    conditioned = np.linspace(-0.5, 0.5, 16, dtype=np.float32)

    def generate(text, speed=1, voice="default", *, ref_audio=None, ref_text=None, instruct=None):
        matches = (
            voice == "chosen-speaker" and ref_audio == "reference.wav"
            and ref_text == "reference words" and instruct == "a warm narrator"
        )
        yield _result(conditioned if matches else np.zeros(16, dtype=np.float32))

    backend._model.generate = generate
    wav = backend.generate(
        "hello", voice="chosen-speaker", ref_audio="reference.wav",
        ref_text="reference words", instruct="a warm narrator", language="en",
    )
    np.testing.assert_array_equal(wav.numpy(), conditioned[None, :])


def test_variadic_model_can_use_language_conditioning(backend):
    english = np.linspace(-0.25, 0.25, 16, dtype=np.float32)

    def generate(text, speed=1, **conditioning):
        audio = english if conditioning.get("lang_code") == "en" else -english
        yield _result(audio)

    backend._model.generate = generate
    np.testing.assert_array_equal(backend.generate("hello", language="en").numpy(), english[None, :])


@pytest.mark.parametrize("signature_error", [TypeError, ValueError])
def test_unavailable_signature_preserves_model_failure(backend, signature_error):
    calls = 0
    error = TypeError("invalid speaker embedding")

    class Generate:
        @property
        def __signature__(self):
            raise signature_error("signature unavailable")

        def __call__(self, **kwargs):
            nonlocal calls
            calls += 1
            if "ref_audio" in kwargs:
                raise error
            return iter([_result(np.zeros(16, dtype=np.float32))])

    backend._model.generate = Generate()
    with pytest.raises(TypeError) as excinfo:
        backend.generate("hello", ref_audio="reference.wav", ref_text="reference words")
    assert excinfo.value is error
    assert calls == 1
