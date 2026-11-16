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
