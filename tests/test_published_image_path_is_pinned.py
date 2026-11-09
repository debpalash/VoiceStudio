"""Keep the canonical and compatibility GHCR coordinates in one tag stream."""

import os
import re

import pytest

from test_docker_registry_owner import resolved_env

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "docker.yml")
# CUDA amd64, ROCm amd64 and CUDA arm64 (#2831) image jobs.
FLAVORS = 3


def _env():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["env"]


def test_the_ghcr_paths_are_explicit_and_keep_the_legacy_alias(tmp_path):
    env = resolved_env(tmp_path, "debpalash")
    assert env["IMAGE_NAME"] == "debpalash/voicestudio"
    assert env["LEGACY_IMAGE_NAME"] == "debpalash/omnivoice-studio"


def test_a_repository_rename_cannot_move_the_published_images():
    """Only the owner is derived; image names stay explicit across repository renames."""
    with open(WORKFLOW, encoding="utf-8") as fh:
        jobs = yaml.safe_load(fh)["jobs"].values()
    resolvers = [step["run"] for job in jobs for step in job["steps"] if step.get("id") == "source"]
    assert len(resolvers) == FLAVORS
    for run in resolvers:
        assert "GITHUB_REPOSITORY_OWNER" in run
        assert not re.search(r"GITHUB_REPOSITORY(?!_OWNER)|github\.repository(?!_owner)|github\.event\.repository", run)


def test_release_backfills_require_an_actual_git_tag():
    workflow = open(WORKFLOW, encoding="utf-8").read()
    assert workflow.count('git rev-parse "refs/tags/${RELEASE_REF}^{commit}"') == FLAVORS
    assert 'git rev-parse "${RELEASE_REF}^{commit}"' not in workflow


def test_both_ghcr_paths_are_published_by_every_build():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert text.count("${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}") == FLAVORS
    assert text.count("${{ env.REGISTRY }}/${{ env.LEGACY_IMAGE_NAME }}") == FLAVORS


def test_current_release_tags_are_backfilled_for_every_gpu_flavor():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert text.count('[[ "$RELEASE_REF" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+(-beta\\.[0-9]+)?$ ]]') == FLAVORS
    assert text.count('${RELEASE_REF}^{commit}') == FLAVORS
    assert text.count('VERSION="${RELEASE_REF#v}"') == 2 * FLAVORS
    assert text.count("test \"$PACKAGE_VERSION\" = '1.0.0'") == FLAVORS
    assert text.count('test "$PACKAGE_VERSION" = "$TAG_VERSION"') == FLAVORS
    assert text.count('if [[ "$PROMOTE_STABLE" == "true" ]]') == FLAVORS
    assert text.count("docker buildx imagetools create") == 2 * FLAVORS


def test_sha_tags_follow_the_checked_out_source_commit():
    text = open(WORKFLOW, encoding="utf-8").read()
    workflow = yaml.safe_load(text)
    resolvers = [step for job in workflow["jobs"].values() for step in job["steps"] if step.get("id") == "source"]
    assert len(resolvers) == FLAVORS
    assert all("git rev-parse --short=7 HEAD" in step["run"] for step in resolvers)
    assert "type=sha" not in text
    assert "GITHUB_SHA::7" not in text


def test_docker_hub_keeps_its_existing_coordinate():
    assert _env()["DOCKERHUB_IMAGE"] == "palashdeb/omnivoice-studio"


def test_active_templates_use_the_canonical_public_path():
    canonical = "ghcr.io/debpalash/voicestudio"
    for rel in (
        "deploy/docker-compose.yml",
        "docs/integration-directory.md",
        "docs/install/linux.md",
        "electron/src/renderer/src/features/integrations/setup-registry.ts",
    ):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert canonical in text, f"{rel} does not use the canonical GHCR path"


def test_the_docs_name_the_path_that_is_actually_published(tmp_path):
    """Docs drift here is invisible: a wrong pull command fails only for users."""
    env = resolved_env(tmp_path, "debpalash")
    published = f"ghcr.io/{env['IMAGE_NAME']}"
    legacy = f"ghcr.io/{env['LEGACY_IMAGE_NAME']}"
    for rel in ("docs/install/docker.md", "deploy/dockerhub-overview.md"):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        if "ghcr.io/" not in text:
            continue
        assert published in text, f"{rel} does not document {published}"
        assert legacy in text, f"{rel} does not document compatibility alias {legacy}"
