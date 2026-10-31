"""Documentation and test-only changes skip builds without hiding runtime edits."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_scope", ROOT / ".github/scripts/ci_scope.py")
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


@pytest.mark.parametrize("path", [
    "README.md", "docs/install/windows.md", "docs/readme/README_CN.md",
    "docs/sponsors/spicrawl.svg", "changelog.d/3721.docs.md", "mobile/README.md",
    "tests/test_mobile_contract.py", "electron/src/main/data-relocation.test.ts",
    "electron/tests/workflow-execution-smoke.mjs", "conftest.py",
    "mobile/android/app/src/test/kotlin/ExampleTest.kt",
    "mobile/android/app/src/androidTest/kotlin/ExampleTest.kt",
    "mobile/ios/VoiceStudioKit/Tests/VoiceStudioKitTests/ExampleTests.swift",
    "mobile/ios/VoiceStudioUITests/ExampleTests.swift",
])
def test_documentation_and_tests_skip_runtime_builds(path):
    assert not scope.runtime_needed([{"status": "modified", "filename": path}], 3000)


@pytest.mark.parametrize("path", [
    "electron/src/main/backend.ts", "backend/core/config.py", "mobile/contract/models.json",
    "mobile/android/features/src/main/kotlin/Example.kt", "mobile/ios/VoiceStudio/App.swift",
    "docs/logo.svg", "docs/logo.png", "docs/licensing/model-license-data.json", "package.json", "bun.lock", "pyproject.toml",
    ".github/workflows/ci.yml", ".github/scripts/ci_scope.py", "scripts/prepare_resources.py",
])
def test_mixed_changes_still_run_runtime_builds(path):
    assert scope.runtime_needed([
        {"status": "modified", "filename": "README.md"},
        {"status": "modified", "filename": path},
    ], 3000)


def test_rename_cannot_hide_deleted_runtime_code_behind_a_documentation_name():
    assert scope.runtime_needed([{"status": "renamed", "filename": "docs/old-code.md",
                                  "previous_filename": "electron/src/main/backend.ts"}], 3000)
    assert not scope.runtime_needed([{"status": "renamed", "filename": "docs/new.md",
                                      "previous_filename": "docs/old.md"}], 3000)
    assert scope.runtime_needed([{"status": "renamed", "filename": "docs/new.md"}], 3000)


@pytest.mark.parametrize("limit", [300, 3000])
def test_empty_or_truncated_change_lists_run_everything(limit):
    assert scope.runtime_needed([], limit)
    assert scope.runtime_needed([{"status": "modified", "filename": "README.md"}] * limit, limit)


@pytest.mark.parametrize("event_name,event", [
    ("pull_request", {"number": 1}),
    ("push", {"before": "a" * 40, "after": "b" * 40}),
    ("merge_group", {"merge_group": {"base_sha": "a" * 40, "head_sha": "b" * 40}}),
])
def test_api_scope_covers_pr_push_and_merge_queue(monkeypatch, event_name, event):
    requests = []
    def run(args, **kwargs):
        requests.append(args)
        files = [{"status": "modified", "filename": "README.md"}]
        return subprocess.CompletedProcess(args, 0, json.dumps([files] if event_name == "pull_request" else files))
    monkeypatch.setattr(scope.subprocess, "run", run)
    assert not scope.detect(event_name, event, "owner/repo")
    if event_name == "pull_request":
        assert "--paginate" in requests[0] and "--slurp" in requests[0]
        assert "--jq" not in requests[0], "gh cannot combine --slurp with --jq"
    else:
        assert "/compare/" in requests[0][2]


def test_detection_failure_emits_runtime_true(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text('{"number": 1}')
    output = tmp_path / "output"
    for key, value in {"GITHUB_EVENT_PATH": str(event), "GITHUB_EVENT_NAME": "pull_request",
                       "GITHUB_REPOSITORY": "owner/repo", "GITHUB_OUTPUT": str(output)}.items():
        monkeypatch.setenv(key, value)
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "gh")
    monkeypatch.setattr(scope.subprocess, "run", fail)
    scope.main()
    assert output.read_text() == "runtime=true\n"


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule", "release"])
def test_manual_and_scheduled_runs_remain_full(event):
    assert scope.detect(event, {}, "owner/repo")


def test_only_an_explicit_successful_skip_can_satisfy_the_required_check():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    gate = workflow["jobs"]["test"]
    assert gate["name"] == "Tests (backend + frontend)"
    script = gate["steps"][0]["run"]
    env = {"RUNTIME_NEEDED": "false", "CHANGES_RESULT": "success", "PYTHON": "skipped",
           "ELECTRON": "skipped", "SMOKES": "skipped", "ELECTRON_NEEDED": "true"}
    assert subprocess.run(["bash", "-eu", "-c", script], env=env, capture_output=True).returncode == 0
    for override in ({"CHANGES_RESULT": "failure"}, {"RUNTIME_NEEDED": ""}, {"PYTHON": "failure"}):
        assert subprocess.run(["bash", "-eu", "-c", script], env=env | override, capture_output=True).returncode != 0


@pytest.mark.parametrize("filename", ["ci.yml", "mobile.yml", "security.yml", "install-smoke.yml", "cosyvoice-dependencies.yml"])
def test_all_workflows_share_the_conservative_runtime_classifier(filename):
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    assert jobs["changes"]["outputs"]["runtime"] == "${{ steps.scope.outputs.runtime }}"
    assert any(step.get("run") == "python3 .github/scripts/ci_scope.py" for step in jobs["changes"]["steps"])
    names = {"ci.yml": ["installer", "test-python", "test-electron", "electron-smokes", "ui-crawler", "linux-native-package", "electron-ui"],
             "mobile.yml": ["android", "ios", "ios-inference", "android-inference", "android-screenshots"],
             "security.yml": ["codeql", "bandit", "dependencies"],
             "install-smoke.yml": ["install"], "cosyvoice-dependencies.yml": ["native-dependencies"]}
    for name in names[filename]:
        assert "needs.changes.outputs.runtime != 'false'" in jobs[name]["if"]


def test_backend_smokes_run_after_success_despite_a_skipped_internal_approval():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    smoke = workflow["jobs"]["smoke-matrix"]
    assert smoke["needs"] == "test-python"
    # An explicit status function avoids Actions' implicit success() propagating
    # the skipped approve-external ancestor through an otherwise successful job.
    assert "!cancelled()" in smoke.get("if", "")
    assert "needs.test-python.result == 'success'" in smoke["if"]
