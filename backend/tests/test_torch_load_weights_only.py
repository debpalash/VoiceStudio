"""Every ``torch.load`` in the tree must keep PyTorch's secure unpickler on.

``weights_only=True`` is the default since PyTorch 2.6 and is what stops a
downloaded checkpoint from executing arbitrary code through the pickle
unpickler. The repository already enforces that rule for the ASR path
(``backend/tests/test_asr_pickle_safety.py`` guards ``asr_backend.py``), but two
bare ``torch.load`` calls lived in the offline evaluation suite, outside that
guard's file list, and would have accepted a hostile checkpoint.

This is a whole-tree scan rather than a per-file assertion so a new evaluator
cannot quietly reintroduce the pattern.
"""
from __future__ import annotations

import ast
import pathlib

# ``backend/tests/<this file>`` -> repository root is three levels up, not two:
# the offline eval suite that motivated this guard lives under ``omnivoice/``.
_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Directories that never ship or never load a checkpoint: vendored research
# code, the virtualenv, caches and the agent support folders.
_SKIP_PARTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "research", "AUDIT_PR",
     "codex-security", "superpowers", "humanizer", "artifacts"}
)


def _is_torch_load(node: ast.AST) -> bool:
    func = getattr(node, "func", None)
    return (
        isinstance(node, ast.Call)
        and isinstance(func, ast.Attribute)
        and func.attr == "load"
        and isinstance(func.value, ast.Name)
        and func.value.id == "torch"
    )


def _unsafe(call: ast.Call) -> bool:
    """True unless the call's own ``weights_only`` is literally ``True``.

    ``**kwargs`` forwarding is accepted (a helper may set the flag); any
    explicit ``weights_only`` other than the literal ``True`` is not.
    """
    for keyword in call.keywords:
        if keyword.arg is None:
            return False
        if keyword.arg == "weights_only":
            return not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True)
    return True


def _calls(source: str) -> list[ast.Call]:
    return [node for node in ast.walk(ast.parse(source)) if _is_torch_load(node)]


def _python_files() -> list[pathlib.Path]:
    return [
        path
        for path in _ROOT.rglob("*.py")
        if not _SKIP_PARTS.intersection(path.parts)
    ]


def _parsed_calls(path: pathlib.Path) -> list[ast.Call]:
    try:
        return _calls(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []


def test_the_scan_finds_torch_load_call_sites():
    # Guard against a vacuous pass if the layout or the skip list changes.
    assert any(_parsed_calls(path) for path in _python_files()), (
        "the torch.load scan found no call sites at all"
    )


def test_every_torch_load_passes_weights_only():
    offenders = [
        f"{path.relative_to(_ROOT)}:{call.lineno}"
        for path in _python_files()
        for call in _parsed_calls(path)
        if _unsafe(call)
    ]
    assert not offenders, (
        "These torch.load calls do not pass weights_only=True, so a hostile "
        "checkpoint could execute code through the pickle unpickler. Pass "
        "weights_only=True, or register the specific pickled classes with "
        "torch.serialization.add_safe_globals:\n" + "\n".join(offenders)
    )


def test_the_guard_classifies_calls():
    flagged = [
        "torch.load(p)",
        "torch.load(p, weights_only=False)",
        "torch.load(resolve(weights_only=True))",
        "torch.load(paths[2**0])",
        "f'{torch.load(p)}'",
    ]
    for src in flagged:
        calls = _calls(src)
        assert calls and all(_unsafe(c) for c in calls), src
    for src in ("torch.load(p, weights_only=True)", "torch.load(p, **kw)", "'torch.load(p)'"):
        assert not any(_unsafe(c) for c in _calls(src)), src
