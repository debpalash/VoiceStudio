"""Worker suites must not bound hang guards or barriers with a short literal.

Two kinds of wall-clock bound live in ``tests/test_worker_*.py``:

* Hang guards: ``asyncio.wait_for(...)`` and blocking waits in the test body
  (``to_thread(started.wait, N)``, ``thread.join(N)``). They surround work
  that fsyncs and hops threads, so a short literal is a flake waiting for a
  busy disk. They must use ``HANG_GUARD_S``.
* Barrier watchdogs: the ``release.wait(N)`` inside a blocking fake that stops
  a failed test from hanging forever. A watchdog shorter than the hang guard
  quietly turns "the loop stalled on this barrier" into a stall that ends
  after N seconds, which the hang guard then accepts. So watchdogs must
  outlast it: ``BARRIER_WATCHDOG_S``.

Short literals are allowed only where the timeout firing is the expected
outcome (``pytest.raises(TimeoutError)``, ``except TimeoutError``,
``assert not event.wait(...)``) and for bare ``asyncio.sleep(0)`` yields.
Awaited ``.wait(timeout=...)`` calls are product APIs (``Scheduler.wait``)
whose timeout is the behaviour under test, and are not bounds of this kind.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

_TESTS = Path(__file__).resolve().parent


def _is_number(node) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _is_wait_for(call: ast.Call) -> bool:
    return ast.unparse(call.func) == "asyncio.wait_for"


def _wait_for_timeout(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "timeout":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


def _blocking_wait_timeout(call: ast.Call):
    """Timeout of a sync ``x.wait(N)`` / ``x.join(N)`` or ``to_thread(x.wait, N)``."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in {"wait", "join"}:
        for kw in call.keywords:
            if kw.arg == "timeout":
                return kw.value
        return call.args[0] if call.args else None
    if (
        ast.unparse(func) == "asyncio.to_thread"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
        and call.args[0].attr in {"wait", "join"}
    ):
        for kw in call.keywords:
            if kw.arg == "timeout":
                return kw.value
        return call.args[1] if len(call.args) > 1 else None
    return None


def _expects_timeout(ancestors) -> bool:
    for node in ancestors:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                src = ast.unparse(item.context_expr)
                if "raises" in src and "Timeout" in src:
                    return True
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is not None and "Timeout" in ast.unparse(handler.type):
                    return True
    return False


def _negated_assert(ancestors) -> bool:
    """``assert not event.wait(...)``: the timeout expiring is the claim."""
    for index, node in enumerate(ancestors):
        if isinstance(node, ast.Assert):
            return any(
                isinstance(inner, ast.UnaryOp) and isinstance(inner.op, ast.Not)
                for inner in ancestors[index + 1:]
            )
    return False


def _is_coroutine_api(ancestors) -> bool:
    """Awaited or scheduled: an async product API, not a thread-side wait."""
    parent = ancestors[-1] if ancestors else None
    if isinstance(parent, ast.Await):
        return True
    return isinstance(parent, ast.Call) and ast.unparse(parent.func) in {
        "asyncio.create_task",
        "asyncio.ensure_future",
    }


def short_bounds(source: str):
    """Return ``(literal_node, required_name)`` for every bound below its floor."""
    found = []

    def walk(node, ancestors, in_nested_def):
        if isinstance(node, ast.Call):
            if _is_wait_for(node):
                timeout = _wait_for_timeout(node)
                if (
                    _is_number(timeout)
                    and timeout.value < HANG_GUARD_S
                    and not _expects_timeout(ancestors)
                    and not (
                        node.args
                        and ast.unparse(node.args[0]) == "asyncio.sleep(0)"
                    )
                ):
                    found.append((timeout, "HANG_GUARD_S"))
            else:
                timeout = _blocking_wait_timeout(node)
                is_to_thread = ast.unparse(node.func) == "asyncio.to_thread"
                if (
                    _is_number(timeout)
                    and (is_to_thread or not _is_coroutine_api(ancestors))
                    and not _negated_assert(ancestors)
                    and not _expects_timeout(ancestors)
                ):
                    if in_nested_def and not is_to_thread:
                        if timeout.value < BARRIER_WATCHDOG_S:
                            found.append((timeout, "BARRIER_WATCHDOG_S"))
                    elif timeout.value < HANG_GUARD_S:
                        found.append((timeout, "HANG_GUARD_S"))
        depth_def = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        for child in ast.iter_child_nodes(node):
            walk(
                child,
                ancestors + [node],
                in_nested_def or (depth_def and bool(ancestors) and not isinstance(ancestors[-1], ast.Module)),
            )

    walk(ast.parse(source), [], False)
    return found


def test_worker_suites_bound_hang_guards_and_watchdogs_by_the_shared_ceilings():
    offenders = []
    for path in sorted(_TESTS.glob("test_worker_*.py")):
        for node, required in short_bounds(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{node.lineno} {node.value!r} -> {required}")
    assert not offenders, (
        "worker-suite hang guards must use hang_guard.HANG_GUARD_S and barrier "
        "watchdogs hang_guard.BARRIER_WATCHDOG_S (or sit where the timeout is "
        "the expected outcome):\n" + "\n".join(offenders)
    )


def test_the_watchdog_outlasts_the_hang_guard():
    assert BARRIER_WATCHDOG_S > HANG_GUARD_S


def test_the_check_catches_short_bounds_and_spares_expected_timeouts():
    source = '''
async def test_t():
    def fake():
        if not release.wait(timeout=2):
            raise TimeoutError
        release.wait(BARRIER_WATCHDOG_S)
    await asyncio.wait_for(commit(), timeout=1)
    await asyncio.wait_for(commit(), 0.5)
    assert await asyncio.to_thread(started.wait, 1.0)
    assert await asyncio.to_thread(started.wait, timeout=1.0)
    worker.join(2)
    await asyncio.wait_for(commit(), timeout=HANG_GUARD_S)
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
    assert not finished.wait(0.05)
    await sched.wait(task_id, timeout=0.05)
    waiter = asyncio.ensure_future(sched.wait(task_id, timeout=5))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(never(), timeout=0.1)
'''
    assert [(node.lineno, required) for node, required in short_bounds(source)] == [
        (4, "BARRIER_WATCHDOG_S"),
        (7, "HANG_GUARD_S"),
        (8, "HANG_GUARD_S"),
        (9, "HANG_GUARD_S"),
        (10, "HANG_GUARD_S"),
        (11, "HANG_GUARD_S"),
    ]
