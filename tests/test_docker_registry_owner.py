"""Fork publishing must never target upstream GHCR images or layer caches."""
import os
import re
from pathlib import Path, PureWindowsPath
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/docker.yml"
JOBS = ("build-and-push", "build-and-push-rocm", "build-and-push-cuda-arm64")


def resolved_env(tmp_path, owner, job="build-and-push"):
    """Execute the exact shell step used by every image flavor."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = next(step for step in workflow["jobs"][job]["steps"] if step.get("id") == "source")
    git_bash = Path(os.environ.get("ProgramFiles", "")) / "Git/bin/bash.exe"
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash and PureWindowsPath(bash).parent.name.casefold() in {"system32", "sysnative"}:
        bash = None  # WSL launchers do not inherit these native runner variables.
    if bash is None:
        pytest.skip("Bash required for Docker workflow shell contracts")
    exported, output = tmp_path / "env", tmp_path / "output"
    exported.touch()
    result = subprocess.run([bash, "-e", "-c", source["run"]], cwd=ROOT,
        env={**os.environ, "GITHUB_REPOSITORY_OWNER": owner,
             "GITHUB_ENV": exported.as_posix(), "GITHUB_OUTPUT": output.as_posix()},
        capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split("=", 1) for line in exported.read_text(encoding="utf-8").splitlines())
    return values


@pytest.mark.parametrize("owner,namespace", [
    ("debpalash", "debpalash"), ("dutch2005", "dutch2005"), ("Mixed-Case", "mixed-case"),
])
@pytest.mark.parametrize("job", JOBS)
def test_image_and_cache_coordinates_follow_repository_owner(tmp_path, owner, namespace, job):
    """Execute the workflow resolver and inspect every publish/cache consumer."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job]["steps"]
    source = next(step for step in steps if step.get("id") == "source")
    values = resolved_env(tmp_path, owner, job)
    assert values["IMAGE_NAME"] == f"{namespace}/voicestudio"
    assert values["LEGACY_IMAGE_NAME"] == f"{namespace}/omnivoice-studio"
    assert values["BUILDCACHE_IMAGE"] == f"ghcr.io/{namespace}/voicestudio-buildcache"
    assert not {"IMAGE_NAME", "LEGACY_IMAGE_NAME", "BUILDCACHE_IMAGE"} & workflow["env"].keys()
    metadata = next(step for step in steps if step.get("id") == "meta")
    assert steps.index(source) < steps.index(metadata)
    assert "${{ env.IMAGE_NAME }}" in metadata["with"]["images"]
    assert "${{ env.LEGACY_IMAGE_NAME }}" in metadata["with"]["images"]
    build = next(step for step in steps if step.get("uses", "").startswith("docker/build-push-action@"))
    for key in ("cache-from", "cache-to"):
        if key in build["with"]:
            assert "${{ env.BUILDCACHE_IMAGE }}" in build["with"][key]
    for step in steps:
        assert "ghcr.io/debpalash/" not in str(step), "Forks must never publish/cache upstream"


def test_owner_resolution_supports_macos_system_bash():
    """Bash 3.2 has no case-conversion expansion; run portable ASCII folding."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in JOBS:
        source = next(step for step in workflow["jobs"][job]["steps"] if step.get("id") == "source")
        assert not re.search(r"\$\{[^}]*[,^][,^]?[^}]*\}", source["run"]), "Bash 4-only case conversion"
