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


def test_docker_hub_keeps_its_existing_coordinate():
    assert _env()["DOCKERHUB_IMAGE"] == "palashdeb/omnivoice-studio"


def test_active_ghcr_templates_use_the_canonical_path():
    canonical = "ghcr.io/debpalash/voicestudio"
    legacy = "ghcr.io/debpalash/omnivoice-studio"
    for rel in (
        "deploy/docker-compose.yml",
        "docs/production-private-api.md",
        "docs/integration-directory.md",
        "docs/install/linux.md",
        "electron/src/renderer/src/features/integrations/setup-registry.ts",
    ):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert canonical in text, f"{rel} does not use {canonical}"
        assert legacy not in text, f"{rel} still recommends {legacy}"


def test_the_docs_name_the_path_that_is_actually_published():
    """Docs drift here is invisible: a wrong pull command fails only for users."""
    env = _env()
    published = f"ghcr.io/{env['IMAGE_NAME']}"
    for rel in ("docs/install/docker.md", "deploy/dockerhub-overview.md"):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        if "ghcr.io/" not in text:
            continue
        assert published in text, f"{rel} does not document {published}"
