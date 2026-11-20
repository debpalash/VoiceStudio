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

import io
import pathlib
import re
import tokenize

# ``backend/tests/<this file>`` -> repository root is three levels up, not two:
# the offline eval suite that motivated this guard lives under ``omnivoice/``.
_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Directories that never ship or never load a checkpoint: vendored research
# code, the virtualenv, caches and the agent support folders.
_SKIP_PARTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "research", "AUDIT_PR",
     "codex-security", "superpowers", "humanizer", "artifacts"}
)

_LOAD = re.compile(r"torch\.load\s*\(")


def _executable_source(path: pathlib.Path) -> str:
    """Return source with comments and string literals stripped.

    The rule is about executable calls; a docstring that documents
    ``torch.load(weights_only=True)`` (or the rationale for the rule) must not be
    mistaken for a call site.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return "".join(
            token.string
            for token in tokens
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable file: fall back to the raw text so it still gets checked.
        return text


def _call_arguments(source: str, start: int) -> str:
    """Text of the call whose opening parenthesis is at ``start``."""
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    return source[start + 1 :]


def _python_files() -> list[pathlib.Path]:
    return [
        path
        for path in _ROOT.rglob("*.py")
        if not _SKIP_PARTS.intersection(path.parts)
    ]


def test_the_scan_finds_torch_load_call_sites():
    # Guard against a vacuous pass if the layout or the skip list changes.
    found = [
        path
        for path in _python_files()
        if _LOAD.search(_executable_source(path))
    ]
    assert found, "the torch.load scan found no call sites at all"


def test_every_torch_load_passes_weights_only():
    offenders: list[str] = []
    for path in _python_files():
        source = _executable_source(path)
        for match in _LOAD.finditer(source):
            arguments = _call_arguments(source, match.end() - 1)
            # ``**kwargs`` forwarded from a helper that sets it is acceptable;
            # anything else must name the flag explicitly.
            if "weights_only" not in arguments and "**" not in arguments:
                offenders.append(f"{path.relative_to(_ROOT)}: torch.load({arguments.strip()[:60]}...)")
    assert not offenders, (
        "These torch.load calls do not pass weights_only=True, so a hostile "
        "checkpoint could execute code through the pickle unpickler. Pass "
        "weights_only=True, or register the specific pickled classes with "
        "torch.serialization.add_safe_globals:\n" + "\n".join(offenders)
    )
