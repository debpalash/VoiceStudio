"""XCTest's identifier shortcut rejects strings over 128 UTF-16 code units.

Long disclosures must be queried with an exact predicate or a stable identifier,
so a privacy-copy change cannot crash the native UI test process.
"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
QUERY = re.compile(r'\.(?:staticTexts|buttons|textFields|textViews|navigationBars|switches|otherElements)\[\s*"((?:\\.|[^"\\])*)"\s*\]')


def test_literal_xctest_identifier_queries_fit_framework_limit():
    failures = []
    for path in sorted((ROOT / "mobile/ios/VoiceStudioUITests").glob("*.swift")):
        source = path.read_text()
        for match in QUERY.finditer(source):
            raw = match[1]
            if "\\(" in raw:
                continue  # Dynamic queries have no statically known length.
            try:
                label = json.loads('"' + raw + '"')
            except ValueError:
                continue  # Swift-specific escapes are not JSON string escapes.
            if len(label.encode("utf-16-le")) // 2 > 128:
                line = source.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(ROOT)}:{line}")
    assert not failures, "Use an exact NSPredicate or a stable identifier for long copy: " + ", ".join(failures)
