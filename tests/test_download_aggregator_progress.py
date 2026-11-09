"""Overall download progress must never lose bytes a finished file already earned.

``DownloadAggregator`` credits a file's full size when its per-file tqdm bar
closes and keeps counting it for the rest of the download. Two things used to
break that: bars were keyed by ``id()``, which CPython recycles for the next
same-shaped object, and a completed entry could be overwritten by the next
bar's first update. Either way ``bytes_done`` dropped backwards and a multi-file
download under-reported until ``complete()`` forced the final total.
"""
import itertools

import pytest


def _aggregator(total_bytes=300):
    from utils.download_aggregator import DownloadAggregator

    return DownloadAggregator("org/model", total_bytes=total_bytes)


def _keys(n):
    """Distinct per-bar keys, as ``hf_progress`` now allocates them."""
    counter = itertools.count(1)
    for _ in range(n):
        yield next(counter)


def test_completed_bytes_survive_a_recycled_key():
    agg = _aggregator()
    first = next(_keys(1))

    agg.update_byte_bar(first, 100, 100)
    agg.credit_complete(first, 100)
    assert agg.snapshot()["bytes_done"] == 100

    # Same key again: the next file's bar landed on the freed address.
    agg.update_byte_bar(first, 0, 50)
    assert agg.snapshot()["bytes_done"] == 100, "a finished file's bytes were wiped"


def test_progress_never_goes_backwards_when_a_live_bar_shrinks():
    """Recycling the key is not the only way to lose bytes: re-reporting a
    smaller value for a bar that is still live must not lower the sum."""
    agg = _aggregator()
    agg.update_byte_bar("bar", 120, 200)
    assert agg.snapshot()["bytes_done"] == 120

    agg.update_byte_bar("bar", 40, 200)
    assert agg.snapshot()["bytes_done"] == 120, "bytes_done regressed"


def test_many_sequential_files_credit_their_full_size():
    total_files, size = 100, 100
    agg = _aggregator(total_files * size)

    for key in _keys(total_files):
        agg.update_byte_bar(key, 0, size)
        agg.credit_complete(key, size)

    snap = agg.snapshot()
    assert snap["bytes_done"] == total_files * size
    assert snap["bytes_done"] <= snap["total_bytes"]


def test_repeated_completion_of_one_bar_is_not_double_counted():
    agg = _aggregator()
    agg.update_byte_bar("bar", 0, 100)
    agg.credit_complete("bar", 100)
    agg.credit_complete("bar", 100)
    assert agg.snapshot()["bytes_done"] == 100


def test_segmented_add_bytes_still_accumulates():
    agg = _aggregator()
    agg.add("rel/file.bin", 30)
    agg.add("rel/file.bin", 70)
    assert agg.snapshot()["bytes_done"] == 100


def test_aggregate_never_exceeds_the_preflight_total():
    """More live bars than the preflight expected (e.g. a retried file) must not
    push the overall bar past 100%."""
    agg = _aggregator(total_bytes=100)
    agg.update_byte_bar("a", 80, 80)
    agg.update_byte_bar("b", 80, 80)
    assert agg.snapshot()["bytes_done"] == 100


def test_a_closed_bar_ignores_later_updates_for_its_key():
    """tqdm's close() calls display(), which reaches the sink with the same
    key after the bytes were credited. Counting that again would double the
    file and could show 100% before the download finished."""
    agg = _aggregator(total_bytes=None)
    agg.update_byte_bar("bar", 0, 100)
    agg.credit_complete("bar", 100)
    assert agg.snapshot()["bytes_done"] == 100

    agg.update_byte_bar("bar", 100, 100)  # the post-close display()
    assert agg.snapshot()["bytes_done"] == 100, "the closed bar was counted twice"


def test_files_done_counts_closed_bars_when_no_count_bar_reports():
    """A file whose bar closes before the 'Fetching N files' bar ever reports
    must still show up as finished."""
    agg = _aggregator(total_bytes=500)
    agg.update_byte_bar("a", 100, 100)
    agg.credit_complete("a", 100)
    assert agg.snapshot()["files_done"] == 1

    agg.update_byte_bar("b", 100, 100)
    agg.credit_complete("b", 100)
    assert agg.snapshot()["files_done"] == 2


def test_files_done_never_drops_when_the_count_bar_lags():
    """The count bar can report fewer files than the bars that already closed.
    Switching from the fallback to the count bar must not lower the display."""
    agg = _aggregator(total_bytes=1000)
    for key in ("a", "b"):
        agg.update_byte_bar(key, 100, 100)
        agg.credit_complete(key, 100)
    assert agg.snapshot()["files_done"] == 2

    agg.update_files(1, 10)  # the count bar lags behind the closed bars
    assert agg.snapshot()["files_done"] == 2, "files_done regressed"
    agg.update_files(2, 10)
    assert agg.snapshot()["files_done"] == 2


def test_files_done_prefers_the_count_bar_when_it_reports():
    agg = _aggregator()
    agg.update_byte_bar("a", 100, 100)
    agg.credit_complete("a", 100)
    agg.update_files(4, 9)
    assert agg.snapshot()["files_done"] == 4


@pytest.mark.parametrize("completed, live", [(100, 0), (0, 100), (100, 100)])
def test_byte_and_count_bars_stay_separate(completed, live):
    """The count bar ('Fetching N files') must never be summed as bytes."""
    agg = _aggregator()
    if completed:
        agg.update_byte_bar("done", completed, completed)
        agg.credit_complete("done", completed)
    if live:
        agg.update_byte_bar("live", live, live)
    agg.update_files(3, 7)

    snap = agg.snapshot()
    assert snap["bytes_done"] == completed + live
    assert snap["files_done"] == 3
    assert snap["files_total"] == 7


def test_each_tracked_bar_gets_its_own_key():
    """The producer side: two bars alive at the same time, and a bar created
    after another was dropped, must never share the aggregator key."""
    from utils import hf_progress

    keys = []
    original_emit = hf_progress._listeners

    def sink(repo_id, key, unit, done, total, complete):
        keys.append(key)

    hf_progress._listeners = {}
    previous_sink = hf_progress._byte_sink
    hf_progress.set_byte_sink(sink)
    token = hf_progress.current_repo_id.set("org/model")
    try:
        hf_progress.install()
        bar_cls = hf_progress.tracked_tqdm_class()
        assert bar_cls is not None, "install() did not expose the tracked tqdm class"

        first = bar_cls(total=10, unit="B")
        first._feed_sink(1, 10, complete=False)
        second = bar_cls(total=10, unit="B")
        second._feed_sink(1, 10, complete=False)
        first.close()

        third = bar_cls(total=10, unit="B")
        third._feed_sink(1, 10, complete=False)
    finally:
        hf_progress.current_repo_id.reset(token)
        # Restore whatever consumer was configured before this test, not None.
        hf_progress.set_byte_sink(previous_sink)
        hf_progress._listeners = original_emit

    assert len(keys) >= 3
    # Each bar reports several times under one key; what must never happen is
    # two different bars sharing a key.
    assert set(keys) == {first._bar_key, second._bar_key, third._bar_key}
    assert len({first._bar_key, second._bar_key, third._bar_key}) == 3, (
        f"two bars shared a key: {[first._bar_key, second._bar_key, third._bar_key]}"
    )


def test_closing_a_bar_with_a_float_total_still_credits_it_complete():
    """tqdm accepts float (and numpy) totals; close() must still report the
    bar complete, or its bytes stay unfinished in the overall progress."""
    from utils import hf_progress

    seen = []
    previous_sink = hf_progress._byte_sink
    hf_progress.set_byte_sink(lambda repo_id, key, unit, done, total, complete: seen.append((done, total, complete)))
    token = hf_progress.current_repo_id.set("org/model")
    try:
        hf_progress.install()
        bar = hf_progress.tracked_tqdm_class()(total=12.0, unit="B")
        bar.close()
    finally:
        hf_progress.current_repo_id.reset(token)
        hf_progress.set_byte_sink(previous_sink)

    assert (12, 12, True) in seen
