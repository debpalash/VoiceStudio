#!/usr/bin/env python3
"""Crash-class recurrence report — the reliability cycle's definition of done.

The project's #1 lifetime failure class is "the backend died or never came
up" (~1 in 5 of every issue ever filed). This script measures whether that
class is actually shrinking, *filtered to reports from a given version* —
because 6 in 10 sampled reports historically came from builds that were
already obsolete when filed, and counting those as recurrence would judge
the work against noise it can't fix.

With --version, reports are bucketed by the live reporter's `**App:**` field
or the historical `**Version:**` field. Explicit beta versions match exactly;
historical numeric previews can still match their base version. Without a
target, only a `**Build status:**` stamp establishes freshness: absent stamps
remain unknown, including reports from current Electron builds.

Usage:
    uv run python scripts/crash_class_report.py            # since latest release
    uv run python scripts/crash_class_report.py --since 2026-08-13
    uv run python scripts/crash_class_report.py --version 0.5.0

Needs the `gh` CLI authenticated for debpalash/VoiceStudio.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

REPO = "debpalash/VoiceStudio"

# One pattern per sub-class, matched against issue TITLES (the auto-reporter
# seeds titles from the error message, so titles are machine-stable).
CRASH_CLASS = {
    "unreachable": re.compile(r"can'?t reach the local .* backend|not answered|stopped responding", re.I),
    "never-started": re.compile(r"still starting|abandoned before ready|failed to start", re.I),
    "backend-died": re.compile(r"backend (died|ended uncleanly)|\[crash\]", re.I),
}

OUTDATED = re.compile(r"\*\*Build status:\*\* OUTDATED")
CURRENT = re.compile(r"\*\*Build status:\*\* current at filing time")
VERSION_LINE = re.compile(r"\*\*Version:\*\* `([^`]+)`")
APP_LINE = re.compile(r"^\*\*App:\*\* VoiceStudio ([^\s`]+)", re.M)


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def latest_release_date() -> str:
    data = json.loads(sh("gh", "release", "view", "--repo", REPO, "--json", "publishedAt,tagName"))
    print(f"Window: since {data['tagName']} ({data['publishedAt'][:10]})", file=sys.stderr)
    return data["publishedAt"][:10]


_FETCH_LIMIT = 500


def fetch_issues(since: str) -> list[dict]:
    out = sh(
        "gh", "issue", "list", "--repo", REPO, "--state", "all",
        "--search", f"created:>={since}", "--limit", str(_FETCH_LIMIT),
        "--json", "number,title,body,createdAt",
    )
    issues = json.loads(out)
    # No silent caps: an understated metric is worse than a loud one.
    if len(issues) >= _FETCH_LIMIT:
        print(
            f"WARNING: hit the {_FETCH_LIMIT}-issue fetch cap — results are "
            "truncated and the metric UNDERSTATES. Narrow --since and merge "
            "the windows.",
            file=sys.stderr,
        )
    return issues


def classify_build(body: str, version: "str | None") -> str:
    """'current' / 'outdated' / 'unknown' for one issue body.

    With a target version, the App/Environment Version field is authoritative: a
    report stamped "current at filing time" during a DIFFERENT version's
    window must not count toward this version's recurrence. Without one, the
    reporter's Build-status stamp decides.
    """
    ver = APP_LINE.search(body) or VERSION_LINE.search(body)
    if version and ver:
        reported = ver.group(1)
        # Preserve the old numeric preview/base convention without folding a
        # named prerelease (beta/RC) into its stable version or another beta.
        legacy_preview = re.fullmatch(r"(\d+\.\d+\.\d+)-\d+", reported)
        matches = reported == version or (legacy_preview is not None and legacy_preview[1] == version)
        return "current" if matches else "outdated"
    if OUTDATED.search(body):
        return "outdated"
    if CURRENT.search(body):
        return "current"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="count issues created on/after this date (default: latest release)")
    ap.add_argument("--version", help="split by the reported App/Version field (beta versions match exactly)")
    args = ap.parse_args()

    since = args.since or latest_release_date()
    issues = fetch_issues(since)

    counts: dict[str, dict[str, int]] = {k: {"current": 0, "outdated": 0, "unknown": 0} for k in CRASH_CLASS}
    matched: list[tuple[int, str, str, str]] = []

    for issue in issues:
        title, body = issue["title"], issue.get("body") or ""
        sub = next((k for k, rx in CRASH_CLASS.items() if rx.search(title)), None)
        if not sub:
            continue
        build = classify_build(body, args.version)
        counts[sub][build] += 1
        matched.append((issue["number"], sub, build, title[:70]))

    total = sum(sum(b.values()) for b in counts.values())
    print(f"\nCrash-class issues created since {since}: {total} of {len(issues)} total issues\n")
    print(f"{'sub-class':<15} {'current':>8} {'outdated':>9} {'unknown':>8}")
    for sub, b in counts.items():
        print(f"{sub:<15} {b['current']:>8} {b['outdated']:>9} {b['unknown']:>8}")
    current_total = sum(b["current"] for b in counts.values())
    print(
        f"\nRecurrence metric (current-version crash-class reports): {current_total}"
        "\n  → this is the number the reliability work is judged on; 'outdated' is"
        "\n    other-version reports; 'unknown' means freshness was not established."
    )
    if matched:
        print("\nMatched issues:")
        for num, sub, build, title in matched:
            print(f"  #{num:<6} [{sub}/{build}] {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
