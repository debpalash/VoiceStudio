"""Literal marker text must survive the streaming TTS chunker."""
import pytest


@pytest.mark.parametrize("step", [1, 7, 1000])
def test_literal_internal_markers_survive_streaming(step):
    from services.sentence_chunker import SentenceChunker
    text = "The literal <stop> marker should survive this sentence. The literal <prd> marker should too."
    chunker = SentenceChunker(min_sentence_len=10)
    out = []
    for start in range(0, len(text), step):
        out.extend(chunker.push(text[start:start + step]))
    out.extend(chunker.flush())
    assert out == [
        "The literal <stop> marker should survive this sentence.",
        "The literal <prd> marker should too.",
    ]


def test_colliding_marker_variants_and_abbreviations_survive_together():
    from services.sentence_chunker import SentenceChunker
    text = "Dr. Smith preserves <prd>, <prd_>, <prd_0>, <stop>, <stop_>, and <stop_0> in this example. Another sentence follows."
    chunker = SentenceChunker(min_sentence_len=10)
    assert chunker.push(text) + chunker.flush() == [
        "Dr. Smith preserves <prd>, <prd_>, <prd_0>, <stop>, <stop_>, and <stop_0> in this example.",
        "Another sentence follows.",
    ]


def test_many_literal_variants_do_not_rescan_the_full_input():
    from services.sentence_chunker import _split_sentences

    class CountedText(str):
        scans = 0

        def __contains__(self, value):
            self.scans += 1
            return super().__contains__(value)

    markers = [f"<{kind}{'_' * index}>" for kind in ("prd", "stop") for index in range(256)]
    markers += [f"<{kind}_{index}>" for kind in ("prd", "stop") for index in range(256)]
    text = CountedText("Keep " + " ".join(markers) + ". Dr. Smith follows.")
    sentences = _split_sentences(text, min_sentence_len=10)
    assert " ".join(sentence for sentence, _, _ in sentences) == text
    # Count whole-input membership probes rather than relying on machine timing.
    assert text.scans <= 4
