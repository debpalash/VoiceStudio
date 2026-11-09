"""FunASR ASR backend — opt-in alternative to WhisperX (#182).

Tests the pure output-normaliser (no funasr install needed) + registration.
"""
import sys
import types

import pytest

from services.asr_backend import _normalize_funasr, FunASRBackend, list_backends


def test_normalize_sentence_info_with_timestamps_and_speaker():
    res = [{
        "language": "en",
        "sentence_info": [
            {"text": "<|en|><|NEUTRAL|>Hello there", "start": 0, "end": 1200, "spk": 0},
            {"text": "Goodbye", "start": 1500, "end": 2300, "spk": 1},
        ],
    }]
    out = _normalize_funasr(res)
    assert out["language"] == "en"
    assert out["chunks"][0] == {"text": "Hello there", "timestamp": (0.0, 1.2)}   # ms → s, tokens stripped
    assert out["chunks"][1]["timestamp"] == (1.5, 2.3)
    assert out["segments"][0]["speaker"] == "Speaker 1"   # spk 0 → 1-based label
    assert out["segments"][1]["speaker"] == "Speaker 2"


def test_sentence_info_using_sentence_field_is_read():
    res = [{
        "sentence_info": [
            {"sentence": "<|zh|><|HAPPY|><|Speech|><|woitn|>Good morning everyone",
             "start": 1340, "end": 3875, "spk": 2},
            {"sentence": "<|zh|><|Speech|>See you later", "start": 4000, "end": 5120, "spk": 0},
        ],
    }]
    out = _normalize_funasr(res)
    assert [c["text"] for c in out["chunks"]] == ["Good morning everyone", "See you later"]
    assert out["chunks"][0]["timestamp"] == (1.34, 3.875)
    assert [x["speaker"] for x in out["segments"]] == ["Speaker 3", "Speaker 1"]


def test_normalize_single_utterance_fallback():
    res = [{"text": "<|en|><|HAPPY|>Hello world", "timestamp": [[0, 500], [500, 1000]]}]
    out = _normalize_funasr(res)
    assert out["chunks"] == [{"text": "Hello world", "timestamp": (0.0, 1.0)}]


def test_normalize_empty_and_tokens_only():
    assert _normalize_funasr([]) == {"chunks": [], "segments": [], "language": None}
    assert _normalize_funasr([{"text": "<|en|>"}])["chunks"] == []  # only rich tokens → nothing spoken


def test_is_available_reports_install_hint_when_absent():
    ok, msg = FunASRBackend.is_available()
    if not ok:  # funasr is not a hard dependency; absent in CI
        assert "funasr" in msg.lower()


class _KwRecorder:
    """Stands in for funasr.AutoModel and records constructor kwargs."""
    last = None

    def __init__(self, **kw):
        type(self).last = dict(kw)


def _load_with(monkeypatch, spk):
    _KwRecorder.last = None
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=_KwRecorder))
    backend = FunASRBackend()
    backend._spk_model = spk
    backend._ensure_model()
    return _KwRecorder.last


def test_vad_segment_mode_requested_with_speaker_model(monkeypatch):
    opts = _load_with(monkeypatch, "cam++")
    assert (opts["spk_model"], opts["spk_mode"]) == ("cam++", "vad_segment")


def test_plain_load_without_speaker_model_has_no_spk_options(monkeypatch):
    opts = _load_with(monkeypatch, "")
    assert not {"spk_mode", "spk_model"} & set(opts)


class _GenRecorder:
    def __init__(self):
        self.calls = []

    def generate(self, **kw):
        self.calls.append(kw)
        return []


def test_timestamps_requested_iff_speaker_model_active():
    for spk, want in (("cam++", True), ("", False)):
        rec = _GenRecorder()
        backend = FunASRBackend()
        backend._spk_model = spk
        backend._model = rec
        backend.transcribe("clip.wav")
        (kw,) = rec.calls
        assert kw["input"] == "clip.wav"
        assert ("output_timestamp" in kw) is want
        if want:
            assert kw["output_timestamp"] is True


def test_registered_in_picker():
    ids = [b["id"] for b in list_backends()]
    assert "funasr" in ids
