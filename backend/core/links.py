"""Canonical project repository URL used by backend documentation links."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("omnivoice.core.links")
_THIS = Path(__file__).resolve()


def _find_repo_root() -> Path:
    for ancestor in (_THIS.parent, *_THIS.parents):
        if (ancestor / "pyproject.toml").exists():
            return ancestor
    return _THIS.parent.parent.parent


_PYPROJECT = _find_repo_root() / "pyproject.toml"


def _from_pyproject() -> Optional[str]:
    """Read `[project.urls].Repository` from pyproject.toml."""
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover - repository pins Python 3.11+
            import tomli as tomllib  # type: ignore[no-redef]
        with _PYPROJECT.open("rb") as file:
            data = tomllib.load(file)
        repo = data.get("project", {}).get("urls", {}).get("Repository")
        if isinstance(repo, str) and repo.startswith("https://github.com/"):
            return repo.rstrip("/").removesuffix(".git")
    except Exception:
        logger.debug("links: pyproject.toml read failed", exc_info=True)
    return None


def _resolve() -> str:
    return _from_pyproject() or "https://github.com/debpalash/VoiceStudio"


PROJECT_REPO_URL: str = _resolve()
PROJECT_REPO_BLOB_MAIN: str = f"{PROJECT_REPO_URL}/blob/main"
