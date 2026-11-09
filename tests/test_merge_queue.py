"""The merge queue must be able to merge: every required check reports on its commit.

`main`'s ruleset requires five checks. The merge queue builds a temporary
`gh-readonly-queue/...` commit and waits for them there. Code checks come from ci.yml;
the PR-level ones (CLA, commit identities) cannot run on that commit, so
merge-queue-checks.yml reports them. If a name drifts, the queue waits forever.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# The ruleset's required_status_checks (Settings > Rules > main). Update with it.
REQUIRED_JOB_CHECKS = {"Tests (backend + frontend)", "Commit identities", "CLA gate"}
REQUIRED_STATUS_CONTEXTS = {"CLA", "Commit identity policy"}


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def _job_names(workflow: dict) -> set[str]:
    return {job.get("name", key) for key, job in workflow["jobs"].items()}


def test_ci_runs_on_the_merge_group():
    assert "merge_group" in _triggers(_workflow("ci.yml"))


def test_ci_gives_a_merge_group_its_own_unsuperseded_run():
    concurrency = _workflow("ci.yml")["concurrency"]
    # A merge group has no PR number: it keys on its own commit and is never cancelled.
    assert concurrency["group"].endswith("github.sha }}")
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_the_queue_workflow_runs_only_on_the_merge_group():
    assert set(_triggers(_workflow("merge-queue-checks.yml"))) == {"merge_group"}


def test_every_required_check_is_reported_on_the_merge_group_commit():
    reported_by_jobs = _job_names(_workflow("ci.yml")) | _job_names(_workflow("merge-queue-checks.yml"))
    assert REQUIRED_JOB_CHECKS <= reported_by_jobs, REQUIRED_JOB_CHECKS - reported_by_jobs
    script = "\n".join(
        step.get("run", "")
        for step in _workflow("merge-queue-checks.yml")["jobs"]["statuses"]["steps"]
    )
    for context in REQUIRED_STATUS_CONTEXTS:
        assert f'"{context}"' in script, f"status {context!r} is never posted on the merge group commit"


def test_the_queue_workflow_cannot_write_anything_but_statuses():
    workflow = _workflow("merge-queue-checks.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["statuses"]["permissions"] == {"statuses": "write"}
