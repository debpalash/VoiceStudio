#!/usr/bin/env python3
"""Render the table that opens every release's notes: download and signing per platform.

Each package leg verifies its own installers after building and writes
`signing-<platform>-<arch>.json` with the status it measured, so the notes say
what shipped rather than what the workflow intended. Download links point only
at files present in the release assets; a missing one shows "—".
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# (platform, arch, label, [(link text, asset glob)]); desktop rows need a signing report.
DESKTOP = [
    ("win32", "x64", "Windows x64", [("Installer", "VoiceStudio-*-win-x64.exe")]),
    ("win32", "arm64", "Windows ARM64 (experimental)", [("Installer", "VoiceStudio-*-win-arm64.exe")]),
    ("darwin", "arm64", "macOS Apple Silicon", [("DMG", "VoiceStudio-*-mac-arm64.dmg")]),
    ("darwin", "x64", "macOS Intel", [("DMG", "VoiceStudio-*-mac-x64.dmg")]),
    (
        "linux",
        "x64",
        "Linux x64",
        [("AppImage", "VoiceStudio-*-linux-x64.AppImage"), ("deb", "VoiceStudio-*-linux-x64.deb")],
    ),
]

# Phone builds carry no signing report: Android is always release-signed, iOS never is.
PHONES = [
    (
        "Android",
        [("APK", "VoiceStudio-*-android.apk"), ("AAB", "VoiceStudio-*-android.aab")],
        "✅ Signed APK; the AAB is the Play Store bundle",
    ),
    (
        "iPhone",
        [("Unsigned IPA", "VoiceStudio-*-ios-unsigned.ipa")],
        "⚠️ Unsigned: sign it with your own Apple ID to sideload; not a TestFlight build",
    ),
]

STATUS = {
    "notarized": "✅ Signed with Developer ID and notarized by Apple",
    "adhoc": "⚠️ Not Developer ID signed: macOS may refuse to open it from a browser download; use the shell installer, and update manually",
    "signed": "✅ Signed",
    "unsigned": "⚠️ Unsigned: SmartScreen shows \"Windows protected your PC\"; choose More info → Run anyway",
    "not-applicable": "— Linux packages are not code-signed",
}

# A release rehearsal may sign Windows installers with SignPath's test
# certificate; a real release never accepts (or describes) such a build.
REHEARSAL_STATUS = {
    "test-signed": "⚠️ Signed with a test certificate (release rehearsal only)",
}

HEADER = ["", "### Download", "", "| Platform | Download | Signing |", "|---|---|---|"]


def _links(assets: Path | None, base_url: str, files: list[tuple[str, str]]) -> str:
    links = []
    for text, pattern in files:
        found = sorted(assets.glob(pattern)) if assets else []
        if found:
            links.append(f"[{text}]({base_url.rstrip('/')}/{found[0].name})")
    return " · ".join(links) or "—"


def render(
    reports_dir: Path, rehearsal: bool = False, assets: Path | None = None, base_url: str = ""
) -> str:
    known = {**STATUS, **REHEARSAL_STATUS} if rehearsal else STATUS
    reports = {}
    for path in sorted(reports_dir.glob("signing-*.json")):
        report = json.loads(path.read_text())
        if report["status"] not in known:
            raise ValueError(f"Unknown signing status in {path.name}: {report['status']}")
        reports[(report["platform"], report["arch"])] = report["status"]
    unknown = set(reports) - {(platform, arch) for platform, arch, _, _ in DESKTOP}
    if unknown or (not reports and not rehearsal):
        raise ValueError(f"Unexpected signing reports: {sorted(unknown) or 'none'}")
    lines = list(HEADER)
    if not reports:
        # Every package leg failed or was skipped; the rehearsal lists them as
        # missing at its end, so the notes must not stop it first. Phones still show.
        lines[2:2] = ["No package leg reported its signing.", ""]
    for platform, arch, label, files in DESKTOP:
        if (platform, arch) in reports:
            lines.append(f"| {label} | {_links(assets, base_url, files)} | {known[reports[(platform, arch)]]} |")
    for label, files, signing in PHONES:
        lines.append(f"| {label} | {_links(assets, base_url, files)} | {signing} |")
    return "\n".join(lines) + "\n"


# A changelog section's own download table: a bold `**Download**` label, then a
# Markdown table. Older tags carry one; the generated table replaces it.
_OWN_TABLE = re.compile(r"^\*\*Downloads?\*\*[ \t]*\n+(?:\|[^\n]*\n?)+\n*", re.M)


def without_own_download_table(notes: str) -> str:
    return _OWN_TABLE.sub("", notes, count=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", action="store_true", help="Also accept rehearsal-only statuses")
    parser.add_argument("--assets", type=Path, help="Release assets folder the download links point into")
    parser.add_argument("--base-url", default="", help="Download URL prefix of the release's assets")
    parser.add_argument("--notes", type=Path, help="Changelog notes to print after the table, minus their own download table")
    parser.add_argument("reports", type=Path)
    args = parser.parse_args()
    print(render(args.reports, rehearsal=args.rehearsal, assets=args.assets, base_url=args.base_url), end="")
    if args.notes:
        print()
        print(without_own_download_table(args.notes.read_text(encoding="utf-8")), end="")


if __name__ == "__main__":
    main()
