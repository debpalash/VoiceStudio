"""Regression coverage for conservative phrase-level speaker recovery."""

from __future__ import annotations

import importlib

import numpy as np
import pytest


PHRASES = [
    {"start": 0.0, "end": 1.0, "text": "first speaker one"},
    {"start": 1.0, "end": 2.0, "text": "second speaker one"},
    {"start": 2.0, "end": 3.0, "text": "first speaker two"},
    {"start": 3.0, "end": 4.0, "text": "second speaker two"},
]
SEGMENTS = [
    {"id": str(index), "start": phrase["start"], "end": phrase["end"],
     "text": phrase["text"], "speaker_id": "Speaker 1"}
    for index, phrase in enumerate(PHRASES)
]


class _Audio:
    def __init__(self):
        self.crops = []

    def crop(self, target, segment, *, duration, mode):
        self.crops.append((target, segment.start, segment.end, duration, mode))
        return np.zeros((1, 16), dtype=np.float32), 16_000


class _Pipeline:
    def __init__(self, vectors):
        self._audio = _Audio()
        iterator = iter(vectors)
        self._embedding = lambda _waveform: next(iterator)


def _recover(pipeline, *, diarized=None, requested=None):
    recover = importlib.import_module(
        "api.routers.dub_core"
    )._recover_from_phrase_embeddings
    return recover(
        pipeline,
        [dict(item) for item in (diarized or SEGMENTS)],
        phrases=PHRASES,
        requested_speakers=requested,
        audio_target="recording.wav",
        segments=[dict(item) for item in SEGMENTS],
        words=[],
    )


def test_distinct_balanced_phrase_embeddings_recover_two_speakers():
    pipeline = _Pipeline([
        [1.0, 0.0], [0.0, 1.0], [0.99, 0.01], [0.01, 0.99],
    ])

    recovered, separation = _recover(pipeline)

    assert separation > 0.9
    assert [item[1:4] for item in pipeline._audio.crops] == [
        (0.0, 1.0, 1.0), (1.0, 2.0, 1.0),
        (2.0, 3.0, 1.0), (3.0, 4.0, 1.0),
    ]
    assert {item["speaker_id"] for item in recovered} == {"Speaker 1", "Speaker 2"}


def test_existing_multi_speaker_result_is_never_reclustered():
    pipeline = _Pipeline([])
    diarized = [dict(item) for item in SEGMENTS]
    diarized[1]["speaker_id"] = "Speaker 2"

    assert _recover(pipeline, diarized=diarized) is None
    assert pipeline._audio.crops == []


def test_explicit_non_two_speaker_request_is_never_reclustered():
    pipeline = _Pipeline([])

    assert _recover(pipeline, requested=3) is None
    assert pipeline._audio.crops == []


def test_rejected_assignment_does_not_mutate_original_segments():
    pipeline = _Pipeline([
        [1.0, 0.0], [0.0, 1.0], [0.99, 0.01], [0.01, 0.99],
    ])
    original = [{
        "id": "merged", "start": 0.0, "end": 4.0,
        "text": "one merged segment", "speaker_id": "Speaker 1",
    }]

    recover = importlib.import_module(
        "api.routers.dub_core"
    )._recover_from_phrase_embeddings
    result = recover(
        pipeline,
        [dict(item) for item in original],
        phrases=PHRASES,
        requested_speakers=None,
        audio_target="recording.wav",
        segments=original,
        words=[],
    )

    assert result is None
    assert original[0]["speaker_id"] == "Speaker 1"


def test_fewer_than_four_usable_phrases_stops_before_audio_access():
    pipeline = _Pipeline([])
    recover = importlib.import_module("api.routers.dub_core")._recover_from_phrase_embeddings
    result = recover(
        pipeline, SEGMENTS, phrases=PHRASES[:3], requested_speakers=None,
        audio_target="recording.wav", segments=SEGMENTS, words=[],
    )
    assert result is None
    assert pipeline._audio.crops == []


def test_short_phrases_are_not_usable():
    pipeline = _Pipeline([])
    phrases = [{"start": i, "end": i + 0.74, "text": "speech"} for i in range(4)]
    recover = importlib.import_module("api.routers.dub_core")._recover_from_phrase_embeddings
    assert recover(
        pipeline, SEGMENTS, phrases=phrases, requested_speakers=None,
        audio_target="recording.wav", segments=SEGMENTS, words=[],
    ) is None
    assert pipeline._audio.crops == []


@pytest.mark.parametrize("missing", ["_embedding", "_audio"])
def test_missing_private_pyannote_capability_is_rejected(missing):
    pipeline = _Pipeline([])
    setattr(pipeline, missing, None)
    assert _recover(pipeline) is None


def test_non_finite_embedding_is_rejected():
    pipeline = _Pipeline([[1.0, 0.0], [np.nan, 1.0], [0.0, 1.0], [1.0, 0.0]])
    assert _recover(pipeline) is None


def test_cluster_with_only_one_phrase_is_rejected(monkeypatch):
    class Unbalanced:
        def __init__(self, **_kwargs):
            pass

        def fit_predict(self, _matrix):
            return np.array([0, 0, 0, 1])

    monkeypatch.setattr("sklearn.cluster.AgglomerativeClustering", Unbalanced)
    pipeline = _Pipeline([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert _recover(pipeline) is None


def test_explicit_two_speaker_hint_uses_lower_separation_threshold():
    other = [0.85, np.sqrt(1.0 - 0.85**2)]
    vectors = [[1.0, 0.0], other, [1.0, 0.0], other]
    assert _recover(_Pipeline(vectors), requested=None) is None
    recovered = _recover(_Pipeline(vectors), requested=2)
    assert recovered is not None
    assert 0.12 <= recovered[1] < 0.18


def test_embedding_exception_is_a_conservative_rejection():
    pipeline = _Pipeline([])

    def fail(_waveform):
        raise RuntimeError("embedding unavailable")

    pipeline._embedding = fail
    assert _recover(pipeline) is None
