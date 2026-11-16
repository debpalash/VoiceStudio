"""Offline API-limit, incremental cleanup and closed-PR authorization regressions."""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit
import urllib.error

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/cancel_stale_runs.py"
CLOSED = "2026-10-24T12:00:00Z"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cancel_stale_runs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(run_id: int, status: str = "queued", *, created: str | None = None,
         sha: str = "a" * 40, event: str = "pull_request") -> dict[str, Any]:
    return {"id": run_id, "name": f"CI {run_id}", "head_branch": f"feature-{sha}",
            "head_repository": {"full_name": "contributor/source"}, "pull_requests": [],
            "head_sha": sha, "event": event, "status": status,
            "created_at": created or f"{datetime.date.today().isoformat()}T00:00:00Z"}


def _pr(state: str = "closed", closed: str = CLOSED, sha: str = "a" * 40, merged: bool | None = False) -> dict[str, Any]:
    return {"number": 7, "state": state, "merged": merged, "closed_at": closed if state == "closed" else None,
            "head": {"sha": sha, "ref": f"feature-{sha}", "repo": {"full_name": "contributor/source"}}}


class FakeApi:
    """The real 100/page and 1000/search caps, including exact date-filtered pages."""
    def __init__(self, runs: list[dict[str, Any]], *, refuse: set[int] | None = None,
                 pulls: dict[str, list[dict[str, Any]]] | None = None,
                 bad_commits: set[str] | None = None, latency: float = 0,
                 remove_cancelled: bool = False,
                 open_pulls: list[dict[str, Any]] | None = None, bad_open: bool = False) -> None:
        self.runs = sorted(runs, key=lambda run: (run["created_at"], run["id"]), reverse=True)
        self.refuse = refuse or set()
        self.pulls = pulls
        self.open_pulls = open_pulls or []
        self.bad_open = bad_open
        self.bad_commits = bad_commits or set()
        self.cancelled: list[int] = []
        self.requests: list[tuple[str, str]] = []
        self.pages: list[list[dict[str, Any]]] = []
        self.now = 0.0
        self.latency = latency
        self.first_post_at: float | None = None
        self.remove_cancelled = remove_cancelled
        self.requested_pr = _pr(closed="2999-01-01T00:00:00Z")

    def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
        self.requests.append((method, path))
        self.now += self.latency
        if method == "POST":
            if self.first_post_at is None:
                self.first_post_at = self.now
            run_id = int(path.rstrip("/").split("/")[-2])
            if run_id in self.refuse:
                raise RuntimeError("502 Bad Gateway")
            self.cancelled.append(run_id)
            if self.remove_cancelled:
                for run in self.runs:
                    if run["id"] == run_id:
                        run["status"] = "completed"
            return None
        if "/commits/" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            if sha in self.bad_commits:
                raise RuntimeError("Controlled commit lookup failure")
            pulls = self.pulls.get(sha, []) if self.pulls is not None else [_pr(closed="2999-01-01T00:00:00Z")]
            query = parse_qs(urlsplit(path).query)
            per_page = int(query.get("per_page", ["30"])[0])
            assert 1 <= per_page <= 100
            start = (int(query.get("page", ["1"])[0]) - 1) * per_page
            return pulls[start:start + per_page]
        if urlsplit(path).path.endswith("/pulls"):
            if self.bad_open:
                raise RuntimeError("Controlled open-PR lookup failure")
            query = parse_qs(urlsplit(path).query)
            assert query["state"] == ["open"]
            assert query["per_page"] == ["100"]
            start = (int(query["page"][0]) - 1) * 100
            return self.open_pulls[start:start + 100]
        if "/pulls/" in path:
            return self.requested_pr
        assert "/actions/runs?" in path
        query = parse_qs(urlsplit(path).query)
        assert int(query["per_page"][0]) == 100
        selected = [run for run in self.runs if run["status"] == query["status"][0]]
        if "head_sha" in query:
            selected = [run for run in selected if run["head_sha"] == query["head_sha"][0]]
        if "created" in query:
            start, end = query["created"][0].split("..")
            selected = [run for run in selected if start <= run["created_at"][:10] <= end]
        total = len(selected)
        start = (int(query["page"][0]) - 1) * 100
        page = selected[:1000][start:start + 100]
        assert len(page) <= 100
        self.pages.append(page)
        return {"total_count": total, "workflow_runs": page}


def _main(module: ModuleType, api: FakeApi, monkeypatch: pytest.MonkeyPatch, mode: list[str] | None = None) -> int:
    monkeypatch.setattr(module, "api", lambda method, path, token, data=None: api(method, path, data))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: api.now), raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "offline-fixture-token")
    monkeypatch.setenv("GITHUB_RUN_ID", "0")
    return module.main(["--repo", "owner/repo", *(mode or ["--sweep"])])


def test_a_full_first_page_does_not_hide_the_second() -> None:
    module = _module()
    api = FakeApi([_run(1000 + index) for index in range(101)])
    runs = list(module.unfinished_runs(api, "owner/repo"))
    assert {run["id"] for run in runs} == set(range(1000, 1101))
    assert any("page=2" in path for _, path in api.requests)


def test_a_capped_search_preserves_all_paginated_overlapping_date_results(capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    today = datetime.date.today()
    rows = [_run(1000 + i, created=f"{(today - datetime.timedelta(days=i // 500)).isoformat()}T01:00:00Z") for i in range(1003)]
    api = FakeApi(rows)
    runs = list(module.unfinished_runs(api, "owner/repo"))
    assert {run["id"] for run in runs} == {row["id"] for row in rows}
    assert len(runs) == len(rows)
    assert all(len(page) <= 100 for page in api.pages)
    today_queries = [path for _, path in api.requests if f"created={today.isoformat()}..{today.isoformat()}" in path]
    assert len(today_queries) == 6
    assert "search cap" in capsys.readouterr().err


def test_saturated_day_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    api = FakeApi([_run(i) for i in range(1001)])
    assert _main(module, api, monkeypatch) == 1
    assert "also reached the search cap" in capsys.readouterr().err
    assert len(api.cancelled) == 1000


def test_window_budget_is_incomplete_not_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    monkeypatch.setattr(module, "MAX_WINDOW_REQUESTS", 2)
    api = FakeApi([_run(i) for i in range(1001)])
    assert _main(module, api, monkeypatch) == 1
    assert "stopped after 2 day windows" in capsys.readouterr().err
    assert len([path for _, path in api.requests if "created=" in path]) == 2


def test_cancels_eligible_runs_before_finishing_capped_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    today = datetime.date.today()
    api = FakeApi([_run(i, created=f"{(today-datetime.timedelta(days=i // 500)).isoformat()}T01:00:00Z") for i in range(1001)], latency=4)
    _main(module, api, monkeypatch)
    # One run page, commit associations, two matching open-PR reads, POST.
    assert api.first_post_at is not None and api.first_post_at <= 5 * api.latency


def test_deadline_keeps_partial_progress_and_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(i, sha=f"{i:040x}") for i in range(101)], latency=20)
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled, "Known eligible runs must make progress before the deadline"
    assert api.now < 300, "The sweep must stop before its five-minute job deadline"


def test_one_failed_commit_lookup_does_not_strand_other_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(1, sha="bad"), _run(2, sha="good"), _run(3, sha="bad")], bad_commits={"bad"})
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled == [2]
    assert len([path for _, path in api.requests if "/commits/bad/" in path]) == 1


def test_one_refused_cancellation_does_not_strand_the_rest() -> None:
    module = _module()
    api = FakeApi([], refuse={2})
    accepted, failed = module.cancel(api, "owner/repo", [_run(1), _run(2), _run(3)], dry_run=False)
    assert api.cancelled == [1, 3]
    assert (accepted, failed) == (2, 1)


def test_real_api_conflict_is_skipped_not_accepted(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    def conflict(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("https://api.github.com/fixture", 409, "Cannot cancel a workflow run that is not queued or in progress", {}, None)
    monkeypatch.setattr(module.urllib.request, "urlopen", conflict)
    accepted, failed = module.cancel(lambda method, path: module.api(method, path, "offline-fixture-token"), "owner/repo", [_run(1)], dry_run=False)
    assert (accepted, failed) == (0, 0)
    assert "skipped" in capsys.readouterr().out.lower()


def test_a_failure_becomes_a_non_zero_exit_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    api = FakeApi([_run(7)], refuse={7})
    assert _main(module, api, monkeypatch) == 1
    captured = capsys.readouterr()
    assert "could not be cancelled" in captured.out + captured.err


def test_a_closed_pr_loses_only_its_older_unfinished_pr_runs() -> None:
    module = _module()
    rows = [_run(1, created="2026-10-24T11:00:00Z"), _run(2, "in_progress", created="2026-10-24T11:00:00Z"),
            _run(3, event="pull_request_target", created="2026-10-24T11:00:00Z"), _run(4, event="push"),
            _run(5, event="schedule"), _run(6, created="2026-10-24T12:00:01Z"), _run(7, sha="other")]
    api = FakeApi(rows)
    runs = list(module.stale_for_pr(api, "o/r", _pr()))
    assert {r["id"] for r in runs} == {1, 2, 3}
    module.cancel(api, "o/r", runs, dry_run=False)
    assert set(api.cancelled) == {1, 2, 3}


def test_an_open_pr_is_never_touched() -> None:
    module = _module()
    assert list(module.stale_for_pr(FakeApi([_run(1)]), "o/r", _pr(state="open"))) == []


def test_the_running_workflow_never_cancels_itself() -> None:
    module = _module()
    rows = [_run(1, created="2026-10-24T11:00:00Z"), _run(2, created="2026-10-24T11:00:00Z")]
    assert [r["id"] for r in module.stale_for_pr(FakeApi(rows), "o/r", _pr(), exclude_run=2)] == [1]


def test_dry_run_cancels_nothing() -> None:
    module = _module()
    api = FakeApi([])
    module.cancel(api, "o/r", [_run(1)], dry_run=True)
    assert api.cancelled == []


def test_sweep_requires_every_related_pr_closed_and_preserves_current_run() -> None:
    module = _module()
    api = FakeApi([_run(1, sha="merged", created="2026-10-24T11:00:00Z"), _run(2, sha="open"),
                   _run(3, sha="mixed"), _run(4, sha="none"), _run(5, sha="merged", event="push"),
                   _run(6, sha="merged", created="2026-10-24T11:00:00Z")],
                  pulls={"merged": [_pr()], "open": [_pr(state="open")], "mixed": [_pr(), _pr(state="open")], "none": []})
    assert [r["id"] for r in module.stale_in_repo(api, "o/r", exclude_run=6)] == [1]


def test_the_workflow_is_a_safe_pull_request_target_job() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/cancel-closed-pr-runs.yml").read_text(encoding="utf-8"))
    assert workflow[True]["pull_request_target"]["types"] == ["closed"]
    assert workflow[True]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    assert workflow["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"}
    steps = workflow["jobs"]["cancel"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    assert "ref" not in checkout["with"]
    assert "${{" not in "".join(s.get("run", "") for s in steps)


def test_streaming_cancellation_does_not_skip_shifted_status_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(i) for i in range(1, 252)], remove_cancelled=True)
    assert _main(module, api, monkeypatch) == 0
    assert set(api.cancelled) == set(range(1, 252))
    assert len(api.cancelled) == 251


def test_capped_discovery_cannot_claim_success_for_unseen_older_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    today = datetime.date.today()
    rows = [_run(i, created=f"{(today-datetime.timedelta(days=(i-1) // 500)).isoformat()}T01:00:00Z") for i in range(1, 1001)]
    outside = (today-datetime.timedelta(days=module.LOOKBACK_DAYS+1)).isoformat()
    rows.append(_run(9999, created=f"{outside}T01:00:00Z"))
    api = FakeApi(rows)
    assert _main(module, api, monkeypatch) == 1
    assert len(api.cancelled) == 1000
    assert not any(f"created={outside}..{outside}" in path for _, path in api.requests)


def test_get_conflict_is_not_silently_an_empty_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    def conflict(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError("https://api.github.com/fixture", 409, "Conflict", {}, None)
    monkeypatch.setattr(module.urllib.request, "urlopen", conflict)
    with pytest.raises(urllib.error.HTTPError):
        module.api("GET", "/repos/owner/repo/actions/runs", "offline-fixture-token")


@pytest.mark.parametrize("closed_count", [30, 100])
def test_later_page_open_pr_prevents_shared_commit_cancellation(monkeypatch: pytest.MonkeyPatch, closed_count: int) -> None:
    module = _module()
    api = FakeApi([_run(1, sha="shared", created="2026-10-24T11:00:00Z")],
                  pulls={"shared": [_pr() for _ in range(closed_count)] + [_pr(state="open")]})
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == []


def test_shared_commit_preserves_runs_at_or_after_the_earliest_close(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(1, sha="shared", created="2026-10-24T09:00:00Z"),
                   _run(2, sha="shared", created="2026-10-24T10:00:00Z"),
                   _run(3, sha="shared", created="2026-10-24T11:00:00Z"),
                   _run(4, sha="shared", created="2026-10-24T13:00:00Z")],
                  pulls={"shared": [_pr(closed="2026-10-24T10:00:00Z"), _pr(closed="2026-10-24T12:00:00Z")]})
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == [1]



def test_all_closed_associations_are_paginated_before_cancelling(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(1, sha="shared", created="2026-10-24T11:00:00Z")], pulls={"shared": [_pr() for _ in range(150)]})
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == [1]
    paths = [path for _, path in api.requests if "/commits/" in path]
    assert paths == ["/repos/owner/repo/commits/shared/pulls?per_page=100&page=1",
                     "/repos/owner/repo/commits/shared/pulls?per_page=100&page=2"]


def test_open_association_stops_pagination_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(1, sha="shared")], pulls={"shared": [_pr(state="open")] + [_pr() for _ in range(150)]})
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == []
    assert len([path for _, path in api.requests if "/commits/" in path]) == 1


def test_incomplete_association_lookup_cannot_cancel_but_other_commit_can(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "MAX_ASSOCIATED_PR_PAGES", 2)
    api = FakeApi([_run(1, sha="shared", created="2026-10-24T11:00:00Z"),
                   _run(2, sha="good", created="2026-10-24T11:00:00Z")],
                  pulls={"shared": [_pr() for _ in range(200)], "good": [_pr()]})
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled == [2]
    assert len([path for _, path in api.requests if "/commits/shared/" in path]) == 2



def test_direct_closed_pr_cannot_cancel_shared_commit_with_an_open_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    sha = "a" * 40
    api = FakeApi([_run(1, sha=sha, created="2026-10-24T11:00:00Z")],
                  pulls={sha: [_pr(), _pr(state="open")]})
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, ["--pr", "7"]) == 0
    assert api.cancelled == []


def test_direct_closed_pr_uses_the_earliest_associated_close(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    sha = "a" * 40
    api = FakeApi([_run(1, sha=sha, created="2026-10-24T09:00:00Z"),
                   _run(2, sha=sha, created="2026-10-24T10:00:00Z"),
                   _run(3, sha=sha, created="2026-10-24T11:00:00Z")],
                  pulls={sha: [_pr(closed="2026-10-24T10:00:00Z"), _pr()]})
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, ["--pr", "7"]) == 0
    assert api.cancelled == [1]



def test_direct_closed_pr_unknown_associations_cannot_authorize_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    sha = "a" * 40
    api = FakeApi([_run(1, sha=sha, created="2026-10-24T11:00:00Z")], bad_commits={sha})
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, ["--pr", "7"]) == 1
    assert api.cancelled == []


def test_delayed_cancellation_does_not_hide_shifted_unseen_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    class DelayedApi(FakeApi):
        repeated = False
        removed = False
        run_pages: list[tuple[int, list[int]]]

        def __init__(self) -> None:
            super().__init__([_run(i) for i in range(1, 201)])
            self.run_pages = []

        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            query = parse_qs(urlsplit(path).query)
            queued = method == "GET" and "/actions/runs?" in path and query.get("status") == ["queued"]
            page = int(query.get("page", ["1"])[0])
            # Accepted cancellations are still visible for the repeated page1.
            # Complete them only when the following page2request begins.
            if queued and self.repeated and not self.removed:
                assert page == 2
                for run in self.runs:
                    if run["id"] in self.cancelled:
                        run["status"] = "completed"
                self.removed = True
            response = super().__call__(method, path, data)
            if queued:
                self.run_pages.append((page, [run["id"] for run in response["workflow_runs"]]))
                if page == 1 and self.cancelled and not self.repeated:
                    self.repeated = True
            return response

    api = DelayedApi()
    assert _main(module, api, monkeypatch) == 0
    assert api.repeated and api.removed
    assert api.run_pages[0] == api.run_pages[1]
    assert api.run_pages[2] == (2, [])
    assert set(api.cancelled) == set(range(1, 201))
    assert len(api.cancelled) == 200


@pytest.mark.parametrize("dry_run", [False, True])
def test_stable_multi_page_results_remain_complete_and_truthful(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], dry_run: bool) -> None:
    module = _module()
    api = FakeApi([_run(i) for i in range(1, 201)])
    mode = ["--sweep", "--dry-run"] if dry_run else ["--sweep"]
    assert _main(module, api, monkeypatch, mode) == 0
    assert set(api.cancelled) == (set() if dry_run else set(range(1, 201)))
    assert len(api.cancelled) == (0 if dry_run else 200)
    output = capsys.readouterr().out
    assert ("200 run(s) would be cancelled" if dry_run else "200 cancellation request(s) accepted, 0 skipped") in output


def test_unaccounted_short_search_remains_incomplete_after_bounded_rescan(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    class MissingApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            response = super().__call__(method, path, data)
            query = parse_qs(urlsplit(path).query)
            if method == "GET" and "/actions/runs?" in path and query.get("status") == ["queued"]:
                response["total_count"] += 1
            return response
    api = MissingApi([_run(i) for i in range(1, 201)])
    assert _main(module, api, monkeypatch) == 1
    assert set(api.cancelled) == set(range(1, 201))
    assert len(api.cancelled) == 200
    assert len([path for _, path in api.requests if "/actions/runs?" in path and "status=queued" in path]) <= 10


@pytest.mark.parametrize("mode", [["--sweep"], ["--pr", "7"]])
@pytest.mark.parametrize("sha_field", ["head", "merge_commit_sha"])
def test_independent_open_inventory_protects_default_branch_shared_sha(monkeypatch: pytest.MonkeyPatch, mode: list[str], sha_field: str) -> None:
    module = _module()
    sha = "a" * 40
    open_pr = _pr(state="open", sha=sha if sha_field == "head" else "other")
    if sha_field == "merge_commit_sha":
        open_pr["merge_commit_sha"] = sha
    api = FakeApi([_run(1, created="2026-10-24T11:00:00Z")],
                  pulls={sha: [_pr()]}, open_pulls=[open_pr])
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, mode) == 0
    assert api.cancelled == []


def test_direct_closed_unmerged_empty_associations_use_requested_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    sha = "a" * 40
    api = FakeApi([_run(1, created="2026-10-24T11:00:00Z"),
                   _run(2, created="2026-10-24T12:00:00Z"),
                   _run(3, created="2026-10-24T11:00:00Z", event="push")], pulls={sha: []})
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, ["--pr", "7"]) == 0
    assert api.cancelled == [1]


@pytest.mark.parametrize("mode", [["--sweep"], ["--pr", "7"]])
def test_unknown_open_inventory_cannot_authorize_cancellation(monkeypatch: pytest.MonkeyPatch, mode: list[str]) -> None:
    module = _module()
    api = FakeApi([_run(1, created="2026-10-24T11:00:00Z")], bad_open=True)
    assert _main(module, api, monkeypatch, mode) == 1
    assert api.cancelled == []


def test_open_inventory_later_page_and_exhausted_budget_keep_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    unrelated = [dict(_pr(state="open", sha=f"{i:040x}"), number=i + 8) for i in range(100)]
    api = FakeApi([_run(1)], open_pulls=[*unrelated, _pr(state="open")])
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == []
    assert any("state=open" in path and "page=2" in path for _, path in api.requests)
    monkeypatch.setattr(module, "MAX_OPEN_PR_PAGES", 1, raising=False)
    capped = FakeApi([_run(1)], open_pulls=unrelated)
    assert _main(module, capped, monkeypatch) == 1
    assert capped.cancelled == []


def test_arrivals_cannot_mask_a_short_day_window(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(module, "_day_windows", lambda: [f"{today}..{today}"])
    class ArrivingApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            query = parse_qs(urlsplit(path).query)
            if method == "GET" and query.get("status") == ["queued"] and "created" in query:
                self.requests.append((method, path))
                return {"total_count": 2, "workflow_runs": [_run(2000)]}
            return super().__call__(method, path, data)
    api = ArrivingApi([_run(i) for i in range(1, 1002)])
    assert _main(module, api, monkeypatch) == 1
    assert len(set(api.cancelled)) == 1001
    assert 1 not in api.cancelled
    assert len([path for _, path in api.requests if "created=" in path]) == 2
    assert "discovered 1 of 2" in capsys.readouterr().err


@pytest.mark.parametrize("response", [{}, [None], [{"state": "closed", "head": {"sha": "a" * 40}}],
                                      [{"state": "open", "head": None}],
                                      [{"state": "open", "head": {"sha": "a" * 40}, "merge_commit_sha": 1}]])
def test_malformed_independent_open_inventory_fails_closed(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    module = _module()
    class MalformedApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            if urlsplit(path).path.endswith("/pulls") and "/commits/" not in path:
                return response
            return super().__call__(method, path, data)
    api = MalformedApi([_run(1)])
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled == []


def test_short_day_window_can_complete_on_one_bounded_rescan(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(module, "_day_windows", lambda: [f"{today}..{today}"])
    class RecoveringApi(FakeApi):
        attempts = 0
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            query = parse_qs(urlsplit(path).query)
            if method == "GET" and query.get("status") == ["queued"] and "created" in query:
                self.requests.append((method, path))
                self.attempts += 1
                # The second pass finds the previously shifted row. Earlier
                # accepted cancellations have completed, so its total is now1.
                return {"total_count": 2 if self.attempts == 1 else 1,
                        "workflow_runs": [_run(2000 if self.attempts == 1 else 1)]}
            return super().__call__(method, path, data)
    api = RecoveringApi([_run(i) for i in range(1, 1002)])
    assert _main(module, api, monkeypatch) == 0
    assert api.attempts == 2
    assert len(api.cancelled) == len(set(api.cancelled)) == 1002


def test_short_window_retry_stays_within_window_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(module, "MAX_WINDOW_REQUESTS", 1)
    monkeypatch.setattr(module, "_day_windows", lambda: [f"{today}..{today}"])
    class ShortApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            query = parse_qs(urlsplit(path).query)
            if method == "GET" and query.get("status") == ["queued"] and "created" in query:
                self.requests.append((method, path))
                return {"total_count": 2, "workflow_runs": [_run(2000)]}
            return super().__call__(method, path, data)
    api = ShortApi([_run(i) for i in range(1, 1002)])
    assert _main(module, api, monkeypatch) == 1
    assert len([path for _, path in api.requests if "created=" in path]) == 1


@pytest.mark.parametrize("mode", [["--sweep"], ["--pr", "7"]])
@pytest.mark.parametrize("identity", ["sha", "branch", "number"])
def test_reopened_pr_is_rechecked_before_each_cancellation(
    monkeypatch: pytest.MonkeyPatch, mode: list[str], identity: str
) -> None:
    module = _module()
    sha = "a" * 40
    rows = [_run(i, created="2026-10-24T11:00:00Z") for i in (1, 2)]
    if identity == "number":
        for row in rows:
            row["pull_requests"] = [{"number": 7}]
    class ReopeningApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            result = super().__call__(method, path, data)
            if method == "POST":
                reopened = _pr(state="open", sha=sha if identity == "sha" else "b" * 40)
                if identity == "branch":
                    reopened["head"]["ref"] = f"feature-{sha}"
                if identity == "number":
                    reopened["head"]["repo"]["full_name"] = "another/source"
                self.open_pulls = [reopened]
            return result
    api = ReopeningApi(rows)
    api.requested_pr = _pr()
    assert _main(module, api, monkeypatch, mode) == 0
    assert api.cancelled == [2]


@pytest.mark.parametrize("mode", [["--sweep"], ["--pr", "7"]])
def test_failed_open_refresh_preserves_remaining_runs_after_partial_progress(
    monkeypatch: pytest.MonkeyPatch, mode: list[str]
) -> None:
    module = _module()
    class FailingRefreshApi(FakeApi):
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            result = super().__call__(method, path, data)
            if method == "POST":
                self.bad_open = True
            return result
    api = FailingRefreshApi([_run(i, created="2026-10-24T11:00:00Z") for i in (1, 2)])
    assert _main(module, api, monkeypatch, mode) == 1
    assert api.cancelled == [2]


def test_new_open_head_on_an_unrelated_branch_does_not_protect_closed_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    api = FakeApi([_run(1)], open_pulls=[_pr(state="open", sha="b" * 40)])
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == [1]


@pytest.mark.parametrize("field", ["head_branch", "head_repository"])
def test_missing_run_ownership_cannot_authorize_cancellation(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    module = _module()
    row = _run(1)
    del row[field]
    api = FakeApi([row])
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled == []


@pytest.mark.parametrize("mode, problem", [
    (["--sweep"], "repository"), (["--sweep"], "branch"),
    (["--sweep"], "associations"), (["--sweep"], "number"),
    (["--pr", "7"], "associations"), (["--pr", "7"], "number"),
])
def test_unknown_candidate_does_not_strand_later_eligible_runs(
    monkeypatch: pytest.MonkeyPatch, mode: list[str], problem: str,
) -> None:
    module = _module()
    unknown = _run(2)
    if problem == "repository":
        unknown.pop("head_repository")
    elif problem == "branch":
        unknown.pop("head_branch")
    elif problem == "associations":
        unknown["pull_requests"] = "unknown"
    else:
        unknown["pull_requests"] = [{"number": "unknown"}]
    api = FakeApi([unknown, _run(1)])
    assert _main(module, api, monkeypatch, mode) == 1
    assert api.cancelled == [1]


def test_retry_arrivals_cannot_cover_an_unseen_original_page(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    class ShiftingRuns(FakeApi):
        passes = 0
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            query = parse_qs(urlsplit(path).query)
            if method == 'GET' and '/actions/runs?' in path and query.get('status') == ['queued']:
                self.requests.append((method, path))
                if query['page'] == ['1']:
                    self.passes += 1
                    # Original IDs1..100 remain queued and unseen. The first
                    # visible100 complete; new arrivals replace them on retry.
                    start = 101 if self.passes == 1 else 301
                    return {'workflow_runs': [_run(i) for i in range(start, start + 100)], 'total_count': 200}
                return {'workflow_runs': [], 'total_count': 200}
            return super().__call__(method, path, data)
    api = ShiftingRuns([])
    observed: list[dict[str, Any]] = []
    with pytest.raises(module.IncompleteDiscovery):
        observed.extend(module.unfinished_runs(api, 'owner/repo'))
    assert {run['id'] for run in observed} == set(range(101, 201)) | set(range(301, 401))
    assert api.passes == 2


@pytest.mark.parametrize('mode', [['--sweep'], ['--pr', '7']])
def test_open_inventory_closure_shift_cannot_hide_the_last_active_pr(
    monkeypatch: pytest.MonkeyPatch, mode: list[str],
) -> None:
    module = _module()
    unrelated = [dict(_pr(state='open', sha=f'{i:040x}'), number=i + 10) for i in range(100)]
    active = _pr(state='open')
    class ClosingPr(FakeApi):
        shifted = False
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            if '/commits/' not in path and urlsplit(path).path.endswith('/pulls'):
                query = parse_qs(urlsplit(path).query)
                if query.get('page') == ['2'] and not self.shifted:
                    self.open_pulls.pop(0)
                    self.shifted = True
            return super().__call__(method, path, data)
    api = ClosingPr([_run(1)], open_pulls=[*unrelated, active])
    assert _main(module, api, monkeypatch, mode) == 0
    assert api.shifted and api.cancelled == []


@pytest.mark.parametrize('mode', [['--sweep'], ['--pr', '7']])
def test_unstable_mutable_open_rows_preserve_candidate_but_not_strand_later_runs(
    monkeypatch: pytest.MonkeyPatch, mode: list[str],
) -> None:
    module = _module()
    class ChurningPr(FakeApi):
        reads = 0
        row = dict(_pr(state='open', sha='unrelated'), number=19)
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            if '/commits/' not in path and urlsplit(path).path.endswith('/pulls'):
                self.requests.append((method, path))
                self.reads += 1
                if self.reads <= 3:
                    self.row['head']['sha'] = f'changing-{self.reads}'
                    return [self.row]  # Same mutable object, distinct identity each read.
                return []
            return super().__call__(method, path, data)
    api = ChurningPr([_run(1), _run(2)])
    assert _main(module, api, monkeypatch, mode) == 1
    assert api.cancelled == [1]
    assert api.reads == 5


def test_duplicate_pr_number_across_pages_is_not_a_complete_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    open_prs = [dict(_pr(state='open', sha=f'{i:040x}'), number=i + 10) for i in range(100)]
    open_prs.append(dict(_pr(state='open', sha='unrelated'), number=109))
    api = FakeApi([_run(1)], open_pulls=open_prs)
    assert _main(module, api, monkeypatch) == 1
    assert api.cancelled == []


def test_non_identity_open_pr_churn_does_not_prevent_stability(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    class CosmeticChurn(FakeApi):
        reads = 0
        def __call__(self, method: str, path: str, data: dict | None = None) -> Any:
            result = super().__call__(method, path, data)
            if '/commits/' not in path and urlsplit(path).path.endswith('/pulls'):
                self.reads += 1
                for pr in result:
                    pr['updated_at'] = str(self.reads)
            return result
    api = CosmeticChurn([_run(1)], open_pulls=[dict(_pr(state='open', sha='unrelated'), number=19)])
    assert _main(module, api, monkeypatch) == 0
    assert api.cancelled == [1]
    assert api.reads == 2


@pytest.mark.parametrize('merged', [True, None, 'missing'])
def test_empty_associations_cannot_borrow_a_merged_or_unknown_requested_closure(
    monkeypatch: pytest.MonkeyPatch, merged: bool | None | str,
) -> None:
    module = _module()
    api = FakeApi([_run(1, created='2026-10-24T11:00:00Z')], pulls={'a' * 40: []})
    api.requested_pr = _pr()
    if merged == 'missing':
        api.requested_pr.pop('merged')
    else:
        api.requested_pr['merged'] = merged
    assert _main(module, api, monkeypatch, ['--pr', '7']) == 0
    assert api.cancelled == []


@pytest.mark.parametrize('merged', [False, True, None])
def test_present_associations_still_preserve_initial_minimum_closure(
    monkeypatch: pytest.MonkeyPatch, merged: bool | None,
) -> None:
    module = _module()
    api = FakeApi([_run(1, created='2026-10-24T09:00:00Z'),
                   _run(2, created='2026-10-24T10:00:00Z')],
                  pulls={'a' * 40: [_pr(closed='2026-10-24T10:00:00Z')]})
    api.requested_pr = _pr(merged=merged)
    assert _main(module, api, monkeypatch, ['--pr', '7']) == 0
    assert api.cancelled == [1]


@pytest.mark.parametrize('mode', [['--sweep'], ['--pr', '7']])
@pytest.mark.parametrize('field', ['head_repository', 'head_branch'])
def test_pr_numbers_cannot_replace_missing_branch_proof_or_strand_valid_runs(
    monkeypatch: pytest.MonkeyPatch, mode: list[str], field: str,
) -> None:
    module = _module()
    unknown = _run(2)
    unknown['pull_requests'] = [{'number': 7}]
    unknown.pop(field)
    # This different open PR has moved to a newer SHA on the same source branch.
    # Missing ownership must not prevent checking that possibility before cancel.
    unrelated = dict(_pr(state='open', sha='other'), number=8)
    api = FakeApi([unknown, _run(1)], open_pulls=[unrelated])
    assert _main(module, api, monkeypatch, mode) == 1
    assert api.cancelled == [1]


@pytest.mark.parametrize('mode', [['--sweep'], ['--pr', '7']])
@pytest.mark.parametrize('match', ['number', 'sha'])
def test_positive_open_association_preserves_incomplete_run_without_requiring_branch(
    monkeypatch: pytest.MonkeyPatch, mode: list[str], match: str,
) -> None:
    module = _module()
    row = _run(1)
    row.pop('head_repository')
    row['pull_requests'] = [{'number': 7}] if match == 'number' else []
    opened = _pr(state='open', sha='other' if match == 'number' else 'a' * 40)
    if match == 'sha':
        opened['number'] = 8
    api = FakeApi([row], open_pulls=[opened])
    assert _main(module, api, monkeypatch, mode) == 0
    assert api.cancelled == []
