"""Single source of truth for the app version at runtime.

A source checkout declares its version in ``pyproject.toml``; an installed wheel
has no ``pyproject.toml`` and carries it in package metadata. The file is checked
first because metadata is a cache: a venv that was not re-synced after a bump
still reports the old version, which is how the v0.3.6 desktop build came to
report "0.3.5". The old order let that stale value win whenever the package
happened to be installed.

Resolution order:
  1. ``pyproject.toml`` walked up from this file - the checkout is authoritative;
  2. installed package metadata - correct for a wheel or a frozen build;
  3. ``_FALLBACK_VERSION`` - a last resort, kept in lockstep with the maintained
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
_FALLBACK_VERSION = "0.5.9-beta.6"


def _source_version() -> str | None:
    """The version this checkout declares, when a ``pyproject.toml`` is reachable."""
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            # TOML is UTF-8 by definition. A bare read_text() decodes in the
            # locale code page instead, and pyproject.toml carries characters
            # that are undecodable in cp932/cp936/cp949/cp950, so on a Chinese,
            # Japanese or Korean Windows this raised UnicodeDecodeError while
            # `core.version` was importing and the backend never started.
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            # An unreadable or mis-encoded manifest must not stop the import:
            # fall through to the installed metadata.
            continue
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        if match:
            return match.group(1)
    return None


def _installed_version() -> str | None:
    try:
        return version("omnivoice")
    except PackageNotFoundError:  # frozen build w/o metadata, or non-installed checkout
        return None


def _resolve_version() -> str:
    """Checkout file first, then the installed metadata cache, then the literal."""
    return _source_version() or _installed_version() or _FALLBACK_VERSION


def _fallback_version() -> str:
    """Version for contexts where package metadata is unavailable."""
    return _source_version() or _FALLBACK_VERSION


APP_VERSION = _resolve_version()


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
