#!/usr/bin/env python3
"""Fail closed when a published Electron update channel is incomplete.

The per-platform release legs validate local bytes before uploading. This check
runs after the matrix and verifies that GitHub actually serves one coherent
manifest + updater payload for every supported Electron target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


TARGETS = {
    # electron-updater's Provider.getCustomChannelName appends an OS suffix
    # for macOS/Linux but leaves Windows channel names unchanged.
    "darwin-arm64": ("darwin-arm64-mac", "mac", "arm64", ".zip"),
    "darwin-x64": ("darwin-x64-mac", "mac", "x64", ".zip"),
    "linux-x64": ("linux-x64-linux", "linux", "x64", ".AppImage"),
    "win32-x64": ("win32-x64", "win", "x64", ".exe"),
}

# Targets whose build leg is allowed to fail without blocking a release
# (continue-on-error in the workflow matrix). They are verified in full as soon
# as any trace of them is published, and skipped only when entirely absent, so
# a half-published feed still fails closed.
OPTIONAL_TARGETS = {
    "win32-arm64": ("win32-arm64", "win", "arm64", ".exe"),
}


class ReleaseContractError(RuntimeError):
    """The published release cannot safely serve an Electron update."""


def _capture(text: str, pattern: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ReleaseContractError(f"manifest is missing {label}")
    return match.group(1).strip().strip("'\"")


def _asset_map(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseContractError("release JSON has no asset list")
    mapped = {asset.get("name"): asset for asset in assets if isinstance(asset, dict)}
    if None in mapped:
        mapped.pop(None)
    return mapped


def verify_release(
    release: dict[str, Any], manifest_dir: Path, *, channel: str, version: str
) -> list[str]:
    # Stable is the only Electron update feed; there is no `preview` release.
    if channel != "stable":
        raise ReleaseContractError(f"unsupported channel: {channel}")
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ReleaseContractError(f"{channel} version has the wrong shape: {version}")
    if release.get("isPrerelease"):
        raise ReleaseContractError(f"{channel} prerelease flag does not match the channel")
    expected_tag = f"v{version}"
    if release.get("tagName") != expected_tag:
        raise ReleaseContractError(f"{channel} release tag does not match {expected_tag}")

    assets = _asset_map(release)
    verified: list[str] = []
    for target, (manifest_target, os_token, arch, extension) in {
        **TARGETS,
        **OPTIONAL_TARGETS,
    }.items():
        manifest_name = f"electron-{channel}-{manifest_target}.yml"
        manifest_path = manifest_dir / manifest_name
        if target in OPTIONAL_TARGETS:
            # Skip only when NOTHING of this target is published: an installer
            # or blockmap without its manifest is an incomplete release.
            payload = f"VoiceStudio-Electron-{version}-{os_token}-{arch}{extension}"
            traces = (manifest_name, payload, f"{payload}.blockmap")
            if not manifest_path.is_file() and not any(name in assets for name in traces):
                continue
        if not manifest_path.is_file():
            raise ReleaseContractError(f"missing downloaded manifest: {manifest_name}")
        manifest_asset = assets.get(manifest_name)
        if not manifest_asset:
            raise ReleaseContractError(f"release is missing manifest asset: {manifest_name}")
        manifest_bytes = manifest_path.read_bytes()
        if manifest_asset.get("size") != len(manifest_bytes):
            raise ReleaseContractError(f"published size does not match {manifest_name}")
        digest = manifest_asset.get("digest")
        expected_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        if digest != expected_digest:
            raise ReleaseContractError(f"published digest does not match {manifest_name}")

        manifest = manifest_bytes.decode("utf-8")
        declared_version = _capture(manifest, r"^version:\s*(.+)$", "version")
        artifact = _capture(manifest, r"^\s+- url:\s*(.+)$", "files[0].url")
        declared_size = int(_capture(manifest, r"^\s{4}size:\s*(\d+)$", "files[0].size"))
        file_sha = _capture(manifest, r"^\s{4}sha512:\s*(.+)$", "files[0].sha512")
        legacy_path = _capture(manifest, r"^path:\s*(.+)$", "path")
        legacy_sha = _capture(manifest, r"^sha512:\s*(.+)$", "sha512")

        if declared_version != version:
            raise ReleaseContractError(
                f"{manifest_name} has version {declared_version}, expected {version}"
            )
        expected_artifact = f"VoiceStudio-Electron-{version}-{os_token}-{arch}{extension}"
        if artifact != expected_artifact or legacy_path != artifact:
            raise ReleaseContractError(f"{manifest_name} points at unexpected artifact {artifact}")
        if legacy_sha != file_sha or len(file_sha) < 80:
            raise ReleaseContractError(f"{manifest_name} has inconsistent SHA-512 metadata")

        artifact_asset = assets.get(artifact)
        if not artifact_asset:
            raise ReleaseContractError(f"release is missing updater payload: {artifact}")
        if artifact_asset.get("size") != declared_size or declared_size <= 0:
            raise ReleaseContractError(f"published size does not match {artifact}")
        if not str(artifact_asset.get("digest") or "").startswith("sha256:"):
            raise ReleaseContractError(f"published payload has no GitHub digest: {artifact}")
        if target.startswith("win32-") and f"{artifact}.blockmap" not in assets:
            raise ReleaseContractError(f"release is missing differential blockmap: {artifact}.blockmap")
        verified.append(f"{manifest_name} -> {artifact}")
    return verified


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--channel", choices=("stable",), required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    release = json.loads(args.release_json.read_text(encoding="utf-8"))
    verified = verify_release(
        release, args.manifest_dir, channel=args.channel, version=args.version
    )
    print(f"Electron {args.channel} release: {len(verified)} update targets verified")
    for item in verified:
        print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
