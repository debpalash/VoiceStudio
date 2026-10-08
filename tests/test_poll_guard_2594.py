"""#2594 / #2601 / #2247: status polls must not be able to starve the worker pool.

The renderer polls ``/model/status``, ``/model/loaded`` and ``/workers/target``
about once a second per widget while anything runs. They were sync routes
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

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)


@pytest.fixture()
def PollGuard():
    # Imported at run time so collecting this file never imports backend code.
    from core.poll_guard import PollGuard as guard_cls

    return guard_cls


def test_status_poll_routes_and_admin_gate_never_use_the_worker_pool():
    from api import dependencies
    from api.routers import system, workers

    for fn in (
        dependencies.require_admin,
        system.model_status,
        system.loaded_models,
        workers.get_target,
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
    from api.routers import system, workers

    app = FastAPI()
    app.include_router(system.router)
    app.include_router(workers.router)
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
                target = await asyncio.wait_for(client.get("/workers/target?op=tts"), 5)
                return status, loaded, target, time.monotonic() - started
            finally:
                release.set()
                await asyncio.gather(*blockers, return_exceptions=True)

    status, loaded, target, elapsed = asyncio.run(scenario())
    assert status.status_code == 200, status.text
    assert loaded.status_code == 200, loaded.text
    assert target.status_code == 200, target.text
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


def _renderer_workers_target_ops() -> set[str]:
    """Every `op` the renderer can send to /workers/target, read from its source."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "electron" / "src" / "renderer" / "src"
    patterns = [
        r"operation=\"([^\"]+)\"",  # <EngineNotice operation="design" />
        r"useTtsReadiness\('([^']+)'",
        r"useComputeTarget\([^)]*?,\s*'([^']+)'",
        r"workers/target\?op=([A-Za-z0-9_-]+)",
        r"ttsOperation = [^?]*\? '([^']+)' : '([^']+)'",
    ]
    found: set[str] = set()
    for path in list(root.rglob("*.ts")) + list(root.rglob("*.tsx")):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                found.update(g for g in match.groups() if g)
    return found


def test_every_operation_the_renderer_sends_is_accepted_by_workers_target():
    from api.routers import workers
    from worker import routing

    ops = _renderer_workers_target_ops()
    # Sanity: the scan sees the surfaces that regressed (#2608 review).
    assert {"tts", "clone", "dub", "batch", "design", "compare", "profile-preview"} <= ops
    # Backend-known operations, including the non-remote ones the picker asks about.
    ops |= set(routing.REMOTE_OPERATIONS) | set(routing._OP_LABELS) | {"asr", "dictation", ""}
    for op in sorted(ops):
        payload = asyncio.run(workers.get_target(op=op))
        assert payload["op"] == op, op


def test_target_poll_still_rejects_arbitrary_strings():
    from fastapi import HTTPException
    from api.routers import workers

    for bad in ("x" * 40, "Has Space", "../etc", "a;b", "UPPER"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(workers.get_target(op=bad))
        assert caught.value.status_code == 422, bad


@pytest.mark.parametrize("target", ["local", "gpu2"])
def test_one_target_snapshot_answers_every_operation_like_a_per_op_query(monkeypatch, target):
    """The derivation is what lets /workers/target keep a single in-flight key."""
    from worker import routing

    monkeypatch.setattr(routing, "get_target_id", lambda: target)
    base = routing.status()
    ops = set(routing.REMOTE_OPERATIONS) | set(routing._OP_LABELS) | {"design", "compare", "x-y"}
    for op in sorted(ops):
        assert routing.status_for_operation(base, op) == routing.status(op=op), (target, op)
    assert routing.status_for_operation(base, "") == routing.status()


def test_remote_caller_cannot_exhaust_target_poll_slots_with_distinct_ops(monkeypatch):
    """Unauthenticated server-mode callers pick `op`; it must never pick the work."""
    httpx = pytest.importorskip("httpx")
    from fastapi import FastAPI
    from api.routers import workers
    from worker import routing

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    guard = workers._target_poll
    guard._last.clear()
    guard._inflight.clear()
    release = threading.Event()
    calls = []

    def _stalled(*_args, **_kwargs):
        calls.append(threading.current_thread().name)
        release.wait(30)
        return {"target": "local", "op": "", "active": {}, "targets": []}

    monkeypatch.setattr(routing, "status", _stalled)
    app = FastAPI()
    app.include_router(workers.router)
    ops = [f"op{i}" for i in range(40)] + ["tts", "clone", "dub", "design"]

    async def scenario():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            flood = await asyncio.gather(
                *(client.get("/workers/target", params={"op": op}) for op in ops)
            )
            # A legitimate poll during the stall gets the bounded answer too,
            # and consumes no further work.
            legit = await client.get("/workers/target", params={"op": "tts"})
            return flood, legit

    try:
        flood, legit = asyncio.run(scenario())
    finally:
        release.set()
    assert {r.status_code for r in flood} == {503}
    assert legit.status_code == 503
    assert len(calls) == 1, f"{len(calls)} snapshots were started for distinct ops"
