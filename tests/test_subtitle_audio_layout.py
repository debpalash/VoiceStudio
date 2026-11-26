import pytest


def subtitle_audio_layout(*args):
    # Resolved at run time, never a collection-time module object.
    from services.subtitle_audio import subtitle_audio_layout as layout
    return layout(*args)


def test_keeps_leading_and_between_cue_silence():
    layout, total = subtitle_audio_layout([(2, 4), (7, 9)], [1, 1], 10)
    assert layout == [(2, 3, 1), (7, 8, 1)]
    assert total == 10


def test_long_speech_fits_each_window_without_overlapping_or_shifting():
    layout, total = subtitle_audio_layout([(0, 1), (1, 2)], [5, 5], 2)
    assert layout[0] == (0, 1, 5)
    assert layout[1] == (1, 2, 5)
    assert total == 2


def test_empty_or_unreadable_duration_is_not_a_valid_plan():
    with pytest.raises(ValueError):
        subtitle_audio_layout([(0, 1)], [float('nan')], 1)


def test_bounded_blocks_never_shift_the_next_phrase_or_borrow_its_gap():
    from services.subtitle_audio import bounded_audio_layout
    layout, total = bounded_audio_layout([(2, 4), (7, 9)], [5, 1], 10)
    assert layout == [(2, 4, 2.5), (7, 8, 1)]
    assert total == 10


def test_bounded_blocks_do_not_accumulate_delay_and_reject_overlaps():
    from services.subtitle_audio import bounded_audio_layout
    layout, total = bounded_audio_layout([(0, 1), (1, 2), (2, 3)], [2, 5, 4], 3)
    assert [(a, b) for a, b, _ in layout] == [(0, 1), (1, 2), (2, 3)]
    assert total == 3
    for cues in [[(0, 0)], [(2, 1)], [(0, 2), (1, 3)], [(-1, 1)]]:
        with pytest.raises(ValueError):
            bounded_audio_layout(cues, [1] * len(cues), 3)
