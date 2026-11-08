"""Beta is a second release channel cut from main: `vX.Y.Z-beta.N` prereleases.

Stable users must never receive a beta, so these guards pin the places where a
beta could leak: GitHub's `latest`, the Docker `:stable`/`:X.Y` tags, the Snap
Store, and the Tauri sunset feeds.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture
def release_module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_release_beta_test", ROOT / "scripts/prepare_electron_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("version", "channel"),
    [("0.5.9", "stable"), ("0.5.9-beta.1", "beta"), ("1.0.0-beta.12", "beta")],
)
def test_channel_follows_the_version_shape(release_module, version, channel):
    assert release_module.channel_for(version) == channel


@pytest.mark.parametrize(
    "version", ["0.5.9-rc.1", "0.5.9-beta", "0.5.9-beta.1.2", "0.5.9-alpha.1", "0.5.9+build", "0.5"]
)
def test_other_prerelease_shapes_are_not_releases(release_module, version):
    with pytest.raises(ValueError, match="stable or beta"):
        release_module.channel_for(version)


def test_beta_release_uses_beta_notes_and_skips_sunset_feeds(release_module, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        release_module, "verify_release", lambda release, *a, **k: seen.update(release=release, **k)
    )
    monkeypatch.setattr(
        release_module,
        "validate_sunset",
        lambda *a: pytest.fail("betas carry no Tauri sunset feeds"),
    )
    monkeypatch.chdir(tmp_path)
    Path("package.json").write_text(json.dumps({"version": "1.2.3-beta.2"}))
    Path("CHANGELOG.md").write_text(
        "## [Unreleased]\n\n- Later\n\n## [1.2.3-beta.2] — 2026-10-22\n\n- Beta fix\n\n"
        "## [1.2.2] — 2026-10-01\n\n- Older\n"
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        (assets / f"VoiceStudio-1.2.3-beta.2-{suffix}").write_bytes(b"x")
    payload = b"foo"
    digest = base64.b64encode(hashlib.sha512(payload).digest()).decode()
    (assets / "electron-beta-win32-x64.yml").write_text(
        f"files:\n  - url: a.exe\n    sha512: {digest}\n"
    )
    (assets / "a.exe").write_bytes(payload)
    # An unrelated stable manifest must not be what the checksum loop trusts.
    (assets / "electron-stable-win32-x64.yml").write_text("garbage")
    release_module.prepare(assets, "v1.2.3-beta.2", sunset_tag="v0.5.3")

    assert seen["channel"] == "beta"
    assert seen["release"]["isPrerelease"] is True
    assert (assets / "RELEASE_NOTES.md").read_text(encoding="utf-8") == "- Beta fix\n"


def test_prepare_rejects_a_tag_that_is_not_the_package_version(release_module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("package.json").write_text(json.dumps({"version": "1.2.3-beta.2"}))
    with pytest.raises(ValueError, match="Tag must match"):
        release_module.prepare(tmp_path, "v1.2.3")


def _release_workflow() -> dict:
    return yaml.safe_load((WORKFLOWS / "electron-release.yml").read_text(encoding="utf-8"))


def test_release_workflow_stamps_the_channel_into_every_feed():
    workflow = _release_workflow()
    assert workflow["jobs"]["validate"]["outputs"]["channel"]
    env = workflow["jobs"]["package"]["env"]["VOICESTUDIO_UPDATE_CHANNEL"]
    assert "needs.validate.outputs.channel" in env
    assert "electron-stable" not in env, "a hardcoded stable feed would ship betas to stable users"


def test_a_beta_is_never_published_as_latest():
    steps = {s["name"]: s for s in _release_workflow()["jobs"]["release"]["steps"] if "name" in s}
    publish = steps["Publish only when explicitly requested"]["run"]
    beta_branch, stable_branch = publish.split("else")
    assert "--prerelease=true" in beta_branch and "--latest=false" in beta_branch
    assert "--latest" in stable_branch and "--prerelease=false" in stable_branch
    draft = steps["Create or update draft"]["run"]
    assert 'CHANNEL" = beta' in draft and '--prerelease="$PRERELEASE"' in draft


def test_only_stable_moves_the_stable_container_tags():
    run = next(
        s["run"]
        for s in _release_workflow()["jobs"]["release"]["steps"]
        if s.get("name") == "Publish release container images"
    )
    assert 'CHANNEL" = stable' in run and 'promote_stable="$PROMOTE"' in run
    docker = (WORKFLOWS / "docker.yml").read_text(encoding="utf-8")
    assert docker.count('test "$PROMOTE_STABLE" != "true"') == 3, "every flavor must refuse"
    for line in docker.splitlines():
        if "pattern={{major}}.{{minor}}" in line or "value=stable" in line:
            assert "!contains(github.ref, '-')" in line, f"beta must not move: {line.strip()}"


def test_snap_store_skips_beta_tags():
    snap = yaml.safe_load((WORKFLOWS / "snap-store.yml").read_text(encoding="utf-8"))
    assert "-beta." in snap["jobs"]["package"]["if"]
    assert snap["jobs"]["upload"]["needs"] == "package"


def test_every_electron_channel_regex_accepts_beta():
    config = (ROOT / "electron" / "electron-builder.config.mjs").read_text(encoding="utf-8")
    assert "stable|beta" in config
    assert "electron-beta-linux-x64" in (
        ROOT / "electron" / "scripts" / "embed-appimage-update.mjs"
    ).read_text(encoding="utf-8")


def test_appimage_update_info_contract_matches_the_embed_script():
    """v0.5.7-beta.1 failed Linux packaging: the embed script pinned a beta image to
    its tag while the post-build contract still demanded `latest`. Both must key
    the release field off the channel in the same way."""
    embed = (ROOT / "electron/scripts/embed-appimage-update.mjs").read_text(encoding="utf-8")
    contract = (ROOT / "electron/tests/update-package-contract.mjs").read_text(encoding="utf-8")
    assert "isBeta ? releaseTag : 'latest'" in embed
    assert "channel.startsWith('electron-beta-') ? `v${version}` : 'latest'" in contract
    assert "|latest|VoiceStudio-" not in contract, "contract must not hardcode latest"
