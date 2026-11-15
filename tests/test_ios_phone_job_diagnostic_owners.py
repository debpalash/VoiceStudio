"""Execute the real PhoneJobs owner loop with host-only OS lifecycle facades.

This proves grant provenance across automatic CPU handoff, not UIKit scheduling,
background entitlements, simulator behavior, native inference, or model execution.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ios-phone-job-diagnostics"
pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="The app adapter uses Apple's MetricKit module")


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
    kit = json.dumps(str(ROOT / "mobile/ios/VoiceStudioKit"))
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
        VOICE_JOB_POLICY_PATH=str(ROOT / "config/diagnostics.json"))
    result = subprocess.run(["swift", "test", "--disable-sandbox", "--package-path", str(package),
        "--scratch-path", str(tmp_path / "build"), "--filter", "PhoneJobDiagnosticOwnerTests"],
        env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 test cases passed" in result.stdout + result.stderr
