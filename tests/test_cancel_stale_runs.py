"""The stale-run sweep must finish the list, not the first page or the first error.

`cancel_stale_runs.py` asks the runs endpoint once per status with
`per_page=100`, so on a repository with more than a hundred unfinished runs the
sweep reported success while everything past the first page kept holding runner
slots. It also let the first cancellation failure (`api()` raises on any HTTP
error except 409) abort the loop, so one unlucky run stranded every run after it.

Both are pinned here against a fake API call: no network, no token.
"""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "cancel_stale_runs.py"


def _module():
    spec = importlib.util.spec_from_file_location("cancel_stale_runs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(run_id: int, status: str = "in_progress") -> dict:
    return {
        "id": run_id,
        "name": f"CI {run_id}",
        "head_branch": "feature",
        "head_sha": "a" * 40,
        "event": "pull_request",
        "status": status,
        "created_at": "2026-11-15T00:00:00Z",
    }


class FakeApi:
    """Pages of runs per (status, page), plus run ids that refuse to cancel."""

    def __init__(self, pages: dict[tuple[str, int], list[dict]], refuse: set[int] | None = None):
        self.pages = pages
        self.refuse = refuse or set()
        self.cancelled: list[int] = []
        self.requests: list[str] = []

    def __call__(self, method: str, path: str, data=None):
        self.requests.append(f"{method} {path}")
        if method == "GET":
            query = parse_qs(urlsplit(path).query)
            status = query.get("status", [""])[0]
            page = int(query.get("page", ["1"])[0])
            if "created" in query:
                key = (status, query["created"][0])
                return {"workflow_runs": self.pages.get(key, [])}
            return {"workflow_runs": self.pages.get((status, page), [])}
        run_id = int(path.rstrip("/").split("/")[-2])
        if run_id in self.refuse:
            raise RuntimeError("502 Bad Gateway")
        self.cancelled.append(run_id)
        return None


def test_a_full_first_page_does_not_hide_the_second():
    module = _module()
    full_page = [_run(1000 + index) for index in range(module.PER_PAGE)]
    api = FakeApi({("in_progress", 1): full_page, ("in_progress", 2): [_run(2000)]})

    runs = module.unfinished_runs(api, "owner/repo")

    assert 2000 in [run["id"] for run in runs], "the run on the second page was missed"
    assert any("page=2" in request for request in api.requests)


def test_a_capped_search_is_re_swept_over_the_lookback(capsys):
    """A status with more runs than the search cap returns needs a narrower window.

    GitHub stops a status-filtered run search at 1000 results and a page at 100, so
    the fixtures respect both. The capped search only ever shows the newest runs,
    which is why the re-sweep walks the sweep's own lookback instead of the dates
    it happened to see: the extra run here sits three days back, outside the range
    the first 1000 results cover.
    """
    module = _module()
    today = datetime.date.today()
    old_day = (today - datetime.timedelta(days=3)).isoformat()
    capped: list[dict] = []
    for index in range(module.SEARCH_CAP):
        run = _run(1000 + index)
        run["created_at"] = f"{today.isoformat()}T00:00:00Z"
        capped.append(run)
    extra = _run(9999)
    extra["created_at"] = f"{old_day}T23:00:00Z"
    older = [_run(8888), _run(8889)]
    for run in older:
        run["created_at"] = f"{old_day}T01:00:00Z"

    # The day window answers with runs created on that day, which is the only
    # thing a `created` filter can return.
    pages: dict = {("in_progress", old_day + ".." + old_day): [*older, extra]}
    for page in range(1, module.SEARCH_CAP // module.PER_PAGE + 2):
        start = (page - 1) * module.PER_PAGE
        pages[("in_progress", page)] = capped[start : start + module.PER_PAGE]
    api = FakeApi(pages)

    runs = module.unfinished_runs(api, "owner/repo")

    assert 9999 in [run["id"] for run in runs], "the day-bucketed sweep must find the older run"
    # The capped search returned real runs; the narrower windows add to them.
    assert 1000 in [run["id"] for run in runs], "the capped search results must survive the re-sweep"
    assert len(runs) == module.SEARCH_CAP + 3
    assert any(f"created={old_day}..{old_day}" in request for request in api.requests)
    assert "search cap" in capsys.readouterr().err


def test_a_day_that_is_capped_is_reported(capsys):
    """One day can hold more runs than a search returns; say so instead of guessing.

    Nothing narrower than a day is derived, so the sweep reports the saturation
    rather than pretending those runs were covered.
    """
    module = _module()
    day = datetime.date.today().isoformat()
    capped: list[dict] = []
    for index in range(module.SEARCH_CAP):
        run = _run(5000 + index)
        run["created_at"] = f"{day}T00:00:00Z"
        capped.append(run)

    pages: dict = {("in_progress", f"{day}..{day}"): capped}
    for page in range(1, module.SEARCH_CAP // module.PER_PAGE + 2):
        start = (page - 1) * module.PER_PAGE
        pages[("in_progress", page)] = capped[start : start + module.PER_PAGE]
    api = FakeApi(pages)

    module.unfinished_runs(api, "owner/repo")

    assert "also reached the search cap" in capsys.readouterr().err


def test_the_re_sweep_is_bounded(monkeypatch, capsys):
    """The cap path must not walk windows until the job times out."""
    module = _module()
    monkeypatch.setattr(module, "MAX_WINDOW_REQUESTS", 2)
    day = datetime.date.today().isoformat()
    capped: list[dict] = []
    for index in range(module.SEARCH_CAP):
        run = _run(7000 + index)
        run["created_at"] = f"{day}T00:00:00Z"
        capped.append(run)

    pages: dict = {}
    for page in range(1, module.SEARCH_CAP // module.PER_PAGE + 2):
        start = (page - 1) * module.PER_PAGE
        pages[("in_progress", page)] = capped[start : start + module.PER_PAGE]
    api = FakeApi(pages)

    module.unfinished_runs(api, "owner/repo")

    err = capsys.readouterr().err
    assert "stopped after 2 day windows" in err
    assert len([r for r in api.requests if "created=" in r]) == 2


def test_one_refused_cancellation_does_not_strand_the_rest():
    module = _module()
    runs = [_run(1), _run(2), _run(3)]
    api = FakeApi({}, refuse={2})

    cancelled, failed = module.cancel(api, "owner/repo", runs, dry_run=False)

    assert api.cancelled == [1, 3], "the runs after the failure must still be cancelled"
    assert (cancelled, failed) == (2, 1)


def test_a_failure_becomes_a_non_zero_exit_status(monkeypatch, capsys):
    module = _module()
    runs = [_run(7)]
    api = FakeApi({}, refuse={7})
    monkeypatch.setattr(module, "api", lambda method, path, token, data=None: api(method, path, data))
    monkeypatch.setattr(module, "stale_in_repo", lambda call, repo, exclude_run=0: runs)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_RUN_ID", "0")

    assert module.main(["--repo", "owner/repo", "--sweep"]) == 1
    captured = capsys.readouterr()
    assert "could not be cancelled" in captured.out + captured.err
