"""Every ``torch.load`` in the tree must keep PyTorch's secure unpickler on.

``weights_only=True`` is the default since PyTorch 2.6 and is what stops a
downloaded checkpoint from executing arbitrary code through the pickle
unpickler. The repository already enforces that rule for the ASR path
(``backend/tests/test_asr_pickle_safety.py`` guards ``asr_backend.py``), but two
bare ``torch.load`` calls lived in the offline evaluation suite, outside that
guard's file list, and would have accepted a hostile checkpoint.

The check parses each file and inspects the call's own keyword argument, rather
than looking for the text "weights_only" near a call. Looking for the text
accepts ``weights_only=False`` and rejects a call whose argument happens to be
built by a helper, and on Python 3.12+ the text inside an f-string survives a
tokenizer pass, so a mention in a message could be read as a call site.
"""
from __future__ import annotations

import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Directories that never ship or never load a checkpoint: vendored research code,
# the virtualenv, caches and the agent support folders.
_SKIP_PARTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "research", "AUDIT_PR",
     "codex-security", "superpowers", "humanizer", "artifacts"}
)


def _python_files() -> list[pathlib.Path]:
    # Compare against the path inside the checkout: the checkout itself may live under
    # a directory whose name is on the skip list, which would exclude everything.
    return [
        path
        for path in _ROOT.rglob("*.py")
        if not _SKIP_PARTS.intersection(path.relative_to(_ROOT).parts)
    ]


def _is_torch_load(node: ast.AST) -> bool:
    """Whether ``node`` is a call to ``torch.load`` (not any other ``load``)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "torch"
    )


def _load_calls(path: pathlib.Path) -> list[ast.Call]:
    """Every ``torch.load`` call in ``path``, or an empty list if it cannot parse."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    return [node for node in ast.walk(tree) if _is_torch_load(node)]


def _declares_safe_load(call: ast.Call) -> bool:
    """Whether the call itself proves ``weights_only=True``.

    A ``**kwargs`` splat is accepted: the caller forwarded a mapping it built.
    Everything else has to name the keyword with a literal ``True``. A literal
    ``False``, a variable, or a nested call is not proof.
    """
    explicit = next((k for k in call.keywords if k.arg == "weights_only"), None)
    if explicit is not None:
        return isinstance(explicit.value, ast.Constant) and explicit.value.value is True
    # No explicit argument: a **kwargs splat means the caller forwarded a mapping it
    # built, which this scan cannot see into.
    return any(keyword.arg is None for keyword in call.keywords)


def test_the_scan_finds_torch_load_call_sites():
    # Guard against a vacuous pass if the layout or the skip list changes.
    found = [path for path in _python_files() if _load_calls(path)]
    assert found, "the torch.load scan found no call sites at all"


def test_every_torch_load_passes_weights_only():
    offenders: list[str] = []
    for path in _python_files():
        for call in _load_calls(path):
            if not _declares_safe_load(call):
                offenders.append(f"{path.relative_to(_ROOT)}:{call.lineno}")
    assert not offenders, (
        "These torch.load calls do not pass weights_only=True, so a hostile "
        "checkpoint could execute code through the pickle unpickler. Pass "
        "weights_only=True, or register the specific pickled classes with "
        "torch.serialization.add_safe_globals:\n" + "\n".join(offenders)
    )


def test_the_check_rejects_an_explicitly_unsafe_call():
    """The guard reads the argument's value, not the presence of its name."""
    unsafe = ast.parse("torch.load(path, weights_only=False)").body[0].value
    missing = ast.parse("torch.load(path)").body[0].value
    variable = ast.parse("torch.load(path, weights_only=flag)").body[0].value
    safe = ast.parse("torch.load(path, weights_only=True)").body[0].value
    forwarded = ast.parse("torch.load(path, **kwargs)").body[0].value

    forwarded_with_false = ast.parse(
        "torch.load(path, **{'map_location': 'cpu'}, weights_only=False)"
    ).body[0].value
    forwarded_with_true = ast.parse(
        "torch.load(path, weights_only=True, **{'map_location': 'cpu'})"
    ).body[0].value

    assert _declares_safe_load(safe)
    assert _declares_safe_load(forwarded)
    assert _declares_safe_load(forwarded_with_true)
    for call in (unsafe, missing, variable, forwarded_with_false):
        assert not _declares_safe_load(call)