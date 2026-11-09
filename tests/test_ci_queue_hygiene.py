"""CI must not pile up runs: superseded PR runs cancel, and bots never take a runner.

A contributor force-pushed ~65 PR branches at once (04:20 UTC) and 362 runs queued:
one CI run per push, plus a scam-guard run per review-bot comment.
"""
from __future__ import annotations

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def test_ci_cancels_superseded_pr_runs_but_never_main_commits():
    concurrency = _load("ci.yml")["concurrency"]
    assert "pull_request.number" in concurrency["group"]
    assert concurrency["group"].endswith("github.sha }}"), "main pushes are keyed by commit"
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_docker_cancels_only_older_main_pushes_never_releases_or_backfills():
    concurrency = _load("docker.yml")["concurrency"]
    assert "run_id" in concurrency["group"], "release/dispatch runs must get their own group"
    assert "event_name == 'push'" in concurrency["cancel-in-progress"]


def test_scam_guard_skips_bots_but_still_checks_every_human():
    job = _load("scam-guard.yml")["jobs"]["guard"]
    condition = job["if"]
    assert "sender.type != 'Bot'" in condition
    for human_roles in ("OWNER", "MEMBER", "COLLABORATOR", "author_association"):
        assert human_roles not in condition, "members and collaborators stay checked"
    assert set(_load("scam-guard.yml")[True]) == {"issues", "issue_comment"}


def test_other_pr_workflows_already_cancel_superseded_runs():
    for name in ("security.yml", "commit-identity.yml", "mobile.yml"):
        assert _load(name)["concurrency"]["cancel-in-progress"], name


def test_mobile_builds_on_pull_requests_only_for_mobile_changes():
    """Locale and package.json edits must not start the 25-minute iOS and Android builds.

    They reach the apps through generated files under mobile/, which
    tests/test_mobile_contract.py (run by ci.yml) keeps in step; `main` still builds on them.
    """
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    pr_paths = triggers["pull_request"]["paths"]
    assert "mobile/**" in pr_paths
    assert not [p for p in pr_paths if "locales" in p or p in ("package.json", "electron/src/shared/languages.json")]
    assert any("locales" in p for p in triggers["push"]["paths"])


def test_platform_smokes_start_after_the_python_tests_not_the_whole_gate():
    needs = _load("ci.yml")["jobs"]["smoke-matrix"]["needs"]
    assert needs == "test-python", "waiting on `test` delays the smokes by the Electron jobs' runtime"


def test_workflows_do_not_schedule_retired_macos_runners():
    # Parse first: historical comments are fine, scheduling an unavailable image is not.
    # macOS 14 is already in scheduled brownout and retires on November 2, 2026.
    retired = re.compile(r"\bmacos-(?:1[0-4])(?:-(?:large|xlarge))?\b")
    offenders = {}
    for path in (ROOT / ".github/workflows").glob("*.y*ml"):
        labels = retired.findall(yaml.safe_dump(_load(path.name)))
        if labels:
            offenders[path.name] = sorted(set(labels))
    assert not offenders, f"Use a maintained macOS runner: {offenders}"
