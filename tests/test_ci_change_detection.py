"""CI skips the Electron-only jobs for pull requests that provably cannot affect them.

The detection script is extracted from ci.yml and run for real (bash with the same
flags Actions uses) against a fake `gh`, so the shipped regex and control flow are
what is tested. The rule is fail-safe: an allowlist, and any doubt runs everything.
An independent review found three ways a breaking PR could be skipped (a rename out
of electron/, root files the packaging contract reads, and a swallowed grep error);
each has a test below.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
STEP = next(s for s in WORKFLOW["jobs"]["changes"]["steps"] if s.get("id") == "detect")
SCRIPT = STEP["run"]
HAS_GREP_P = subprocess.run(["grep", "-P", "x"], input=b"", capture_output=True).returncode != 2
pytestmark = pytest.mark.skipif(not HAS_GREP_P, reason="needs GNU grep -P, as on the Actions runner")


def _detect(tmp_path: Path, rows, *, event="pull_request", gh_exit=0, gh_output_then_fail=False, grep_exit=None):
    """rows: iterable of (status, filename) as `gh api ... --jq` prints them (TSV)."""
    listing = "\n".join(f"{status}\t{name}" for status, name in rows)
    gh = tmp_path / "gh"
    if gh_exit:
        body = f"echo boom >&2; exit {gh_exit}"
    else:
        body = f"cat <<'LIST'\n{listing}\nLIST" + ("\nexit 1" if gh_output_then_fail else "")
    gh.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    if grep_exit is not None:  # a grep that is broken on this runner (e.g. BSD grep without -P)
        fake = tmp_path / "grep"
        fake.write_text(f"#!/bin/sh\ncat >/dev/null\nexit {grep_exit}\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "output"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out),
               EVENT=event, PR="1", REPO="o/r", GH_TOKEN="x")
    # `shell: bash` in Actions is: bash --noprofile --norc -eo pipefail {0}
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", SCRIPT],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return out.read_text(encoding="utf-8").strip(), proc.stdout


def _mod(*names):
    return [("modified", n) for n in names]


def test_the_step_uses_the_pipefail_shell():
    assert STEP.get("shell") == "bash"


@pytest.mark.parametrize("files", [
    ["backend/api/routers/dub_export.py", "tests/test_dub_export.py"],
    ["CHANGELOG.md", "docs/RELEASING.md", "README.md", "LICENSE-NOTICE.md"],   # edits only
    ["omnivoice/utils/text.py", "uv.lock"],
    ["scripts/release.py", "tests/backend/api/test_x.py"],
    ["mobile/android/app/build.gradle.kts", "mobile/contract/openapi.json"],
    [".github/ISSUE_TEMPLATE/bug.yml", "Makefile"],
    [".github/workflows/docker.yml", ".github/workflows/mobile.yml"],
    [".github/scripts/scam_guard.py", ".github/CONTRIBUTING.md"],
])
def test_python_docs_and_mobile_edits_skip_the_electron_jobs(tmp_path, files):
    assert _detect(tmp_path, _mod(*files))[0] == "electron=false"


@pytest.mark.parametrize("files", [
    ["electron/src/renderer/src/app.tsx"],
    ["backend/x.py", "electron/src/main/updater.ts"],            # one Electron file is enough
    ["native/desktop-bridge/src/lib.rs"],
    ["package.json"], ["bun.lock"], ["config/app.json"],
    ["pyproject.toml"],                                         # the contract parses its `readme =` key
    ["tests/fixtures/longform_parser_cases.json"],              # shared with the JS suite
    ["tests/frontend/ssml.test.mjs"],
    [".github/workflows/ci.yml"], [".github/workflows/electron-release.yml"],
    [".github/workflows/electron-build.yml"], ["backend/x.py", ".github/workflows/ci.yml"],
    ["scripts/install.sh"], ["scripts/export-mobile-tokens.ts"],
    ["something/unknown.txt"], ["LICENSE"], ["store/listing.json"],
])
def test_anything_else_runs_the_electron_jobs(tmp_path, files):
    assert _detect(tmp_path, _mod(*files))[0] == "electron=true"


# --- the three bypasses found in review -------------------------------------------

def test_a_file_renamed_out_of_electron_runs_everything(tmp_path):
    """`git mv electron/src/app.tsx docs/app.tsx` lists only the NEW path."""
    assert _detect(tmp_path, [("renamed", "docs/app.tsx")])[0] == "electron=true"


@pytest.mark.parametrize("name", ["LICENSE-NOTICE.md", "README.md", "uv.lock", "backend/__init__.py"])
def test_deleting_a_file_the_packaging_contract_requires_runs_everything(tmp_path, name):
    assert _detect(tmp_path, [("removed", name)])[0] == "electron=true"


@pytest.mark.parametrize("grep_exit", [2, 127])
def test_a_broken_grep_never_means_skip(tmp_path, grep_exit):
    """grep exit 2 (bad pattern, no -P) was swallowed by `|| true` and gave false."""
    rows = _mod("electron/src/main/updater.ts")
    assert _detect(tmp_path, rows, grep_exit=grep_exit)[0] == "electron=true"


# --- found by the second review: early-exit consumers under pipefail ------------------
# `grep -q` / `head -5` close the pipe early; the producer dies of SIGPIPE, `pipefail`
# reports the pipeline as failed, and on a list larger than the pipe buffer (64 KiB)
# "a file was deleted" read as "no match". The lists below are well past that.

def _bulk(prefix, n, status="modified"):
    return [(status, f"{prefix}/module_{i:04}.py") for i in range(n)]


@pytest.mark.parametrize("first", [("removed", "README.md"), ("removed", "LICENSE-NOTICE.md"),
                                   ("renamed", "docs/app.tsx"), ("removed", "uv.lock")])
@pytest.mark.parametrize("position", ["first", "last"])
def test_a_deletion_or_rename_is_found_however_large_the_list_and_wherever_it_sits(tmp_path, first, position):
    bulk = _bulk("backend/services", 2500) + [("modified", f"docs/page{i:04}.md") for i in range(400)]
    rows = [first] + bulk if position == "first" else bulk + [first]
    # well past the 64 KiB pipe buffer, but under the 3000-file cap (which would mask the bug)
    assert 100_000 < sum(len(f"{a}\t{b}\n") for a, b in rows) and len(rows) < 3000
    assert _detect(tmp_path, rows)[0] == "electron=true"


def test_a_huge_electron_pr_does_not_crash_the_detection_step(tmp_path):
    """`printf | head -5` exited 141 here and the step failed before writing its output."""
    rows = [("modified", f"electron/src/renderer/file{i:04}.tsx") for i in range(2500)]
    result, stdout = _detect(tmp_path, rows)    # _detect asserts the step itself exited 0
    assert result == "electron=true"
    assert stdout.count("    electron/") == 5


def test_a_huge_but_safe_pr_still_skips(tmp_path):
    rows = _bulk("backend/services", 2000) + [("modified", f"docs/page{i:04}.md") for i in range(900)]
    assert _detect(tmp_path, rows)[0] == "electron=false"


@pytest.mark.parametrize("status", ["copied", "changed", "unchanged", "removed", "renamed", "weird"])
def test_only_added_and_modified_files_can_be_skipped(tmp_path, status):
    """`changed` is a mode/type change (a file turned into a symlink); fail safe on any status."""
    assert _detect(tmp_path, [(status, "backend/x.py")])[0] == "electron=true"


def test_added_and_modified_mixed_is_still_skippable(tmp_path):
    assert _detect(tmp_path, [("added", "backend/new.py"), ("modified", "docs/a.md")])[0] == "electron=false"


@pytest.mark.parametrize("name", [
    "backend/assets/samples/dictation/en_conversational.wav",   # played by the gallery smoke
    "backend/assets/anything.bin",
])
def test_backend_assets_are_read_by_the_renderer_smokes_so_they_run_everything(tmp_path, name):
    assert _detect(tmp_path, _mod(name))[0] == "electron=true"


def test_the_rest_of_backend_is_still_skippable(tmp_path):
    assert _detect(tmp_path, _mod("backend/assets_helper.py", "backend/api/x.py"))[0] == "electron=false"


# --- other fail-safes --------------------------------------------------------------

def test_3000_files_is_the_api_cap_so_it_runs_everything(tmp_path):
    rows = _mod(*[f"docs/page{i}.md" for i in range(2999)] + ["electron/src/x.ts"])
    assert _detect(tmp_path, rows)[0] == "electron=true"
    assert _detect(tmp_path, _mod(*[f"docs/page{i}.md" for i in range(2999)]))[0] == "electron=false"


def test_hostile_file_names_cannot_start_a_workflow_command(tmp_path):
    rows = _mod("::set-output name=electron::false", "::add-mask::value", "electron/a.ts")
    result, stdout = _detect(tmp_path, rows)
    assert result == "electron=true"
    assert not [line for line in stdout.splitlines() if line.startswith("::")], stdout


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_legacy_electron_scope_remains_conservative_off_pull_requests(tmp_path, event):
    assert _detect(tmp_path, _mod("docs/only.md"), event=event)[0] == "electron=true"


def test_a_failed_partial_or_empty_lookup_runs_everything(tmp_path):
    assert _detect(tmp_path, [], gh_exit=1)[0] == "electron=true"
    assert _detect(tmp_path, [])[0] == "electron=true"
    # gh printed a page of safe files, then failed (partial pagination)
    assert _detect(tmp_path, _mod("docs/a.md"), gh_output_then_fail=True)[0] == "electron=true"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_the_jq_expression_emits_status_and_filename_for_renames_too(tmp_path):
    expr = re.search(r"--jq '([^']+)'", SCRIPT).group(1)
    api = [{"status": "renamed", "filename": "docs/app.tsx", "previous_filename": "electron/app.tsx"},
           {"status": "modified", "filename": "backend/x.py"}]
    got = subprocess.run(["jq", "-r", expr], input=json.dumps(api), capture_output=True, text=True).stdout
    assert got.splitlines() == ["renamed\tdocs/app.tsx", "modified\tbackend/x.py"]


# --- structure ----------------------------------------------------------------------

def test_the_electron_jobs_are_gated_on_the_detection_and_survive_its_failure():
    jobs = WORKFLOW["jobs"]
    for name in ("ui-crawler", "linux-native-package", "electron-ui", "test-electron", "electron-smokes"):
        assert jobs[name]["needs"] == "changes", name
        condition = jobs[name]["if"]
        assert "!cancelled()" in condition and "outputs.electron != 'false'" in condition, name
        assert "needs.changes.result != 'skipped'" in condition, name
    # the merge gate always reports, even if detection broke
    assert jobs["test-python"]["needs"] == "changes"
    python_condition = jobs["test-python"]["if"]
    assert "!cancelled()" in python_condition and "needs.changes.result != 'skipped'" in python_condition
    assert "needs.changes.outputs.runtime != 'false'" in python_condition
    # `test` is the required check: it must wait for both halves and always report,
    # and may accept a skipped Electron half only when detection said "false".
    assert set(jobs["test"]["needs"]) == {"changes", "test-python", "test-electron", "electron-smokes"}
    assert jobs["test"]["if"] == "${{ !cancelled() }}"
    gate = "\n".join(str(s.get("run", "")) for s in jobs["test"]["steps"])
    assert '[ "$PYTHON" = success ]' in gate
    assert '"$result" = skipped' in gate and '"$ELECTRON_NEEDED" = false' in gate
    assert '"$ELECTRON" "$SMOKES"' in gate
    electron_steps = {s["name"] for s in jobs["test-electron"]["steps"] if "name" in s}
    assert {
        "Frontend unit tests", "Electron typecheck, tests and production contract",
        "Snap artifact repair regression",
    } <= electron_steps
    smoke_steps = {s["name"] for s in jobs["electron-smokes"]["steps"] if "name" in s}
    assert {"Install Playwright chromium", "Electron renderer workflow smokes"} <= smoke_steps
    # the crawler is sharded; every shard must be run exactly once
    crawler = jobs["ui-crawler"]
    shards = crawler["strategy"]["matrix"]["shard"]
    assert sorted(shards) == list(range(1, len(shards) + 1))
    assert f"/{len(shards)}" in crawler["name"]
    crawl_env = next(s["env"] for s in crawler["steps"] if s.get("name") == "Crawl every button")
    assert crawl_env["VOICESTUDIO_CRAWL_SHARD"] == "${{ matrix.shard }}/" + str(len(shards))
    # nothing Electron-only stays behind in the Python job
    assert not electron_steps & {s.get("name") for s in jobs["test-python"]["steps"]} - {
        "Setup Node 22", "Setup Bun", "System deps (ffmpeg and squashfs-tools)",
        "Cache bun deps", "Install Electron deps",
    }


def test_required_check_names_are_unchanged():
    names = {j["name"] for j in WORKFLOW["jobs"].values()}
    assert {"Tests (backend + frontend)", "Linux native package",
            "Electron installer contracts"} <= names
    assert any(n.startswith("UI crawler (") for n in names)
    assert any(n.startswith("Desktop UI (") for n in names)


# --- security.yml: skip the code scanners for docs/mobile-only pull requests ------------

SECURITY = yaml.safe_load((ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8"))


def test_security_code_scanners_skip_docs_only_prs_but_the_secret_scan_never_does():
    jobs = SECURITY["jobs"]
    assert "needs" not in jobs["secrets"] and "if" not in jobs["secrets"]
    for name in ("codeql", "bandit", "dependencies"):
        assert jobs[name]["needs"] == "changes", name
        condition = jobs[name]["if"]
        assert "!cancelled()" in condition and "outputs.code != 'false'" in condition, name


def _security_detect(rows, tmp_path, event="pull_request"):
    script = next(step["run"] for step in SECURITY["jobs"]["changes"]["steps"] if step.get("id") == "detect")
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_ROWS\"\n", encoding="utf-8")
    gh.chmod(0o755)
    out = tmp_path / "out"
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", EVENT=event, PR="1",
               REPO="o/r", GITHUB_OUTPUT=str(out), GH_TOKEN="x", FAKE_ROWS=rows)
    done = subprocess.run(["bash", "-eo", "pipefail", "-c", script], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell and grep -P")
@pytest.mark.parametrize("rows,expected", [
    ("modified\tdocs/a.md\nadded\tmobile/ios/App.swift", "code=false"),
    ("modified\tREADME.md", "code=false"),
    ("modified\tbackend/x.py\nmodified\tdocs/a.md", "code=true"),
    ("modified\telectron/src/a.ts", "code=true"),
    ("removed\tdocs/a.md", "code=true"),
    ("renamed\tdocs/a.md", "code=true"),
])
def test_security_change_detection(rows, expected, tmp_path):
    assert _security_detect(rows, tmp_path) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell and grep -P")
def test_security_change_detection_runs_everything_off_pull_requests(tmp_path):
    assert _security_detect("modified\tdocs/a.md", tmp_path, event="push") == "code=true"


# --- docker.yml: skip the image build for changes that cannot alter the image -----------

DOCKER = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text(encoding="utf-8"))


def test_docker_main_pushes_skip_docs_and_tests_but_keep_runtime_changes():
    ignored = set(DOCKER[True]["push"]["paths-ignore"])
    assert {"docs/**", "mobile/**", "changelog.d/**"} <= ignored
    # Markdown and tests skip images by owner policy. Docker Hub text publishes
    # separately; test_docker_workflow.py validates that lightweight trigger.
    for path in ("README.md", "deploy/dockerhub-overview.md", "tests/test_api.py", "electron/tests/installer.test.mjs"):
        assert any(_matches(path, pattern) for pattern in ignored), path
    for path in ("deploy/Dockerfile", "backend/main.py",
                 ".github/workflows/docker.yml", "pyproject.toml"):
        assert not any(_matches(path, pattern) for pattern in ignored), path
    assert "paths-ignore" not in (DOCKER[True].get("release") or {})


def _matches(path, pattern):
    import fnmatch
    # GitHub's leading **/ also matches zero directory segments.
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])
    )


def test_fork_pull_requests_wait_for_approval_before_any_job_runs():
    jobs = WORKFLOW["jobs"]
    gate = jobs["approve-external"]
    assert gate["environment"] == "external-pr"
    # only fork PRs wait; same-repo branches, pushes and the merge queue skip the job
    assert "head.repo.full_name != github.repository" in gate["if"]
    assert "pull_request" in gate["if"]
    assert jobs["changes"]["needs"] == "approve-external"
    # a skipped approval (internal PR) must not stop `changes`; a rejected one must
    assert "needs.approve-external.result != 'failure'" in jobs["changes"]["if"]
    # every job reachable without `changes` would let a fork PR use a runner unapproved
    for name, job in jobs.items():
        if name in {"approve-external", "changes"}:
            continue
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert needs, f"{name} has no needs, so it would run before the approval"
        assert "changes" in needs or "test-python" in needs, name
