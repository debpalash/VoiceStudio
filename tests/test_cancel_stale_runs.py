"""Cancel unfinished CI of closed pull requests (.github/scripts/cancel_stale_runs.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("cancel_stale_runs", _REPO / ".github/scripts/cancel_stale_runs.py")
stale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stale)

CLOSED = "2026-10-24T12:00:00Z"


def run(id, event="pull_request", status="queued", created="2026-10-24T11:00:00Z", sha="abc", name="CI"):
    return {"id": id, "event": event, "status": status, "created_at": created,
            "head_sha": sha, "head_branch": "feature", "name": name}


class FakeApi:
    def __init__(self, runs, pulls=None):
        self.runs, self.pulls, self.calls = runs, pulls or {}, []

    def __call__(self, method, path, data=None):
        self.calls.append((method, path))
        if method == "POST":
            return None
        if "/actions/runs?" in path:
            status = path.split("status=")[1].split("&")[0]
            sha = path.split("head_sha=")[1].split("&")[0] if "head_sha=" in path else None
            return {"workflow_runs": [r for r in self.runs
                                      if r["status"] == status and (sha is None or r["head_sha"] == sha)]}
        if "/commits/" in path:
            return self.pulls.get(path.split("/commits/")[1].split("/")[0], [])
        raise AssertionError(path)

    def cancelled(self):
        return sorted(int(p.split("/")[-2]) for m, p in self.calls if m == "POST")


def pr(state="closed", closed=CLOSED, sha="abc"):
    return {"state": state, "closed_at": closed if state == "closed" else None, "head": {"sha": sha}}


def test_a_closed_pr_loses_its_unfinished_pull_request_runs_only():
    api = FakeApi([
        run(1), run(2, status="in_progress"), run(3, event="pull_request_target"),
        run(4, event="push"), run(5, event="schedule"),               # never touched
        run(6, created="2026-10-24T12:00:01Z"),                       # started by the close itself
        run(7, sha="other"),                                          # another commit
    ])
    runs = stale.stale_for_pr(api, "o/r", pr())
    assert {r["id"] for r in runs} == {1, 2, 3}
    stale.cancel(api, "o/r", runs, dry_run=False)
    assert api.cancelled() == [1, 2, 3]


def test_an_open_pr_is_never_touched():
    assert stale.stale_for_pr(FakeApi([run(1)]), "o/r", pr(state="open")) == []


def test_the_running_workflow_never_cancels_itself():
    runs = stale.stale_for_pr(FakeApi([run(1), run(2)]), "o/r", pr(), exclude_run=2)
    assert [r["id"] for r in runs] == [1]


def test_dry_run_cancels_nothing():
    api = FakeApi([run(1)])
    stale.cancel(api, "o/r", stale.stale_for_pr(api, "o/r", pr()), dry_run=True)
    assert api.cancelled() == []


def test_sweep_cancels_only_runs_whose_every_pull_request_is_closed():
    api = FakeApi(
        [run(1, sha="merged"), run(2, sha="open"), run(3, sha="mixed"), run(4, sha="none"),
         run(5, sha="merged", event="push")],
        pulls={
            "merged": [pr()],
            "open": [pr(state="open")],
            "mixed": [pr(), pr(state="open")],
            "none": [],
        },
    )
    assert [r["id"] for r in stale.stale_in_repo(api, "o/r")] == [1]


def test_the_workflow_is_a_safe_pull_request_target_job():
    workflow = yaml.safe_load((_REPO / ".github/workflows/cancel-closed-pr-runs.yml").read_text(encoding="utf-8"))
    triggers = workflow[True]
    assert triggers["pull_request_target"]["types"] == ["closed"]
    assert workflow["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    steps = workflow["jobs"]["cancel"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    # base.sha is the base as of the PR's last update: it lacks this script for every PR
    # opened before the script merged, so the default (current base tip) must be used.
    assert "ref" not in checkout["with"]
    # the PR number reaches the script through the environment, not the command line
    assert "${{" not in "".join(s.get("run", "") for s in steps)
