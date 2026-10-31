#!/usr/bin/env python3
"""Check every shipped executable bundle before submitting an Apple archive."""
import argparse
from pathlib import Path
import plistlib
import re


def validate(app):
    bundles = [app, *sorted((app / "PlugIns").glob("*.appex")),
               *sorted((app / "Frameworks").glob("*.framework"))]
    for bundle in bundles:
        path = bundle / "PrivacyInfo.xcprivacy"
        if not path.is_file():
            raise ValueError(f"Privacy manifest is missing: {bundle.name}")
        data = plistlib.loads(path.read_bytes())
        if data.get("NSPrivacyTracking") is not False or data.get("NSPrivacyTrackingDomains", []):
            raise ValueError(f"Unexpected tracking declaration: {bundle.name}")
        entries = data.get("NSPrivacyAccessedAPITypes")
        if not isinstance(entries, list):
            raise ValueError(f"Required-reason declarations must be an array: {bundle.name}")
        seen = set()
        for entry in entries:
            category = entry.get("NSPrivacyAccessedAPIType")
            reasons = entry.get("NSPrivacyAccessedAPITypeReasons")
            if (not isinstance(category, str) or not category.startswith("NSPrivacyAccessedAPICategory")
                    or category in seen or not isinstance(reasons, list) or not reasons
                    or any(not isinstance(reason, str) or not re.fullmatch(r"[A-F0-9]{4}\.\d+", reason)
                           for reason in reasons)):
                raise ValueError(f"Invalid required-reason declaration: {bundle.name}")
            seen.add(category)
    return len(bundles)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    count = validate(args.app)
    print(f"PASS: privacy declarations in {count} executable bundles")
