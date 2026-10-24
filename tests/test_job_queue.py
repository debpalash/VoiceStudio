"""Tests for core.job_queue — serial execution, cancel, position, failure isolation."""

import asyncio

import pytest
import pytest_asyncio

from core.job_queue import Job, JobQueue, JobState


@pytest_asyncio.fixture
async def queue():
    q = JobQueue(name="test", concurrency=1)
    await q.start()
    try:
        yield q
    finally:
        await q.stop()


@pytest.mark.asyncio
async def test_single_job_runs_and_completes(queue):
    async def work(job: Job):
        return 42
    j = await queue.submit("one", work)
    done = await queue.wait(j.id, timeout=2.0)
    assert done.state == JobState.DONE
    assert done.result == 42


@pytest.mark.asyncio
async def test_jobs_run_serially_not_in_parallel(queue):
    """Two submits must not overlap — concurrency=1."""
    events = []
    start_a = asyncio.Event()
    release_a = asyncio.Event()

    async def slow_a(job):
        events.append("a-start")
        start_a.set()
        await release_a.wait()
        events.append("a-end")

    async def quick_b(job):
        events.append("b-start")
        events.append("b-end")

    ja = await queue.submit("a", slow_a)
    jb = await queue.submit("b", quick_b)
    await start_a.wait()
    # While A runs, B must still be queued.
    assert queue.get(jb.id).state == JobState.QUEUED
    release_a.set()
    await queue.wait(jb.id, timeout=2.0)
    assert events == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_cancel_while_queued_skips_execution(queue):
    ran = asyncio.Event()
    block = asyncio.Event()

    async def blocker(job):
        await block.wait()

    async def never(job):
        ran.set()

    ja = await queue.submit("blocker", blocker)
    jb = await queue.submit("skipped", never)
    cancelled = await queue.cancel(jb.id)
    assert cancelled is True
    assert queue.get(jb.id).state == JobState.CANCELLED
    block.set()
    await queue.wait(ja.id, timeout=2.0)
    # Give worker a tick to process the cancelled entry.
    await asyncio.sleep(0.05)
    assert not ran.is_set()


@pytest.mark.asyncio
async def test_cancel_while_running_sets_flag_for_cooperative_exit(queue):
    async def cooperative(job):
        for _ in range(20):
            if job.is_cancelled:
                return "stopped-early"
            await asyncio.sleep(0.02)
        return "finished-normally"

    j = await queue.submit("coop", cooperative)
    await asyncio.sleep(0.05)  # let it start
    await queue.cancel(j.id)
    done = await queue.wait(j.id, timeout=2.0)
    # Worker marks cancelled when is_cancelled was true at return.
    assert done.state == JobState.CANCELLED
    assert done.result == "stopped-early" or done.result is None


@pytest.mark.asyncio
async def test_failure_isolated_next_job_still_runs(queue):
    async def boom(job):
        raise RuntimeError("kaboom")

    async def ok(job):
        return "ok"

    ja = await queue.submit("fail", boom)
    jb = await queue.submit("ok", ok)
    await queue.wait(ja.id, timeout=2.0)
    await queue.wait(jb.id, timeout=2.0)
    assert queue.get(ja.id).state == JobState.FAILED
    assert queue.get(ja.id).error == "kaboom"
    assert queue.get(jb.id).state == JobState.DONE


@pytest.mark.asyncio
@pytest.mark.parametrize("child_task", [False, True])
async def test_job_cancellation_does_not_stop_the_shared_worker(queue, child_task):
    async def cancelled_work(job):
        if child_task:
            child = asyncio.create_task(asyncio.sleep(60))
            child.cancel()
            await child
        raise asyncio.CancelledError()

    async def next_work(job):
        return "next job completed"

    cancelled = await queue.submit("cancelled", cancelled_work)
    following = await queue.submit("following", next_work)
    first = await queue.wait(cancelled.id, timeout=1)
    assert first.state == JobState.CANCELLED
    second = await queue.wait(following.id, timeout=1)
    assert second.state == JobState.DONE
    assert second.result == "next job completed"


@pytest.mark.asyncio
async def test_stopping_queue_cancels_running_worker_without_starting_next_job():
    queue = JobQueue(name="shutdown")
    entered = asyncio.Event()
    following_called = []

    async def running(job):
        entered.set()
        await asyncio.Event().wait()

    async def following(job):
        following_called.append(True)

    await queue.start()
    first = await queue.submit("running", running)
    second = await queue.submit("following", following)
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(queue.stop(), 1)
    assert first.state == JobState.CANCELLED
    assert first.done_event.is_set()
    assert following_called == []
    assert second.state == JobState.QUEUED


@pytest.mark.asyncio
async def test_position_reports_zero_for_next_up(queue):
    block = asyncio.Event()

    async def wait_a(job):
        await block.wait()

    async def wait_b(job):
        await block.wait()

    ja = await queue.submit("a", wait_a)
    jb = await queue.submit("b", wait_b)
    jc = await queue.submit("c", wait_b)
    await asyncio.sleep(0.02)  # let worker pick a
    assert queue.get(ja.id).state == JobState.RUNNING
    # positions among QUEUED jobs
    assert queue.position(jb.id) == 0
    assert queue.position(jc.id) == 1
    assert queue.position(ja.id) == -1
    block.set()
    await queue.wait(jc.id, timeout=2.0)


@pytest.mark.asyncio
async def test_submit_idempotent_on_job_id(queue):
    async def work(job):
        return 1

    j1 = await queue.submit("x", work, job_id="fixed")
    j2 = await queue.submit("x", work, job_id="fixed")
    assert j1 is j2
    await queue.wait("fixed", timeout=2.0)


@pytest.mark.asyncio
async def test_cancel_unknown_returns_false(queue):
    assert await queue.cancel("does-not-exist") is False


@pytest.mark.asyncio
async def test_purge_finished_drops_old_rows(queue):
    async def work(job):
        return 1

    j = await queue.submit("x", work)
    await queue.wait(j.id, timeout=2.0)
    # Force finished_at into the past.
    queue.get(j.id).finished_at = 0.0
    dropped = queue.purge_finished(older_than_seconds=1.0)
    assert dropped == 1
    assert queue.get(j.id) is None


@pytest.mark.asyncio
async def test_list_jobs_live_only_by_default(queue):
    finished_evt = asyncio.Event()
    block = asyncio.Event()

    async def finish_fast(job):
        finished_evt.set()

    async def wait_forever(job):
        await block.wait()

    j1 = await queue.submit("done", finish_fast)
    await finished_evt.wait()
    await queue.wait(j1.id, timeout=1.0)
    j2 = await queue.submit("live", wait_forever)
    await asyncio.sleep(0.02)
    live = queue.list_jobs()
    all_jobs = queue.list_jobs(include_finished=True)
    assert j2.id in [j.id for j in live]
    assert j1.id not in [j.id for j in live]
    assert j1.id in [j.id for j in all_jobs]
    block.set()
    await queue.wait(j2.id, timeout=1.0)
