"""HTTP error details must never leak a raw exception or a machine path.

`electron/src/renderer/src/lib/api/client.ts` shows `detail` verbatim as a toast,
and a prefilled bug report pastes that toast into a public issue; under LAN
sharing a second device reads it directly. An exception message can carry a home
directory, a temp path or an upstream provider body, so the repository already
owns both the policy and the helper: `backend/core/public_errors.py` withholds the
message on purpose, and `core.scrub.scrub_text` redacts secrets and home paths. It
is the precedent `routers/settings.py::_scrub_llm_detail` follows.

Three checks run over `backend/api/routers`:

* an `HTTPException(detail=...)` f-string may not interpolate a caught exception
  (bare, `str(...)`, `repr(...)`, `type(...).__name__`, or a slice of those)
  without a scrub helper, and a helper only covers the subtree it wraps, so
  `scrub_text(str(e)) + str(cause)` still reports the raw `cause`;
* an f-string detail may not interpolate a path-shaped variable without a scrub
  helper, because scrubbing only the exception still ships the path, which is what
  the log read and clear routes did;
* a function that calls `scrub_text` has to be able to resolve it: a module-level
  import, or an import inside that same function. `settings.py` imported it inside
  `_scrub_llm_detail`, so a second call site in the same module raised `NameError`
  and turned a 400 into a 500.

`detail=str(exc)` and typed `exc.detail` payloads stay out of scope: application
errors with a message written for the user share that shape, so the rule would be
noise rather than a guard.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "backend" / "api" / "routers"

# Helpers whose result is safe to show.
SAFE_HELPERS = (
    "scrub_text",
    "scrub_provider_error",
    "public_failure",
    "log_safe",
    "_safe_exc_text",
)
# Variables that conventionally hold a caught exception.
EXCEPTION_NAMES = {"e", "exc", "err", "error", "cause"}
# Names that hold a filesystem location: any `*path`/`*dir`/`*root` shape, the
# bare forms of those words, and the single-letter / generic file names.
_PATH_SHAPED = re.compile(
    r"^(?:[A-Za-z_]\w*)?(?:paths?|dirs?|folder|root|dest|destination)$|^(?:p|file|filename)$"
)
# Exception attributes whose value the application wrote for the user, so showing
# it is intended: `ArchiveError.detail`, a validation error's `type`. Everything
# else, including `e.args`, stays in scope.
DESIGNED_ATTRIBUTES = {"detail", "type"}
# (file, line) of justified exceptions, with the reason on the same row. Paths are
# relative to the routers package, so a subpackage entry reads `setup/foo.py`.
ALLOWLIST: dict[tuple[str, int], str] = {
    ("marketplace.py", 465): "user-supplied bundle name, already validated by _contained_path",
    ("marketplace.py", 505): "user-supplied bundle name, already validated by _contained_path",
}


def _walk(nodes):
    """Walk statements, descending into blocks but not into nested scopes.

    Functions, lambdas and classes introduce their own scope: an import inside one
    is not a binding for the code outside it, which is why the walk stops there.
    """
    for node in nodes:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        for child in ast.iter_child_nodes(node):
            yield from _walk([child])


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _detail_expressions(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "HTTPException":
            continue
        for keyword in node.keywords:
            if keyword.arg == "detail":
                yield node, keyword.value


def _unsafe_names(node: ast.AST) -> list[str]:
    """Names in this expression that would reach the user unscrubbed."""
    if _call_name(node) in SAFE_HELPERS:
        return []  # the helper covers everything it wraps
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in EXCEPTION_NAMES
        and node.attr in DESIGNED_ATTRIBUTES
    ):
        # `exc.detail`, `e.type`: a value the application wrote for the user.
        # `e.args` is not: it holds the raw message, so it stays in scope.
        return []
    if isinstance(node, ast.Name):
        if node.id in EXCEPTION_NAMES:
            return [f"raw exception: {node.id}"]
        if _PATH_SHAPED.match(node.id):
            return [f"unscrubbed path: {node.id}"]
        return []
    found: list[str] = []
    for child in ast.iter_child_nodes(node):
        found.extend(_unsafe_names(child))
    return found


def _problems(detail: ast.AST) -> list[str]:
    node = detail
    # A truncated or otherwise sliced f-string is still the detail; `f"..."[:200]`
    # reaches the user exactly like the unsliced form.
    while isinstance(node, ast.Subscript):
        node = node.value
    if not isinstance(node, ast.JoinedStr):
        return []
    found: list[str] = []
    for value in node.values:
        if isinstance(value, ast.FormattedValue):
            found.extend(_unsafe_names(value.value))
    return found


def _snippet(detail: str) -> ast.AST:
    return ast.parse(f"def route():\n    raise HTTPException(status_code=400, detail={detail})\n")


def _problems_in(detail: str) -> list[str]:
    tree = _snippet(detail)
    found: list[str] = []
    for _call, value in _detail_expressions(tree):
        found.extend(_problems(value))
    return found


def _offenders() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(ROUTERS.rglob("*.py")):
        relative = path.relative_to(ROUTERS).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        for call, detail in _detail_expressions(tree):
            if (relative, call.lineno) in ALLOWLIST:
                continue
            for problem in _problems(detail):
                found.append((relative, call.lineno, problem))
    return found


def test_http_error_details_never_leak_an_exception_or_a_path():
    offenders = _offenders()
    assert not offenders, (
        "Wrap the whole `HTTPException(detail=...)` value in `core.scrub.scrub_text`: the "
        "renderer shows the text verbatim and bug reports paste it publicly.\n  "
        + "\n  ".join(f"{name}:{line} {problem}" for name, line, problem in offenders)
    )


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ('f"Failed: {e}"', ["raw exception: e"]),
        ('f"Failed: {str(e)[:200]}"', ["raw exception: e"]),
        ('f"Failed: {type(e).__name__}"', ["raw exception: e"]),
        ('f"Failed: {path}"', ["unscrubbed path: path"]),
        ('f"Failed: {p}"', ["unscrubbed path: p"]),
        ('f"Failed: {profile_dir}"', ["unscrubbed path: profile_dir"]),
        ('f"Failed: {scrub_text(str(e))}"', []),
        ('f"Failed: {_safe_exc_text(e)}"', []),
        ('f"Failed: {log_safe(str(e))}"', []),
        # A helper only covers what it wraps.
        ('f"Failed: {scrub_text(str(e)) + str(cause)}"', ["raw exception: cause"]),
        ('f"Failed: {scrub_text(path)} at {destination}"', ["unscrubbed path: destination"]),
        # An attribute the application wrote for the user is fine; a raw payload is not.
        ('f"Invalid bundle: {exc.detail}"', []),
        ('f"Unknown type {e.type!r}"', []),
        ('f"Failed: {e.args[0]}"', ["raw exception: e"]),
        # A sliced f-string still reaches the user.
        ('f"Failed: {str(e)}"[:200]', ["raw exception: e"]),
        ('f"Failed: {path}"[:80]', ["unscrubbed path: path"]),
        # Not a leak: a plain word that merely resembles a path name.
        ('f"Bundle not found: {filename}"', ["unscrubbed path: filename"]),
        ('f"Saved {count} files"', []),
    ],
)
def test_the_guard_flags_the_expected_shapes(detail: str, expected: list[str]) -> None:
    assert _problems_in(detail) == expected


def _own_calls_scrub_text(node: ast.AST) -> bool:
    return any(_call_name(child) == "scrub_text" for child in _walk(node.body))


def _bound_scrub_names(nodes) -> set[str]:
    """Local names an import from `core.scrub` binds, honouring `as` aliases."""
    names: set[str] = set()
    for node in _walk(nodes):
        if isinstance(node, ast.ImportFrom) and node.module == "core.scrub":
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def _imports_scrub_text(nodes) -> bool:
    # The binding is what matters: `from core.scrub import scrub_text as sanitize`
    # leaves `scrub_text` undefined, so a call to it raises NameError.
    return "scrub_text" in _bound_scrub_names(nodes)


def _import_offenders(source: str) -> list[str]:
    """Functions whose `scrub_text` calls cannot resolve the name at run time.

    A function may use an import from its own body, from the module, or from an
    enclosing function (Python closures). An import in a *nested* function is not
    visible to its parent, which is the case that shipped a `NameError`.
    """
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    module_level = _imports_scrub_text(tree.body)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _own_calls_scrub_text(node):
            continue
        if module_level or _imports_scrub_text(node.body):
            continue
        ancestor = parents.get(node)
        visible = False
        while ancestor is not None and not isinstance(ancestor, ast.Module):
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)) and _imports_scrub_text(
                ancestor.body
            ):
                visible = True
                break
            ancestor = parents.get(ancestor)
        if not visible:
            problems.append(node.name)
    return problems


def test_every_call_site_can_see_the_scrub_helper():
    problems: list[str] = []
    for path in sorted(ROUTERS.rglob("*.py")):
        relative = path.relative_to(ROUTERS).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        problems.extend(f"{relative}: {name}()" for name in _import_offenders(source))
    assert not problems, (
        "these call sites cannot resolve `scrub_text` at run time (import it at module level "
        "or in the same function):\n  " + "\n  ".join(problems)
    )


def test_the_import_check_uses_the_right_scope():
    """An import in one function must not cover a sibling, but must cover a child."""
    separate = (
        "from core.scrub import scrub_text\n"  # module level covers both
        "def a():\n    return scrub_text('x')\n"
        "def b():\n    return scrub_text('y')\n"
    )
    assert _import_offenders(separate) == []

    local_only = (
        "def a():\n    from core.scrub import scrub_text\n    return scrub_text('x')\n"
        "def b():\n    return scrub_text('y')\n"
    )
    assert _import_offenders(local_only) == ["b"]

    nested_helper = (
        "def a():\n"
        "    def inner():\n        from core.scrub import scrub_text\n        return scrub_text('x')\n"
        "    return inner()\n"
    )
    assert _import_offenders(nested_helper) == []

    outer_without_import = (
        "def a():\n"
        "    def inner():\n        from core.scrub import scrub_text\n        return scrub_text('x')\n"
        "    return inner() + scrub_text('y')\n"
    )
    assert _import_offenders(outer_without_import) == ["a"]

    child_uses_parent_import = (
        "def a():\n"
        "    from core.scrub import scrub_text\n"
        "    def inner():\n        return scrub_text('x')\n"
        "    return inner()\n"
    )
    assert _import_offenders(child_uses_parent_import) == []

    # An alias binds a different name, so the call is unbound.
    aliased = (
        "from core.scrub import scrub_text as sanitize\n"
        "def a():\n    return scrub_text('x')\n"
    )
    assert _import_offenders(aliased) == ["a"]

    aliased_and_used = (
        "from core.scrub import scrub_text as sanitize\n"
        "def a():\n    return sanitize('x')\n"
    )
    assert _import_offenders(aliased_and_used) == []

    # A class body is its own scope: a method resolves names through globals, so
    # an import there does not cover a module-level function.
    class_import = (
        "class Helpers:\n    from core.scrub import scrub_text\n"
        "def route():\n    return scrub_text('x')\n"
    )
    assert _import_offenders(class_import) == ["route"]


def test_the_allowlist_does_not_rot():
    """Every allowlist row must still point at a real detail expression."""
    for (name, line), reason in ALLOWLIST.items():
        source = (ROUTERS / name).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        assert any(call.lineno == line for call, _ in _detail_expressions(tree)), (
            f"{name}:{line} no longer holds an HTTPException detail: {reason}"
        )
