"""Execute the real PhoneJobs owner loop with host-only OS lifecycle facades.

This proves grant provenance across automatic CPU handoff, not UIKit scheduling,
background entitlements, simulator behavior, native inference, or model execution.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import platform
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ios-phone-job-diagnostics"
pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="The app adapter uses Apple's MetricKit module")


def _swift_path_literal(value):
    # JSON's Unicode, backspace and form-feed escapes are not Swift escapes.
    escapes = {'"': '\\"', '\\': '\\\\', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    pieces = []
    for character in value:
        if character in escapes:
            pieces.append(escapes[character])
        elif ord(character) < 0x20 or 0x7f <= ord(character) <= 0x9f or character in '\u2028\u2029':
            pieces.append(f'\\u{{{ord(character):x}}}')
        else:
            pieces.append(character)
    return '"' + ''.join(pieces) + '"'


@pytest.mark.parametrize("value", [
    "a café/😀/VoiceStudioKit",
    'quotes"and\\backslash/VoiceStudioKit',
    "control\x00\b\f\n\r\t\x1f\x7f/VoiceStudioKit",
    "literal \\u00e9 \\n \\(notInterpolation)/VoiceStudioKit",
])
def test_swift_path_literal_roundtrips_through_the_actual_compiler(tmp_path, value):
    source = tmp_path / "literal.swift"
    executable = tmp_path / "literal-proof"
    source.write_text("import Foundation\nlet value = " + _swift_path_literal(value)
        + "\nprint(Data(value.utf8).base64EncodedString())\n", encoding="utf-8")
    compile_result = subprocess.run([shutil.which("swiftc"), str(source), "-o", str(executable)],
        capture_output=True, text=True, timeout=60)
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_actual_phone_job_cpu_handoff_keeps_original_diagnostic_consent(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    for name in ("UIKit", "BackgroundTasks"):
        target = package / name
        target.mkdir()
        shutil.copyfile(FIXTURES / (name + ".swift"), target / (name + ".swift"))
    sources = package / "AppTests"
    sources.mkdir()
    for name in ("PhoneJobs.swift", "AppDiagnostics.swift", "AppDiagnosticPolicy.swift"):
        original = ROOT / "mobile/ios/VoiceStudio/Platform" / name
        copied = sources / name
        shutil.copyfile(original, copied)
        assert copied.read_bytes() == original.read_bytes(), "Production Swift must be tested unchanged"
    shutil.copyfile(FIXTURES / "PhoneJobDiagnosticTests.swift", sources / "PhoneJobDiagnosticTests.swift")
    kit = _swift_path_literal(str(ROOT / "mobile/ios/VoiceStudioKit"))
    (package / "Package.swift").write_text("""// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "PhoneJobDiagnosticProof", platforms: [.macOS(.v14)],
    dependencies: [.package(name: "VoiceStudioKit", path: KIT)],
    targets: [
        .target(name: "UIKit", path: "UIKit"),
        .target(name: "BackgroundTasks", path: "BackgroundTasks"),
        .testTarget(name: "PhoneJobDiagnosticProofTests",
            dependencies: ["UIKit", "BackgroundTasks", .product(name: "VoiceStudioKit", package: "VoiceStudioKit")],
            path: "AppTests", swiftSettings: [.unsafeFlags(["-strict-concurrency=complete"])])],
    swiftLanguageModes: [.v6])
""".replace("path: KIT", "path: " + kit), encoding="utf-8")
    env = dict(os.environ, HF_HUB_OFFLINE="1", HF_HUB_CACHE=str(tmp_path / "empty-hf"),
        CLANG_MODULE_CACHE_PATH=str(tmp_path / "modules"), SWIFT_MODULECACHE_PATH=str(tmp_path / "modules"),
        OMNIVOICE_DIAGNOSTICS_DISABLED="0", OMNIVOICE_ANALYTICS_DISABLED="0",
        VOICE_JOB_POLICY_PATH=str(ROOT / "config/diagnostics.json"))
    result = subprocess.run(["swift", "test", "--disable-sandbox", "--package-path", str(package),
        "--scratch-path", str(tmp_path / "build"), "--filter", "PhoneJobDiagnosticOwnerTests|IncomingPrivacyGateTests"],
        env=env, capture_output=True, text=True, timeout=180)
    transcript = result.stdout + result.stderr
    (tmp_path / "swift-test.log").write_text(transcript, encoding="utf-8")
    assert result.returncode == 0, transcript
    assert "Test automaticCPUHandoffRetainsTheUserStartedDiagnosticOwner(_:) with 5 test cases passed" in transcript
    assert "Suite IncomingPrivacyGateTests passed" in transcript
