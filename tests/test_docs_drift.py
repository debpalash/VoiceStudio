"""Documentation-only pull requests must reach the offline inventory check.

The real-repository inventory guard already lives in
``tests/scripts/test_check_docs_drift.py``. Runtime tests skip documentation-only
changes, so the dedicated workflow also needs relevant pull-request paths and
must leave rolling-issue mutation to scheduled/manual runs.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "docs-drift.yml"


def test_the_workflow_also_checks_documentation_pull_requests() -> None:
    """Documentation paths must reach the checker even when the runtime suite is skipped."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    assert "pull_request" in triggers, (
        "a documentation-only pull request skips the runtime suite, so the workflow needs "
        "its own pull_request trigger for the files this check reads"
    )
    paths = triggers["pull_request"]["paths"]
    for expected in (
        "docs/features.yaml", "docs/**", "README.md", "scripts/check-docs-drift.py",
        ".github/workflows/docs-drift.yml",
    ):
        assert expected in paths, f"{expected} changes must run the inventory check"

    steps = workflow["jobs"]["drift"]["steps"]
    issue_step = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/github-script@")
    )
    assert issue_step.get("if") == "github.event_name != 'pull_request'", (
        "a pull request must not create or close the rolling docs-drift issue"
    )


def test_pull_request_checker_has_no_issue_write_permission() -> None:
    """A step condition cannot narrow the token granted to a PR job."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}, (
        "workflow-level issue-write permission would also reach same-repository PRs"
    )
    maintenance = workflow["jobs"]["drift"]
    assert maintenance["if"] == "github.event_name != 'pull_request'"
    assert maintenance["permissions"] == {"contents": "read", "issues": "write"}
    pull_request = workflow["jobs"]["pull-request"]
    assert pull_request["if"] == "github.event_name == 'pull_request'"
    assert pull_request["permissions"] == {"contents": "read"}
    assert not any(
        str(step.get("uses", "")).startswith("actions/github-script@")
        for step in pull_request["steps"]
    ), "PR validation must not contain the rolling-issue mutation"
    checker = next(
        step for step in pull_request["steps"]
        if step.get("run") == "python scripts/check-docs-drift.py"
    )
    assert not checker.get("continue-on-error", False), "PR inventory drift must fail its job"
    for action in ("actions/checkout@", "actions/setup-python@"):
        original = next(step for step in maintenance["steps"] if str(step.get("uses", "")).startswith(action))
        copied = next(step for step in pull_request["steps"] if str(step.get("uses", "")).startswith(action))
        assert copied == original, "Both jobs must use the same reviewed checker toolchain"
    for job in (maintenance, pull_request):
        assert any(step.get("run") == 'pip install "pyyaml==6.0.3"' for step in job["steps"])
    final = maintenance["steps"][-1]
    assert final["if"] == "steps.drift.outcome == 'failure'"
    assert "exit 1" in final["run"], "Scheduled/manual drift must still fail after issue maintenance"
