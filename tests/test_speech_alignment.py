"""Character timings for synthesized speech (services.speech_alignment)."""
from __future__ import annotations

import importlib

import numpy as np
import pytest


def _al():
    return importlib.import_module("services.speech_alignment")


def _check_shape(text, result, duration):
    starts = result["character_start_times_seconds"]
    ends = result["character_end_times_seconds"]
    assert result["characters"] == list(text)
    assert len(starts) == len(ends) == len(text)
    assert all(s <= e + 1e-9 for s, e in zip(starts, ends))
    assert all(a <= b + 1e-9 for a, b in zip(starts, starts[1:]))
    assert ends[-1] <= duration + 1e-9


def test_recognized_words_set_their_characters_times():
    text = "Hello, big world!"
    words = [{"word": "hello", "start": 0.5, "end": 0.9},
             {"word": "big", "start": 1.0, "end": 1.2},
             {"word": "world", "start": 1.3, "end": 1.9}]
    result = _al().character_alignment(text, 2.0, words=words, speech=(0.5, 1.9))
    _check_shape(text, result, 2.0)
    starts = result["character_start_times_seconds"]
    assert starts[text.index("H")] == pytest.approx(0.5)
    assert starts[text.index("b")] == pytest.approx(1.0)
    assert starts[text.index("w")] == pytest.approx(1.3)
    assert result["character_end_times_seconds"][-1] == pytest.approx(1.9)


def test_a_word_heard_differently_is_interpolated_between_neighbours():
    text = "I have 3 cats"
    words = [{"word": "I", "start": 0.0, "end": 0.1},
             {"word": "have", "start": 0.2, "end": 0.5},
             {"word": "three", "start": 0.6, "end": 0.9},
             {"word": "cats", "start": 1.0, "end": 1.4}]
    result = _al().character_alignment(text, 1.5, words=words)
    _check_shape(text, result, 1.5)
    three = text.index("3")
    assert 0.5 <= result["character_start_times_seconds"][three] < 1.0
    assert result["character_start_times_seconds"][text.index("c")] == pytest.approx(1.0)


def test_without_words_time_is_spread_over_the_speech_span():
    text = "one two"
    result = _al().character_alignment(text, 2.0, speech=(0.4, 1.8))
    _check_shape(text, result, 2.0)
    assert result["character_start_times_seconds"][0] == pytest.approx(0.4)
    assert result["character_end_times_seconds"][-1] == pytest.approx(1.8)


def test_speech_bounds_ignore_quiet_edges():
    sr = 1000
    take = np.zeros(2000, dtype=np.float32)
    take[500:1500] = 0.5
    assert _al().speech_bounds(take, sr) == pytest.approx((0.5, 1.5))
    assert _al().speech_bounds(np.zeros(10, dtype=np.float32), sr) == pytest.approx((0.0, 0.01))
