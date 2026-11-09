"""The build commit that bug reports carry next to the app version."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from core import version

_REPO = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


def _fresh(monkeypatch, env: str | None = None):
    version.build_commit.cache_clear()
    if env is None:
        monkeypatch.delenv("OMNIVOICE_BUILD_COMMIT", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_BUILD_COMMIT", env)


def test_environment_commit_is_shortened_and_lowercased(monkeypatch):
    _fresh(monkeypatch, "  ABCDEF1234567890  ")
    assert version.build_commit() == "abcdef123"


def test_values_that_are_not_commit_ids_never_reach_a_report(monkeypatch, tmp_path):
    # a tree without .git, so only the environment can supply a value
    monkeypatch.setattr(version, "__file__", str(tmp_path / "a" / "b" / "version.py"))
    for junk in ("main; rm -rf /", "HEAD", "abc", "../../etc"):
        _fresh(monkeypatch, junk)
        assert version.build_commit() == "unknown"


def test_a_source_checkout_reads_its_own_head(monkeypatch):
    _fresh(monkeypatch)
    assert re.fullmatch(r"[0-9a-f]{9}|unknown", version.build_commit())


def test_docker_images_receive_the_commit_as_a_late_build_arg():
    dockerfile = (_REPO / "deploy/Dockerfile").read_text(encoding="utf-8")
    assert "ARG BUILD_COMMIT=unknown\nENV OMNIVOICE_BUILD_COMMIT=${BUILD_COMMIT}" in dockerfile
    # after the heavy layers, so a new commit never invalidates them
    assert dockerfile.index("ARG BUILD_COMMIT") > dockerfile.index("ARG GPU_FLAVOR")
    workflow = yaml.safe_load((_REPO / ".github/workflows/docker.yml").read_text(encoding="utf-8"))
    builds = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(builds) == 3  # CUDA amd64, ROCm, CUDA arm64
    for step in builds:
        assert "BUILD_COMMIT=${{ steps.source.outputs.commit }}" in step["with"]["build-args"]
