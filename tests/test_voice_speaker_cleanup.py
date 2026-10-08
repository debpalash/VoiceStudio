"""Cleanup must retain attributed non-ultra-short speaker turns."""
import pytest

@pytest.mark.parametrize("answer_first", [False, True])
def test_cleanup_keeps_short_answer_attributed_to_its_speaker(answer_first):
    from services.segmentation import clean_up_segments
    whole = {"id": "whole", "start": 0, "end": 3, "text": "First speaker delivers a whole sentence.", "speaker_id": "Speaker 1"}
    answer = {"id": "answer", "start": 3, "end": 4, "text": "Second speaker answers right away", "speaker_id": "Speaker 2", "translations": {"fr": "Une autre voix"}}
    if answer_first:
        answer.update(start=0, end=1)
        whole.update(start=1, end=4)
    source = [answer, whole] if answer_first else [whole, answer]
    assert clean_up_segments(source) == source

def test_same_speaker_short_answer_still_merges():
    from services.segmentation import clean_up_segments
    source = [{"start": 0, "end": 3, "text": "First speaker delivers a whole sentence.", "speaker_id": "Speaker 1"}, {"start": 3, "end": 4, "text": "Second clause follows right away", "speaker_id": "Speaker 1"}]
    out = clean_up_segments(source)
    assert len(out) == 1
    assert out[0]["text"] == " ".join(s["text"] for s in source)

def test_documented_ultra_short_stray_token_still_folds():
    from services.segmentation import clean_up_segments
    source = [{"start": 0, "end": 3, "text": "First speaker delivers a whole sentence.", "speaker_id": "Speaker 1"}, {"start": 3, "end": 3.2, "text": "STR", "speaker_id": "Speaker 2"}]
    out = clean_up_segments(source)
    assert len(out) == 1
    assert out[0]["text"].endswith(" STR")
