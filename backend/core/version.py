"""Single source of truth for the app version at runtime.

Read from the installed package metadata (driven by ``pyproject.toml``) so the
FastAPI/API version and exported-bundle metadata never drift to a stale literal
— the prior "0.4.0" / "0.2.7" bug, and the v0.3.6 desktop build that reported
"0.3.5" because the *frozen* backend couldn't read its own metadata.

Resolution order:
  1. installed package metadata — correct in any ``uv sync``'d env;
  2. ``pyproject.toml`` walked up from this file — correct for a raw source
     checkout that was never installed;
  3. ``_FALLBACK_VERSION`` — a last resort, kept in lockstep with the maintained
     version files by ``tests/test_app_version.py`` so it can never silently
     drift again.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Last-resort literal. Guarded by
# tests/test_app_version.py::test_all_version_files_in_lockstep and bumped by
# the owner-approved release bump, so it stays equal to
# pyproject.toml and the root package.json.
_FALLBACK_VERSION = "0.5.8"


def _fallback_version() -> str:
    """Version for contexts where package metadata is unavailable."""
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            # TOML is UTF-8 by definition. A bare read_text() decodes in the
            # locale code page instead, and pyproject.toml's em dashes are
            # undecodable in cp932/cp936/cp949/cp950 — so on a Chinese,
            # Japanese or Korean Windows this raised UnicodeDecodeError while
            # `core.version` was still importing, and the backend never started.
            match = re.search(
                r'(?m)^version\s*=\s*"([^"]+)"',
                pyproject.read_text(encoding="utf-8"),
            )
            if match:
                return match.group(1)
    return _FALLBACK_VERSION


try:
    APP_VERSION = version("omnivoice")
except PackageNotFoundError:  # frozen build w/o metadata, or non-installed checkout
    APP_VERSION = _fallback_version()


_COMMIT = re.compile(r"[0-9a-f]{7,40}", re.IGNORECASE)


@lru_cache(maxsize=1)
def build_commit() -> str:
    """The commit this backend was built from (9 characters), or ``"unknown"``.

    A version string cannot tell two builds of one version apart, so bug reports
    carry this too. ``OMNIVOICE_BUILD_COMMIT`` is set by the desktop app and the
    Docker image; a source checkout falls back to its own git HEAD. Only hex
    commit ids are accepted, so a stray value never reaches a report.
    """
    candidates = [os.environ.get("OMNIVOICE_BUILD_COMMIT", "")]
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        try:
            candidates.append(
                subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                    text=True, errors="replace", timeout=5, check=False,
                ).stdout
            )
        except (OSError, subprocess.SubprocessError):
            pass
    for candidate in candidates:
        value = candidate.strip()
        if _COMMIT.fullmatch(value):
            return value[:9].lower()
    return "unknown"
