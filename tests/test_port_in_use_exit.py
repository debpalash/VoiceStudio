"""#1223: a port conflict must exit with a code the shell can recognise.

The reporter's backend died with `[Errno 10048] error while attempting to bind
on address ('127.0.0.1', 3900)` — port already taken, almost certainly by an
orphan from a previous session. uvicorn re-raised the bare OSError, Python
exited 1, and the desktop shell reported "Backend died (exit code 1)" with no
cause: the Windows wording is OS-translated (the report was in Russian), so no
English phrase in the log could be matched.

The fix is to make the signal locale-independent — a dedicated exit code that
Electron's backend supervisor and the shared renderer crash hint both key off.
This test pins the code and its cross-language agreement.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPECTED_EXIT = 78  # EX_CONFIG


def _read(*parts: str) -> str:
    with open(os.path.join(_REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_backend_declares_the_exit_code():
    src = _read("backend", "main.py")
    assert f"_EXIT_PORT_IN_USE = {_EXPECTED_EXIT}" in src


def test_electron_shell_agrees_on_the_exit_code():
    """The desktop supervisor must distinguish a conflict from a crash."""
    src = _read("electron", "src", "main", "backend.ts")
    match = re.search(r"const EXIT_PORT_IN_USE = (\d+);", src)
    assert match, "EXIT_PORT_IN_USE missing from Electron backend supervisor"
    assert int(match.group(1)) == _EXPECTED_EXIT


def test_frontend_crash_hint_agrees_on_the_exit_code():
    src = _read("frontend", "src", "utils", "backendCrash.ts")
    assert f"marker.exit_code === {_EXPECTED_EXIT}" in src


@pytest.mark.parametrize("errno", [48, 98, 10048])
def test_every_platforms_eaddrinuse_is_recognised(errno):
    """EADDRINUSE is 48 on macOS/BSD, 98 on Linux, 10048 on Windows. Matching
    the errno rather than the message is the whole point — the message is
    translated by the OS."""
    src = _read("backend", "main.py")
    match = re.search(r"errno in \(([\d, ]+)\)", src)
    assert match, "errno guard missing from main.py"
    assert str(errno) in {p.strip() for p in match.group(1).split(",")}


def test_uvicorn_swallows_the_bind_error_into_systemexit(tmp_path):
    """The assumption the first version of this fix got wrong.

    `except OSError` around `uvicorn.run()` looks obviously right and is
    inert: uvicorn catches the bind failure inside its own startup, logs the
    raw errno, and raises `SystemExit(1)`. Nothing propagates. This test
    documents that behaviour against the real installed uvicorn, so a future
    refactor back to the "obvious" shape fails here instead of silently
    restoring "Backend died (exit code 1)".
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        script = tmp_path / "naive.py"
        script.write_text(
            "import sys\n"
            "import uvicorn\n"
            "from fastapi import FastAPI\n"
            "try:\n"
            f"    uvicorn.run(FastAPI(), host='127.0.0.1', port={port}, "
            "log_level='critical')\n"
            "except SystemExit as exc:\n"
            "    print('UVICORN_SYSTEMEXIT', exc.code, file=sys.stderr); raise\n"
            "except OSError:\n"
            "    print('OSERROR', file=sys.stderr); sys.exit(78)\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert "OSERROR" not in proc.stderr, (
            "uvicorn now propagates the bind OSError — the pre-probe in "
            "main.py can be simplified, but verify before doing so"
        )
        assert proc.returncode != 0, (
            "uvicorn must still turn the bind failure into a process exit"
        )
        assert "UVICORN_SYSTEMEXIT" in proc.stderr
    finally:
        holder.close()


def test_real_bind_conflict_exits_with_the_dedicated_code(tmp_path):
    """End-to-end against the REAL uvicorn: hold a port, run main.py's guard
    shape against it, and confirm the process exits 78 with an actionable
    message — not uvicorn's bare exit 1.

    Reproduces the guard rather than booting the whole backend (a real boot
    downloads models), but drives genuine `uvicorn.run` so the swallowed-
    SystemExit trap above cannot silently reappear.
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        guard = _read("backend", "main.py")
        start = guard.index("    def _port_taken(")
        end = guard.index("    # #1223: uvicorn does NOT")
        body = "\n".join(line[4:] for line in guard[start:end].splitlines())

        script = tmp_path / "guarded.py"
        script.write_text(
            "import logging, socket, sys\n"
            "import uvicorn\n"
            "from fastapi import FastAPI\n"
            f"_EXIT_PORT_IN_USE = {_EXPECTED_EXIT}\n"
            f"_port = {port}\n"
            "app = FastAPI()\n"
            + body
            + "\n"
            "if (_e := _port_taken('127.0.0.1', _port)) is not None:\n"
            "    _fail_port_in_use(_e)\n"
            "try:\n"
            "    uvicorn.run(app, host='127.0.0.1', port=_port, log_level='critical')\n"
            "except SystemExit:\n"
            "    if _port_taken('127.0.0.1', _port) is not None:\n"
            "        _fail_port_in_use(None)\n"
            "    raise\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert proc.returncode == _EXPECTED_EXIT, (
            f"expected exit {_EXPECTED_EXIT}, got {proc.returncode}\n{proc.stderr}"
        )
        assert "already in use" in proc.stderr
    finally:
        holder.close()


def test_the_probe_does_not_false_positive_on_a_free_port(tmp_path):
    """A free port must start normally. The probe uses uvicorn's own socket
    options (SO_REUSEADDR off Windows) precisely so a TIME_WAIT socket uvicorn
    could bind isn't reported as taken."""
    guard = _read("backend", "main.py")
    start = guard.index("    def _port_taken(")
    end = guard.index("    def _fail_port_in_use(")
    body = "\n".join(line[4:] for line in guard[start:end].splitlines())

    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()  # now free (possibly TIME_WAIT)

    script = tmp_path / "probe.py"
    script.write_text(
        "import socket, sys\n" + body + "\n"
        f"print('TAKEN' if _port_taken('127.0.0.1', {port}) is not None else 'FREE')\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert proc.stdout.strip() == "FREE", proc.stderr


def test_a_lost_bind_race_is_explained_even_if_the_port_is_free_again(tmp_path):
    """#1364: the same crash, still unexplained, when the squatter exits too.

    The #1223 guard handles the race by re-probing after uvicorn dies. That
    only helps while the other process is still holding the port. The common
    case is an orphaned backend from the previous session which is *itself*
    shutting down — it releases the port between uvicorn's failed bind and our
    re-probe, the probe reports "free", and the user gets a bare `exit code 1`
    for a crash we had already diagnosed.

    Reported on Windows with the tell-tale ordering: `Application startup
    complete` (uvicorn's lifespan runs before the bind), then
    `[Errno 10048] error while attempting to bind`, then a plain exit 1.

    Simulated deterministically by making both probes report the port free
    while it is genuinely held, which is precisely the state the race leaves
    us in. Fails before the watcher: exit 1, no message.
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        guard = _read("backend", "main.py")
        start = guard.index("    def _port_taken(")
        end = guard.index("    # #1223: uvicorn does NOT")
        body = "\n".join(line[4:] for line in guard[start:end].splitlines())

        script = tmp_path / "raced.py"
        script.write_text(
            "import logging, socket, sys\n"
            "import uvicorn\n"
            "from fastapi import FastAPI\n"
            f"_EXIT_PORT_IN_USE = {_EXPECTED_EXIT}\n"
            f"_port = {port}\n"
            "app = FastAPI()\n"
            + body
            + "\n"
            # Both probes lie: the port looks free, exactly as it does when the
            # process that held it has since exited.
            "_port_taken = lambda *a, **k: None\n"
            "_watcher = _BindErrorWatcher()\n"
            "logging.getLogger('uvicorn.error').addFilter(_watcher)\n"
            "try:\n"
            "    uvicorn.run(app, host='127.0.0.1', port=_port)\n"
            "except SystemExit:\n"
            "    if _watcher.bind_error is not None:\n"
            "        _fail_port_in_use(_watcher.bind_error)\n"
            "    if _port_taken('127.0.0.1', _port) is not None:\n"
            "        _fail_port_in_use(None)\n"
            "    raise\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert proc.returncode == _EXPECTED_EXIT, (
            f"a lost bind race still exits {proc.returncode} with no explanation; "
            f"expected {_EXPECTED_EXIT}\n{proc.stderr}"
        )
        # Our wording, not uvicorn's. `already in use` is also what macOS/Linux
        # strerror puts in uvicorn's own log line, so asserting on that would
        # pass against the unfixed build -- and would be the exact
        # locale-dependent match #1223 exists to avoid.
        assert "FATAL: port" in proc.stderr and "orphaned backend" in proc.stderr
    finally:
        holder.close()


def test_the_watcher_ignores_unrelated_errors(tmp_path):
    """It must not turn every logged OSError into "port in use" — a permission
    failure or an unreachable bind host is a different problem with different
    advice."""
    guard = _read("backend", "main.py")
    start = guard.index("    class _BindErrorWatcher(")
    end = guard.index("    # #1223: uvicorn does NOT")
    body = "\n".join(line[4:] for line in guard[start:end].splitlines())

    script = tmp_path / "watcher.py"
    script.write_text(
        "import errno, logging\n" + body + "\n"
        "w = _BindErrorWatcher()\n"
        "log = logging.getLogger('probe'); log.addFilter(w)\n"
        "log.error(OSError(errno.EACCES, 'permission denied'))\n"
        "log.error(OSError(errno.ECONNREFUSED, 'refused'))\n"
        "log.error('a plain string message')\n"
        "print('CLEAN' if w.bind_error is None else 'FALSE_POSITIVE')\n"
        "log.error(OSError(98, 'address already in use'))\n"
        "print('CAUGHT' if w.bind_error is not None else 'MISSED')\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert proc.stdout.split() == ["CLEAN", "CAUGHT"], (proc.stdout, proc.stderr)


# ── the properties the watcher depends on, measured not assumed ───────────

def test_uvicorn_logging_config_preserves_filters(tmp_path):
    """greptile on #1370 argued uvicorn's logging setup removes the filter,
    which would make the whole mechanism inert.

    It does not: `dictConfig` replaces a logger's HANDLERS and leaves its
    FILTERS alone. Measured here against the installed uvicorn so a future
    version that *does* start clearing filters fails loudly, rather than
    silently restoring the unexplained exit 1.
    """
    script = tmp_path / "filters.py"
    script.write_text(
        "import logging\n"
        "from uvicorn.config import Config\n"
        "class F(logging.Filter):\n"
        "    def filter(self, r): return True\n"
        "log = logging.getLogger('uvicorn.error')\n"
        "f = F(); log.addFilter(f)\n"
        "Config(app=None).configure_logging()\n"
        "print('KEPT' if f in log.filters else 'DROPPED')\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert proc.stdout.strip() == "KEPT", (
        "uvicorn now drops filters when it configures logging — the bind "
        f"watcher in main.py is inert.\n{proc.stdout}{proc.stderr}"
    )


def test_uvicorn_owns_the_logger_level(tmp_path):
    """The other half, and the reason main.py must not set `log_level`.

    uvicorn resets `uvicorn.error`'s level from its config during startup —
    after any level we set. A level above ERROR drops the bind record before
    filters run, so the watcher would go blind. Documenting the behaviour is
    what makes the next test's rule non-arbitrary.
    """
    script = tmp_path / "level.py"
    script.write_text(
        "import logging\n"
        "from uvicorn.config import Config\n"
        "log = logging.getLogger('uvicorn.error')\n"
        "log.setLevel(logging.ERROR)\n"
        "Config(app=None, log_level='critical').configure_logging()\n"
        "print('OVERRIDDEN' if log.level > logging.ERROR else 'PRESERVED')\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert proc.stdout.strip() == "OVERRIDDEN", (
        "uvicorn no longer overrides the logger level, so main.py could set it "
        "defensively again — verify before relying on that"
    )


def test_main_does_not_raise_the_uvicorn_log_level():
    """Given the two facts above, this is the actual precondition of the fix:
    `log_level` must stay at or below ERROR so the bind record reaches the
    filter. Someone quietening the backend later would otherwise silently
    disarm the #1364 diagnosis.

    AST rather than a regex (CodeRabbit): a pattern that only recognises string
    literals silently passes on `log_level=settings.level` or any other
    computed value — the check would look present and verify nothing, which is
    the same class of bug as the pin in #1357 that did not apply.
    """
    import ast

    src = _read("backend", "main.py")
    tree = ast.parse(src)

    # Scope to the GUARDED serve call: the one after the watcher is attached.
    # main.py has a second uvicorn.run() on the --health-check smoke path which
    # sets log_level="warning" (below ERROR, irrelevant here).
    attach = next(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "addFilter"
    )
    guarded = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
        and getattr(n.func.value, "id", None) == "uvicorn"
        and n.lineno > attach
    ]
    assert guarded, "the guarded uvicorn.run() call was not found after the watcher"

    _ALLOWED = {"debug", "info", "warning", "error"}
    for call in guarded:
        for kw in call.keywords:
            if kw.arg != "log_level":
                continue
            assert isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str), (
                "the guarded uvicorn.run() computes log_level, so this test "
                "cannot verify it stays at or below ERROR — the #1364 watcher "
                "would go blind with no warning. Use a literal."
            )
            assert kw.value.value.lower() in _ALLOWED, (
                f"the guarded uvicorn.run() sets log_level={kw.value.value!r}, "
                f"which suppresses the ERROR record the #1364 watcher reads"
            )


def test_main_actually_wires_the_watcher():
    """The end-to-end tests above rebuild the guard from extracted source, so
    they would still pass if main.py stopped installing the filter or stopped
    consulting it (CodeRabbit). Pin the production wiring itself."""
    src = _read("backend", "main.py")
    assert "class _BindErrorWatcher(" in src
    assert 'logging.getLogger("uvicorn.error").addFilter(' in src, (
        "the watcher is defined but never attached"
    )
    assert "_watcher.bind_error is not None" in src, (
        "the watcher is attached but never consulted, so a lost race still "
        "exits 1 with no explanation"
    )
