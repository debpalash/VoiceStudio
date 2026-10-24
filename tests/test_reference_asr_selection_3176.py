"""#3176 — reference transcription must honor the app's selected ASR engine.

The bundled OmniVoice clone-reference path hardcoded
``openai/whisper-large-v3-turbo`` and consulted no external engine, so a user
whose selected ASR was faster-whisper (CTranslate2) got
"Automatic reference transcription needs an installed speech-to-text model"
even though the app had transcribed the very same clip at profile-save time.
The engine selection is now injected into the model and consulted first; the
bundled snapshot chain remains as the fallback.
"""
import pathlib
import sys
import types

import numpy as np
import pytest
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))



def _model_stub():
    """An OmniVoice instance without loading the ~3 GB TTS core."""
    m = object.__new__(ov.OmniVoice)
    object.__setattr__(m, "_modules", {})
    object.__setattr__(m, "_parameters", {})
    object.__setattr__(m, "_buffers", {})
    object.__setattr__(m, "_asr_pipe", None)
    object.__setattr__(m, "sampling_rate", 24000)
    m.audio_tokenizer = types.SimpleNamespace(
        config=types.SimpleNamespace(hop_length=1),
        encode=lambda _w: types.SimpleNamespace(
            audio_codes=torch.zeros(1, 2, 8, dtype=torch.long)
        ),
        device="cpu",
    )
    return m


@pytest.fixture(autouse=True)
def _reset_transcriber():
    import importlib
    global ov
    ov = importlib.import_module("omnivoice.models.omnivoice")
    ov.set_reference_transcriber(None)
    yield
    ov.set_reference_transcriber(None)


def test_selected_engine_transcribes_the_reference():
    """The host transcriber's answer IS the transcript — bundled ASR untouched."""
    m = _model_stub()
    calls = []

    def _host(audio):
        calls.append(audio)
        return "selected engine words"

    ov.set_reference_transcriber(_host)
    m.transcribe = lambda _a: (_ for _ in ()).throw(
        AssertionError("bundled Whisper consulted although the host answered")
    )
    wav = torch.full((1, 2400), 0.1)
    prompt = m.create_voice_clone_prompt((wav, 24000), ref_text=None, preprocess_prompt=False)
    assert calls == [(wav, 24000)]
    assert prompt.ref_text == "selected engine words"


def test_bundled_fallback_still_runs_without_a_host_transcriber():
    """No transcriber installed → existing snapshot-chain behavior intact."""
    m = _model_stub()
    m._asr_pipe = object()
    m.transcribe = lambda _a: "bundled words"
    prompt = m.create_voice_clone_prompt((torch.full((1, 2400), 0.1), 24000), ref_text=None,
                                         preprocess_prompt=False)
    assert prompt.ref_text == "bundled words"


def test_empty_host_answer_falls_through_to_bundled_asr():
    """Whitespace is not a transcript — the fallback must still get its turn."""
    m = _model_stub()
    m._asr_pipe = object()
    m.transcribe = lambda _a: "bundled words"
    ov.set_reference_transcriber(lambda _a: "   ")
    prompt = m.create_voice_clone_prompt((torch.full((1, 2400), 0.1), 24000), ref_text=None,
                                         preprocess_prompt=False)
    assert prompt.ref_text == "bundled words"


def test_host_transcriber_failure_does_not_break_cloning():
    """A raising engine must degrade to the bundled path, not 400 the request."""
    m = _model_stub()
    m._asr_pipe = object()
    m.transcribe = lambda _a: "bundled words"
    ov.set_reference_transcriber(lambda _a: (_ for _ in ()).throw(RuntimeError("engine down")))
    prompt = m.create_voice_clone_prompt((torch.full((1, 2400), 0.1), 24000), ref_text=None,
                                         preprocess_prompt=False)
    assert prompt.ref_text == "bundled words"


def test_long_reference_passage_selection_uses_the_host_engine():
    """A >20s clip ranks 15s windows through the host engine — no bundled ASR."""
    m = _model_stub()
    m._asr_pipe = None

    def _host(audio):
        wav = audio[0]
        idx = 0 if wav.size(-1) == 15 * 24000 else 1
        return ["first window speech", "second window words"][idx]

    ov.set_reference_transcriber(_host)
    m.transcribe = lambda _a: (_ for _ in ()).throw(
        AssertionError("bundled Whisper consulted")
    )
    wav = (0.05 + 0.1 * (torch.arange(31 * 24000) % 97) / 97).unsqueeze(0)
    prompt = m.create_voice_clone_prompt((wav, 24000), ref_text=None, preprocess_prompt=False)
    assert prompt.ref_text in ("first window speech", "second window words")


def test_unload_asr_model_is_independent_and_idempotent():
    """ASR unloads alone; TTS weights are untouched; repeat calls are safe."""
    m = _model_stub()
    object.__setattr__(m, "llm", types.SimpleNamespace())  # TTS-core stand-in
    pipe = object()
    m._asr_pipe = pipe
    assert m.unload_asr_model() is True
    assert m._asr_pipe is None
    assert m.llm is not None  # TTS model untouched
    assert m.unload_asr_model() is False  # idempotent


def test_broken_transcription_error_names_an_installable_model(monkeypatch):
    """The dead-end message must point at something the UI can actually install."""
    monkeypatch.setattr(ov, "_find_cached_reference_asr", lambda: None)
    m = _model_stub()
    with pytest.raises(ov._ReferenceAsrNotInstalled) as excinfo:
        m._load_cached_reference_asr()
    assert "Model Catalogue" in str(excinfo.value)
    assert "openai/whisper-large-v3-turbo" not in str(excinfo.value)  # repo-id wall of text no

@pytest.mark.parametrize('seconds', [4, 27])
def test_real_host_bridge_transcribes_mono_reference(monkeypatch, seconds):
    import soundfile as sf
    from services import asr_backend as ab
    paths = []
    def transcribe(path):
        samples, sr = sf.read(path)
        assert samples.ndim == 1
        assert sr == 24000
        assert 0 < len(samples) <= 15 * sr
        paths.append(path)
        return 'Palabras de la referencia.'
    monkeypatch.setattr(ab, 'transcribe_reference', transcribe)
    ab.install_reference_transcriber()
    m = _model_stub()
    def missing():
        raise ov._ReferenceAsrNotInstalled('Automatic reference transcription needs an installed speech-to-text model')
    m._load_cached_reference_asr = missing
    prompt = m.create_voice_clone_prompt(
        (torch.full((1, seconds * 24000), 0.1), 24000), None, preprocess_prompt=False)
    assert prompt.ref_text == 'Palabras de la referencia.'
    assert len(paths) == (2 if seconds == 27 else 1)
    assert all(not pathlib.Path(path).exists() for path in paths)

@pytest.mark.parametrize('host_fails', [False, True])
def test_long_host_miss_loads_bundled_fallback(monkeypatch, host_fails):
    m = _model_stub()
    def host(audio):
        if host_fails:
            raise RuntimeError('unavailable')
        return None
    ov.set_reference_transcriber(host)
    loads = []
    def load():
        loads.append(True)
        m._asr_pipe = object()
    m._load_cached_reference_asr = load
    def transcribe(audio):
        assert m._asr_pipe is not None, 'bundled ASR was never loaded'
        return 'Fallback words.'
    m.transcribe = transcribe
    prompt = m.create_voice_clone_prompt(
        (torch.full((1, 27 * 24000), 0.1), 24000), None, preprocess_prompt=False)
    assert prompt.ref_text == 'Fallback words.'
    assert loads == [True]


def test_long_host_miss_without_cached_asr_explains_trim(monkeypatch):
    monkeypatch.setattr(ov, "_find_cached_reference_asr", lambda: None)
    ov.set_reference_transcriber(lambda audio: None)
    m = _model_stub()
    with pytest.raises(ValueError, match=r"\[clone_ref_too_long\].*27.0"):
        m.create_voice_clone_prompt(
            (torch.full((1, 27 * 24000), 0.1), 24000), None, preprocess_prompt=False)


def test_asr_unload_refuses_active_transcription_without_blocking():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, finish = threading.Event(), threading.Event()
    m = _model_stub()
    def pipeline(audio):
        entered.set()
        assert finish.wait(5)
        return {'text': 'Still transcribing'}
    m._asr_pipe = pipeline
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(m.transcribe, (torch.ones(1, 2400), 24000))
        try:
            assert entered.wait(5)
            assert m.unload_asr_model() is False
            assert m._asr_pipe is pipeline
        finally:
            finish.set()
        assert pending.result(timeout=5) == 'Still transcribing'
    assert m.unload_asr_model() is True


@pytest.mark.parametrize('speech_first', [False, True])
def test_silent_window_does_not_hide_speech_without_bundled_whisper(monkeypatch, speech_first):
    monkeypatch.setattr(ov, '_find_cached_reference_asr', lambda: None)
    def host(audio):
        first = audio[0].shape[-1] == 15 * 24000
        return 'Palabras de la referencia.' if first == speech_first else ''
    ov.set_reference_transcriber(host)
    m = _model_stub()
    prompt = m.create_voice_clone_prompt(
        (torch.full((1, 27 * 24000), 0.1), 24000), None, preprocess_prompt=False)
    assert prompt.ref_text == 'Palabras de la referencia.'
