"""Code provenance guard for the explicitly selected bundled-runtime distribution.

The default distribution does not install a hook. Model data may live in the
user's cache; executable modules and subprocesses must come from the signed
resources or the operating system. This module is also staged as sitecustomize
so child Python interpreters retain the same boundary.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

_installed = False
_SYSTEM_ROOTS = (Path("/System/Library"), Path("/usr/lib"))
_SYSTEM_COMMANDS = frozenset({
    "/usr/bin/uname", "/usr/bin/sw_vers", "/usr/sbin/sysctl", "/usr/bin/which",
    "/usr/bin/open", "/usr/bin/osascript", "/usr/bin/security", "/usr/bin/afplay", "/usr/bin/file",
    "/usr/bin/xattr", "/usr/sbin/ioreg",
})
_INSTALLER_MODULES = frozenset({"pip", "ensurepip", "venv", "uv"})


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root)


def install() -> bool:
    """Return whether the process-wide policy is active, including repeat calls."""
    global _installed
    if _installed:
        return _installed
    if os.environ.get("VOICESTUDIO_BUNDLED_CODE_ONLY") != "1":
        return False
    raw = os.environ.get("VOICESTUDIO_BUNDLED_RESOURCES", "")
    if not raw or not Path(raw).is_absolute():
        raise SystemExit("BUNDLED_CODE_POLICY: missing signed resource directory")
    root = Path(raw).resolve()

    def audit(event: str, args: tuple) -> None:
        if event == "exec":
            filename = getattr(args[0], "co_filename", "")
            path = Path(filename)
            if path.is_absolute() and not _inside(path, root):
                raise RuntimeError("BUNDLED_CODE_POLICY: Python source is outside signed resources")
        elif event == "import" and len(args) > 1 and args[1]:
            path = Path(args[1])
            if path.is_absolute() and not _inside(path, root):
                raise RuntimeError("BUNDLED_CODE_POLICY: module is outside signed resources")
        elif event == "ctypes.dlopen" and args and args[0]:
            path = Path(args[0])
            if path.is_absolute() and not (
                _inside(path, root) or any(_inside(path, p) for p in _SYSTEM_ROOTS)
            ):
                raise RuntimeError("BUNDLED_CODE_POLICY: native library is outside signed resources")
        elif event == "os.system":
            raise RuntimeError("BUNDLED_CODE_POLICY: shell execution is not bundled executable code")
        elif event in {"subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn"}:
            offset = 1 if event == "os.spawn" else 0
            executable = os.fsdecode(args[offset])
            resolved = shutil.which(executable) if not Path(executable).is_absolute() else executable
            if not resolved or not (_inside(Path(resolved), root) or resolved in _SYSTEM_COMMANDS):
                raise RuntimeError("BUNDLED_CODE_POLICY: executable is outside signed resources")
            argv = args[offset + 1]
            if isinstance(argv, (list, tuple)):
                words = [os.fsdecode(word) for word in argv]
                if "-S" in words or any(
                    word == "-m" and index + 1 < len(words) and words[index + 1] in _INSTALLER_MODULES
                    for index, word in enumerate(words)
                ):
                    raise RuntimeError("BUNDLED_CODE_POLICY: executable installation is store-managed")

    sys.addaudithook(audit)
    _installed = True
    return _installed
