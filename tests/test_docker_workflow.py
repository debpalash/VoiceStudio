"""Release images publish only from a published GitHub Release; :latest is main-only.

A `v*` tag push used to publish :X.Y.Z / :X.Y / :stable (and, through
metadata-action's `latest=auto` default, :latest) before the Electron draft
release was reviewed. These guards keep the release path behind publication
and keep :latest on the rolling main build in both GPU flavors.
"""
from __future__ import annotations

import re

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOCKER = _ROOT / ".github" / "workflows" / "docker.yml"
_RELEASE = _ROOT / ".github" / "workflows" / "electron-release.yml"
_JOBS = ("build-and-push", "build-and-push-rocm", "build-and-push-cuda-arm64")
_MAIN_ONLY = "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def _meta_step(job: str) -> dict:
    steps = _load(_DOCKER)["jobs"][job]["steps"]
    return next(s for s in steps if s.get("uses", "").startswith("docker/metadata-action@"))


def _tag_lines(job: str) -> list[str]:
    return [line.strip() for line in _meta_step(job)["with"]["tags"].splitlines() if line.strip()]


def test_release_images_wait_for_a_published_release():
    on = _load(_DOCKER)["on"]
    assert on["release"]["types"] == ["published"]
    assert "tags" not in (on.get("push") or {}), "a tag push must not publish release images"
    assert on["push"]["branches"] == ["main"]


def test_metadata_never_adds_latest_implicitly():
    for job in _JOBS:
        flavor = _meta_step(job)["with"].get("flavor", "")
        assert "latest=false" in flavor.split(), f"{job} must set flavor latest=false"


def test_rolling_tags_come_only_from_main_pushes():
    rolling = {
        "build-and-push": ("latest", "main"),
        "build-and-push-rocm": ("rocm",),
        "build-and-push-cuda-arm64": ("cuda-arm64",),
    }
    for job, names in rolling.items():
        lines = _tag_lines(job)
        for name in names:
            matches = [line for line in lines if f"value={name}," in line]
            assert len(matches) == 1, f"{job} must emit :{name} exactly once"
            assert _MAIN_ONLY in matches[0], f"{job} :{name} must be main-push only"
        assert not any("value=latest" in line and _MAIN_ONLY not in line for line in lines)


def test_release_tags_come_only_from_release_events():
    for job in _JOBS:
        release_lines = [line for line in _tag_lines(job) if "type=semver" in line or "value=stable" in line]
        assert len(release_lines) == 3, job
        for line in release_lines:
            assert "github.event_name == 'release'" in line, line
            assert "github.event_name == 'push'" not in line, line
        stable = next(line for line in release_lines if "value=stable" in line)
        assert "!github.event.release.prerelease" in stable


def test_token_published_releases_still_publish_images():
    """A GITHUB_TOKEN publish fires no `release` event, so it must dispatch docker.yml."""
    job = _load(_RELEASE)["jobs"]["release"]
    assert job["permissions"].get("actions") == "write"
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "gh workflow run docker.yml" in runs
    assert "backfill_release=true" in runs
    # Stable promotes :stable; a beta dispatches the same path with promotion off.
    assert 'promote_stable="$PROMOTE"' in runs and "PROMOTE=true" in runs
    # Every -f key must be a real docker.yml dispatch input, or the run fails at publish time.
    command = runs[runs.index("gh workflow run docker.yml"):]
    command = command[: command.find("\n", command.find("promote_stable"))]
    keys = set(re.findall(r"-f\s+([A-Za-z_]+)=", command))
    on = _load(_DOCKER)["on"]
    inputs = set((on.get("workflow_dispatch") or {}).get("inputs") or {})
    assert {"backfill_release", "release_ref", "promote_stable"} <= keys <= inputs, (keys, inputs)


def test_image_builds_keep_their_layer_cache_in_ghcr_not_the_actions_cache():
    """The Actions cache is capped at 10 GB per repository. Docker layers (5+ GB, one of
    4 GB) filled it and evicted the iOS native-runtime, Gradle and bun caches, so mobile CI
    rebuilt for 17 minutes. The cache lives in a registry package instead; a failed export
    must never fail the image publish, and only the CUDA job writes it."""
    workflow = _load(_DOCKER)
    assert workflow["env"]["BUILDCACHE_IMAGE"] == "ghcr.io/debpalash/voicestudio-buildcache"
    builds = [
        step["with"]
        for job in _JOBS
        for step in workflow["jobs"][job]["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 3
    for options in builds:
        assert "type=gha" not in options.get("cache-from", "")
        assert options["cache-from"].startswith("type=registry,ref=${{ env.BUILDCACHE_IMAGE }}")
    cuda, rocm, arm64 = builds
    assert "ignore-error=true" in cuda["cache-to"] and "mode=max" in cuda["cache-to"]
    assert "cache-to" not in rocm
    # arm64 layers are useless to the amd64 jobs, so the arm64 job keeps its own
    # cache tag and never writes the CUDA job's.
    assert arm64["cache-from"].endswith(":buildkit-cuda-arm64")
    assert ":buildkit-cuda-arm64," in arm64["cache-to"] and "ignore-error=true" in arm64["cache-to"]


def _build_step(job: str) -> dict:
    steps = _load(_DOCKER)["jobs"][job]["steps"]
    return next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action@"))


def test_arm64_cuda_image_builds_natively_from_the_arm64_base_stage():
    """#2831: DGX Spark / Grace hosts get a native arm64 CUDA image under
    `-cuda-arm64` tags, without touching the amd64 CUDA and ROCm images."""
    jobs = _load(_DOCKER)["jobs"]
    assert jobs["build-and-push-cuda-arm64"]["runs-on"] == "ubuntu-24.04-arm", "native, never QEMU"
    options = _build_step("build-and-push-cuda-arm64")["with"]
    assert options["platforms"] == "linux/arm64"
    args = options["build-args"].split()
    assert "BASE_IMAGE=cuda-arm64-base" in args and "GPU_FLAVOR=cuda" in args
    for job in ("build-and-push", "build-and-push-rocm"):
        assert jobs[job]["runs-on"] == "ubuntu-22.04"
        assert "arm64" not in str(_build_step(job)["with"].get("platforms", ""))
    # Every arm64 tag is suffixed: none may land on an amd64 tag name.
    for line in _tag_lines("build-and-push-cuda-arm64"):
        value = line.split("value=", 1)[-1].split(",", 1)[0]
        assert "suffix=-cuda-arm64" in line or value.endswith("cuda-arm64"), line


def test_arm64_base_stage_installs_the_pinned_cu129_torch_trio():
    dockerfile = (_ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM ubuntu:24.04 AS cuda-arm64-base" in dockerfile
    start = dockerfile.index("AS cuda-arm64-base")
    end = dockerfile.index("FROM ${BASE_IMAGE} AS runtime")
    assert start < end
    stage = dockerfile[start:end]
    # cu128 has no aarch64 torch 2.8.0. cu129 is the only index, so PyPI can
    # never substitute a different torch build.
    assert "--index-url https://download.pytorch.org/whl/cu129" in stage
    assert "--extra-index-url" not in stage
    pins = dict(
        line.split("==", 1)
        for line in (_ROOT / "deploy" / "torch-constraints.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert f"torch=={pins['torch']}+cu129" in stage
    assert f"torchaudio=={pins['torchaudio']}" in stage
    assert f"torchvision=={pins['torchvision']}" in stage
    assert "libopus-dev" in stage, "sphn builds from source on aarch64 and links Opus"


def test_dockerhub_documentation_publishes_without_building_images():
    workflow = _load(_ROOT / ".github/workflows/dockerhub-description.yml")
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "deploy/dockerhub-overview.md" in workflow["on"]["push"]["paths"]
    assert "workflow_dispatch" in workflow["on"]
    job = workflow["jobs"]["description"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "needs" not in job
    assert not any("docker/build-push-action" in step.get("uses", "") for step in job["steps"])
    publish = next(step for step in job["steps"] if step.get("name") == "Update Docker Hub description")
    assert "steps.credentials.outputs.enabled == 'true'" in publish["if"]
    assert publish["continue-on-error"] is True
    assert publish["with"]["readme-filepath"] == "./deploy/dockerhub-overview.md"
    assert workflow["env"]["DOCKERHUB_IMAGE"] == _load(_DOCKER)["env"]["DOCKERHUB_IMAGE"]
    assert not any(step.get("name") == "Update Docker Hub description" for step in _load(_DOCKER)["jobs"]["build-and-push"]["steps"])
