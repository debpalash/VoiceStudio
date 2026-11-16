"""The docs feature inventory must match the code in the same pull request.

`scripts/check-docs-drift.py` is the mechanical half of the docs-sync rule: it
verifies the ids in `docs/features.yaml` against the code that implements them. It
used to run only from the nightly `docs-drift.yml` cron, so a stale engine or
feature entry shipped to `main` and was found up to a day later on a rolling issue
nobody was blocked on. The checker is offline, pure stdlib plus yaml, and takes
under a second, so it belongs in the pull-request gate with the other mechanical
guards (`tests/test_structure_doc.py` pins the same kind of counts,
`tests/test_install_docs_headings.py` the install-doc structure).

A Markdown-only pull request skips the runtime suite, which is why the workflow
itself also runs the checker for documentation paths: this test covers the suite
run, and `test_the_workflow_also_checks_documentation_pull_requests` covers the
paths that would otherwise fall through both.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-docs-drift.py"
WORKFLOW = ROOT / ".github" / "workflows" / "docs-drift.yml"


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_docs_drift", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_feature_inventory_matches_the_code() -> None:
    checker = _checker()
    drifts = checker._check(ROOT)
    assert drifts == [], (
        "docs/features.yaml drifted from the code — fix the entry (or the code) in this "
        "pull request:\n  " + "\n  ".join(drifts)
    )


def test_the_workflow_also_checks_documentation_pull_requests() -> None:
    """Documentation paths must reach the checker even when the runtime suite is skipped."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    assert "pull_request" in triggers, (
        "a documentation-only pull request skips the runtime suite, so the workflow needs "
        "its own pull_request trigger for the files this check reads"
    )
    paths = triggers["pull_request"]["paths"]
    for expected in ("docs/features.yaml", "README.md", "scripts/check-docs-drift.py"):
        assert expected in paths, f"{expected} changes must run the inventory check"

    steps = workflow["jobs"]["drift"]["steps"]
    issue_step = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/github-script@")
    )
    assert "pull_request" in str(issue_step.get("if", "")), (
        "a pull request must not create or close the rolling docs-drift issue"
    )
