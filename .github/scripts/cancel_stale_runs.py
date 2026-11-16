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
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from typing import Any
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
# A commit with more associations than this remains unknown, never eligible.
MAX_ASSOCIATED_PR_PAGES = 10
# A bounded independent open-PR inventory protects SHAs omitted by commit lookup.
MAX_OPEN_PR_PAGES = 10
# Leave room for the last bounded 30-second API request and summary before the five-minute job.
WORK_SECONDS = 240
SKIPPED = object()
Run = dict[str, Any]
ApiCall = Callable[..., Any]


class IncompleteDiscovery(RuntimeError):
    """Some eligible candidates could not be inspected; never report success."""


class SweepDeadline(RuntimeError):
    """The bounded work budget expired before discovery/delivery completed."""


class _BoundedCall:
    def __init__(self, token: str) -> None:
        self.token = token
        self.deadline = time.monotonic() + WORK_SECONDS
        self.cancellations = 0
        self.skipped = 0

    def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
        if time.monotonic() >= self.deadline:
            raise SweepDeadline("work deadline reached; discovery is incomplete")
        result = api(method, path, self.token, data)
        if method == "POST":
            if result is SKIPPED:
                self.skipped += 1
            else:
                self.cancellations += 1
        return result


def api(method: str, path: str, token: str, data: dict | None = None) -> Any:
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
        if error.code == 409 and method == "POST" and path.endswith("/cancel"):
            # This is not an accepted cancellation (including not-yet-queued runs).
            return SKIPPED
        raise
    return json.loads(body) if body else None


def _pages(call: ApiCall, repo: str, params: dict) -> Iterator[tuple[list[Run], int]]:
    """Stream bounded pages; accepted cancellations can shift status offsets."""
    seen: set[int] = set()
    page = 1
    while True:
        query = "&".join(
            f"{k}={v}" for k, v in {**params, "per_page": PER_PAGE, "page": page}.items()
        )
        response = call("GET", f"/repos/{repo}/actions/runs?{query}")
        if not isinstance(response, dict) or not isinstance(response.get("workflow_runs"), list):
            raise ValueError("Invalid workflow-runs response")
        batch = response["workflow_runs"]
        total = response.get("total_count")
        if type(total) is not int or total < 0:
            raise ValueError("Invalid workflow-runs total count")
        before = getattr(call, "cancellations", 0)
        fresh = [run for run in batch if run["id"] not in seen]
        seen.update(run["id"] for run in fresh)
        yield fresh, total
        if len(batch) < PER_PAGE or len(seen) >= SEARCH_CAP:
            break
        # Removing earlier rows from a status-filtered search changes offsets.
        page = 1 if getattr(call, "cancellations", 0) != before else page + 1
        if page > SEARCH_CAP // PER_PAGE:
            break


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


def unfinished_runs(call: ApiCall, repo: str, **query: Any) -> Iterator[Run]:
    """Stream unique runs, then fail visibly if bounded discovery was incomplete.

    Capped searches are re-swept over the existing 30-day lookback. No day above
    the API cap or exhausted window budget is claimed as fully inspected.
    """
    seen: set[int] = set()
    errors = 0

    status_seen: set[int] = set()

    def search(params: dict) -> Generator[Run, None, tuple[int, int]]:
        nonlocal errors
        count = total = 0
        try:
            for batch, reported_total in _pages(call, repo, params):
                count += len(batch)
                total = max(total, reported_total)
                for run in batch:
                    status_seen.add(run["id"])
                    if run["id"] not in seen:
                        seen.add(run["id"])
                        yield run
        except SweepDeadline:
            raise
        except Exception as error:  # One failed search must not strand other statuses.
            errors += 1
            print(f"::warning::run discovery failed: {error}", file=sys.stderr)
        return count, total

    for status in UNFINISHED:
        status_seen = set()
        found, total = yield from search({**query, "status": status})
        if found >= SEARCH_CAP or total > SEARCH_CAP:
            print(f"::warning::{status}: {SEARCH_CAP} runs is the search cap; re-sweeping day by day", file=sys.stderr)
            window_requests = 0
            for window in _day_windows():
                if window_requests >= MAX_WINDOW_REQUESTS:
                    errors += 1
                    print(f"::warning::{status}: stopped after {MAX_WINDOW_REQUESTS} day windows; discovery is incomplete", file=sys.stderr)
                    break
                params = {**query, "status": status, "created": window}
                window_requests += 1
                count, window_total = yield from search(params)
                if count < window_total and count < SEARCH_CAP and window_requests < MAX_WINDOW_REQUESTS:
                    # Status offsets can shift while accepted cancellations finish.
                    # Check this window independently: arrivals elsewhere do not
                    # account for a missing row within it.
                    window_requests += 1
                    count, window_total = yield from search(params)
                if count < window_total:
                    errors += 1
                    print(f"::warning::{status} {window}: discovered {count} of {window_total} reported runs; discovery is incomplete", file=sys.stderr)
                if count >= SEARCH_CAP:
                    errors += 1
                    print(f"::warning::{status} {window} also reached the search cap; discovery is incomplete", file=sys.stderr)
        elif len(status_seen) < total:
            # Accepted cancellation can finish after its repeated page1 read,
            # shifting unseen rows behind the next page offset. Revisit once
            # under the same deadline; an unresolved deficit is not success.
            _, retried_total = yield from search({**query, "status": status})
            total = max(total, retried_total)
        if len(status_seen) < total:
            errors += 1
            print(f"::warning::{status}: discovered {len(status_seen)} of {total} reported runs; bounded lookback is incomplete", file=sys.stderr)
    if errors:
        raise IncompleteDiscovery(f"{errors} search(es) could not be fully inspected")


def stale_for_pr(call: ApiCall, repo: str, pr: dict, exclude_run: int = 0) -> Iterator[Run]:
    """Unfinished PR runs created before this closed PR's close event."""
    if pr.get("state") != "closed" or not pr.get("closed_at"):
        return
    sha = pr["head"]["sha"]
    try:
        associated_close = _commit_closed_before(call, repo, sha, known_closed_at=pr["closed_at"])
    except SweepDeadline:
        raise
    except Exception as error:
        raise IncompleteDiscovery(f"Could not inspect associated PRs for {sha}") from error
    if not associated_close:
        return
    closed_before = min(pr["closed_at"], associated_close)
    for run in unfinished_runs(call, repo, head_sha=sha):
        if run["event"] in PR_EVENTS and run["id"] != exclude_run and run["created_at"] < closed_before:
            if not _has_open_pr(call, repo, run, requested_pr=pr.get("number")):
                yield run


def _open_prs(call: ApiCall, repo: str) -> list[dict[str, Any]]:
    """Commit lookup omits open associations once a SHA is on the default branch."""
    result: list[dict[str, Any]] = []
    for page in range(1, MAX_OPEN_PR_PAGES + 1):
        try:
            pulls = call("GET", f"/repos/{repo}/pulls?state=open&sort=created&direction=asc&per_page={PER_PAGE}&page={page}")
        except SweepDeadline:
            raise
        except Exception as error:
            raise IncompleteDiscovery("Could not refresh open pull requests") from error
        if not isinstance(pulls, list) or len(pulls) > PER_PAGE:
            raise IncompleteDiscovery("Invalid open-pull-request page")
        for pr in pulls:
            if not isinstance(pr, dict) or pr.get("state") != "open":
                raise IncompleteDiscovery("Invalid open pull request")
            head = pr.get("head")
            sha = head.get("sha") if isinstance(head, dict) else None
            merge_sha = pr.get("merge_commit_sha")
            if not isinstance(sha, str) or not sha or (merge_sha is not None and (not isinstance(merge_sha, str) or not merge_sha)):
                raise IncompleteDiscovery("Open pull-request SHA is unknown")
            if type(pr.get("number")) is not int or pr["number"] <= 0:
                raise IncompleteDiscovery("Open pull-request number is unknown")
            _branch_identity(head.get("ref"), head.get("repo"))
            result.append(pr)
        if len(pulls) < PER_PAGE:
            return result
    raise IncompleteDiscovery("Open pull-request page budget reached")


def _branch_identity(branch: Any, repository: Any) -> tuple[str, str]:
    name = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(branch, str) or not branch or not isinstance(name, str) or len(name.split("/")) != 2 or not all(name.split("/")):
        raise IncompleteDiscovery("Pull-request branch ownership is unknown")
    return name.casefold(), branch


def _has_open_pr(call: ApiCall, repo: str, run: Run, *, requested_pr: int | None = None) -> bool:
    """Refresh immediately before yielding each cancellation candidate.

    A reopened PR may already have a newer head SHA. Match its original branch
    and head repository, or the run's explicit PR association, as well as SHA.
    No positive closure cache can replace this current open-state check.
    """
    associations = run.get("pull_requests", [])
    if not isinstance(associations, list):
        raise IncompleteDiscovery("Workflow-run pull requests are unknown")
    numbers: set[int] = set()
    for pr in associations:
        number = pr.get("number") if isinstance(pr, dict) else None
        if type(number) is not int or number <= 0:
            raise IncompleteDiscovery("Workflow-run pull-request number is unknown")
        numbers.add(number)
    if type(requested_pr) is int and requested_pr > 0:
        numbers.add(requested_pr)
    try:
        branch = _branch_identity(run.get("head_branch"), run.get("head_repository"))
    except IncompleteDiscovery:
        if not numbers:
            raise
        branch = None
    for pr in _open_prs(call, repo):
        head = pr["head"]
        if (pr["number"] in numbers or run["head_sha"] in (head["sha"], pr.get("merge_commit_sha"))
                or (branch is not None and branch == _branch_identity(head["ref"], head["repo"]))):
            return True
    return False


def _commit_closed_before(call: ApiCall, repo: str, sha: str, *,
                          known_closed_at: str | None = None) -> str | None:
    """Find the initial associated closure; each candidate also gets a fresh open check."""
    # Closed, unmerged PRs can have no commit-endpoint association. Only direct
    # mode has a separately verified requested PR's closure to use in that case.
    earliest = known_closed_at
    for page in range(1, MAX_ASSOCIATED_PR_PAGES + 1):
        pulls = call("GET", f"/repos/{repo}/commits/{sha}/pulls?per_page={PER_PAGE}&page={page}")
        if not isinstance(pulls, list) or len(pulls) > PER_PAGE:
            raise IncompleteDiscovery("Invalid associated-pull-request page")
        for pr in pulls:
            if not isinstance(pr, dict):
                raise IncompleteDiscovery("Invalid associated pull request")
            if pr.get("state") == "open":
                return None
            closed_at = pr.get("closed_at")
            if pr.get("state") != "closed" or not isinstance(closed_at, str) or not closed_at:
                raise IncompleteDiscovery("Associated pull-request closure is unknown")
            earliest = min(earliest, closed_at) if earliest else closed_at
        if len(pulls) < PER_PAGE:
            return earliest
    raise IncompleteDiscovery("Associated pull-request page budget reached")


def stale_in_repo(call: ApiCall, repo: str, exclude_run: int = 0) -> Iterator[Run]:
    """Inspect candidates incrementally; one failed commit lookup is isolated."""
    closed: dict[str, str | None] = {}
    failures = 0
    try:
        for run in unfinished_runs(call, repo):
            if run["event"] not in PR_EVENTS or run["id"] == exclude_run:
                continue
            sha = run["head_sha"]
            if sha not in closed:
                try:
                    closed[sha] = _commit_closed_before(call, repo, sha)
                except SweepDeadline:
                    raise
                except Exception as error:
                    closed[sha] = None
                    failures += 1
                    print(f"::warning::could not inspect commit {sha}: {error}", file=sys.stderr)
            closed_before = closed[sha]
            if closed_before and run["created_at"] < closed_before:
                if not _has_open_pr(call, repo, run):
                    yield run
    except IncompleteDiscovery:
        failures += 1
    if failures:
        raise IncompleteDiscovery(f"{failures} discovery/eligibility check(s) failed")


def cancel(call: ApiCall, repo: str, runs: Iterable[Run], dry_run: bool) -> tuple[int, int]:
    """Return accepted requests (or dry-run candidates) and failures.

    An accepted request is not proof that a run has finished cancelling. An HTTP 409
    refusal is skipped, and neither it nor one failed POST strands later runs.
    """
    accepted = failed = 0
    try:
        for run in runs:
            label = f"{run['id']} {run['name']} ({run['head_branch']}, {run['status']})"
            if dry_run:
                print("would cancel " + label)
                accepted += 1
                continue
            try:
                result = call("POST", f"/repos/{repo}/actions/runs/{run['id']}/cancel")
            except SweepDeadline:
                raise
            except Exception as error:
                failed += 1
                print(f"could not cancel {label}: {error}", file=sys.stderr)
                continue
            if result is SKIPPED:
                print("skipped " + label + ": cancellation not accepted (HTTP409)")
                continue
            print("cancellation requested " + label)
            accepted += 1
    except (IncompleteDiscovery, SweepDeadline) as error:
        failed += 1
        print(f"::warning::incomplete sweep: {error}", file=sys.stderr)
    return accepted, failed


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
    call = _BoundedCall(token)
    current = int(os.environ.get("GITHUB_RUN_ID") or 0)
    if args.sweep:
        runs = stale_in_repo(call, args.repo, current)
    else:
        pr = call("GET", f"/repos/{args.repo}/pulls/{args.pr}")
        runs = stale_for_pr(call, args.repo, pr, current)
    count, failed = cancel(call, args.repo, runs, args.dry_run)
    summary = f"{count} run(s) would be cancelled" if args.dry_run else f"{count} cancellation request(s) accepted, {call.skipped} skipped"
    print(summary + (f", {failed} could not be cancelled or fully inspected" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
