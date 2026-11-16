#!/usr/bin/env python3
"""Cancel workflow runs that belong to a pull request that is already closed.

A merged or closed PR keeps queued and in-progress CI runs that nobody will read
and that hold runner slots other PRs are waiting for. Two modes:

* ``--pr N``: cancel the unfinished pull-request runs of one closed PR. Runs the
  close event itself started (CLA recording, for example) are left alone: only
  runs created before the PR was closed are cancelled.
* ``--sweep``: do that for every unfinished pull-request run in the repository
  whose PR is closed (a manual catch-up, or a scheduled safety net).

Only ``pull_request`` and ``pull_request_target`` runs are touched, never pushes,
schedules or releases. A PR that is still open is never touched. ``--dry-run``
prints what would be cancelled.

Security: this is run from the base branch on ``pull_request_target``. It reads
the PR head commit from the API only to look runs up; no PR code is checked out
or executed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
UNFINISHED = ("queued", "in_progress", "waiting", "pending", "requested")
PR_EVENTS = {"pull_request", "pull_request_target"}
PER_PAGE = 100
# GitHub stops a status-filtered run search at this many results, no matter how
# many pages are requested.
SEARCH_CAP = 1000
# How far back a capped status is swept, one day at a time. Older unfinished runs
# are outside the sweep's scope by design; the warning says when the cap was hit.
LOOKBACK_DAYS = 30
# The day-by-day re-sweep of a capped status is a lot of requests, and the job has
# a timeout: cap the windows it will walk and say so, rather than collecting for
# minutes and reaching the cancel loop with nothing left.
MAX_WINDOW_REQUESTS = 45


def api(method: str, path: str, token: str, data: dict | None = None):
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "voicestudio-cancel-stale-runs",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 409:  # already finished or being cancelled
            return None
        raise
    return json.loads(body) if body else None


def _pages(call, repo: str, params: dict) -> list[dict]:
    """Every run a single search returns, page by page, up to the API's cap."""
    runs: list[dict] = []
    page = 1
    while True:
        query = "&".join(
            f"{k}={v}" for k, v in {**params, "per_page": PER_PAGE, "page": page}.items()
        )
        batch = (call("GET", f"/repos/{repo}/actions/runs?{query}") or {}).get("workflow_runs", [])
        runs += batch
        if len(batch) < PER_PAGE or len(runs) >= SEARCH_CAP:
            break
        page += 1
    return runs


def _day_windows(today: datetime.date | None = None) -> list[str]:
    """`YYYY-MM-DD..YYYY-MM-DD` windows for the lookback this sweep covers.

    The windows come from the sweep's own lookback, not from the dates the capped
    search returned: a capped search is biased towards the newest runs, so deriving
    the windows from it would leave older unfinished runs outside every window and
    report success while they keep holding runner slots.
    """
    end = today or datetime.date.today()
    day = end - datetime.timedelta(days=LOOKBACK_DAYS)
    windows: list[str] = []
    while day <= end:
        windows.append(f"{day.isoformat()}..{day.isoformat()}")
        day += datetime.timedelta(days=1)
    return windows


def unfinished_runs(call, repo: str, **query) -> list[dict]:
    """Every unfinished run for the query, across pages and past the search cap.

    The endpoint caps a page at ``per_page`` items, so asking once silently
    missed everything past the first hundred runs. It also caps a status-filtered
    search at ``SEARCH_CAP`` results in total, which is why a capped status is
    re-swept one day at a time over ``LOOKBACK_DAYS``: the cap applies to the
    search, not to the window, and a busy status can hold more unfinished runs
    than a single search returns. A day that is itself capped is reported, because
    some of its runs are then out of reach without a narrower window.
    """
    runs: list[dict] = []
    for status in UNFINISHED:
        found = _pages(call, repo, {**query, "status": status})
        if len(found) >= SEARCH_CAP:
            print(
                f"::warning::{status}: {SEARCH_CAP} runs is the search cap; re-sweeping day by day",
                file=sys.stderr,
            )
            batched: list[dict] = []
            windows_walked = 0
            for window in _day_windows():
                if windows_walked >= MAX_WINDOW_REQUESTS:
                    print(
                        f"::warning::{status}: stopped after {MAX_WINDOW_REQUESTS} day windows; "
                        "older unfinished runs are left for the next sweep",
                        file=sys.stderr,
                    )
                    break
                windows_walked += 1
                part = _pages(call, repo, {**query, "status": status, "created": window})
                if len(part) >= SEARCH_CAP:
                    print(
                        f"::warning::{status} {window} also reached the search cap; "
                        "some runs in that day are not retrievable",
                        file=sys.stderr,
                    )
                batched += part
            if batched:
                # Union, never replace: the capped search returned real runs, and a
                # narrower window is asked to find the ones it could not reach.
                found = found + batched
        runs += found
    seen: set[int] = set()
    unique: list[dict] = []
    for run in runs:
        if run["id"] not in seen:
            seen.add(run["id"])
            unique.append(run)
    return unique


def stale_for_pr(call, repo: str, pr: dict, exclude_run: int = 0) -> list[dict]:
    """Unfinished pull-request runs of a closed PR that began before it closed."""
    if pr.get("state") != "closed" or not pr.get("closed_at"):
        return []
    sha = pr["head"]["sha"]
    return [
        run
        for run in unfinished_runs(call, repo, head_sha=sha)
        if run["event"] in PR_EVENTS
        and run["id"] != exclude_run
        # ISO-8601 UTC strings from the same API compare correctly as text.
        and run["created_at"] < pr["closed_at"]
    ]


def stale_in_repo(call, repo: str, exclude_run: int = 0) -> list[dict]:
    """Every unfinished pull-request run whose pull request has been closed."""
    stale: list[dict] = []
    closed: dict[str, list[dict]] = {}
    for run in unfinished_runs(call, repo):
        if run["event"] not in PR_EVENTS or run["id"] == exclude_run:
            continue
        sha = run["head_sha"]
        if sha not in closed:
            closed[sha] = call("GET", f"/repos/{repo}/commits/{sha}/pulls") or []
        pulls = closed[sha]
        # Cancel only when the commit has pull requests and every one is closed.
        if pulls and all(p["state"] == "closed" for p in pulls):
            latest = max(p["closed_at"] or "" for p in pulls)
            if latest and run["created_at"] < latest:
                stale.append(run)
    return stale


def cancel(call, repo: str, runs: list[dict], dry_run: bool) -> tuple[int, int]:
    """Cancel each run, returning ``(cancelled, failed)``.

    One run that refuses to cancel (a transient 5xx, a permission blip) must not
    strand every run after it in the list: keep going, report the failures on
    stderr, and let the caller turn a non-zero failure count into a non-zero exit
    status so the sweep cannot look successful while it skipped work.
    """
    cancelled = failed = 0
    for run in runs:
        label = f"{run['id']} {run['name']} ({run['head_branch']}, {run['status']})"
        if dry_run:
            print("would cancel " + label)
            cancelled += 1
            continue
        try:
            call("POST", f"/repos/{repo}/actions/runs/{run['id']}/cancel")
        except Exception as error:  # noqa: BLE001 - one run must not strand the rest
            failed += 1
            print(f"could not cancel {label}: {error}", file=sys.stderr)
            continue
        print("cancelling " + label)
        cancelled += 1
    return cancelled, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pr", type=int, help="closed pull request number")
    mode.add_argument("--sweep", action="store_true", help="every closed PR's unfinished runs")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.repo or not token:
        parser.error("--repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN are required")
    call = lambda method, path, data=None: api(method, path, token, data)  # noqa: E731
    current = int(os.environ.get("GITHUB_RUN_ID") or 0)
    if args.sweep:
        runs = stale_in_repo(call, args.repo, current)
    else:
        pr = call("GET", f"/repos/{args.repo}/pulls/{args.pr}")
        runs = stale_for_pr(call, args.repo, pr, current)
    count, failed = cancel(call, args.repo, runs, args.dry_run)
    summary = f"{count} run(s) {'would be ' if args.dry_run else ''}cancelled"
    print(summary + (f", {failed} could not be cancelled" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
