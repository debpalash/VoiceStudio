"""iOS UI tests must tap through `tapReady()`, never a bare `XCUIElement.tap()`.

A bare tap fires while the element is still animating (e.g. right after a sheet
closes) and fails with "Failed to not hittable". `tapReady()` waits for the
element to exist and be hittable first.
"""
import re
from pathlib import Path

UITESTS = Path(__file__).resolve().parent.parent / "mobile" / "ios" / "VoiceStudioUITests"

TAP = re.compile(r"\.tap\(\)")
LINE_COMMENT = re.compile(r"//[^\n]*")
# The helper's own body is the one place a bare tap is correct.
HELPER = re.compile(r"func tapReady\([^{]*\{.*?\n\s*\}", re.S)

# (file name, stripped line) pairs allowed to tap bare. Keep empty unless a tap
# must deliberately fire on a non-hittable element; say why next to the entry.
ALLOWLIST: set[tuple[str, str]] = set()


def _receiver_is_coordinate(source: str, dot: int) -> bool:
    """True when the expression `.tap()` is called on ends in `coordinate(...)`.

    Each call is classified on its own receiver, across line breaks, so a
    coordinate on the same line never hides an ordinary element tap.
    """
    end = len(source[:dot].rstrip())
    if end == 0 or source[end - 1] != ")":
        return False
    depth = 0
    for i in range(end - 1, -1, -1):
        if source[i] == ")":
            depth += 1
        elif source[i] == "(":
            depth -= 1
            if depth == 0:
                return source[:i].rstrip().endswith("coordinate")
    return False


def bare_taps(source: str) -> list[int]:
    """Offsets of `.tap()` calls on an element (not a coordinate), outside the helper."""
    code = LINE_COMMENT.sub(lambda m: " " * len(m.group()), source)
    helper = [m.span() for m in HELPER.finditer(code)]
    return [
        m.start()
        for m in TAP.finditer(code)
        if not any(a <= m.start() < b for a, b in helper)
        and not _receiver_is_coordinate(code, m.start())
    ]


def test_matcher_classifies_each_call():
    assert len(bare_taps('app.buttons["Generate"].tap()')) == 1
    assert len(bare_taps("rows.element(boundBy: 0).tap()")) == 1
    assert len(bare_taps('app.buttons["Generate"]\n    .tap()')) == 1
    assert not bare_taps('app.buttons["x"].tapReady()')
    assert not bare_taps("x.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()")
    assert not bare_taps("x.coordinate(withNormalizedOffset: offset)\n    .tap()")
    assert len(bare_taps("let p = x.coordinate(withNormalizedOffset: o); button.tap()")) == 1
    assert not bare_taps("// button.tap()")
    assert not bare_taps("func tapReady(timeout: T) {\n    guard ok else { return }\n    tap()\n}")


def test_no_bare_element_taps():
    offenders = []
    for path in sorted(UITESTS.glob("*.swift")):
        source = path.read_text()
        lines = source.splitlines()
        for offset in bare_taps(source):
            no = source.count("\n", 0, offset) + 1
            code = lines[no - 1].strip()
            if (path.name, code) not in ALLOWLIST:
                offenders.append(f"{path.name}:{no}: {code}")
    assert not offenders, "use .tapReady() instead of a bare .tap():\n" + "\n".join(offenders)
