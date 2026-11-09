#!/usr/bin/env python3
"""Refuse a pull request that moves the ``omnivoice-gallery`` pin backwards.

A commit made in a checkout whose submodule was never updated records the
stale commit as the new pin: #3447, a docs-only change, silently reverted the
gallery from the #3438 bump to the original seed commit. Nothing failed, because
nothing looked. This looks:

- the new pin must be on the gallery's ``main`` (no unmerged branch commits);
- it must descend from the old pin (forward only — a deliberate rollback is a
  revert in the gallery repo, not a stale pointer here).

    python .github/scripts/gallery_pin.py <old-sha> <new-sha> <gallery-git-dir>

``gallery-git-dir`` is a clone of the gallery with ``main`` fetched.
"""
from __future__ import annotations

import subprocess
import sys


def _git(repo: str, *args: str) -> int:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True).returncode


def check(old: str, new: str, repo: str, main_ref: str = "origin/main") -> str | None:
    """Return why the move is refused, or ``None`` when it is fine."""
    if old == new:
        return None
    if _git(repo, "cat-file", "-e", f"{new}^{{commit}}") != 0:
        return f"the new pin {new} is not a commit in the gallery repository"
    if _git(repo, "merge-base", "--is-ancestor", new, main_ref) != 0:
        return f"the new pin {new} is not on the gallery's main branch"
    if _git(repo, "merge-base", "--is-ancestor", old, new) != 0:
        return (
            f"the pin moves backwards ({old[:9]} -> {new[:9]}). This is usually a stale "
            "submodule committed by accident: run `git submodule update omnivoice-gallery` "
            "and drop the pointer change, or bump it to the gallery's current main."
        )
    return None


def main(argv: list[str]) -> int:
    old, new, repo = argv[1:4]
    problem = check(old, new, repo)
    if problem:
        print(f"::error file=omnivoice-gallery::{problem}")
        return 1
    print(f"omnivoice-gallery pin {old[:9]} -> {new[:9]}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
