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

# A release rehearsal may sign Windows installers with SignPath's test
# certificate; a real release never accepts (or describes) such a build.
REHEARSAL_STATUS = {
    "test-signed": "⚠️ Signed with a test certificate (release rehearsal only)",
}


def render(reports_dir: Path, rehearsal: bool = False) -> str:
    known = {**STATUS, **REHEARSAL_STATUS} if rehearsal else STATUS
    reports = {}
    for path in sorted(reports_dir.glob("signing-*.json")):
        report = json.loads(path.read_text())
        if report["status"] not in known:
            raise ValueError(f"Unknown signing status in {path.name}: {report['status']}")
        reports[(report["platform"], report["arch"])] = report["status"]
    rows = [(label, reports[(platform, arch)]) for platform, arch, label in PLATFORMS if (platform, arch) in reports]
    unknown = set(reports) - {(platform, arch) for platform, arch, _ in PLATFORMS}
    if rehearsal and not reports:
        # Every package leg failed or was skipped; the rehearsal lists them as
        # missing at its end, so the notes must not stop it first.
        return "\n### Installer signing\n\nNo package leg reported its signing.\n"
    if unknown or not rows:
        raise ValueError(f"Unexpected signing reports: {sorted(unknown) or 'none'}")
    lines = ["", "### Installer signing", "", "| Platform | Signing |", "|---|---|"]
    lines += [f"| {label} | {known[status]} |" for label, status in rows]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearsal", action="store_true", help="Also accept rehearsal-only statuses")
    parser.add_argument("reports", type=Path)
    args = parser.parse_args()
    print(render(args.reports, rehearsal=args.rehearsal), end="")


if __name__ == "__main__":
    main()
