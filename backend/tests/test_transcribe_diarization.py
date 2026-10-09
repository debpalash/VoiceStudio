"""`POST /transcribe` identifies speakers on request (accurate mode).

Before this, file transcription never ran diarization — only the dub pipeline
did — so the transcription UI could not label speakers at all.
"""
from __future__ import annotations

import sys
import types

import pytest

from services import transcript_diarization as td

from .test_asr_request_path_degrades import _client, _tiny_wav, asr  # noqa: F401


class _Sortformer:
    """Stands in for NativeSortformer: rejects an exact speaker count."""

    def __call__(self, path, *, num_speakers=None, **_kw):
        assert num_speakers is None
        return "sortformer-result"


@pytest.fixture
def fakes(monkeypatch):
    """Inject the heavy collaborators `diarize_segments` imports lazily."""
    state = {"pipeline": None, "error": None, "calls": []}

    def get_pipeline(return_error=False):
        return state["pipeline"], state["error"]

    def assign(segments, diarization):
        # Speaker by midpoint: before 1.0s -> Speaker 1, otherwise Speaker 2.
        for s in segments:
            s["speaker_id"] = "Speaker 1" if (s["start"] + s["end"]) / 2 < 1.0 else "Speaker 2"
        return segments

    for name, attrs in {
        "services.model_manager": {"get_diarization_pipeline": get_pipeline},
        "services.segmentation": {"assign_speakers_from_diarization": assign},
        "services.diarization_native": {"NativeSortformer": _Sortformer},
    }.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    return state


SEGMENTS = [
    {"start": 0.0, "end": 1.0, "text": "hi"},
    {"start": 1.5, "end": 2.5, "text": "hello"},
    {"start": None, "end": None, "text": "untimed"},
]


def test_speaker_hint_is_bounded():
    assert td.parse_speaker_hint("2") == 2
    assert td.parse_speaker_hint(" 3 ") == 3
    assert [td.parse_speaker_hint(v) for v in (None, "", "0", "-1", "99", "two")] == [None] * 6


def test_labels_follow_segments_and_skip_untimed(fakes):
    seen = {}

    def pipeline(path, num_speakers=None):
        seen["hint"] = num_speakers
        return "diar"

    fakes["pipeline"] = pipeline
    labels, error = td.diarize_segments("a.wav", SEGMENTS, num_speakers=2)
    assert (labels, error) == (["Speaker 1", "Speaker 2", None], None)
    assert seen["hint"] == 2


def test_missing_model_degrades_with_its_error_code(fakes):
    fakes["error"] = "MODEL_MISSING"
    labels, error = td.diarize_segments("a.wav", SEGMENTS)
    assert labels == [None, None, None]
    assert error == "MODEL_MISSING"


def test_pipeline_crash_never_raises(fakes):
    def boom(path, **_kw):
        raise RuntimeError("cuda oom")

    fakes["pipeline"] = boom
    assert td.diarize_segments("a.wav", SEGMENTS) == ([None, None, None], "DIARIZATION_FAILED")


def test_sortformer_never_receives_an_exact_count(fakes):
    fakes["pipeline"] = _Sortformer()
    labels, error = td.diarize_segments("a.wav", SEGMENTS, num_speakers=2)
    assert error is None and labels[0] == "Speaker 1"


def test_no_timed_segments_skips_the_model(fakes):
    fakes["pipeline"] = lambda *a, **k: pytest.fail("must not load a model with nothing to label")
    assert td.diarize_segments("a.wav", [SEGMENTS[2]]) == ([None], None)


@pytest.fixture
def transcript(asr, monkeypatch):  # noqa: F811
    """A healthy engine returning two timed segments."""
    monkeypatch.setattr(asr.WhisperXBackend, "ensure_loaded", lambda self: None)
    monkeypatch.setattr(
        asr.WhisperXBackend, "transcribe",
        lambda self, path, **kw: {
            "text": "hi hello",
            "segments": [
                {"text": "hi", "start": 0.0, "end": 1.0},
                {"text": "hello", "start": 1.5, "end": 2.5},
            ],
            "language": "en",
        },
    )


def _post(client, **data):
    return client.post(
        "/transcribe",
        files={"audio": ("a.wav", _tiny_wav(), "audio/wav")},
        data={"mode": "accurate", **data},
    )


def test_accurate_diarize_labels_segments(transcript, monkeypatch):
    monkeypatch.setattr(td, "diarize_segments", lambda *a, **k: (["Speaker 1", "Speaker 2"], None))
    body = _post(_client("api.routers.capture"), diarize="true").json()
    assert [s["speaker"] for s in body["segments"]] == ["Speaker 1", "Speaker 2"]
    assert body["speakers"] == ["Speaker 1", "Speaker 2"]
    assert "diarization_error" not in body


def test_diarize_is_off_unless_requested(transcript, monkeypatch):
    monkeypatch.setattr(td, "diarize_segments", lambda *a, **k: pytest.fail("not requested"))
    body = _post(_client("api.routers.capture")).json()
    assert all("speaker" not in s for s in body["segments"])
    assert "speakers" not in body


def test_diarization_failure_still_returns_the_transcript(transcript, monkeypatch):
    monkeypatch.setattr(td, "diarize_segments", lambda *a, **k: ([None, None], "MODEL_MISSING"))
    r = _post(_client("api.routers.capture"), diarize="true")
    assert r.status_code == 200
    assert r.json()["text"].startswith("hi")
    assert r.json()["diarization_error"] == "MODEL_MISSING"
    assert "speakers" not in r.json()
