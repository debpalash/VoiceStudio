"""The native helper must dispatch Carbon events on the macOS backend smoke runner."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="Carbon is a macOS API")
def test_native_carbon_shortcut_event_delivery():
    cargo = shutil.which("cargo")
    assert cargo, "The macOS native smoke requires the runner's Rust toolchain"
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [cargo, "run", "--locked", "--manifest-path", "native/desktop-bridge/Cargo.toml",
         "--example", "macos-shortcut-pump-smoke"],
        cwd=root, capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: Carbon application hotkey dispatch and idle pump" in result.stdout
