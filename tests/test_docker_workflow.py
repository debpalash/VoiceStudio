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
_JOBS = ("build-and-push", "build-and-push-rocm")
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
    rolling = {"build-and-push": ("latest", "main"), "build-and-push-rocm": ("rocm",)}
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
    assert "promote_stable=true" in runs
    # Every -f key must be a real docker.yml dispatch input, or the run fails at publish time.
    command = runs[runs.index("gh workflow run docker.yml"):]
    command = command[: command.find("\n", command.find("promote_stable"))]
    keys = set(re.findall(r"-f\s+([A-Za-z_]+)=", command))
    on = _load(_DOCKER)["on"]
    inputs = set((on.get("workflow_dispatch") or {}).get("inputs") or {})
    assert {"backfill_release", "release_ref", "promote_stable"} <= keys <= inputs, (keys, inputs)
