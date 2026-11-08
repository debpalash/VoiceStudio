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
import sys
from pathlib import Path
from typing import Any


# Stable is `X.Y.Z`; Beta is `X.Y.Z-beta.N`. Nothing else is an update channel.
CHANNEL_VERSION = {
    "stable": re.compile(r"\d+\.\d+\.\d+"),
    "beta": re.compile(r"\d+\.\d+\.\d+-beta\.\d+"),
}

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


def artifact_name(version: str, os_token: str, arch: str, extension: str) -> str:
    """Direct-download name (electron-builder.config.mjs `artifactName`).

    Releases up to v0.5.7-beta.3 were `VoiceStudio-Electron-*`; they are built
    and verified from their own tags, so only the current name is checked here.
    """
    return f"VoiceStudio-{version}-{os_token}-{arch}{extension}"


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


def _foreign_feed(name: str, channel: str) -> bool:
    """True for an updater feed that belongs to a channel other than `channel`."""
    if name.startswith("electron-") and name.endswith(".yml"):
        return not name.startswith(f"electron-{channel}-")
    # electron-builder's default feeds: this project publishes only channel feeds.
    if re.fullmatch(r"(latest|beta|alpha)(-[a-z0-9]+)*\.yml", name):
        return True
    # The archived Tauri sunset feeds ride on Stable only.
    return channel != "stable" and name in {"latest.json", "latest-user.json"}


def verify_release(
    release: dict[str, Any], manifest_dir: Path, *, channel: str, version: str
) -> list[str]:
    if channel not in CHANNEL_VERSION:
        raise ReleaseContractError(f"unsupported channel: {channel}")
    if CHANNEL_VERSION[channel].fullmatch(version) is None:
        raise ReleaseContractError(f"{channel} version has the wrong shape: {version}")
    # Beta ships as a GitHub prerelease so it can never become `latest`.
    if bool(release.get("isPrerelease")) != (channel == "beta"):
        raise ReleaseContractError(f"{channel} prerelease flag does not match the channel")
    expected_tag = f"v{version}"
    if release.get("tagName") != expected_tag:
        raise ReleaseContractError(f"{channel} release tag does not match {expected_tag}")

    assets = _asset_map(release)
    # A feed of the other channel on this release would move its users: a
    # stable feed on a beta pulls stable users onto the beta's payloads (and
    # Tauri's latest*.json is served only through `latest/download`, which a
    # prerelease never answers), a beta feed on stable is unreachable noise.
    forbidden = [name for name in assets if _foreign_feed(name, channel)]
    if forbidden:
        raise ReleaseContractError(
            f"{channel} release carries another channel's update feed: {', '.join(sorted(forbidden))}"
        )
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
            payload = artifact_name(version, os_token, arch, extension)
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
        expected_artifact = artifact_name(version, os_token, arch, extension)
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
        # Patch updates: electron-updater fetches `<payload>.blockmap` for the
        # NSIS installer and the macOS zip, and reads the AppImage's embedded
        # blockmap through `blockMapSize`. Without them every update is a full
        # download, silently.
        if extension == ".AppImage":
            # Only the AppImage's own entry counts (the deb entry has none).
            entry = re.search(
                rf"^\s+- url:\s*['\"]?{re.escape(artifact)}['\"]?\s*$(.*?)(?=^\s+- url:|^\S|\Z)",
                manifest,
                re.MULTILINE | re.DOTALL,
            )
            if not entry or not re.search(r"^\s{4}blockMapSize:\s*[1-9]\d*$", entry.group(1), re.MULTILINE):
                raise ReleaseContractError(
                    f"{manifest_name} has no blockMapSize, so AppImage patch updates are off"
                )
        elif f"{artifact}.blockmap" not in assets:
            raise ReleaseContractError(f"release is missing differential blockmap: {artifact}.blockmap")
        verified.append(f"{manifest_name} -> {artifact}")
    return verified


def foreign_feeds_main(channel: str, names: list[str]) -> int:
    """Exit non-zero when asset names (one per line on stdin) include another channel's feed."""
    forbidden = sorted(name for name in names if name and _foreign_feed(name, channel))
    if forbidden:
        print(f"{channel} release carries another channel's update feed: {', '.join(forbidden)}")
        return 1
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--foreign-feeds":
        return foreign_feeds_main(sys.argv[2], sys.stdin.read().split("\n"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--channel", choices=tuple(CHANNEL_VERSION), required=True)
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
