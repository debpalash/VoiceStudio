"""A class must not define the same method twice (#2507).

Python keeps only the LAST definition, so an earlier, carefully written one is
silently dead code. ``SubprocessBackend.unload`` had a busy-guarded version
shadowed by a later one that killed a mid-synthesis sidecar. Property
setters/deleters, ``@overload`` stubs and ``@x.register`` variants are the only
legitimate repeats.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("backend",)
_SKIP_PARTS = {"node_modules", ".venv", "venv", "__pycache__"}
_ALLOWED_DECORATORS = ("setter", "getter", "deleter", "overload", "register")


def _decorated_as_variant(fn: ast.AST) -> bool:
    return any(
        tag in ast.unparse(d) for d in fn.decorator_list for tag in _ALLOWED_DECORATORS
    )


def find_duplicate_methods(source: str) -> list[tuple[str, str, list[int]]]:
    out = []
    for cls in ast.walk(ast.parse(source)):
        if not isinstance(cls, ast.ClassDef):
            continue
        seen: dict[str, list[ast.AST]] = defaultdict(list)
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[node.name].append(node)
        for name, defs in seen.items():
            if len(defs) > 1 and not any(_decorated_as_variant(d) for d in defs):
                out.append((cls.name, name, [d.lineno for d in defs]))
    return out


def test_detector_flags_shadowed_method():
    src = "class A:\n    def f(self): pass\n    def f(self): pass\n"
    assert find_duplicate_methods(src) == [("A", "f", [2, 3])]


def test_detector_allows_property_setter():
    src = (
        "class A:\n"
        "    @property\n    def x(self): ...\n"
        "    @x.setter\n    def x(self, v): ...\n"
    )
    assert find_duplicate_methods(src) == []


def test_no_class_defines_a_method_twice():
    offenders = []
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if _SKIP_PARTS & set(path.parts):
                continue
            try:
                dups = find_duplicate_methods(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            offenders += [
                f"{path.relative_to(ROOT)}: {c}.{m} at lines {ls}" for c, m, ls in dups
            ]
    assert not offenders, "duplicate method definitions (last one silently wins):\n" + "\n".join(offenders)
