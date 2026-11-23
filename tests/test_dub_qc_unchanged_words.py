"""A minor mid-cue difference should not compare all unchanged words."""

import pytest


@pytest.mark.parametrize("change", ["insert", "delete", "replace"])
def test_edit_distance_skips_matching_word_prefix_and_suffix(change):
    from services.dub_qc import _edit_distance, word_error_rate

    class CountedWord(str):
        comparisons = 0

        def __eq__(self, other):
            CountedWord.comparisons += 1
            return super().__eq__(other)

    prefix = [CountedWord(f"before{i}") for i in range(300)]
    suffix = [CountedWord(f"after{i}") for i in range(300)]
    reference = [*prefix, "expected", *suffix]
    middle = {
        "insert": ["addition", "expected"],
        "delete": [],
        "replace": ["recognized"],
    }[change]
    hypothesis = [*prefix, *middle, *suffix]
    assert _edit_distance(reference, hypothesis) == 1
    assert CountedWord.comparisons <= len(reference) + 2
    assert word_error_rate(" ".join(reference), " ".join(hypothesis)) == pytest.approx(1 / len(reference))
