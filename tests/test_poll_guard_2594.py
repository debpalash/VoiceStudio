"""#2594 / #2601 / #2247: status polls must not starve the thread pool.

The renderer polls ``/model/status`` and ``/model/loaded`` about once a second
per widget while anything runs. They were sync routes
behind a sync ``require_admin`` dependency, so every poll (and every other
admin-router request) took a thread from the shared 40-thread pool. When the
pool filled with blocked sync routes the whole API stopped answering although
the process was alive.

Fail-before: the routes and the admin dependency are plain functions, so with
the pool saturated a poll never gets a thread. Pass-after: they run on the
event loop / a private executor and answer regardless.
"""

import asyncio
import inspect
import os
import sys
import threading
import time

import pytest

_BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, os.path.normpath(_BACKEND_DIR))


@pytest.fixture
def PollGuard():
    # Imported lazily so that collecting this module stays backend-free.
    from core.poll_guard import PollGuard as guard_cls

    return guard_cls


def test_status_poll_routes_and_admin_gate_never_use_the_worker_pool():
    from api import dependencies
    from api.routers import system

    for fn in (
        dependencies.require_admin,
        system.model_status,
        system.loaded_models,
    ):
        assert inspect.iscoroutinefunction(fn), (
            f"{fn.__name__} is sync: it would queue behind every blocked sync route"
        )


def test_wedged_snapshot_costs_one_private_thread_and_answers_503(PollGuard):
    release = threading.Event()
    calls = []

    def _wedged():
        calls.append(threading.current_thread().name)
        release.wait(30)
        return {"ok": True}

    async def scenario():
        guard = PollGuard("unit", _wedged, deadline_s=0.2)
        started = time.monotonic()
        results = await asyncio.gather(*(guard.get() for _ in range(60)), return_exceptions=True)
        elapsed = time.monotonic() - started
        return results, elapsed

    try:
        results, elapsed = asyncio.run(scenario())
    finally:
        release.set()
    assert elapsed < 2.0, "callers waited on the wedged snapshot"
    assert all(getattr(r, "status_code", None) == 503 for r in results)
    assert len(calls) == 1, "concurrent polls must share one in-flight computation"
    assert calls[0].startswith("poll-unit")


def test_overrun_serves_the_last_good_snapshot(PollGuard):
    state = {"block": False}
    release = threading.Event()

    def _snapshot(key):
        if state["block"]:
            release.wait(30)
        return {"key": key, "n": 1}

    async def scenario():
        guard = PollGuard("unit", _snapshot, deadline_s=0.2)
        first = await guard.get("tts")
        state["block"] = True
        stale = await guard.get("tts")
        other = None
        try:
            await guard.get("clone")  # never answered: no stale value for it
        except Exception as exc:  # noqa: BLE001
            other = exc
        return first, stale, other

    try:
        first, stale, other = asyncio.run(scenario())
    finally:
        release.set()
    assert first == stale == {"key": "tts", "n": 1}
    assert getattr(other, "status_code", None) == 503


def test_sequential_polls_are_not_cached(PollGuard):
    counter = iter(range(100))
    guard = PollGuard("unit", lambda: next(counter))

    async def scenario():
        return [await guard.get(), await guard.get()]

    assert asyncio.run(scenario()) == [0, 1]


def test_polls_answer_while_the_shared_pool_is_saturated(monkeypatch, tmp_path):
    """The real routes, behind the real admin gate, with all 40 pool threads held."""
    httpx = pytest.importorskip("httpx")
    import anyio.to_thread
    from fastapi import FastAPI
    from api.routers import system

    app = FastAPI()
    app.include_router(system.router)
    release = threading.Event()

    @app.get("/_block")
    def block():  # a sync route: runs in the shared pool
        release.wait(30)
        return {}

    from services import model_lifecycle

    monkeypatch.setattr(model_lifecycle, "list_loaded", lambda: {"models": [], "count": 0})

    async def scenario():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            blockers = [asyncio.ensure_future(client.get("/_block")) for _ in range(60)]
            # Barrier: every worker-pool token is held by a blocked sync route,
            # so the polls below cannot pass by finding a free thread.
            limiter = anyio.to_thread.current_default_thread_limiter()
            deadline = time.monotonic() + 10
            while limiter.borrowed_tokens < limiter.total_tokens:
                assert time.monotonic() < deadline, "worker pool never saturated"
                await asyncio.sleep(0.01)
            try:
                started = time.monotonic()
                status = await asyncio.wait_for(client.get("/model/status"), 5)
                loaded = await asyncio.wait_for(client.get("/model/loaded"), 5)
                return status, loaded, time.monotonic() - started
            finally:
                release.set()
                await asyncio.gather(*blockers, return_exceptions=True)

    status, loaded, elapsed = asyncio.run(scenario())
    assert status.status_code == 200, status.text
    assert loaded.status_code == 200, loaded.text
    assert elapsed < 4.0


def test_extra_keys_cannot_queue_unbounded_work_behind_a_wedged_snapshot(PollGuard):
    release = threading.Event()
    started = []

    def _wedged(key):
        started.append(key)
        release.wait(30)
        return key

    async def scenario():
        guard = PollGuard("unit", _wedged, deadline_s=0.05)
        results = await asyncio.gather(
            *(guard.get(f"op{i}") for i in range(40)), return_exceptions=True
        )
        return results

    try:
        results = asyncio.run(scenario())
    finally:
        release.set()
    assert all(getattr(r, "status_code", None) == 503 for r in results)
    # Only a few distinct snapshots were ever submitted to the one thread.
    from core import poll_guard

    assert len(started) <= poll_guard._MAX_INFLIGHT


def test_last_good_snapshot_expires(PollGuard):
    state = {"block": False}
    release = threading.Event()

    def _snapshot():
        if state["block"]:
            release.wait(30)
        return {"n": 1}

    async def scenario():
        guard = PollGuard("unit", _snapshot, deadline_s=0.05, stale_max_age_s=0.2)
        await guard.get()
        state["block"] = True
        within = await guard.get()
        await asyncio.sleep(0.3)
        try:
            await guard.get()
        except Exception as exc:  # noqa: BLE001
            return within, exc
        return within, None

    try:
        within, expired = asyncio.run(scenario())
    finally:
        release.set()
    assert within == {"n": 1}
    assert getattr(expired, "status_code", None) == 503, "a stale snapshot was served forever"
