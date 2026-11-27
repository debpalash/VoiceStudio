"""Maven Central rate limits (HTTP 429) must be retried before every Android Gradle build."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "mobile/android/scripts/resolve-dependencies.sh"
WORKFLOWS = ROOT / ".github/workflows"

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"), reason="runs on the Linux CI runner"
)


def _runs_gradle(step: dict) -> bool:
    script = step.get("run") or (step.get("with") or {}).get("script") or ""
    return "./gradlew" in script and SCRIPT.name not in script


def test_every_gradle_job_resolves_dependencies_through_the_retrying_script_first():
    checked = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            builds = [i for i, step in enumerate(steps) if _runs_gradle(step)]
            if not builds:
                continue
            resolves = [i for i, step in enumerate(steps) if SCRIPT.name in (step.get("run") or "")]
            assert resolves and resolves[0] < builds[0], f"{path.name}:{name} builds before resolving"
            checked.append(f"{path.name}:{name}")
    assert len(checked) >= 5, checked


def test_gradle_reads_the_retry_limit():
    properties = (ROOT / "mobile/android/gradle.properties").read_text(encoding="utf-8")
    # Gradle 8.13 reads max.tentatives; a max.retries key is silently ignored.
    assert "systemProp.org.gradle.internal.repository.max.tentatives=" in properties
    assert "systemProp.org.gradle.internal.repository.max.retries" not in properties


def _run(tmp_path: Path, outcomes: list[str]) -> tuple[int, list[str]]:
    """Runs the script against a fake gradlew that fails per `outcomes`, then passes."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "outcomes").write_text("\n".join(outcomes) + "\n", encoding="utf-8")
    gradlew = project / "gradlew"
    gradlew.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> calls\n'
        "outcome=$(head -n1 outcomes); tail -n +2 outcomes > o.tmp; mv o.tmp outcomes\n"
        'case "$outcome" in\n'
        "  429) echo 'Received status code 429 from server: Too Many Requests'; exit 1;;\n"
        "  broken) echo 'Could not resolve project :core'; exit 1;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gradlew.chmod(0o755)
    env = {**os.environ, "RESOLVE_RETRY_DELAYS": "0 0 0", "GRADLE_USER_HOME": str(tmp_path / "gradle-home")}
    result = subprocess.run(
        ["bash", str(SCRIPT), ":app:prepareRobolectricSdk"],
        cwd=project, env=env, capture_output=True, text=True, timeout=60,
    )
    return result.returncode, (project / "calls").read_text(encoding="utf-8").splitlines()


@needs_bash
def test_rate_limit_is_retried_until_the_download_succeeds(tmp_path):
    status, calls = _run(tmp_path, ["429", "429"])
    assert status == 0
    assert len(calls) == 3
    assert all(":app:prepareRobolectricSdk resolveAllDependencies" in call for call in calls)
    assert all("resolve-all.init.gradle" in call for call in calls)


@needs_bash
def test_other_failures_are_left_to_the_build_without_retrying(tmp_path):
    status, calls = _run(tmp_path, ["broken"])
    assert status == 0
    assert len(calls) == 1


@needs_bash
def test_a_persistent_rate_limit_fails_after_the_last_retry(tmp_path):
    status, calls = _run(tmp_path, ["429"] * 5)
    assert status == 1
    assert len(calls) == 3


@needs_bash
def test_every_later_gradle_run_in_the_job_reads_maven_central_through_its_mirror(tmp_path):
    _run(tmp_path, [])
    installed = tmp_path / "gradle-home" / "init.d" / "maven-central-mirror.init.gradle"
    assert installed.is_file()
    text = installed.read_text(encoding="utf-8")
    assert "https://maven-central.storage-download.googleapis.com/maven2/" in text
    assert "beforeSettings" in text


def test_release_recovery_checks_out_the_mirror_script_with_the_retry():
    workflow = (WORKFLOWS / "mobile-release.yml").read_text(encoding="utf-8")
    assert "mobile/android/scripts/maven-central-mirror.init.gradle" in workflow
