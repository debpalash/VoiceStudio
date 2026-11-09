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
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
UNFINISHED = ("queued", "in_progress", "waiting", "pending", "requested")
PR_EVENTS = {"pull_request", "pull_request_target"}


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


def unfinished_runs(call, repo: str, **query) -> list[dict]:
    runs: list[dict] = []
    for status in UNFINISHED:
        params = "&".join(f"{k}={v}" for k, v in {**query, "status": status, "per_page": 100}.items())
        runs += (call("GET", f"/repos/{repo}/actions/runs?{params}") or {}).get("workflow_runs", [])
    return runs


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


def cancel(call, repo: str, runs: list[dict], dry_run: bool) -> int:
    for run in runs:
        label = f"{run['id']} {run['name']} ({run['head_branch']}, {run['status']})"
        print(("would cancel " if dry_run else "cancelling ") + label)
        if not dry_run:
            call("POST", f"/repos/{repo}/actions/runs/{run['id']}/cancel")
    return len(runs)


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
    count = cancel(call, args.repo, runs, args.dry_run)
    print(f"{count} run(s) {'would be ' if args.dry_run else ''}cancelled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
