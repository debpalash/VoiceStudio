"""A corrupt NDK or CMake download must get retried, in every Android workflow."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "mobile/android/install-sdk-packages.sh"

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash"), reason="runs on the Linux CI runner"
)


def test_workflows_install_sdk_packages_through_the_retrying_script():
    workflows = list((ROOT / ".github/workflows").glob("*.y*ml"))
    direct = [path.name for path in workflows if "sdkmanager" in path.read_text(encoding="utf-8")]
    assert direct == []
    using = [path.name for path in workflows if SCRIPT.name in path.read_text(encoding="utf-8")]
    assert sorted(using) == ["android-play-internal.yml", "mobile-release.yml", "mobile.yml"]


def test_release_recovery_of_an_older_tag_still_finds_the_installer():
    """`release_ref` can check out a tag that predates the script."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile-release.yml").read_text(encoding="utf-8"))
    steps = next(job["steps"] for job in workflow["jobs"].values() if any(
        step.get("name") == "NDK and CMake" for step in job["steps"]
    ))
    names = [step.get("name") for step in steps]
    tools = steps[names.index("Checkout Android SDK installer")]
    assert tools["with"]["ref"] == "${{ job.workflow_sha }}"
    assert SCRIPT.relative_to(ROOT).as_posix() in tools["with"]["sparse-checkout"]
    install = steps[names.index("NDK and CMake")]
    assert names.index(tools["name"]) < names.index(install["name"])
    assert f'{tools["with"]["path"]}/{SCRIPT.relative_to(ROOT).as_posix()}' in install["run"]


def run_with_failures(tmp_path: Path, failures: int) -> tuple[subprocess.CompletedProcess, int]:
    """Runs the script against a fake sdkmanager that fails `failures` times."""
    tool = tmp_path / "cmdline-tools/latest/bin/sdkmanager"
    tool.parent.mkdir(parents=True)
    calls = tmp_path / "calls"
    tool.write_text(
        "#!/usr/bin/env bash\n"
        f'echo x >> "{calls}"\n'
        # A retry that reused the partial download would still see it here.
        f'if [ -e "{tmp_path}/.temp/partial" ]; then exit 2; fi\n'
        f'if [ "$(wc -l < "{calls}")" -le {failures} ]; then\n'
        f'  mkdir -p "{tmp_path}/.temp"; touch "{tmp_path}/.temp/partial"; exit 1\n'
        "fi\n"
    )
    tool.chmod(0o755)
    env = {**os.environ, "ANDROID_HOME": str(tmp_path), "ANDROID_SDK_RETRY_DELAY": "0"}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    return result, len(calls.read_text(encoding="utf-8").splitlines())


@needs_bash
def test_a_corrupt_download_is_cleared_and_retried(tmp_path):
    result, calls = run_with_failures(tmp_path, failures=2)
    assert result.returncode == 0
    assert calls == 3


@needs_bash
def test_gives_up_after_three_attempts(tmp_path):
    result, calls = run_with_failures(tmp_path, failures=99)
    assert result.returncode == 1
    assert calls == 3
