"""Every text-mode subprocess call in the backend must say how to handle bad bytes (#3612).

On Windows, ``subprocess.run(..., capture_output=True, text=True)`` decodes the
child's output in a reader thread. When the output is not valid in the locale
encoding (PowerShell's OEM code page, a localized tool, a GPU name with a
trademark sign), that thread dies and ``result.stdout`` is silently ``None``.
The caller's ``result.stdout.splitlines()`` then raised, and because the GPU
probe runs at import time the whole backend failed to start. ``errors=...``
(normally ``"replace"``) makes the decode total, so stdout is always a str.

``errors`` also applies when ``input=`` text is encoded for the child. With
the locale codec (cp1252 on many Windows hosts) ``"replace"`` would turn
Cyrillic, Arabic or CJK prompt text into ``?``; a call that sends text in must
name the encoding its child reads (UTF-8 for the native engines).
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_CALLS = {"run", "check_output", "Popen", "call", "check_call"}


def _text_mode_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in _CALLS:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        # text=True, universal_newlines=True, or an encoding/errors argument
        # alone all put Popen in text mode.
        text_mode = any(
            isinstance(kw.get(arg), ast.Constant) and kw[arg].value is True
            for arg in ("text", "universal_newlines")
        ) or "encoding" in kw or "errors" in kw
        if text_mode:
            yield node, kw


def _text_mode_calls_without_errors(tree: ast.AST):
    for node, kw in _text_mode_calls(tree):
        if "errors" not in kw:
            yield node.lineno


def _text_input_calls_without_encoding(tree: ast.AST):
    for node, kw in _text_mode_calls(tree):
        if "input" in kw and "encoding" not in kw:
            yield node.lineno


def test_text_mode_subprocess_calls_never_drop_their_output():
    offenders = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or rel.startswith("backend/tests"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        offenders += [f"{rel}:{line}" for line in _text_mode_calls_without_errors(tree)]
    assert not offenders, (
        "text-mode subprocess calls need errors='replace' (or another handler), "
        "or a bad byte leaves stdout None on Windows: " + ", ".join(offenders)
    )


def test_text_sent_to_a_child_names_its_encoding():
    offenders = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or rel.startswith("backend/tests"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        offenders += [f"{rel}:{line}" for line in _text_input_calls_without_encoding(tree)]
    assert not offenders, (
        "text-mode calls with input= need an explicit encoding, or errors= "
        "silently replaces characters the locale codec lacks: " + ", ".join(offenders)
    )


def test_the_checker_sees_a_bare_text_call():
    tree = ast.parse("subprocess.run(['x'], capture_output=True, text=True)")
    assert list(_text_mode_calls_without_errors(tree)) == [1]
    tree = ast.parse("subprocess.run(['x'], text=True, errors='replace')")
    assert list(_text_mode_calls_without_errors(tree)) == []
    tree = ast.parse("subprocess.run(['x'], input=t, text=True, errors='replace')")
    assert list(_text_input_calls_without_encoding(tree)) == [1]
    tree = ast.parse("subprocess.run(['x'], universal_newlines=True)")
    assert list(_text_mode_calls_without_errors(tree)) == [1]
    tree = ast.parse("subprocess.run(['x'], encoding='utf-8')")
    assert list(_text_mode_calls_without_errors(tree)) == [1]
    tree = ast.parse("subprocess.run(['x'], input=t, errors='replace')")
    assert list(_text_input_calls_without_encoding(tree)) == [1]


_CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
out = sys.argv[2]
from services import binary_preflight
binary_preflight.validate_executable = lambda *a, **k: None
from engines.omnivoice_gguf import backend
backend._spawn_env = lambda: None
backend._binary_path = lambda slug=None: Path(sys.executable)
cls = backend._make_backend_class()
engine = cls.__new__(cls)
write = "import sys, pathlib; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
engine._run_subprocess([sys.executable, "-c", write, out], stdin_text="\u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c")
"""


def test_prompt_text_reaches_the_engine_intact_under_a_non_utf8_locale(tmp_path):
    """Run the real GGUF stdin path in a child whose locale codec is not UTF-8
    (cp1252 on a Windows runner, ASCII elsewhere): non-Latin prompt text must
    arrive as UTF-8, not as question marks."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="")
    if sys.platform != "win32":
        env.update(LC_ALL="C", LANG="C")
    else:
        env.pop("PYTHONIOENCODING")
    probe = subprocess.run(
        [sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    codec = probe.stdout.strip().lower().replace("-", "").replace("_", "")
    if probe.returncode != 0 or codec in ("utf8", "cp65001", ""):
        pytest.skip(f"this host gives child processes a UTF-8 locale ({probe.stdout.strip()!r})")
    out = tmp_path / "stdin.bin"
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), str(ROOT / "backend"), str(out)],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert out.read_bytes().decode("utf-8") == "\u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c"
