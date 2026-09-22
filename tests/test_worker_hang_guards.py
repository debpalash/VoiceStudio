"""Worker suites must not bound hang guards with a sub-ceiling literal.

Every ``asyncio.wait_for`` in ``tests/test_worker_*.py`` whose timeout is a
number literal below ``HANG_GUARD_S`` is a wall-clock budget on work that
fsyncs and hops threads, and so a flake waiting for a busy disk (see
``tests/hang_guard.py``). Allowed short literals are those where the timeout
firing is the expected outcome, and bare ``asyncio.sleep(0)`` yields.
"""
from __future__ import annotations

import ast
from pathlib import Path

from hang_guard import HANG_GUARD_S

_TESTS = Path(__file__).resolve().parent


def _is_wait_for(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "wait_for"
        and isinstance(func.value, ast.Name)
        and func.value.id == "asyncio"
    )


def _timeout_expr(call: ast.Call):
    for kw in call.keywords:
        if kw.arg == "timeout":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


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


def _is_bare_yield(call: ast.Call) -> bool:
    return bool(call.args) and ast.unparse(call.args[0]) == "asyncio.sleep(0)"


def short_hang_guards(source: str):
    """Yield the literal-timeout nodes that should be ``HANG_GUARD_S``."""
    found = []

    def walk(node, ancestors):
        if isinstance(node, ast.Call) and _is_wait_for(node):
            timeout = _timeout_expr(node)
            if (
                isinstance(timeout, ast.Constant)
                and isinstance(timeout.value, (int, float))
                and not isinstance(timeout.value, bool)
                and timeout.value < HANG_GUARD_S
                and not _expects_timeout(ancestors)
                and not _is_bare_yield(node)
            ):
                found.append(timeout)
        for child in ast.iter_child_nodes(node):
            walk(child, ancestors + [node])

    walk(ast.parse(source), [])
    return found


def test_worker_suites_bound_hang_guards_by_the_shared_ceiling():
    offenders = []
    for path in sorted(_TESTS.glob("test_worker_*.py")):
        for node in short_hang_guards(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{node.lineno} timeout={node.value!r}")
    assert not offenders, (
        "asyncio.wait_for hang guards must use hang_guard.HANG_GUARD_S "
        "(or sit where the timeout is the expected outcome):\n"
        + "\n".join(offenders)
    )


def test_the_check_catches_a_short_guard_and_spares_expected_timeouts():
    source = '''
async def t():
    await asyncio.wait_for(commit(), timeout=1)
    await asyncio.wait_for(commit(), 0.5)
    await asyncio.wait_for(commit(), timeout=HANG_GUARD_S)
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(never(), timeout=0.1)
'''
    assert [node.lineno for node in short_hang_guards(source)] == [3, 4]
