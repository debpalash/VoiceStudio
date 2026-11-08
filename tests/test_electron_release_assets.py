from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from check_electron_release_assets import (  # noqa: E402
    OPTIONAL_TARGETS,
    ReleaseContractError,
    TARGETS,
    verify_release,
)


def _release(
    tmp_path: Path,
    *,
    channel: str = "stable",
    version: str = "0.5.3",
    with_arm64: bool = False,
):
    assets = []
    targets = {**TARGETS, **(OPTIONAL_TARGETS if with_arm64 else {})}
    for target, (manifest_target, os_token, arch, extension) in targets.items():
        artifact = f"VoiceStudio-{version}-{os_token}-{arch}{extension}"
        manifest_name = f"electron-{channel}-{manifest_target}.yml"
        body = (
            f"version: {version}\n"
            "files:\n"
            f"  - url: {artifact}\n"
            f"    sha512: {'a' * 88}\n"
            "    size: 1234\n"
            + ("    blockMapSize: 4321\n" if extension == ".AppImage" else "")
            + f"path: {artifact}\n"
            f"sha512: {'a' * 88}\n"
            "releaseDate: '2026-09-14T00:00:00.000Z'\n"
        ).encode()
        (tmp_path / manifest_name).write_bytes(body)
        assets.extend(
            [
                {
                    "name": manifest_name,
                    "size": len(body),
                    "digest": f"sha256:{hashlib.sha256(body).hexdigest()}",
                },
                {"name": artifact, "size": 1234, "digest": "sha256:payload"},
            ]
        )
        if extension != ".AppImage":
            assets.append({"name": f"{artifact}.blockmap", "size": 10, "digest": "sha256:map"})
    return {
        "tagName": f"v{version}",
        "isPrerelease": channel == "beta",
        "assets": assets,
    }


def test_complete_four_target_release_passes(tmp_path: Path):
    result = verify_release(_release(tmp_path), tmp_path, channel="stable", version="0.5.3")
    assert len(result) == 4
    assert {item.split(" -> ", 1)[0] for item in result} == {
        "electron-stable-darwin-arm64-mac.yml",
        "electron-stable-darwin-x64-mac.yml",
        "electron-stable-linux-x64-linux.yml",
        "electron-stable-win32-x64.yml",
    }


def test_missing_platform_manifest_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    (tmp_path / "electron-stable-darwin-arm64-mac.yml").unlink()
    with pytest.raises(ReleaseContractError, match="missing downloaded manifest"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_published_payload_size_must_match_manifest(tmp_path: Path):
    release = _release(tmp_path)
    payload = next(
        asset for asset in release["assets"] if asset["name"].endswith("-win-x64.exe")
    )
    payload["size"] = 999
    with pytest.raises(ReleaseContractError, match="published size"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_a_prerelease_cannot_pass_as_stable(tmp_path: Path):
    release = _release(tmp_path)
    release["isPrerelease"] = True
    with pytest.raises(ReleaseContractError, match="prerelease flag"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_there_is_no_preview_feed(tmp_path: Path):
    """No `preview` GitHub release exists; a preview feed must not be verifiable."""
    with pytest.raises(ReleaseContractError, match="unsupported channel"):
        verify_release(_release(tmp_path), tmp_path, channel="preview", version="0.5.3")


def test_stable_requires_a_plain_version(tmp_path: Path):
    with pytest.raises(ReleaseContractError, match="wrong shape"):
        verify_release(_release(tmp_path), tmp_path, channel="stable", version="0.5.3-144")


def test_beta_release_is_a_prerelease_with_beta_manifests(tmp_path: Path):
    release = _release(tmp_path, channel="beta", version="0.5.9-beta.1")
    result = verify_release(release, tmp_path, channel="beta", version="0.5.9-beta.1")
    assert len(result) == 4
    assert all(item.startswith("electron-beta-") for item in result)


def test_beta_must_never_be_published_as_a_full_release(tmp_path: Path):
    """A non-prerelease beta would become GitHub's `latest` and reach stable users."""
    release = _release(tmp_path, channel="beta", version="0.5.9-beta.1")
    release["isPrerelease"] = False
    with pytest.raises(ReleaseContractError, match="prerelease flag"):
        verify_release(release, tmp_path, channel="beta", version="0.5.9-beta.1")


def test_stable_must_not_be_flagged_a_prerelease(tmp_path: Path):
    release = _release(tmp_path)
    release["isPrerelease"] = True
    with pytest.raises(ReleaseContractError, match="prerelease flag"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


@pytest.mark.parametrize("version", ["0.5.9", "0.5.9-rc.1", "0.5.9-beta", "0.5.9-beta.1.2"])
def test_beta_requires_a_beta_version(tmp_path: Path, version: str):
    with pytest.raises(ReleaseContractError, match="wrong shape"):
        verify_release(_release(tmp_path), tmp_path, channel="beta", version=version)


def test_stable_release_tag_must_match_the_manifest_version(tmp_path: Path):
    release = _release(tmp_path, channel="stable", version="0.5.3")
    release["tagName"] = "v0.5.2"
    with pytest.raises(ReleaseContractError, match="release tag"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_windows_arm64_is_optional_when_wholly_absent(tmp_path: Path):
    """A failed experimental ARM64 leg must not block the four established targets."""
    result = verify_release(_release(tmp_path), tmp_path, channel="stable", version="0.5.3")
    assert not any("win32-arm64" in item for item in result)


def test_published_windows_arm64_is_verified_in_full(tmp_path: Path):
    release = _release(tmp_path, with_arm64=True)
    result = verify_release(release, tmp_path, channel="stable", version="0.5.3")
    assert "electron-stable-win32-arm64.yml -> VoiceStudio-0.5.3-win-arm64.exe" in result
    assert len(result) == 5


def test_half_published_windows_arm64_fails_closed(tmp_path: Path):
    release = _release(tmp_path, with_arm64=True)
    # Manifest published but its installer missing -> must not be waved through.
    release["assets"] = [
        a for a in release["assets"] if a["name"] != "VoiceStudio-0.5.3-win-arm64.exe"
    ]
    with pytest.raises(ReleaseContractError, match="missing updater payload"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")
    # Asset published but manifest file not downloaded -> also an error.
    release = _release(tmp_path, with_arm64=True)
    (tmp_path / "electron-stable-win32-arm64.yml").unlink()
    with pytest.raises(ReleaseContractError, match="missing downloaded manifest"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


@pytest.mark.parametrize("suffix", ["", ".blockmap"])
def test_windows_arm64_payload_without_a_manifest_is_rejected(tmp_path: Path, suffix: str):
    """An ARM64 installer/blockmap published with no manifest must not be waved through."""
    release = _release(tmp_path)
    release["assets"].append(
        {
            "name": f"VoiceStudio-0.5.3-win-arm64.exe{suffix}",
            "size": 10,
            "digest": "sha256:payload",
        }
    )
    with pytest.raises(ReleaseContractError, match="missing downloaded manifest"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


@pytest.mark.parametrize("target", ["darwin-arm64", "darwin-x64", "win32-x64"])
def test_a_payload_without_its_blockmap_fails_closed(tmp_path: Path, target: str):
    """Without `<payload>.blockmap` electron-updater cannot patch; every update is full."""
    release = _release(tmp_path)
    _, os_token, arch, extension = TARGETS[target]
    blockmap = f"VoiceStudio-0.5.3-{os_token}-{arch}{extension}.blockmap"
    release["assets"] = [a for a in release["assets"] if a["name"] != blockmap]
    with pytest.raises(ReleaseContractError, match="differential blockmap"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_an_appimage_manifest_without_blockmapsize_fails_closed(tmp_path: Path):
    release = _release(tmp_path)
    manifest = tmp_path / "electron-stable-linux-x64-linux.yml"
    body = manifest.read_text(encoding="utf-8").replace("    blockMapSize: 4321\n", "").encode()
    manifest.write_bytes(body)
    for asset in release["assets"]:
        if asset["name"] == manifest.name:
            asset.update(size=len(body), digest=f"sha256:{hashlib.sha256(body).hexdigest()}")
    with pytest.raises(ReleaseContractError, match="blockMapSize"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")


def test_blockmapsize_on_another_entry_does_not_count_for_the_appimage(tmp_path: Path):
    """The deb entry carrying blockMapSize must not vouch for the AppImage."""
    release = _release(tmp_path)
    manifest = tmp_path / "electron-stable-linux-x64-linux.yml"
    body = manifest.read_text(encoding="utf-8").replace("    blockMapSize: 4321\n", "")
    body = body.replace(
        "path: ",
        "  - url: VoiceStudio-0.5.3-linux-x64.deb\n"
        f"    sha512: {'b' * 88}\n    size: 99\n    blockMapSize: 7\npath: ",
        1,
    ).encode()
    manifest.write_bytes(body)
    for asset in release["assets"]:
        if asset["name"] == manifest.name:
            asset.update(size=len(body), digest=f"sha256:{hashlib.sha256(body).hexdigest()}")
    with pytest.raises(ReleaseContractError, match="blockMapSize"):
        verify_release(release, tmp_path, channel="stable", version="0.5.3")
