"""No analytics key is committed.

The analytics destination is a public Supermetry site id in
``config/analytics.json``; it only names where opted-in events go. Anything
that grants access (a Supermetry dashboard/API key ``pnl_...``, a PostHog
project or personal key) must never be committed, and the clients take no
token from the product-analytics build environment. Diagnostics-only app intake
uses a write-only key supplied at build time; that key is never committed.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SECRET_RE = re.compile(r"\b(?:pnl|phc|phx)_[A-Za-z0-9]{20,}")
_INGEST_RE = re.compile(r"smk_[a-z2-7]{39}")
_SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", ".venv", "zig-out"}
_SCAN_EXT = {".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".json", ".yml", ".yaml", ".md", ".env", ".mjs", ".swift", ".kt", ".kts"}


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    for name in (n for n in out.split("\0") if n):
        p = Path(name)
        yield name, _REPO / p


def test_no_analytics_key_literal_is_committed():
    offenders = []
    for name, path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        legacy_fixture = name in {"tests/test_no_committed_analytics_token.py", "tests/test_gitleaks_allowlist.py"}
        legacy_scanned = path.suffix.lower() in _SCAN_EXT and not any(part in _SKIP_DIRS for part in Path(name).parts)
        if _INGEST_RE.search(text) or (legacy_scanned and not legacy_fixture and _SECRET_RE.search(text)):
            offenders.append(name)
    assert not offenders, "An analytics key literal is committed in: " + ", ".join(offenders)


def test_ingest_key_detection_covers_native_and_desktop_keys():
    assert _INGEST_RE.search("smk_" + "a" * 39)
    assert not _INGEST_RE.search("smk_" + "a" * 38)
    assert not _INGEST_RE.search("smk_" + "0" * 39)


def test_the_destination_is_a_public_site_id_only():
    config = json.loads((_REPO / "config/analytics.json").read_text(encoding="utf-8"))
    assert set(config) == {"site", "host", "appUrl"}
    assert re.fullmatch(r"[a-z0-9]{6,32}", config["site"])
    assert config["host"] == "https://app.supermetry.com"
    assert config["appUrl"].startswith("https://")
