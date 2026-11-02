"""INST-01 no-regression guard.

PR #62 pinned ``setuptools>=75.0`` in ``[project.dependencies]`` so that
WhisperX / faster-whisper can still `import pkg_resources` on Python 3.12+.
This test fails fast at PR time if anyone removes or weakens the pin.

The user-observable counterpart ("`uv sync` on Python 3.12 imports WhisperX
without ModuleNotFoundError: No module named 'pkg_resources'") is covered by
Phase 0 GATE-02's Python-runtime smoke in `ci.yml`. This unit test is the
cheap PR-time canary.
"""
import re
import sys
from pathlib import Path

import pytest

# tomllib is stdlib on Python 3.11+; we require >=3.11 in pyproject so this
# import is safe everywhere we run.
import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_setuptools_pinned():
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    matches = [d for d in deps if d.startswith("setuptools")]
    assert matches, (
        "INST-01 regression: setuptools must be listed in "
        "[project.dependencies] (PR #62)."
    )
    # Extract numeric lower bound from any `setuptools>=X.Y[.Z]` spec.
    pinned = False
    for spec in matches:
        m = re.search(r"setuptools\s*>=\s*(\d+)(?:\.(\d+))?", spec)
        if m:
            major = int(m.group(1))
            if major >= 75:
                pinned = True
                break
    assert pinned, (
        f"INST-01 regression: setuptools must be pinned >=75.0 "
        f"(found: {matches!r})."
    )
