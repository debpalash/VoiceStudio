"""The two-open-pull-request limit for outside contributors."""
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("enforce_pr_limit", ROOT / ".github/scripts/enforce_pr_limit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pr(number, login):
    return {"number": number, "user": {"login": login}}


def test_a_third_pull_request_is_closed_and_two_stay_open():
    limit = _script()
    prs = [_pr(10, "a"), _pr(12, "a"), _pr(11, "b"), _pr(15, "a")]
    assert limit.plan(15, "a", prs) == ([10, 12], [15])
    assert limit.plan(11, "b", prs) == ([11], [])
    assert limit.plan(12, "a", [_pr(10, "a"), _pr(12, "a")]) == ([10, 12], [])


def test_a_pull_request_missing_from_the_listing_still_counts():
    limit = _script()
    assert limit.plan(20, "a", [_pr(10, "a"), _pr(12, "a")]) == ([10, 12], [20])


def test_reopening_an_older_pull_request_closes_that_one():
    limit = _script()
    prs = [_pr(3, "a"), _pr(10, "a"), _pr(12, "a")]
    assert limit.plan(3, "a", prs) == ([10, 12], [3])


def test_a_pull_request_a_skipped_run_missed_is_closed_too():
    limit = _script()
    prs = [_pr(1, "a"), _pr(2, "a"), _pr(4, "a"), _pr(5, "a")]
    assert limit.plan(5, "a", prs) == ([1, 2], [4, 5])


def test_maintainers_and_bots_are_exempt():
    limit = _script()
    for association in ("OWNER", "MEMBER", "COLLABORATOR", "owner"):
        assert limit.is_exempt(association, "User")
    assert limit.is_exempt("NONE", "Bot")
    for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE"):
        assert not limit.is_exempt(association, "User")


def test_the_workflow_runs_on_open_and_reopen_and_passes_event_data_via_env():
    text = (ROOT / ".github/workflows/pr-limit.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    on = workflow.get("on", workflow.get(True))
    assert set(on["pull_request_target"]["types"]) == {"opened", "reopened"}
    step = workflow["jobs"]["limit"]["steps"][1]
    assert "${{" not in step["run"]
    assert step["env"]["ASSOCIATION"] == "${{ github.event.pull_request.author_association }}"
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
