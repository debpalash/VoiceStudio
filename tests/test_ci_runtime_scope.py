"""Documentation and test-only changes skip builds without hiding runtime edits."""
import base64
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
    assert output.read_text(encoding="utf-8") == "runtime=true\ncla=clear\n"


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule", "release"])
def test_manual_and_scheduled_runs_remain_full(event):
    assert scope.detect(event, {}, "owner/repo")


def test_only_an_explicit_successful_skip_can_satisfy_the_required_check():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
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
    workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
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
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    smoke = workflow["jobs"]["smoke-matrix"]
    assert smoke["needs"] == "test-python"
    # An explicit status function avoids Actions' implicit success() propagating
    # the skipped approve-external ancestor through an otherwise successful job.
    assert "!cancelled()" in smoke.get("if", "")
    assert "needs.test-python.result == 'success'" in smoke["if"]


# ── The CLA hold ────────────────────────────────────────────────────────

ALICE = {"login": "alice", "id": 1001, "type": "User"}


@pytest.mark.parametrize("pr,signed,held", [
    ({"user": ALICE}, set(), True),
    ({"user": ALICE}, {1001}, False),
    ({"user": ALICE}, None, False),  # signatures unreadable: run CI
    ({"user": ALICE, "labels": [{"name": "cla-signed"}]}, set(), False),
    ({"user": ALICE, "labels": [{"name": "cla-override"}]}, set(), False),
    ({"user": {"login": "debpalash", "id": 4178343, "type": "User"}}, set(), False),
    ({"user": {"login": "dependabot[bot]", "id": 49699333, "type": "Bot"}}, set(), False),
    ({"user": {"login": "renovate[bot]", "id": 29139614, "type": "Bot"}}, set(), False),
    ({"user": {"login": "ghost", "type": "User"}}, set(), False),
])
def test_cla_held(pr, signed, held):
    assert scope.cla_held(pr, signed) is held


def _scope_main(tmp_path, monkeypatch, event_name, event, api, argv=()):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    output = tmp_path / "output"
    for key, value in {"GITHUB_EVENT_PATH": str(path), "GITHUB_EVENT_NAME": event_name,
                       "GITHUB_REPOSITORY": "owner/repo", "GITHUB_OUTPUT": str(output)}.items():
        monkeypatch.setenv(key, value)
    calls = []

    def run(args, **kwargs):
        calls.append(args[2])
        return subprocess.CompletedProcess(args, 0, json.dumps(api(args[2])))

    monkeypatch.setattr(scope.subprocess, "run", run)
    monkeypatch.setattr(scope.sys, "argv", ["ci_scope.py", *argv])
    scope.main()
    return output.read_text(encoding="utf-8"), calls


def _api(signed_ids, labels=()):
    store = {"signatures": [{"id": i} for i in signed_ids]}

    def answer(endpoint):
        if endpoint.endswith("/files"):
            return [[{"status": "modified", "filename": "backend/core/config.py"}]]
        if "/contents/" in endpoint:
            assert endpoint.endswith("?ref=cla-signatures")
            return {"content": base64.b64encode(json.dumps(store).encode()).decode()}
        return {"user": ALICE, "labels": [{"name": n} for n in labels]}
    return answer


def test_unsigned_opener_skips_runtime_ci_explicitly(tmp_path, monkeypatch):
    event = {"number": 7, "pull_request": {"user": ALICE}}
    out, _ = _scope_main(tmp_path, monkeypatch, "pull_request", event, _api(set()))
    assert out == "runtime=false\ncla=held\n"


@pytest.mark.parametrize("signed,labels", [({1001}, ()), (set(), ("cla-override",))])
def test_signed_or_overridden_opener_runs_everything(tmp_path, monkeypatch, signed, labels):
    event = {"number": 7, "pull_request": {"user": ALICE, "labels": []}}  # stale labels in a re-run
    out, _ = _scope_main(tmp_path, monkeypatch, "pull_request", event, _api(signed, labels))
    assert out == "runtime=true\ncla=clear\n"


def test_owner_needs_no_cla_lookup(tmp_path, monkeypatch):
    event = {"number": 7, "pull_request": {"user": {"login": "debpalash", "id": 4178343, "type": "User"}}}
    out, calls = _scope_main(tmp_path, monkeypatch, "pull_request", event, _api(set()))
    assert out == "runtime=true\ncla=clear\n" and all(c.endswith("/files") for c in calls)


def test_documentation_only_change_needs_no_cla_lookup(tmp_path, monkeypatch):
    def api(endpoint):
        assert endpoint.endswith("/files")
        return [[{"status": "modified", "filename": "README.md"}]]
    out, _ = _scope_main(tmp_path, monkeypatch, "pull_request", {"number": 7, "pull_request": {"user": ALICE}}, api)
    assert out == "runtime=false\ncla=clear\n"


def test_cla_lookup_failure_runs_everything(tmp_path, monkeypatch):
    def api(endpoint):
        if "/contents/" in endpoint:
            raise subprocess.CalledProcessError(1, "gh")
        return _api(set())(endpoint)
    out, _ = _scope_main(tmp_path, monkeypatch, "pull_request", {"number": 7, "pull_request": {"user": ALICE}}, api)
    assert out == "runtime=true\ncla=clear\n"


@pytest.mark.parametrize("event_name", ["push", "merge_group"])
def test_main_and_the_merge_queue_are_never_held(event_name):
    assert not scope.cla_hold(event_name, {"pull_request": {"user": ALICE}}, "owner/repo")


def test_cla_only_mode_writes_just_the_hold(tmp_path, monkeypatch):
    out, calls = _scope_main(tmp_path, monkeypatch, "pull_request", {"number": 7, "pull_request": {"user": ALICE}},
                             _api(set()), argv=("--cla",))
    assert out == "cla=held\n" and not any(c.endswith("/files") for c in calls)


def test_the_required_check_names_the_cla_hold():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["jobs"]["changes"]["outputs"]["cla"] == "${{ steps.scope.outputs.cla }}"
    gate = workflow["jobs"]["test"]
    assert gate["env"]["CLA_HOLD"] == "${{ needs.changes.outputs.cla }}"
    env = {"RUNTIME_NEEDED": "false", "CHANGES_RESULT": "success", "PYTHON": "skipped", "ELECTRON": "skipped",
           "SMOKES": "skipped", "ELECTRON_NEEDED": "true", "CLA_HOLD": "held"}
    result = subprocess.run(["bash", "-eu", "-c", gate["steps"][0]["run"]], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and "CLA not signed yet" in result.stdout
