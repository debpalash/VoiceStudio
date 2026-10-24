"""Malformed word times must use the text-preserving karaoke fallback."""
import copy

import pytest



WORDS = [
    {"text": "hello", "start": .25, "end": .5},
    {"text": "world", "start": 1.25, "end": 1.5},
]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["start", "end"])
def test_nonfinite_word_times_use_even_split_without_changing_text(value, field):
    from services.karaoke_ass import build_ass
    words = copy.deepcopy(WORDS)
    words[0][field] = value
    cue = {"text": "hello world", "start": 0, "end": 2, "words": words}
    expected = build_ass([{key: val for key, val in cue.items() if key != "words"}])
    assert build_ass([cue]) == expected
    assert words[1] == WORDS[1]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["start", "end"])
def test_nonfinite_word_times_are_not_scaled_into_fitted_subtitles(value, field):
    from services.karaoke_ass import scale_words
    words = copy.deepcopy(WORDS)
    words[0][field] = value
    assert scale_words(words, 0, 2, 2, 6) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("index", range(4))
def test_nonfinite_source_or_fitted_spans_drop_word_times(value, index):
    from services.karaoke_ass import scale_words
    times = [0, 2, 2, 6]
    times[index] = value
    assert scale_words(WORDS, *times) is None


def test_valid_persisted_times_and_metadata_remain_unchanged():
    from services.karaoke_ass import build_ass, scale_words
    words = copy.deepcopy(WORDS)
    words[0]["confidence"] = .9
    scaled = scale_words(words, 0, 2, 2, 6)
    assert scaled == [
        {"text": "hello", "start": 2.5, "end": 3, "confidence": .9},
        {"text": "world", "start": 4.5, "end": 5},
    ]
    assert words[0]["start"] == .25
    script = build_ass([{"text": "hello world", "start": 0, "end": 2, "words": words}])
    assert r"{\k25}{\kf100}hello {\kf75}world" in script


def test_unrepresentable_word_values_use_the_same_fallback():
    from services.karaoke_ass import build_ass, scale_words
    words = copy.deepcopy(WORDS)
    words[0]["start"] = 10**400
    cue = {"text": "hello world", "start": 0, "end": 2, "words": words}
    assert build_ass([cue]) == build_ass([{"text": "hello world", "start": 0, "end": 2}])
    assert scale_words(words, 0, 2, 2, 6) is None


def test_overflow_in_derived_fitted_times_drops_word_times():
    from services.karaoke_ass import scale_words
    assert scale_words(WORDS, -1e308, 1e308, 0, 2) is None
    words = [{"text": "hello", "start": 1e308, "end": 1e308}]
    assert scale_words(words, 0, 1, 0, 2) is None
