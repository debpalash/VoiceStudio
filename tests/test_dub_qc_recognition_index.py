"""Verify Dub matches long tracks without reparsing all ASR cues per row."""


def test_qc_indexes_recognized_cues_once_and_keeps_their_order():
    from services.dub_qc import score_dub

    class CountedCue(dict):
        reads = 0

        def get(self, key, default=None):
            if key in {"start", "end"}:
                CountedCue.reads += 1
            return super().get(key, default)

    recognized = [
        CountedCue(start=i, end=i + 0.5, text=str(i)) for i in reversed(range(300))
    ]
    generated = [dict(start=i, end=i + 1, text=str(i)) for i in range(300)]
    generated.append(dict(start=0, end=2, text="1 0"))
    result = score_dub(generated, recognized)
    assert all(row.drift == 0 for row in result)
    assert result[-1].recognized_text == "1 0"
    assert (result[-1].new_start, result[-1].new_end) == (0, 1.5)
    assert CountedCue.reads == 2 * len(recognized)
