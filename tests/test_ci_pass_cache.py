"""The CI pass cache may skip a job only when its inputs already passed.

`.github/actions/ci-pass` hashes a job's input files; a green job saves a marker
under that key and a later pull-request or merge-queue run with the same inputs
skips its steps. A false skip is worse than a re-run, so this pins: which events
may skip, that the key moves on every kind of input change (content, rename,
deletion, mode), that every later step is gated, that the marker is saved last,
and that gate, release and security workflows never use it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / ".github/actions/ci-pass/action.yml").read_text(encoding="utf-8"))
KEY_SCRIPT = ACTION["runs"]["steps"][0]["run"]
USES = "./.github/actions/ci-pass"
GATE = "steps.pass.outputs.hit != 'true'"
# Required gates, security scans and release paths always run in full.
NEVER = {"cla.yml", "commit-identity.yml", "merge-queue-checks.yml", "security.yml", "scam-guard.yml",
         "electron-release.yml", "mobile-release.yml", "docker.yml"}
SKIPPABLE_EVENTS = {"pull_request", "push", "merge_group", "workflow_dispatch"}


def _workflows():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        yield path.name, yaml.safe_load(path.read_text(encoding="utf-8"))


def _cached_jobs():
    for name, workflow in _workflows():
        for job_id, job in workflow["jobs"].items():
            steps = job.get("steps") or []
            if any(step.get("uses") == USES for step in steps):
                yield name, workflow, job_id, job, steps


def test_the_pass_cache_is_in_use():
    assert {(name, job) for name, _, job, _, _ in _cached_jobs()} >= {
        ("ci.yml", "test-python"), ("ci.yml", "test-electron"), ("ci.yml", "electron-smokes"),
        ("ci.yml", "ui-crawler"), ("ci.yml", "electron-ui"), ("ci.yml", "smoke-matrix"),
        ("mobile.yml", "ios"), ("mobile.yml", "android"),
    }


def test_gate_security_and_release_workflows_never_skip():
    for name, workflow, job_id, _, _ in _cached_jobs():
        assert name not in NEVER, f"{name}:{job_id}"
        triggers = set(workflow.get("on", workflow.get(True)))
        assert triggers <= SKIPPABLE_EVENTS, f"{name} runs on {triggers - SKIPPABLE_EVENTS}"


def test_every_step_after_the_lookup_is_gated_and_the_marker_is_saved_last():
    keys = []
    for name, _, job_id, job, steps in _cached_jobs():
        where = f"{name}:{job_id}"
        assert steps[0]["uses"].startswith("actions/checkout@"), where
        assert steps[1] == {**steps[1], "id": "pass", "uses": USES}, where
        key = steps[1]["with"]["job"]
        if "matrix" in job.get("strategy", {}):
            assert "${{ matrix." in key, f"{where}: matrix legs need their own key"
        keys.append(key)
        *body, save = steps[2:]
        for step in body:
            condition = str(step.get("if", ""))
            assert condition.startswith(GATE) or condition.endswith(f"&& {GATE}"), (
                f"{where}: step {step.get('name') or step.get('uses')} runs on a pass-cache hit")
            assert "||" not in condition, f"{where}: an `||` could bypass the gate"
        assert save["uses"].startswith("actions/cache/save@"), where
        assert save["if"] == f"{GATE} && steps.pass.outputs.record == 'true'", where
        assert save["with"] == {"path": "${{ steps.pass.outputs.path }}", "key": "${{ steps.pass.outputs.key }}"}, where
    assert len(keys) == len(set(keys)), "two jobs share a pass-cache key"


def test_the_lookup_is_exact_and_lookup_only():
    restore = ACTION["runs"]["steps"][1]
    assert restore["uses"].startswith("actions/cache/restore@")
    assert restore["with"]["lookup-only"] is True
    assert "restore-keys" not in restore["with"]
    assert ACTION["outputs"]["hit"]["value"].startswith("${{ steps.key.outputs.skip == 'true' && ")
    assert "steps.lookup.outputs.cache-hit != 'true'" in ACTION["outputs"]["record"]["value"]


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/actions/ci-pass").mkdir(parents=True)
    (repo / ".github/actions/ci-pass/action.yml").write_text("action\n")
    (repo / ".github/workflows/ci.yml").write_text("workflow\n")
    for name in ("app/a.ts", "app/b.ts", "backend/x.py", "backend/assets/s.wav", "docs/c.md"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(name + "\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    return repo


def _key(repo: Path, event: str = "pull_request", content: str = ".", names: str = "") -> dict[str, str]:
    output = repo.parent / "output"
    output.write_text("")
    env = dict(os.environ, GITHUB_WORKFLOW_REF="o/r/.github/workflows/ci.yml@refs/pull/1/merge",
               GITHUB_OUTPUT=str(output), RUNNER_OS="Linux", JOB="job", EVENT=event,
               MARKER=str(repo.parent / "marker"), CONTENT=content, NAMES=names)
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", KEY_SCRIPT],
                          cwd=repo, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (repo.parent / "marker/passed").exists()
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


@needs_git
@pytest.mark.parametrize("event, skip, record", [
    ("pull_request", "true", "true"), ("merge_group", "true", "false"),
    ("push", "false", "true"), ("workflow_dispatch", "false", "true"), ("schedule", "false", "true"),
])
def test_only_pull_requests_and_merge_groups_may_skip(repo, event, skip, record):
    out = _key(repo, event)
    assert (out["skip"], out["record"]) == (skip, record)
    assert out["key"].startswith("ci-pass-v1-job-Linux-")


ELECTRON_LIKE = dict(content=". :(exclude)backend\nbackend/assets", names="backend")


def _edit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", name)


@needs_git
@pytest.mark.parametrize("change", [
    lambda r: _edit(r, "app/a.ts", "edited\n"),                               # content
    lambda r: _git(r, "mv", "app/a.ts", "app/z.ts"),                          # rename, same content
    lambda r: _git(r, "rm", "-qf", "app/b.ts"),                               # deletion
    lambda r: _git(r, "update-index", "--chmod=+x", "app/a.ts"),              # mode only
    lambda r: _edit(r, "backend/assets/s.wav", "new sample\n"),               # added-back subtree
    lambda r: _git(r, "rm", "-qf", "backend/x.py"),                           # names-only deletion
    lambda r: _edit(r, "backend/new.py", "x = 1\n"),                          # names-only addition
    lambda r: _edit(r, ".github/workflows/ci.yml", "changed\n"),             # the workflow itself
    lambda r: _edit(r, ".github/actions/ci-pass/action.yml", "new\n"),        # the action itself
])
def test_every_input_change_moves_the_key(repo, change):
    before = _key(repo, **ELECTRON_LIKE)["key"]
    change(repo)
    assert _key(repo, **ELECTRON_LIKE)["key"] != before


@needs_git
@pytest.mark.parametrize("change", [
    lambda r: _edit(r, "backend/x.py", "edited\n"),           # excluded, names-only: content ignored
    lambda r: (r / "untracked.txt").write_text("not staged"),  # not part of the checkout's index
    lambda r: (r / "app/a.ts").write_text("unstaged edit"),    # the index, not the work tree, is hashed
])
def test_changes_outside_the_inputs_keep_the_key(repo, change):
    before = _key(repo, **ELECTRON_LIKE)["key"]
    change(repo)
    assert _key(repo, **ELECTRON_LIKE)["key"] == before
