"""Speaker re-splitting should locate words without scanning the whole transcript."""


def test_diarization_computes_each_word_midpoint_once():
    from services.segmentation import Word, _resplit_core

    class CountedTime(float):
        additions = 0

        def __add__(self, other):
            CountedTime.additions += 1
            return super().__add__(other)

    words = [Word(CountedTime(i), i + 0.5, "word") for i in range(300)]
    segments = [{"start": i, "end": i + 1, "text": "word"} for i in range(300)]
    assert _resplit_core(segments, words, [(0, 300, "speaker")]) == segments
    assert CountedTime.additions == len(words)
