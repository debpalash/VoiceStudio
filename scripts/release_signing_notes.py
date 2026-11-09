#!/usr/bin/env python3
"""Render the release notes' installer-signing table from the package job's reports.

Each package leg verifies its own installers after building and writes
`signing-<platform>-<arch>.json` with the status it measured, so the notes say
what shipped rather than what the workflow intended.
"""
import argparse
import json
from pathlib import Path

PLATFORMS = [
    ("darwin", "arm64", "macOS (Apple Silicon)"),
    ("darwin", "x64", "macOS (Intel)"),
    ("win32", "x64", "Windows x64"),
    ("win32", "arm64", "Windows ARM64"),
    ("linux", "x64", "Linux x64"),
]

STATUS = {
    "notarized": "✅ Signed with Developer ID and notarized by Apple",
    "adhoc": "⚠️ Not Developer ID signed: macOS may refuse to open it from a browser download; use the shell installer, and update manually",
    "signed": "✅ Signed",
    "unsigned": "⚠️ Unsigned: SmartScreen shows \"Windows protected your PC\"; choose More info → Run anyway",
    "not-applicable": "— Linux packages are not code-signed",
}


def render(reports_dir: Path) -> str:
    reports = {}
    for path in sorted(reports_dir.glob("signing-*.json")):
        report = json.loads(path.read_text())
        if report["status"] not in STATUS:
            raise ValueError(f"Unknown signing status in {path.name}: {report['status']}")
        reports[(report["platform"], report["arch"])] = report["status"]
    rows = [(label, reports[(platform, arch)]) for platform, arch, label in PLATFORMS if (platform, arch) in reports]
    unknown = set(reports) - {(platform, arch) for platform, arch, _ in PLATFORMS}
    if unknown or not rows:
        raise ValueError(f"Unexpected signing reports: {sorted(unknown) or 'none'}")
    lines = ["", "### Installer signing", "", "| Platform | Signing |", "|---|---|"]
    lines += [f"| {label} | {STATUS[status]} |" for label, status in rows]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    print(render(parser.parse_args().reports), end="")


if __name__ == "__main__":
    main()
