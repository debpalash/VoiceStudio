"""The thread-join fixture must not exempt a GPU worker whose pool the test reset."""
from conftest import threads_to_join


def test_retired_pool_worker_is_joined():
    old_worker, unrelated, new_worker, started = object(), object(), object(), object()
    joined = threads_to_join(
        before={old_worker, unrelated},
        pool_before={old_worker},
        pool_now={new_worker},  # the test reset the pool
        current=[old_worker, unrelated, new_worker, started],
    )
    assert old_worker in joined  # retired: must be joined
    assert started in joined
    assert unrelated not in joined and new_worker not in joined


def test_live_pool_worker_stays_exempt():
    worker, started = object(), object()
    joined = threads_to_join({worker}, {worker}, {worker}, [worker, started])
    assert joined == [started]
