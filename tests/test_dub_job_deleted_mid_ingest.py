"""#1252/#1253: a dub ingest failed with the toast ``ingest: 'mgw39lx3'``.

Two reports, same reporter, same session, four `dub:upload` actions in a row.
The entire user-facing error was eight characters of their own job id:

    ingest: 'mgw39lx3'
    Error: ingest: 'mgw39lx3'

That is ``str(KeyError("mgw39lx3"))`` — the repr of a dict key. ``KeyError``
does not put "a lookup failed" in its message, so `build_failure` faithfully
reported a value with no explanation attached to it.

The lookup that failed: ``ingest_pipeline`` finished with a bare
``_dub_jobs[job_id].update(...)``. Everything before it — demucs, scene
detection, thumbnailing — takes minutes, and ``DELETE /dub/history/{id}`` pops
the entry. Deleting an in-flight dub therefore raised ``KeyError`` from a
pipeline that was, by then, doing exactly what it was told.

Both halves are covered: the crash no longer happens, and no exception whose
``str()`` is a bare value can present itself to a user that way again.
"""
from __future__ import annotations

import pytest

import time

from core.failure import build_failure, describe_exception
from services import dub_pipeline


@pytest.fixture(autouse=True)
def _clean_jobs():
    dub_pipeline._dub_jobs.clear()
    yield
    dub_pipeline._dub_jobs.clear()


# ── the crash ────────────────────────────────────────────────────────────


def test_merging_into_a_live_job_updates_it():
    dub_pipeline.put_job("job1", {"filename": "a.mp4", "scene_cuts": []})

    assert dub_pipeline.merge_job("job1", {"scene_cuts": [1.0, 2.0]}) is True
    assert dub_pipeline._dub_jobs["job1"]["scene_cuts"] == [1.0, 2.0]
    assert dub_pipeline._dub_jobs["job1"]["filename"] == "a.mp4", "a merge, not a replace"


def test_merging_into_a_deleted_job_reports_it_instead_of_raising():
    """The #1252 moment: the user deleted the dub while it was still ingesting."""
    dub_pipeline.put_job("mgw39lx3", {"filename": "a.mp4"})
    dub_pipeline._dub_jobs.pop("mgw39lx3")  # DELETE /dub/history/{id}

    assert dub_pipeline.merge_job("mgw39lx3", {"scene_cuts": []}) is False


def test_a_deleted_job_is_not_resurrected():
    """`False` must mean "stop", not "insert it back" — the user deleted this
    on purpose, and re-adding it would put a phantom row back in history."""
    assert dub_pipeline.merge_job("gone", {"scene_cuts": []}) is False
    assert "gone" not in dub_pipeline._dub_jobs


def test_the_ingest_pipeline_no_longer_blind_subscripts_the_job():
    """The call site itself. A direct `_dub_jobs[job_id].update(` anywhere in
    the pipeline reintroduces exactly this KeyError."""
    import inspect

    src = inspect.getsource(dub_pipeline.ingest_pipeline)
    assert "_dub_jobs[job_id].update(" not in src
    assert "run_job_operation(merge_and_save_job," in src


# ── the message ──────────────────────────────────────────────────────────


def test_a_keyerror_no_longer_presents_as_a_bare_key():
    """The exact reason string the reporter saw."""
    failure = build_failure(KeyError("mgw39lx3"), stage="ingest")

    assert failure["reason"] != "'mgw39lx3'"
    assert failure["error_class"] == "KeyError"
    assert "KeyError" in failure["reason"], "name what happened, not just the value"
    assert "mgw39lx3" in failure["reason"], "but keep the value — it is the only clue"


def test_an_exception_with_no_message_at_all_still_names_itself():
    assert describe_exception(RuntimeError()) == "RuntimeError"
    assert describe_exception(ValueError("   ")) == "ValueError"


def test_a_real_message_is_left_exactly_alone():
    """The fix must not prefix class names onto errors that already read fine —
    every existing hint and classification matches on message text."""
    assert describe_exception(RuntimeError("CUDA out of memory")) == "CUDA out of memory"
    assert describe_exception(
        OSError("[Errno 28] No space left on device")
    ) == "[Errno 28] No space left on device"


def test_classification_still_works_through_the_wrapper():
    """`build_failure` classifies on the raw text; adding a class prefix must
    not break the hint lookup for messages that do classify."""
    failure = build_failure(RuntimeError("No module named 'omnivoice'"), stage="startup")
    assert failure["docs_topic"] == "BROKEN_VENV"
    assert failure["hint"]


# ── the delete race the first fix left open ──────────────────────────────


def test_merge_and_save_are_one_step(monkeypatch):
    """Review finding (#1252): merging and persisting as two steps leaves a
    window where the user deletes the dub in between — and the pending save
    then UPSERTs the row straight back, so a dub they deleted reappears."""
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job",
        lambda job_id, job, *a, **kw: saved.append(job_id),
    )
    dub_pipeline.put_job("job1", {"filename": "a.mp4"})

    assert dub_pipeline.merge_and_save_job("job1", {"scene_cuts": [1.0]}) is True
    assert saved == ["job1"]
    assert dub_pipeline._dub_jobs["job1"]["scene_cuts"] == [1.0]


def test_a_deleted_job_is_never_persisted(monkeypatch):
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job",
        lambda job_id, job, *a, **kw: saved.append(job_id),
    )

    assert dub_pipeline.merge_and_save_job("gone", {"scene_cuts": []}) is False
    assert saved == [], "a withdrawn job must not reach the database"


def test_a_delete_landing_mid_save_cannot_be_overtaken(monkeypatch):
    """The race itself — asserted on ORDER, which is the only thing that
    distinguishes it.

    Two earlier versions of this test were not tests. The first deleted the job
    before calling `merge_and_save_job`, so it merely re-checked the absent-job
    case. The second interleaved a real thread but asserted only *what*
    happened, not *when* — so splitting merge from save (the exact bug) still
    passed, because a save landing AFTER the delete looks identical to one
    landing before if you only check that both occurred.

    The resurrection is precisely `save` completing after `delete`. So record
    the order and assert on it: with merge+save atomic under the lock, the
    purge cannot start until the save has finished, and the sequence is always
    save-then-delete. Split them and the purge is free to run first.
    """
    import threading

    order = []
    order_lock = threading.Lock()
    entered_save = threading.Event()
    purge_attempted = threading.Event()

    def _slow_save(job_id, job, *a, **kw):
        entered_save.set()
        # Wait until the purge thread has actually reached its purge call, so
        # the two are genuinely in flight together, then hold a moment.
        purge_attempted.wait(timeout=2.0)
        time.sleep(0.05)
        with order_lock:
            order.append("save")

    monkeypatch.setattr(dub_pipeline, "save_job", _slow_save)
    dub_pipeline.put_job("job1", {"filename": "a.mp4"})

    def _delete_rows():
        with order_lock:
            order.append("delete")

    def _purge():
        entered_save.wait(timeout=2.0)
        purge_attempted.set()
        dub_pipeline.purge_jobs(["job1"], delete_rows=_delete_rows)

    purger = threading.Thread(target=_purge)
    purger.start()
    merged = dub_pipeline.merge_and_save_job("job1", {"scene_cuts": [1.0]})
    purge_attempted.set()  # release the save if the purge never got that far
    purger.join(timeout=5.0)

    assert merged is True, "the save started first, so it must complete"
    assert order == ["save", "delete"], (
        f"the write must never land after the delete — got {order}. "
        "'delete' first means the row was resurrected."
    )
    assert "job1" not in dub_pipeline._dub_jobs


def test_a_purge_that_wins_the_race_stops_the_save_entirely(monkeypatch):
    """The other ordering: purge first, then the pipeline reaches its save."""
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job", lambda job_id, job, *a, **kw: saved.append(job_id),
    )
    dub_pipeline.put_job("job1", {"filename": "a.mp4"})

    dub_pipeline.purge_jobs(["job1"], delete_rows=lambda: None)

    assert dub_pipeline.merge_and_save_job("job1", {"scene_cuts": []}) is False
    assert saved == [], "a withdrawn job must never reach the database"


def test_the_create_checkpoints_are_atomic_too(monkeypatch):
    """Review finding (#1252): the mid-pipeline put_job + save_job pairs were
    still unlocked, so a clear-history landing between them left a ghost row
    behind the purge."""
    import inspect

    src = inspect.getsource(dub_pipeline.ingest_pipeline)
    assert "run_job_operation(put_and_save_job," in src
    assert "put_job(job_id," not in src, "the unlocked pair is the race"

    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job", lambda job_id, job, *a, **kw: saved.append(job_id),
    )
    dub_pipeline.put_and_save_job("job2", {"filename": "b.mp4"})
    assert saved == ["job2"]
    assert dub_pipeline._dub_jobs["job2"]["filename"] == "b.mp4"


def test_clear_history_also_evicts_in_memory_jobs(monkeypatch):
    """`DELETE /dub/history` deleted every row but evicted nothing from memory,
    so an in-flight job survived "clear history" outright and re-saved itself
    on completion."""
    import inspect

    from api.routers import dub_core

    src = inspect.getsource(dub_core.clear_dub_history)
    assert "purge_jobs" in src, "clear-all must evict memory too, not just rows"


def test_the_ingest_pipeline_persists_atomically():
    import inspect

    src = inspect.getsource(dub_pipeline.ingest_pipeline)
    assert "run_job_operation(merge_and_save_job," in src
    # The two-step form is what the race lived in.
    assert "save_job(job_id, get_job(" not in src


# ── withdrawal survives a job's FIRST write ──────────────────────────────


@pytest.fixture(autouse=True)
def _clean_tombstones():
    dub_pipeline._inflight_jobs.clear()
    dub_pipeline._withdrawn_jobs.clear()
    yield
    dub_pipeline._inflight_jobs.clear()
    dub_pipeline._withdrawn_jobs.clear()


def test_clearing_history_mid_ingest_is_not_undone_by_the_next_checkpoint(monkeypatch):
    """Review finding (#1252): dict membership can't express "withdrawn".

    An ingest's FIRST persistence CREATES the entry, so an absent key means
    "not written yet" for a new job and "deleted" for an established one — two
    opposite instructions from one signal. A clear-history landing before that
    first checkpoint was therefore silently undone by the checkpoint recreating
    the row, and the run went on to persist its result into history the user
    had just cleared.
    """
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job", lambda job_id, job, *a, **kw: saved.append(job_id),
    )

    dub_pipeline.begin_ingest("job1")
    # Clear history BEFORE the job has ever been written. It appears in no row,
    # so `job_ids` is empty — only the in-flight sweep can catch it.
    dub_pipeline.purge_jobs([], delete_rows=lambda: None, include_inflight=True)

    assert dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"}) is False
    assert saved == [], "the checkpoint must not recreate a cleared job"
    # ...and the terminal write stays refused too.
    assert dub_pipeline.merge_and_save_job("job1", {"scene_cuts": []}) is False


def test_a_single_delete_also_withdraws_an_inflight_job(monkeypatch):
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job", lambda job_id, job, *a, **kw: saved.append(job_id),
    )
    dub_pipeline.begin_ingest("job1")
    dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"})
    saved.clear()

    dub_pipeline.purge_jobs(["job1"], delete_rows=lambda: None)

    assert dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"}) is False
    assert saved == []


def test_a_normal_ingest_is_unaffected(monkeypatch):
    """The gate must be invisible when nobody deletes anything — this runs on
    every import."""
    saved = []
    monkeypatch.setattr(
        dub_pipeline, "save_job", lambda job_id, job, *a, **kw: saved.append(job_id),
    )
    dub_pipeline.begin_ingest("job1")

    assert dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"}) is True
    assert dub_pipeline.merge_and_save_job("job1", {"scene_cuts": [1.0]}) is True
    assert saved == ["job1", "job1"]


def test_a_delete_during_a_RENDER_is_honoured(monkeypatch):
    """The case Greptile actually reported, and the one an earlier version of
    this fix did not close.

    A dub is imported ONCE and rendered many times, so the realistic delete
    lands during a render — long after its ingest ended. Scoping the tombstone
    to in-flight ingests looked right and protected almost nothing: the render's
    own `save_job` wrote the row straight back.
    """
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )

    # An ordinary saved dub whose import finished long ago.
    dub_pipeline.begin_ingest("old1")
    dub_pipeline.put_and_save_job("old1", {"filename": "a.mp4"})
    dub_pipeline.end_ingest("old1")
    written.clear()

    # Deleted from history while a render is still running.
    dub_pipeline.purge_jobs(["old1"], delete_rows=lambda: None)
    # The render completes and persists, exactly as dub_generate.py does.
    dub_pipeline.save_job("old1", {"filename": "a.mp4", "dubbed_tracks": {"en": {}}})

    assert written == [], "a render finishing after the delete must not revive it"


def test_the_ingest_ending_is_not_an_un_delete(monkeypatch):
    """`end_ingest` used to clear the tombstone, which is what opened the gap
    above — the ingest finishing is not the user un-deleting anything."""
    monkeypatch.setattr(dub_pipeline, "_persist_job", lambda *a, **kw: None)
    dub_pipeline.begin_ingest("job1")
    dub_pipeline.purge_jobs(["job1"], delete_rows=lambda: None)

    dub_pipeline.end_ingest("job1")

    assert "job1" in dub_pipeline._withdrawn_jobs
    assert dub_pipeline._inflight_jobs == set()


def test_re_importing_the_same_id_revives_it(monkeypatch):
    """The one thing that legitimately un-deletes a job."""
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )
    dub_pipeline.purge_jobs(["job1"], delete_rows=lambda: None)
    assert dub_pipeline.save_job("job1", {"filename": "a.mp4"}) is None
    assert written == []

    dub_pipeline.begin_ingest("job1")  # a deliberate re-import
    assert dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"}) is True
    assert written == ["job1"]


def test_clearing_a_large_history_mid_render_does_not_evict_the_running_job(
    monkeypatch,
):
    """Review finding (#1252, Greptile P1): a count-bounded LRU is evictable by
    ordinary use.

    `DELETE /dub/history` selects every row with no limit, so a user with a
    large history clearing it while a render is running would push that very
    job's marker out — and the render would then write it straight back. Age is
    the honest policy: what matters is how long ago the delete happened, not how
    many others happened to follow it.
    """
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )

    # One dub is mid-render; the user clears a history far larger than any
    # count cap would keep.
    # Deliberately larger than the size cap, so the cap is forced to choose —
    # which is exactly the situation that evicted a live marker before.
    everything = ["rendering"] + [
        f"old{i}" for i in range(dub_pipeline._WITHDRAWN_MAX + 500)
    ]
    dub_pipeline.purge_jobs(everything, delete_rows=lambda: None)

    dub_pipeline.save_job("rendering", {"filename": "a.mp4"})
    assert written == [], "the running job's withdrawal must survive the sweep"


def test_markers_expire_by_age(monkeypatch):
    """They are held for the life of the process otherwise, so something has to
    let them go — but it must be time, not volume."""
    import time as _time

    base = _time.monotonic()
    monkeypatch.setattr(dub_pipeline.time, "monotonic", lambda: base)
    dub_pipeline.purge_jobs(["old"], delete_rows=lambda: None)
    assert "old" in dub_pipeline._withdrawn_jobs

    # Well past the TTL, a later delete sweeps the stale one out.
    monkeypatch.setattr(
        dub_pipeline.time, "monotonic",
        lambda: base + dub_pipeline._WITHDRAWN_TTL_S + 1,
    )
    dub_pipeline.purge_jobs(["fresh"], delete_rows=lambda: None)

    assert "old" not in dub_pipeline._withdrawn_jobs
    assert "fresh" in dub_pipeline._withdrawn_jobs


def test_the_marker_map_cannot_grow_without_bound():
    """The count cap is a memory backstop, not the policy."""
    for i in range(dub_pipeline._WITHDRAWN_MAX + 100):
        dub_pipeline.purge_jobs([f"job{i}"], delete_rows=lambda: None)

    assert len(dub_pipeline._withdrawn_jobs) <= dub_pipeline._WITHDRAWN_MAX


def test_the_pipeline_registers_and_releases_its_run():
    import inspect

    src = inspect.getsource(dub_pipeline.ingest_pipeline)
    assert "run_job_operation(begin_ingest, job_id)" in src
    assert "run_job_operation(end_ingest, job_id)" in src, "a leaked tombstone would block a later run"
    # Released in `finally`, so a crash or cancel can't leak it.
    finally_block = src[src.rindex("finally:"):]
    assert "run_job_operation(end_ingest, job_id)" in finally_block


def test_clear_history_sweeps_inflight_jobs():
    import inspect

    from api.routers import dub_core

    src = inspect.getsource(dub_core.clear_dub_history)
    assert "include_inflight=True" in src, (
        "an ingest with no row yet appears in no id list — only the in-flight "
        "sweep can clear it"
    )


# ── the gate is at the choke point, not in the callers ───────────────────


def test_every_direct_save_path_honours_a_withdrawal(monkeypatch):
    """Review finding (#1252, Greptile P1): gating only the ingest helpers left
    eight direct `save_job` call sites — across dub generate, translate, export
    and core — able to resurrect a dub the user deleted *mid-render*. Deleting
    during generation is at least as likely as deleting during import.

    The gate now lives in `save_job` itself, so every caller inherits it and
    the ninth one cannot forget."""
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )

    dub_pipeline.begin_ingest("job1")
    dub_pipeline.purge_jobs(["job1"], delete_rows=lambda: None)

    # The shape every router uses: a bare save_job, no lock, no helper.
    dub_pipeline.save_job("job1", {"filename": "a.mp4"})

    assert written == [], "a post-ingest save must not resurrect a deleted dub"


def test_a_normal_save_still_writes(monkeypatch):
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )
    dub_pipeline.save_job("job1", {"filename": "a.mp4"})
    assert written == ["job1"]


def test_the_lock_is_reentrant():
    """`save_job` acquires the lock, and the atomic helpers call it while
    already holding it — a plain Lock would deadlock the backend here."""
    import threading

    assert isinstance(dub_pipeline._dub_jobs_lock, type(threading.RLock()))


def test_the_atomic_helpers_still_work_through_the_reentrant_path(monkeypatch):
    """Guards the deadlock directly: if this hangs, the RLock regressed."""
    written = []
    monkeypatch.setattr(
        dub_pipeline, "_persist_job", lambda *a, **kw: written.append(a[0]),
    )
    dub_pipeline.begin_ingest("job1")
    assert dub_pipeline.put_and_save_job("job1", {"filename": "a.mp4"}) is True
    assert dub_pipeline.merge_and_save_job("job1", {"scene_cuts": [1.0]}) is True
    assert written == ["job1", "job1"]


def test_eviction_order_does_not_depend_on_hash_seed():
    """A `set` of target ids made eviction order depend on PYTHONHASHSEED — so
    which markers survived a cap-forced trim was luck. The test for the
    behaviour above passed locally and reddened main for that reason alone.

    Same input, same surviving markers, every time.
    """
    ids = [f"j{i}" for i in range(50)]

    dub_pipeline._withdrawn_jobs.clear()
    dub_pipeline.purge_jobs(ids, delete_rows=lambda: None)
    first = list(dub_pipeline._withdrawn_jobs)

    dub_pipeline._withdrawn_jobs.clear()
    dub_pipeline.purge_jobs(ids, delete_rows=lambda: None)
    assert list(dub_pipeline._withdrawn_jobs) == first == ids


def test_a_purge_larger_than_the_cap_keeps_all_of_its_own_markers():
    """The cap must never discard a marker the current purge just recorded:
    those are the newest and the likeliest to still be held. The bound is
    therefore `cap + one purge`, which is the honest guarantee."""
    dub_pipeline._withdrawn_jobs.clear()
    oversized = [f"x{i}" for i in range(dub_pipeline._WITHDRAWN_MAX + 750)]

    dub_pipeline.purge_jobs(oversized, delete_rows=lambda: None)

    assert len(dub_pipeline._withdrawn_jobs) == len(oversized)
    for job_id in oversized:
        assert job_id in dub_pipeline._withdrawn_jobs
