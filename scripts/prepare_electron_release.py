#!/usr/bin/env python3
"""Validate Electron artifacts and immutable Tauri sunset feeds."""
import base64
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from check_electron_release_assets import verify_release

def validate_sunset(manifest, tag):
    if manifest.get("version") != tag.removeprefix("v"):
        raise ValueError("Tauri version must match sunset tag")
    if not manifest.get("platforms"):
        raise ValueError("Missing Tauri platforms")
    for entry in manifest["platforms"].values():
        url = urlparse(entry.get("url", ""))
        prefix = f"/debpalash/VoiceStudio/releases/download/{tag}/"
        if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith(prefix):
            raise ValueError("Tauri URLs must point to immutable sunset assets")
        if not entry.get("signature"):
            raise ValueError("Missing Tauri signature")

def prepare(assets, tag, sunset_tag=None):
    version = json.loads(Path("package.json").read_text())["version"]
    if tag != f"v{version}":
        raise ValueError("Tag must match package version")
    files = [p for p in assets.iterdir() if p.is_file()]
    release = {"tagName": tag, "isPrerelease": False, "assets": [
        {"name": p.name, "size": p.stat().st_size,
         "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in files]}
    verify_release(release, assets, channel="stable", version=version)
    for manifest in assets.glob("electron-stable-*.yml"):
        text = manifest.read_text()
        artifact = re.search(r"^\s+- url:\s*(.+)$", text, re.M)[1].strip().strip("'\"")
        expected = re.search(r"^\s{4}sha512:\s*(.+)$", text, re.M)[1].strip().strip("'\"")
        actual = base64.b64encode(hashlib.sha512((assets / artifact).read_bytes()).digest()).decode()
        if actual != expected:
            raise ValueError("Updater payload checksum mismatch")
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        if not (assets / f"VoiceStudio-Electron-{version}-{suffix}").is_file():
            raise ValueError(f"Missing installer: {suffix}")
    if sunset_tag:
        for name in ("latest.json", "latest-user.json"):
            validate_sunset(json.loads((assets / name).read_text()), sunset_tag)
    section = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
                        Path("CHANGELOG.md").read_text(), re.M | re.S)
    if not section or not section[1].strip():
        raise ValueError("Write versioned CHANGELOG notes before release")
    notes = section[1].strip() + "\n"
    (assets / "RELEASE_NOTES.md").write_text(notes)
    (assets / "SHA256SUMS.txt").write_text("\n".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(files)
        if p.name not in {"SHA256SUMS.txt", "RELEASE_NOTES.md"}) + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sunset-tag")
    args = parser.parse_args()
    prepare(args.assets, args.tag, args.sunset_tag)
