"""Resolving an engine's venv is work, and work must report progress (#1414).

`SubprocessBackend._spawn()` calls `venv_python()`, and on a cold first run
that is not cheap: the probe spawns each candidate interpreter to import the
engine (tens of seconds on a slow disk), and if none is installed it can run
the whole `uv venv` + `uv pip install` bootstrap — which is bounded at 900 s
*by design*, because installing torch takes minutes.

All of that happens on a GPU-pool worker, inside a generate request whose
execution budget defaults to 300 s. Nothing along the way reported progress,
so the budget expired part-way through and the job was abandoned — and, until
#1424, blamed on the machine's compute. The first generation that triggers a
bootstrap could therefore never succeed, no matter how good the hardware.

The sidecar's own cold model load already heartbeats for exactly this reason
(#1367). Resolution is the step immediately before it that never did.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def sb():
    """Resolved at run time, not import time."""
    import services.subprocess_backend as _sb

    return _sb


@pytest.fixture
def mm():
    import services.model_manager as _mm

    return _mm


def test_a_slow_resolution_extends_the_deadline(sb, mm, monkeypatch):
    """The whole point: a resolution that outlives one heartbeat interval
    leaves proof of life on the execution clock."""
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)
    ident = threading.get_ident()
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)

    with sb._heartbeat_while_resolving("indextts2"):
        deadline = time.monotonic() + 2.0
        while ident not in mm._MODEL_LOAD_ACTIVITY and time.monotonic() < deadline:
            time.sleep(0.01)

    assert ident in mm._MODEL_LOAD_ACTIVITY, (
        "a slow venv resolution reported no progress — the generate budget "
        "expires part-way through the install it is waiting for"
    )
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)


def test_it_credits_the_resolving_thread_not_the_beater(sb, mm, monkeypatch):
    """The heartbeat runs on a helper thread so it can tick while resolution
    blocks — but the job the clock is watching is the caller's. Crediting the
    helper's ident would extend nothing and would poison an ident a pool
    worker may later reuse.

    Asserted against the beater's OWN ident rather than a before/after diff of
    the activity map: other tests in the suite have live pool workers, so the
    diff is not this test's to own.
    """
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)
    ident = threading.get_ident()
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)
    beater_idents: set[int] = set()

    real_thread = threading.Thread

    class _Recording(real_thread):
        def run(self):
            beater_idents.add(threading.get_ident())
            super().run()

    monkeypatch.setattr(sb.threading, "Thread", _Recording)

    with sb._heartbeat_while_resolving("indextts2"):
        deadline = time.monotonic() + 2.0
        while ident not in mm._MODEL_LOAD_ACTIVITY and time.monotonic() < deadline:
            time.sleep(0.01)

    assert ident in mm._MODEL_LOAD_ACTIVITY, "the caller's job was never credited"
    assert beater_idents, "no heartbeat thread ran"
    assert not (beater_idents & set(mm._MODEL_LOAD_ACTIVITY)), (
        "the heartbeat thread credited its own ident — that extends nothing "
        "and poisons an ident a pool worker may later reuse"
    )
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)


def test_an_off_pool_caller_never_heartbeats(sb, mm, monkeypatch):
    """#1379's lesson: an off-pool thread's ident is not tracked by the clock,
    and a pool worker that later reuses it would inherit unearned extension."""
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: False)
    ident = threading.get_ident()
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)

    with sb._heartbeat_while_resolving("indextts2"):
        time.sleep(0.05)

    assert ident not in mm._MODEL_LOAD_ACTIVITY


def test_the_beater_stops_when_resolution_finishes(sb, mm, monkeypatch):
    """A thread per spawn that never exits would accumulate one per generate."""
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)
    before = {t.name for t in threading.enumerate()}

    with sb._heartbeat_while_resolving("indextts2"):
        time.sleep(0.05)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        live = {t.name for t in threading.enumerate()} - before
        if not any("resolve-heartbeat" in n for n in live):
            break
        time.sleep(0.01)
    live = {t.name for t in threading.enumerate()} - before
    assert not any("resolve-heartbeat" in n for n in live), live
    mm._MODEL_LOAD_ACTIVITY.pop(threading.get_ident(), None)


def test_a_broken_heartbeat_does_not_break_the_spawn(sb, monkeypatch):
    """Never raises: a generation must not fail because progress reporting
    could not import or could not write."""
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)

    import services.model_manager as mm

    monkeypatch.setattr(
        mm, "running_on_gpu_pool",
        lambda: (_ for _ in ()).throw(RuntimeError("clock is broken")),
    )
    with sb._heartbeat_while_resolving("indextts2"):
        pass  # the point is that this block is reached and exits cleanly


def test_spawn_wraps_the_resolution(sb):
    """A guard against the wrapper being dropped in a later refactor: the
    heartbeat is worthless if `venv_python()` is called outside it."""
    import inspect

    src = inspect.getsource(sb.SubprocessBackend._spawn)
    assert "_heartbeat_while_resolving" in src, (
        "venv_python() is no longer resolved under a heartbeat — a cold "
        "bootstrap will blow the generate budget again (#1414)"
    )
    body = src.split("_heartbeat_while_resolving", 1)[1]
    assert "self.venv_python()" in body.split("script_path")[0], (
        "venv_python() is resolved outside the heartbeat block"
    )


def test_no_heartbeat_write_escapes_the_context(sb, mm, monkeypatch):
    """The late-write race (CodeRabbit, #1426).

    `_beat()` can be past its `stop.wait()` and already committed to a write
    at the moment the context exits. Signalling the stop flag without joining
    lets that write land afterwards — and `_run_on_gpu_pool`'s `_job` pops
    this ident right after, precisely so a stale beat cannot vouch for a later
    job on the same (reused) worker ident. A write that arrives after the pop
    resurrects the entry, and the next job inherits a heartbeat it never sent:
    the wedge detector reads it as progress and keeps extending a stuck job.

    The interleaving is forced rather than waited for. A patched writer parks
    inside the write until the test releases it, so the exit path must be the
    thing that waits — if it only signals, the write lands after the context
    and the assertion catches it deterministically, on every run and every
    scheduler.
    """
    monkeypatch.setattr(sb, "_RESOLVE_HEARTBEAT_S", 0.01)
    monkeypatch.setattr(mm, "running_on_gpu_pool", lambda: True)
    ident = threading.get_ident()
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)

    in_write = threading.Event()
    release = threading.Event()
    wrote_late = []

    class _ParkingMap(dict):
        """Stalls the heartbeat mid-write so the exit path has to wait."""

        def __setitem__(self, key, value):
            if key == ident:
                in_write.set()
                release.wait(5)
                wrote_late.append(context_exited.is_set())
            super().__setitem__(key, value)

    context_exited = threading.Event()
    monkeypatch.setattr(mm, "_MODEL_LOAD_ACTIVITY", _ParkingMap())

    with sb._heartbeat_while_resolving("indextts2"):
        assert in_write.wait(5), "the heartbeat thread never attempted a write"
        release.set()
    context_exited.set()

    assert wrote_late, "no heartbeat write was observed"
    assert not any(wrote_late), (
        "a heartbeat write landed after the resolve context exited — it can "
        "outlive the ident pop and vouch for the next job on this worker"
    )
    mm._MODEL_LOAD_ACTIVITY.pop(ident, None)
