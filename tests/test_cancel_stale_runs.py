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
    return {"id": run_id, "name": f"CI {run_id}", "head_branch": "feature",
            "head_sha": sha, "event": event, "status": status,
            "created_at": created or f"{datetime.date.today().isoformat()}T00:00:00Z"}


def _pr(state: str = "closed", closed: str = CLOSED, sha: str = "a" * 40) -> dict[str, Any]:
    return {"state": state, "closed_at": closed if state == "closed" else None, "head": {"sha": sha}}


class FakeApi:
    """The real 100/page and 1000/search caps, including exact date-filtered pages."""
    def __init__(self, runs: list[dict[str, Any]], *, refuse: set[int] | None = None,
                 pulls: dict[str, list[dict[str, Any]]] | None = None,
                 bad_commits: set[str] | None = None, latency: float = 0,
                 remove_cancelled: bool = False) -> None:
        self.runs = sorted(runs, key=lambda run: (run["created_at"], run["id"]), reverse=True)
        self.refuse = refuse or set()
        self.pulls = pulls
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
    assert api.first_post_at is not None and api.first_post_at <= 12


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
