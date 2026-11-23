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


def test_the_oldest_two_stay_open_and_a_third_is_over_the_limit():
    limit = _script()
    prs = [_pr(10, "a"), _pr(12, "a"), _pr(11, "b"), _pr(15, "a")]
    assert limit.over_limit(10, "a", prs) == []
    assert limit.over_limit(12, "a", prs) == []
    assert limit.over_limit(15, "a", prs) == [10, 12]
    assert limit.over_limit(11, "b", prs) == []


def test_a_pull_request_missing_from_the_listing_still_counts():
    limit = _script()
    assert limit.over_limit(20, "a", [_pr(10, "a"), _pr(12, "a")]) == [10, 12]


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
