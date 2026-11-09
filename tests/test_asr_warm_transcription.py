"""The transcription ASR stays loaded between requests.

``get_active_asr_backend()`` builds a fresh whisper-family instance per call,
so accurate / reference / OpenAI-route / voice-convert transcription reloaded
the model on every request. With ``warm=True`` one loaded instance per
(engine, model) is reused until it is released (idle worker, before TTS,
Unload). Dub and batch keep private instances.
"""
import asyncio
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import services.asr_backend as ab


class _Fake(ab.ASRBackend):
    id = "fake-asr"
    display_name = "Fake ASR"
    loads = 0

    def __init__(self, model_name="small"):
        self._model_name = model_name

    @classmethod
    def is_available(cls):
        return True, "ready"

    def ensure_loaded(self):
        type(self).loads += 1

    def transcribe(self, audio_path, *, word_timestamps=True):
        return {"text": "ok", "segments": [], "language": "en"}


@pytest.fixture
def selection(monkeypatch):
    state = {"model": "small"}
    _Fake.loads = 0
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **_kw: _Fake(state["model"]))
    monkeypatch.setattr(ab, "ensure_backend_licence", lambda _b: None)
    ab.release_transcription_backend(0.0)
    yield state
    ab.release_transcription_backend(0.0)


def test_warm_requests_reuse_one_loaded_instance(selection):
    first = ab.load_active_asr_backend(warm=True)
    second = ab.load_active_asr_backend(warm=True)
    assert first is second
    assert _Fake.loads == 1


def test_private_loads_stay_cold(selection):
    warm = ab.load_active_asr_backend(warm=True)
    private = ab.load_active_asr_backend()
    assert private is not warm
    assert _Fake.loads == 2


def test_model_change_loads_the_new_selection(selection):
    first = ab.load_active_asr_backend(warm=True)
    selection["model"] = "large-v3"
    second = ab.load_active_asr_backend(warm=True)
    assert second is not first and second._model_name == "large-v3"


def test_idle_release_and_reload(selection):
    ab.load_active_asr_backend(warm=True)
    assert ab.release_transcription_backend(3600.0) is False  # used just now
    assert ab.release_transcription_backend(0.0) is True
    assert ab.warm_transcription_backend() is None
    ab.load_active_asr_backend(warm=True)
    assert _Fake.loads == 2


def test_listed_unloadable_and_included_in_unload_all(selection):
    from services import model_lifecycle

    ab.load_active_asr_backend(warm=True)
    row = next(m for m in model_lifecycle.list_loaded()["models"] if m["id"] == "transcription-asr")
    assert row["unloadable"] is True and row["checkpoint"] == "small"
    result = asyncio.run(model_lifecycle.unload_all())
    assert result["results"]["transcription-asr"]["success"] is True
    assert ab.warm_transcription_backend() is None


def test_make_room_before_tts_releases_it(selection):
    from services import model_manager

    ab.load_active_asr_backend(warm=True)
    model_manager._release_idle_tts_memory("generate")
    assert ab.warm_transcription_backend() is None
