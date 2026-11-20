"""Windows ROCm pins its interpreter once, in the recipe.

`scripts/windows-rocm-recipe.json` carries the version, ABI and platform the ROCm
wheels and HIP runtime were reviewed against. The setup script used to spell the
uv request out (`cpython-3.12-windows-x86_64`) and the desktop readiness constant
still does, so a recipe bump could leave both of them asking for an interpreter the
recipe no longer pins. This test compares the two against the recipe through the
shared helper.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "scripts" / "windows-rocm-recipe.json"
HELPER = ROOT / "electron" / "scripts" / "windows-rocm-python.mjs"
SETUP = ROOT / "scripts" / "setup-api.mjs"
NATIVE = ROOT / "electron" / "src" / "main" / "runtime-windows-rocm.ts"


def _helper_request(tmp_path: Path) -> str:
    """`windowsRocmPythonRequest` applied to the shipped recipe, run through node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to exercise the shared helper")
    probe = tmp_path / "probe.mjs"
    # The paths travel on the command line rather than inside the generated file:
    # a checkout under a user's home would otherwise be written to disk.
    probe.write_text(
        "const [helper, recipePath] = process.argv.slice(2);\n"
        "const { readFileSync } = await import('node:fs');\n"
        "const { windowsRocmPythonRequest } = await import(helper);\n"
        "console.log(windowsRocmPythonRequest(JSON.parse(readFileSync(recipePath, 'utf8'))));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(probe), HELPER.as_uri(), str(RECIPE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_native_constant_matches_the_recipe(tmp_path: Path) -> None:
    request = _helper_request(tmp_path)
    match = re.search(r"WINDOWS_ROCM_PYTHON_REQUEST = '([^']+)'", NATIVE.read_text(encoding="utf-8"))
    assert match, "the Windows ROCm interpreter constant was not found"
    assert match.group(1) == request, (
        f"the desktop asks uv for {match.group(1)} while the recipe pins {request}"
    )


def test_the_setup_script_derives_the_interpreter_instead_of_retyping_it() -> None:
    text = SETUP.read_text(encoding="utf-8")
    assert "windowsRocmPythonRequest(" in text, (
        "the setup script must ask the shared helper for the uv request"
    )
    assert "cpython-3." not in text.split("windowsRocmPythonRequest(")[0], (
        "the ROCm branch retypes the interpreter version instead of reading the recipe"
    )


def test_the_helper_rejects_an_incomplete_recipe(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to exercise the shared helper")
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        "const [helper] = process.argv.slice(2);\n"
        "const { windowsRocmPythonRequest } = await import(helper);\n"
        "try { windowsRocmPythonRequest({ python: { version: '3.13' } }); console.log('accepted'); }\n"
        "catch (error) { console.log('rejected: ' + error.message); }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(probe), HELPER.as_uri()], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "rejected" in result.stdout, "a recipe without an ABI or platform must be refused"
