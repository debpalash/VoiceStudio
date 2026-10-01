"""Phase 2.1 — persist task queue. Round-trip tests for core/job_store."""
import os
import threading
import time

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from core import job_store
from core.db import init_db, db_conn


@pytest.fixture(autouse=True)
def _init_db_once():
    init_db()
    yield


def _unique_id(prefix: str = "t") -> str:
    return f"{prefix}_{int(time.time()*1e6)}_{os.getpid()}"


# ── Lifecycle ──────────────────────────────────────────────────────────────


def test_create_mark_done_round_trip():
    jid = _unique_id()
    job_store.create(jid, type="dub_generate", project_id="p1", meta={"segments": 3})

    row = job_store.get(jid)
    assert row is not None
    assert row["status"] == "pending"
    assert row["project_id"] == "p1"
    assert '"segments": 3' in row["meta_json"]

    job_store.mark_running(jid)
    assert job_store.get(jid)["status"] == "running"

    job_store.mark_done(jid)
    done = job_store.get(jid)
    assert done["status"] == "done"
    assert done["finished_at"] is not None


def test_mark_failed_carries_error():
    jid = _unique_id()
    job_store.create(jid, type="dub_translate")
    job_store.mark_failed(jid, "something broke")
    row = job_store.get(jid)
    assert row["status"] == "failed"
    assert row["error"] == "something broke"


# ── Events ─────────────────────────────────────────────────────────────────


def test_append_event_assigns_monotonic_seq():
    jid = _unique_id()
    job_store.create(jid, type="x")
    s1 = job_store.append_event(jid, "data: a\n\n")
    s2 = job_store.append_event(jid, "data: b\n\n")
    s3 = job_store.append_event(jid, "data: c\n\n")
    assert (s1, s2, s3) == (1, 2, 3)


def test_append_event_assigns_unique_seq_under_concurrency():
    """Regression: seq assignment must be atomic, not read-then-write.

    `append_event` used to run `SELECT COALESCE(MAX(seq), 0)` and then a
    separate INSERT. Every call opens its own connection (`core.db.db_conn`)
    and WAL lets a reader run while another writer is mid-transaction, so two
    concurrent callers could both read the same MAX and both insert it.
    `job_events` indexes (job_id, seq) but does NOT constrain it UNIQUE, so the
    duplicates landed silently and an SSE client reconnecting with
    `?after_seq=N` then replayed one event twice and skipped another.

    The `INSERT … SELECT … RETURNING` that replaced it computes and writes the
    next seq inside a single statement, which SQLite serializes behind the
    write lock. Fail-before/pass-after: against the old two-step version the
    barrier below reliably produces duplicate seq values.
    """
    jid = _unique_id("race")
    job_store.create(jid, type="x")

    workers = 8
    barrier = threading.Barrier(workers)
    lock = threading.Lock()
    returned: list[int] = []
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            # Release all threads into append_event at the same moment so the
            # read-then-write window is actually exercised.
            barrier.wait(timeout=10)
            seq = job_store.append_event(jid, f"data: {n}\n\n")
        except BaseException as exc:  # noqa: BLE001 — re-raised via `errors`
            with lock:
                errors.append(exc)
            return
        with lock:
            returned.append(seq)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"append_event raised under concurrency: {errors!r}"
    assert sorted(returned) == list(range(1, workers + 1)), (
        f"returned seqs are not a 1..{workers} permutation: {sorted(returned)}"
    )

    persisted = [e["seq"] for e in job_store.events_since(jid, after_seq=0)]
    assert len(persisted) == len(set(persisted)), f"duplicate seq persisted: {persisted}"
    assert persisted == list(range(1, workers + 1))


def test_events_since_filters():
    jid = _unique_id()
    job_store.create(jid, type="x")
    for i in range(5):
        job_store.append_event(jid, f"data: {i}\n\n")

    all_ = job_store.events_since(jid, after_seq=0)
    assert [e["seq"] for e in all_] == [1, 2, 3, 4, 5]

    tail = job_store.events_since(jid, after_seq=3)
    assert [e["seq"] for e in tail] == [4, 5]
    assert all(e["payload"] for e in tail)


def test_events_respect_per_job_cap():
    # The cap is 500 — simulate it by monkey-patching to a small value.
    from core import job_store as js
    original = js._EVENT_CAP_PER_JOB
    js._EVENT_CAP_PER_JOB = 3
    try:
        jid = _unique_id()
        js.create(jid, type="x")
        for i in range(10):
            js.append_event(jid, f"data: {i}\n\n")
        rows = js.events_since(jid, after_seq=0)
        # Only the last 3 should survive.
        assert len(rows) == 3
        assert [e["seq"] for e in rows] == [8, 9, 10]
    finally:
        js._EVENT_CAP_PER_JOB = original


# ── Queries ────────────────────────────────────────────────────────────────


def test_list_active_returns_only_live_statuses():
    jid_running = _unique_id("running")
    jid_done = _unique_id("done")
    job_store.create(jid_running, type="x"); job_store.mark_running(jid_running)
    job_store.create(jid_done, type="x");    job_store.mark_done(jid_done)

    active = job_store.list_jobs(status="active", limit=50)
    ids = {j["id"] for j in active}
    assert jid_running in ids
    assert jid_done not in ids


# ── Startup sweep ──────────────────────────────────────────────────────────


def test_sweep_orphans_flips_running_to_failed():
    jid_orphan = _unique_id("orphan")
    jid_done = _unique_id("closed")
    job_store.create(jid_orphan, type="x"); job_store.mark_running(jid_orphan)
    job_store.create(jid_done, type="x"); job_store.mark_done(jid_done)

    job_store.sweep_orphans_on_startup()

    orphan = job_store.get(jid_orphan)
    assert orphan["status"] == "failed"
    assert "interrupted" in orphan["error"].lower()

    done = job_store.get(jid_done)
    assert done["status"] == "done"  # untouched
