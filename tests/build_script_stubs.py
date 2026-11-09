"""Run ``scripts/build-omnivoice-tts.sh`` against stub tools on any host (#3549).

The GGUF build tests replace git/cmake with tiny Bash stubs. Three host
details used to leak in on Windows (Git Bash):

* ``Path.write_text`` writes CRLF there, so a stub's ``set -euo pipefail\\r``
  fails before it does anything — and a CRLF checkout breaks the copied script;
* a ``PATH`` of ``C:\\...\\fake-bin:/usr/bin:/bin`` is not a POSIX path list, so
  the stubs vanish from ``PATH`` and the real git/cmake run instead;
* nothing proved which tool the script actually invoked.

``run_build`` writes every file with LF endings, sets ``PATH`` inside Bash from
the stub directory (converted with ``cygpath`` where it exists) and refuses to
start the script unless each declared tool resolves to its stub.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
WRONG_TOOL = 97

_LAUNCHER = r"""set -euo pipefail
bin="$(cygpath -u "$MOCK_BIN" 2>/dev/null || printf '%s' "$MOCK_BIN")"
export PATH="$bin:/usr/bin:/bin"
for tool in $MOCK_TOOLS; do
    found="$(command -v "$tool" || true)"
    if [[ "$found" != "$bin/$tool" ]]; then
        echo "mock $tool resolves to '${found:-nothing}', not $bin/$tool" >&2
        exit 97
    fi
done
exec bash "$@"
"""


def require_bash() -> str:
    if not BASH or not Path(BASH).is_file():
        pytest.skip("POSIX bash required for the build script")
    return BASH


def write_lf(path: Path, text: str, *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    if executable:
        path.chmod(0o755)
    return path


def stage_build_script(root: Path) -> None:
    """Copy the build script and its libraries with LF line endings."""
    sources = [REPO / "scripts/build-omnivoice-tts.sh", *sorted((REPO / "scripts/lib").rglob("*"))]
    for source in sources:
        if source.is_file():
            target = root / "scripts" / source.relative_to(REPO / "scripts")
            write_lf(target, source.read_text(encoding="utf-8"), executable=True)


def write_tools(fake: Path, tools: dict[str, str]) -> None:
    for name, body in tools.items():
        write_lf(fake / name, "#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n", executable=True)


def run_bash(root: Path, args: list[str], *, tools: dict[str, str], expect: tuple[str, ...] = (),
             env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run ``bash <args>`` in ``root`` with only ``tools`` stubbed on ``PATH``.

    ``expect`` names tools that must resolve to a stub (defaults to ``tools``);
    any other resolution exits ``WRONG_TOOL`` before the script starts.
    """
    bash = require_bash()
    fake = root / "fake-bin"
    fake.mkdir(exist_ok=True)
    write_tools(fake, tools)
    launcher = write_lf(root / "mock-launcher.sh", _LAUNCHER)
    full_env = {**os.environ, **(env or {}), "MOCK_BIN": str(fake),
                "MOCK_TOOLS": " ".join(expect or tools)}
    full_env.pop("VULKAN_SDK", None)
    return subprocess.run([bash, launcher.name, *args], env=full_env, cwd=root,
                          capture_output=True, text=True, timeout=timeout, check=False)


def run_build(root: Path, platform: str, *, tools: dict[str, str], **kwargs) -> subprocess.CompletedProcess:
    stage_build_script(root)
    return run_bash(root, ["scripts/build-omnivoice-tts.sh", "--platform", platform,
                           "--commit-sha", "a" * 40], tools=tools, **kwargs)
