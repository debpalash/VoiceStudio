"""Execute the actual XCTest tab helper against delayed accessibility readiness.

The host facade models query/activation ordering, not UIKit hit testing; the
remote iOS UI suite remains the native navigation proof. No Simulator is used.
"""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _method(source: str, name: str) -> str:
    start = source.index("    @MainActor", source.rfind("\n", 0, source.index("func " + name)))
    opening = source.index("{", source.index("func " + name))
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None, reason="Apple host Swift/Foundation required; no Simulator")
def test_actual_tab_helper_waits_for_activation_and_checks_destination(tmp_path: Path) -> None:
    source = (ROOT / "mobile/ios/VoiceStudioUITests/SmokeTests.swift").read_text(encoding="utf-8")
    methods = {name: _method(source, name) for name in ("tapReady", "waitReady", "tabButton", "selectTab")}
    tour = (ROOT / "mobile/ios/VoiceStudioUITests/PhoneScreens.swift").read_text(encoding="utf-8")
    settings_step = next(line.strip() for line in tour.splitlines() if 'capture("Settings",app)' in line)
    facade = r'''
import Foundation
let uiReadyTimeout: TimeInterval = 45
@MainActor var failures = 0
@MainActor var captures = 0
@MainActor func XCTFail(_ message: String, file: StaticString = #filePath, line: UInt = #line) { failures += 1 }
@MainActor func XCTAssertTrue(_ value: Bool, _ message: String = "", file: StaticString = #filePath, line: UInt = #line) { if !value { failures += 1 } }
@MainActor final class State {
    static var current = State()
    var hittable = false
    var selected = false
    var destination = true
    var selectionWorks = true
    var taps = 0
    var accepted = 0
}
@MainActor final class XCUIElement {
    let role: String
    init(_ role: String) { self.role = role }
    var exists: Bool { true }
    var isHittable: Bool { role == "tab" ? State.current.hittable : State.current.selected && State.current.destination }
    var isSelected: Bool { State.current.selected }
    var value: String? { nil }
    var description: String { role }
    func waitForExistence(timeout: TimeInterval) -> Bool { exists }
    func wait<T: Equatable>(for key: KeyPath<XCUIElement, T>, toEqual value: T, timeout: TimeInterval) -> Bool {
        if role == "tab", key == \\XCUIElement.isHittable { State.current.hittable = true }
        return self[keyPath: key] == value
    }
    func tap() { State.current.taps += 1; if isHittable { State.current.accepted += 1; State.current.selected = State.current.selectionWorks } }
}
@MainActor final class Query {
    let role: String
    init(_ role: String) { self.role = role }
    var buttons: Query { self }
    var firstMatch: XCUIElement { XCUIElement(role) }
    subscript(_ label: String) -> XCUIElement { XCUIElement(role) }
    func matching(_ predicate: NSPredicate) -> Query { self }
}
@MainActor final class XCUIApplication {
    var tabBars: Query { Query("tab") }
    var buttons: Query { Query("tab") }
    var navigationBars: Query { Query("destination") }
}
extension XCUIElement {
ELEMENT_METHODS
}
extension XCUIApplication {
APPLICATION_METHODS
}
@MainActor func capture(_ name: String, _ app: XCUIApplication) { captures += 1 }
@MainActor func tourStep(_ app: XCUIApplication) {
TOUR_STEP
}
@main struct Probe {
    @MainActor static func main() throws {
        var results: [[String: Any]] = []
        for (name, ready, destination, selection) in [("delayed", false, true, true), ("ready", true, true, true), ("missing-destination", false, false, true), ("missing-selection", false, true, false)] {
            State.current = State(); State.current.hittable = ready; State.current.destination = destination; State.current.selectionWorks = selection; failures = 0; captures = 0
            tourStep(XCUIApplication())
            results.append(["name": name, "failures": failures, "taps": State.current.taps, "accepted": State.current.accepted, "selected": State.current.selected, "arrived": captures == 1, "captures": captures])
        }
        print(String(decoding: try JSONSerialization.data(withJSONObject: results, options: [.sortedKeys]), as: UTF8.self))
    }
}
'''
    facade = facade.replace("\\\\XCUIElement", "\\XCUIElement")
    facade = facade.replace("ELEMENT_METHODS", "\n".join(methods[name] for name in ("tapReady", "waitReady")))
    facade = facade.replace("APPLICATION_METHODS", "\n".join(methods[name] for name in ("tabButton", "selectTab")))
    facade = facade.replace("TOUR_STEP", settings_step)
    swift = tmp_path / "TabNavigation.swift"
    swift.write_text(facade, encoding="utf-8")
    executable = tmp_path / "tab-navigation"
    compiled = subprocess.run([shutil.which("swiftc"), "-swift-version", "6", "-parse-as-library", "-module-cache-path", str(tmp_path / "module-cache"), str(swift), "-o", str(executable)], capture_output=True, text=True, timeout=45, check=False)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"name": "delayed", "failures": 0, "taps": 1, "accepted": 1, "selected": True, "arrived": True, "captures": 1},
        {"name": "ready", "failures": 0, "taps": 1, "accepted": 1, "selected": True, "arrived": True, "captures": 1},
        {"name": "missing-destination", "failures": 1, "taps": 1, "accepted": 1, "selected": True, "arrived": False, "captures": 0},
        {"name": "missing-selection", "failures": 1, "taps": 1, "accepted": 1, "selected": False, "arrived": False, "captures": 0},
    ]
