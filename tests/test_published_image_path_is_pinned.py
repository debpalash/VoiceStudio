"""Keep the canonical and compatibility GHCR coordinates in one tag stream."""

import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "docker.yml")


def _env():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["env"]


def test_the_ghcr_paths_are_explicit_and_keep_the_legacy_alias():
    env = _env()
    assert env["IMAGE_NAME"] == "debpalash/voicestudio"
    assert env["LEGACY_IMAGE_NAME"] == "debpalash/omnivoice-studio"
    assert "github.repository" not in str(env)


def test_both_ghcr_paths_are_published_by_both_builds():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert text.count("${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}") == 2
    assert text.count("${{ env.REGISTRY }}/${{ env.LEGACY_IMAGE_NAME }}") == 2


def test_current_release_tags_are_backfilled_for_both_gpu_flavors():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert text.count('[[ "$RELEASE_REF" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]') == 2
    assert text.count('${RELEASE_REF}^{commit}') == 2
    assert text.count('VERSION="${RELEASE_REF#v}"') == 4
    assert text.count("test \"$PACKAGE_VERSION\" = '1.0.0'") == 2
    assert text.count('test "$PACKAGE_VERSION" = "$TAG_VERSION"') == 2
    assert text.count('if [[ "$PROMOTE_STABLE" == "true" ]]') == 2
    assert text.count("docker buildx imagetools create") == 4


def test_sha_tags_follow_the_checked_out_source_commit():
    text = open(WORKFLOW, encoding="utf-8").read()
    assert text.count("git rev-parse --short=7 HEAD") == 2
    assert "type=sha" not in text
    assert "GITHUB_SHA::7" not in text


def test_docker_hub_keeps_its_existing_coordinate():
    assert _env()["DOCKERHUB_IMAGE"] == "palashdeb/omnivoice-studio"


def test_active_templates_stay_on_the_public_path_during_rollout():
    legacy = "ghcr.io/debpalash/omnivoice-studio"
    for rel in (
        "deploy/docker-compose.yml",
        "docs/integration-directory.md",
        "docs/install/linux.md",
        "electron/src/renderer/src/features/integrations/setup-registry.ts",
    ):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert legacy in text, f"{rel} switched before the new package is public"


def test_the_docs_name_the_path_that_is_actually_published():
    """Docs drift here is invisible: a wrong pull command fails only for users."""
    env = _env()
    published = f"ghcr.io/{env['LEGACY_IMAGE_NAME']}"
    for rel in ("docs/install/docker.md", "deploy/dockerhub-overview.md"):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        if "ghcr.io/" not in text:
            continue
        assert published in text, f"{rel} does not document {published}"
