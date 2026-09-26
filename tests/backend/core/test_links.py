"""Tests for the canonical project repository URL resolver."""
from __future__ import annotations

import importlib
import sys


def _fresh_links_module():
    sys.modules.pop("core.links", None)
    import core.links as links

    return importlib.reload(links)


def test_project_repo_url_is_set():
    links = _fresh_links_module()
    assert links.PROJECT_REPO_URL.startswith("https://github.com/")


def test_project_repo_blob_main_derives_from_url():
    links = _fresh_links_module()
    assert links.PROJECT_REPO_BLOB_MAIN == links.PROJECT_REPO_URL + "/blob/main"


def test_resolves_from_pyproject():
    links = _fresh_links_module()
    assert links._resolve() == links._from_pyproject()
