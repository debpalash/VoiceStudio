"""The analytics token lives in exactly two canonical files — nowhere else.

History: gitleaks once caught a hardcoded PostHog key in analytics.ts, and the
repo banned token-shaped literals outright (build-time injection only). Owner
reversal #1193: source builds now ship the same consent-gated analytics as
installers, so the *publishable* project token (write-only event ingestion, not
data access — PostHog's client keys are designed to ship in client code) is
committed as an in-repo default in the two files that implement analytics:

    backend/core/analytics.py        (_PUBLIC_PROJECT_TOKEN)
    frontend/src/utils/analytics.ts  (PUBLIC_PROJECT_TOKEN)

This guard pins the new contract: a `phc_` literal may exist ONLY there, both
files must actually carry one, and the two must be the SAME token (one PostHog
project — a drifted pair would silently split the event stream). The build-time
override chain (env / baked secret beats the in-repo default) stays pinned
below. Same file-scanning idiom as test_no_hardcoded_cjk / test_no_literal_borders.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# A PostHog project key. Deliberately matched by SHAPE, not by the specific value,
# so a key can't slip into a *different* file unnoticed.
_POSTHOG_KEY_RE = re.compile(r"phc_[A-Za-z0-9]{20,}")

# The ONLY tracked files allowed to contain a `phc_` literal (#1193).
_CANONICAL_TOKEN_FILES = (
    "backend/core/analytics.py",
    "frontend/src/utils/analytics.ts",
)

_SKIP_DIRS = {"node_modules", ".git", "target", "dist", "build", ".venv", "zig-out"}
_SCAN_EXT = {".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".json", ".yml", ".yaml", ".md", ".env"}


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    for name in (n for n in out.split("\0") if n):
        p = Path(name)
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in _SCAN_EXT:
            yield name, _REPO / p


def test_posthog_token_literals_only_in_the_two_canonical_files():
    offenders = []
    for name, path in _tracked_files():
        # This guard describes the pattern it forbids, so exempt itself.
        if name == "tests/test_no_committed_analytics_token.py":
            continue
        if name in _CANONICAL_TOKEN_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _POSTHOG_KEY_RE.search(text):
            offenders.append(name)

    assert not offenders, (
        "A PostHog token literal is committed outside the canonical files ("
        + ", ".join(_CANONICAL_TOKEN_FILES)
        + "): "
        + ", ".join(offenders)
        + ". The in-repo default lives ONLY there (#1193); everything else takes "
        "the token via VITE_POSTHOG_KEY / POSTHOG_PROJECT_TOKEN at build/run time."
    )


def test_both_canonical_files_carry_the_same_default_token():
    """#1193's whole point: source builds have a destination. Both halves must
    ship the in-repo default, and it must be ONE token — a mismatched pair would
    split installs across PostHog projects with no error anywhere."""
    tokens = {}
    for name in _CANONICAL_TOKEN_FILES:
        found = _POSTHOG_KEY_RE.findall((_REPO / name).read_text(encoding="utf-8"))
        assert found, f"{name} no longer carries the in-repo default token (#1193)"
        assert len(set(found)) == 1, f"{name} contains multiple distinct phc_ literals"
        tokens[name] = found[0]
    assert len(set(tokens.values())) == 1, f"canonical token files disagree: {tokens}"


# ── the OVERRIDE chain must stay wired ───────────────────────────────────────
#
# The in-repo default is the fallback; release builds override it with the repo
# secret, and a developer's process env overrides everything:
#
#     repo secret -> release.yml -> tauri-action -> option_env! in backend.rs
#                 -> spawned backend process env -> analytics._resolved_token()
#
# Every link is invisible when it breaks (events just land in the default
# project instead of the release one). These pin the links that live in files a
# future change could quietly drop.


def test_the_frontend_reads_its_token_override_from_the_build_env():
    """The build-time override mechanism must stay in place."""
    src = (_REPO / "frontend/src/utils/analytics.ts").read_text(encoding="utf-8")
    assert "VITE_POSTHOG_KEY" in src


def test_release_workflow_still_passes_the_secret_to_the_build():
    wf = (_REPO / ".github/workflows/electron-release.yml").read_text(encoding="utf-8")
    assert "VITE_POSTHOG_KEY" in wf, "the build no longer receives the analytics token override"
    assert "secrets.POSTHOG_PROJECT_TOKEN" in wf, "the override must come from the repo secret"


def test_the_shell_hands_the_token_to_the_backend_it_spawns():
    """Without this a baked release token could never beat the in-repo default."""
    src = (_REPO / "electron/src/main/backend.ts").read_text(encoding="utf-8")
    assert "__POSTHOG_PROJECT_TOKEN__" in src, "the shell no longer bakes in the token"
    assert "POSTHOG_PROJECT_TOKEN" in src, "the backend process is no longer given the override"
    # #1193: the shell marks everything it spawns as the "installer" channel.

    # option_env! is resolved at COMPILE time, so cargo must rebuild when the
    # secret changes — otherwise a cached build keeps the token it first saw.
    config = (_REPO / "electron/electron.vite.config.ts").read_text(encoding="utf-8")
    assert "VITE_POSTHOG_KEY" in config
