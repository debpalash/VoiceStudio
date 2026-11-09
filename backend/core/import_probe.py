"""Check that a package imports, without importing it into this process (#3924).

Engine availability probes used to ``import`` their package to prove it loads:
a native library that a hardened kernel, a newer glibc or a broken bundle
refuses (#692, Wave 4.4) raises at import time, and the engine must then be
reported unavailable rather than chosen. But importing ``mlx_whisper`` or
``faster_whisper`` costs about a second and 50-120 MB that the API process then
keeps for its whole life, even when no speech-recognition model is installed.

:func:`ensure_importable` keeps the proof and drops the cost. A module that is
already imported answers at once; one that is not installed fails at once via
``importlib.util.find_spec``; otherwise a short-lived child of the same
interpreter, with this process's ``sys.path`` and environment, does the import
and reports the outcome. A failure is re-raised here as the same exception
type and message the in-process import would have raised, so callers keep
their ``try: ... except`` blocks and their user-facing reasons unchanged. The
answer is cached per installed copy of the package (a failure only for a
minute, so a fix made while the app runs is picked up), so each package costs
one child per backend run; where no child can be used (frozen bundles, a spawn
failure, a timeout) the import runs in-process as before.
"""
from __future__ import annotations

import builtins
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

#: Cold imports of large packages on a slow disk have taken tens of seconds.
_TIMEOUT_S = 180.0
#: A failed import is re-checked after this long, so a fix made while the app
#: runs (``patchelf``, a system library) is seen without a restart, as it was
#: when every probe re-ran the import. A working import is cached for the run.
_FAILURE_TTL_S = 60.0
_MARKER = "__voicestudio_import_probe__"

_CHILD = r"""
import json, os, sys
marker, paths, module, prepare = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
sys.path[:] = paths
try:
    if prepare:
        import importlib
        owner, _, func = prepare.partition(":")
        getattr(importlib.import_module(owner), func)()
    __import__(module)
except BaseException as exc:
    out = {"ok": False, "type": type(exc).__name__, "message": str(exc),
           "name": getattr(exc, "name", None),
           "mro": [c.__module__ + "." + c.__qualname__ for c in type(exc).__mro__]}
else:
    out = {"ok": True}
# fd 1 directly: works even where sys.stdout is None or was replaced on import.
os.write(1, ("\n" + marker + json.dumps(out) + "\n").encode())
"""

# Held by the interpreter, not this module: a verdict describes installed
# files, so it stays true if this module is imported again (test suites purge
# ``core.*`` between tests, which would otherwise re-spawn every child).
_cache: dict[tuple, tuple[dict, float]] = sys.__dict__.setdefault(
    "_voicestudio_import_probe_cache", {}
)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ImportProbeError(Exception):
    """An import failed in the probe child with a non-builtin exception type."""


def _not_installed(module: str) -> ModuleNotFoundError:
    """What ``import module`` raises when its top-level package is missing."""
    top = module.partition(".")[0]
    if top in sys.modules and sys.modules[top] is None:
        return ModuleNotFoundError(f"import of {top} halted; None in sys.modules", name=top)
    return ModuleNotFoundError(f"No module named {top!r}", name=top)


def _cache_key(module: str):
    """Identity of the installed copy, or ``None`` when it is not installed.

    Looks up the top-level package only: ``find_spec`` on a dotted name imports
    its parents, which is the cost this module exists to avoid.
    """
    top = module.partition(".")[0]
    if top in sys.modules and sys.modules[top] is None:
        return None
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    origin = spec.origin or ""
    try:
        stamp = os.stat(origin).st_mtime_ns if origin and os.path.exists(origin) else 0
    except OSError:
        stamp = 0
    return (module, origin, stamp)


def _run_child(module: str, prepare: str) -> dict | None:
    """The child's verdict, or ``None`` when no verdict could be had."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD, _MARKER, json.dumps([str(p) for p in sys.path]), module, prepare],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("import probe for %s could not run a child (%s)", module, exc)
        return None
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_MARKER):
            try:
                return json.loads(line[len(_MARKER):])
            except ValueError:
                break
    # No verdict: the import killed the interpreter (a native crash). In-process
    # this would have taken the whole backend down, so it is a failure.
    return {
        "ok": False,
        "type": "RuntimeError",
        "message": f"importing {module} crashed the interpreter (exit code {proc.returncode})",
        "name": None,
    }


def _builtin_type(verdict: dict) -> type | None:
    """The nearest builtin exception class in the child's exception MRO.

    A third-party subclass of ``ImportError``/``OSError``/``RuntimeError`` is
    re-raised as that builtin, so callers' ``except`` clauses keep matching.
    The bare ``Exception`` base does not count: it would match nothing useful.
    """
    mro = verdict.get("mro") or [f"builtins.{verdict.get('type') or ''}"]
    for i, qualified in enumerate(mro):
        module, _, name = str(qualified).rpartition(".")
        if module != "builtins":
            continue
        exc_type = getattr(builtins, name, None)
        if not (isinstance(exc_type, type) and issubclass(exc_type, Exception)):
            continue
        if exc_type is Exception and i > 0:
            continue
        return exc_type
    return None


def _raise(verdict: dict, module: str) -> None:
    kind, message = verdict.get("type") or "", verdict.get("message") or ""
    exc_type = _builtin_type(verdict)
    if exc_type is not None and issubclass(exc_type, ImportError):
        raise exc_type(message, name=verdict.get("name") or module)
    if exc_type is not None:
        try:
            exc = exc_type(message)
        except Exception:  # noqa: BLE001 - a builtin needing other arguments
            exc = None
        if exc is not None:
            raise exc
    raise ImportProbeError(f"{kind}: {message}" if kind else message)


def ensure_importable(module: str, *, prepare: str = "") -> None:
    """Raise what ``import module`` would raise, without importing it here.

    ``prepare`` names a ``"package.module:function"`` the child calls before
    the import, for packages that need set-up in the importing process (for
    example Windows DLL search paths).
    """
    if module in sys.modules:
        if sys.modules[module] is None:
            raise ModuleNotFoundError(
                f"import of {module} halted; None in sys.modules", name=module
            )
        return
    if getattr(sys, "frozen", False):
        _import_here(module, prepare)
        return
    key = _cache_key(module)
    if key is None:
        raise _not_installed(module)
    with _locks_guard:
        lock = _locks.setdefault(module, threading.Lock())
    with lock:
        verdict, checked = _cache.get(key, (None, 0.0))
        if verdict is None or (
            not verdict.get("ok") and time.monotonic() - checked > _FAILURE_TTL_S
        ):
            verdict = _run_child(module, prepare)
            if verdict is None:
                _import_here(module, prepare)
                return
            _cache[key] = (verdict, time.monotonic())
    if not verdict.get("ok"):
        _raise(verdict, module)


def _import_here(module: str, prepare: str) -> None:
    if prepare:
        owner, _, func = prepare.partition(":")
        getattr(importlib.import_module(owner), func)()
    importlib.import_module(module)


def clear_cache() -> None:
    """Forget every verdict (tests, and after an in-app repair)."""
    _cache.clear()
