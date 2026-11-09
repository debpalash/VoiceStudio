"""PowerShell install commands must use the explicit ``/install.ps1`` URL (#2973).

The bare ``/install`` endpoint is not guaranteed to detect PowerShell and can
return the POSIX shell script, so ``irm .../install | iex`` fails on Windows.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BARE = re.compile(r"irm\s+https://voicestudio\.sh/install\s*\|")
# Release history records what older docs said; it is not instructions.
_SKIP = {"CHANGELOG.md"}


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md", "*.mjs", "*.ps1", "*.ts", "*.tsx", "*.json"],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [_ROOT / p for p in out if p not in _SKIP and not p.startswith("changelog.d/")]


def test_no_powershell_command_uses_the_bare_install_url():
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if _BARE.search(line):
                offenders.append(f"{path.relative_to(_ROOT)}:{number}")
    assert not offenders, (
        "use `irm https://voicestudio.sh/install.ps1 | iex` (#2973): " + ", ".join(offenders)
    )
