"""Stream and audiobook voices reach transcript-requiring engines with one (#3062).

The stream and audiobook routes pass a profile's stored transcript straight
to the engine. A voice locked from a cut take now has none (the cut prefix is
ignored), and MLX-Audio CSM cannot clone without one, so the engine
transcribes the clip itself, with the same recognizer /generate uses.
"""
import importlib
import os
from contextlib import contextmanager

import pytest

CUT = ("word " * 60)[:200]


def _row():
    return {
        "id": "p1", "kind": "clone", "is_locked": 1, "locked_audio_path": "p1_locked.wav",
        "ref_audio_path": "p1.wav", "ref_text": CUT, "instruct": "", "seed": None,
        "language": "Auto", "personality": "",
    }


@pytest.fixture
def voices(tmp_path, monkeypatch):
    config = importlib.import_module("core.config")
    db = importlib.import_module("core.db")
    monkeypatch.setattr(config, "VOICES_DIR", str(tmp_path))
    (tmp_path / "p1_locked.wav").write_bytes(b"RIFF")

    @contextmanager
    def _fake_db():
        class _Conn:
            def execute(self, *_a, **_k):
                return self

            def fetchone(self):
                return _row()

        yield _Conn()

    monkeypatch.setattr(db, "db_conn", _fake_db)
    return tmp_path


class _Result:
    def __init__(self):
        import numpy as np

        self.audio = np.zeros(16, dtype="float32")


class _CSM:
    """Records kwargs like mlx-audio's CSM, which needs ref_audio AND ref_text."""

    class config:
        tts_model_type = "csm"

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield _Result()


@pytest.fixture
def csm(monkeypatch):
    tts = importlib.import_module("services.tts_backend")
    asr = importlib.import_module("services.asr_backend")
    heard = []
    monkeypatch.setattr(asr, "transcribe_reference", lambda path, **_k: heard.append(path) or "what the clip says")
    be = tts.MLXAudioBackend.__new__(tts.MLXAudioBackend)
    be._model_id = "mlx-community/csm-1b-8bit"
    be._sr = 24000
    be._model = _CSM()
    monkeypatch.setattr(be, "_ensure_loaded", lambda: None)
    return be, heard


def test_a_streamed_legacy_voice_is_transcribed_for_csm(voices, csm):
    stream = importlib.import_module("api.routers.tts_stream")
    be, heard = csm
    kw = stream.build_stream_kwargs({"voice": "p1", "text": "hi"})
    assert "ref_text" not in kw  # the cut take prefix never reaches the engine
    be.generate("hi", **kw)
    assert heard == [os.path.join(str(voices), "p1_locked.wav")]
    assert be._model.calls[-1]["ref_text"] == "what the clip says"


def test_an_audiobook_legacy_voice_is_transcribed_for_csm(voices, csm):
    audiobook = importlib.import_module("api.routers.audiobook")
    be, heard = csm
    v = audiobook._resolve_voice("p1")
    assert v["ref_text"] is None
    be.generate("hi", ref_audio=v["ref_audio"], ref_text=v["ref_text"])
    assert be._model.calls[-1]["ref_text"] == "what the clip says"


def test_a_stored_transcript_is_used_as_is(csm):
    be, heard = csm
    be.generate("hi", ref_audio="/clip.wav", ref_text="typed words")
    assert heard == [] and be._model.calls[-1]["ref_text"] == "typed words"


def test_a_failed_recognition_leaves_the_engine_its_own_error(monkeypatch):
    tts = importlib.import_module("services.tts_backend")
    asr = importlib.import_module("services.asr_backend")

    def _boom(*_a, **_k):
        raise RuntimeError("no recognizer")

    monkeypatch.setattr(asr, "transcribe_reference", _boom)
    assert tts.required_reference_transcript("/clip.wav", None) is None
    assert tts.required_reference_transcript(None, None) is None
