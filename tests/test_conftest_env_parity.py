"""The backend suite pins the same environment guards as the root suite.

Both conftests exist so a collection or a lifespan boot cannot touch the
developer's machine or the network, but they were written at different times:
`backend/tests/conftest.py` mirrored the data dir, the host allowlist and the
checkpoint sentinel, and missed the two guards that keep a deferred preload from
loading real models mid-suite and keep the physical-GPU inventory out of routing
tests. Whichever conftest loads first wins in a mixed run, so the two sets have to
match rather than merely overlap.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_CONFTEST = ROOT / "tests" / "conftest.py"
BACKEND_CONFTEST = ROOT / "backend" / "tests" / "conftest.py"


def _module_level_env_keys(path: Path) -> set[str]:
    """`os.environ["X"] = ...` and `os.environ.setdefault("X", ...)` at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "environ"
                    and isinstance(target.slice, ast.Constant)
                ):
                    keys.add(target.slice.value)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "setdefault"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "environ"
                and call.args
                and isinstance(call.args[0], ast.Constant)
            ):
                keys.add(call.args[0].value)
    return keys


def test_the_backend_suite_sets_every_guard_the_root_suite_sets() -> None:
    root_keys = _module_level_env_keys(ROOT_CONFTEST)
    backend_keys = _module_level_env_keys(BACKEND_CONFTEST)
    missing = sorted(root_keys - backend_keys)
    assert not missing, (
        "backend/tests/conftest.py does not set these guards that tests/conftest.py "
        f"sets, so a backend-only run behaves differently: {missing}"
    )