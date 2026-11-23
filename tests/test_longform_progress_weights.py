"""Long-book progress must remain cheap after many chapters finish."""

import pytest

from services.longform_progress import RenderProgress


def test_progress_does_not_rescan_completed_chapters(monkeypatch):
    progress = RenderProgress([10] * 500)
    for index in range(400):
        progress.finish_chapter(index)
    # Finishing twice and delayed chunk notifications must leave the total alone.
    progress.finish_chapter(0)
    progress.chunk(0, 1, 1)
    progress.chunk(400, 1, 2, cached=True)

    original = progress._weights_at
    reads = []

    def counted(index):
        reads.append(index)
        return original(index)

    monkeypatch.setattr(progress, "_weights_at", counted)
    assert progress.fraction() == pytest.approx(400.5 / 500)
    assert progress.remaining_units() == pytest.approx(995)
    assert reads == [400, 400]
