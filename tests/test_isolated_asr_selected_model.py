"""The crash-isolated Faster-Whisper engine loads the Model Catalogue choice.

The selection is saved in prefs.json under ``asr_model_faster``. Selecting it
also exports ``ASR_MODEL_FASTER`` for the running process, but nothing restores
that variable on the next launch, so after a restart the sidecar fell back to
``large-v3`` while the preflight and the in-process engine used the selection.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture
def no_model_env(monkeypatch):
    monkeypatch.delenv("ASR_MODEL_FASTER", raising=False)
    monkeypatch.delenv("ASR_MODEL_FW", raising=False)


@pytest.fixture
def selected(tmp_path, monkeypatch, no_model_env):
    from core import prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    prefs.set_("asr_model_faster", "Systran/faster-whisper-small")
    return "Systran/faster-whisper-small"


@pytest.fixture
def sidecar(monkeypatch):
    loaded: list[str] = []

    class FakeWhisperModel:
        def __init__(self, name, **_kw):
            loaded.append(name)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    from core import execstack

    monkeypatch.setattr(execstack, "ensure_ctranslate2_loadable", lambda: (True, ""))
    module = importlib.import_module("engines._asr_sidecar.main")
    monkeypatch.setattr(module, "_model", None)
    return module, loaded


def test_isolated_request_carries_the_saved_selection(selected, monkeypatch):
    from services import model_manager
    from services.subprocess_asr import IsolatedFasterWhisperBackend

    backend = IsolatedFasterWhisperBackend()
    frames = []
    monkeypatch.setattr(model_manager, "running_on_gpu_pool", lambda: True)
    monkeypatch.setattr(backend, "_spawn", lambda: None)
    monkeypatch.setattr(backend, "_send", frames.append)
    monkeypatch.setattr(
        backend, "_recv_with_timeout",
        lambda _t: {"op": "segments", "result": {"segments": [], "language": "en"}},
    )

    backend.transcribe("/clip.wav", word_timestamps=False)

    assert frames[0]["model"] == selected


def test_sidecar_loads_the_model_the_parent_sent(sidecar, no_model_env):
    module, loaded = sidecar
    module._get_model("Systran/faster-whisper-small")
    assert loaded == ["Systran/faster-whisper-small"]


def test_sidecar_only_override_still_wins(sidecar, no_model_env, monkeypatch):
    module, loaded = sidecar
    monkeypatch.setenv("ASR_MODEL_FW", "medium")
    module._get_model("Systran/faster-whisper-small")
    assert loaded == ["medium"]


def test_sidecar_rejects_a_malformed_model_name(sidecar, no_model_env):
    module, loaded = sidecar
    with pytest.raises(ValueError, match="Invalid ASR model name"):
        module._transcribe("/clip.wav", False, model_name=["large-v3"])
    assert loaded == []
