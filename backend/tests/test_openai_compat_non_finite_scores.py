"""Segment scores an engine reports as NaN or infinity must not 500 the response.

MLX Whisper's temperature fallback sometimes leaves ``avg_logprob`` or
``compression_ratio`` at NaN/inf. ``_num`` passed them through, the strict JSON
encoder raised ``ValueError: Out of range float values are not JSON compliant``,
and ``POST /v1/audio/transcriptions`` answered 500 for the whole file.
"""
from __future__ import annotations

import json
import math

from api.routers.openai_compat import _openai_segments


def test_non_finite_segment_scores_serialize_as_defaults():
    segments = _openai_segments(
        [{
            "start": 0.0,
            "end": 2.5,
            "text": "hello",
            "avg_logprob": math.nan,
            "compression_ratio": math.inf,
            "no_speech_prob": -math.inf,
            "temperature": math.nan,
        }],
        temperature=0.2,
    )

    json.dumps(segments, allow_nan=False)
    assert segments[0]["avg_logprob"] == 0.0
    assert segments[0]["compression_ratio"] == 0.0
    assert segments[0]["no_speech_prob"] == 0.0
    assert segments[0]["temperature"] == 0.2
    assert segments[0]["end"] == 2.5
