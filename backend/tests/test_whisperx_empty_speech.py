from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from services import asr_backend


class _Pipeline:
    def __init__(self, error):
        self.error = error

    def transcribe(self, _audio, **_options):
        raise self.error


def _backend(monkeypatch, error):
    backend = asr_backend.WhisperXBackend()
    backend._asr = _Pipeline(error)
    monkeypatch.setattr(backend, "_ensure_asr", lambda: None)
    monkeypatch.setattr(backend, "_get_align", lambda _language: None)
    monkeypatch.setattr(
        asr_backend, "_decode_audio_16k_mono", lambda _path: np.zeros(16, dtype=np.float32)
    )
    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace())
    return backend


@pytest.mark.parametrize("language, expected", [("fr", "fr"), (None, "en")])
def test_whisperx_no_speech_returns_empty_result(monkeypatch, language, expected):
    result = _backend(monkeypatch, IndexError("no VAD segments")).transcribe(
        "silence.wav", language=language
    )
    assert result["segments"] == []
    assert result["chunks"] == []
    assert result["language"] == expected


def test_whisperx_non_vad_error_propagates(monkeypatch):
    with pytest.raises(ValueError, match="broken pipeline"):
        _backend(monkeypatch, ValueError("broken pipeline")).transcribe("audio.wav")
