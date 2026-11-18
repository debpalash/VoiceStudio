"""iOS UI tests must tap through `tapReady()`, never a bare `XCUIElement.tap()`.

A bare tap fires while the element is still animating (e.g. right after a sheet
closes) and fails with "Failed to not hittable". `tapReady()` waits for the
element to exist and be hittable first.
"""
import re
from pathlib import Path

UITESTS = Path(__file__).resolve().parent.parent / "mobile" / "ios" / "VoiceStudioUITests"

# `foo.tap()`, `app.buttons["x"].tap()`, `rows.element(boundBy: 0).tap()`.
# Coordinate taps (`....coordinate(...).tap()`) have no tapReady and are skipped.
BARE_TAP = re.compile(r"(?:[\w\]\)])\.tap\(\)")

# (file name, stripped line) pairs allowed to tap bare. Keep empty unless a tap
# must deliberately fire on a non-hittable element; say why next to the entry.
ALLOWLIST: set[tuple[str, str]] = set()


def test_no_bare_element_taps():
    offenders = []
    for path in sorted(UITESTS.glob("*.swift")):
        in_helper = False
        for no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.strip()
            if code.startswith("//") or "coordinate(" in code:
                continue
            if "func tapReady" in code:
                in_helper = True
            elif in_helper and code.startswith("}"):
                in_helper = False
            if in_helper:
                continue
            if BARE_TAP.search(code) and (path.name, code) not in ALLOWLIST:
                offenders.append(f"{path.name}:{no}: {code}")
    assert not offenders, "use .tapReady() instead of a bare .tap():\n" + "\n".join(offenders)
