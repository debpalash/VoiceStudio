"""No analytics key is committed.

The analytics destination is a public Supermetry site id in
``config/analytics.json``; it only names where opted-in events go. Anything
that grants access (a Supermetry dashboard/API key ``pnl_...``, a PostHog
project or personal key) must never be committed, and the clients take no
token from the build environment.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SECRET_RE = re.compile(r"\b(?:pnl|phc|phx)_[A-Za-z0-9]{20,}")
_SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", ".venv", "zig-out"}
_SCAN_EXT = {".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".json", ".yml", ".yaml", ".md", ".env", ".mjs"}


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    for name in (n for n in out.split("\0") if n):
        p = Path(name)
        if any(part in _SKIP_DIRS for part in p.parts) or p.suffix.lower() not in _SCAN_EXT:
            continue
        yield name, _REPO / p


def test_no_analytics_key_literal_is_committed():
    offenders = []
    for name, path in _tracked_files():
        if name in {"tests/test_no_committed_analytics_token.py", "tests/test_gitleaks_allowlist.py"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _SECRET_RE.search(text):
            offenders.append(name)
    assert not offenders, "An analytics key literal is committed in: " + ", ".join(offenders)


def test_the_destination_is_a_public_site_id_only():
    config = json.loads((_REPO / "config/analytics.json").read_text(encoding="utf-8"))
    assert set(config) == {"site", "host", "appUrl"}
    assert re.fullmatch(r"[a-z0-9]{6,32}", config["site"])
    assert config["host"] == "https://app.supermetry.com"
    assert config["appUrl"].startswith("https://")
