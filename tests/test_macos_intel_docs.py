"""The macOS install guide must keep the Intel support statement.

The macOS Intel smoke job in .github/workflows/ci.yml asserts this sentence,
but only on that runner; this keeps the contract visible on every platform.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PHRASE = "Intel Macs are not supported"


def test_macos_guide_states_intel_support():
    assert _PHRASE in (_ROOT / "docs" / "install" / "macos.md").read_text(encoding="utf-8")


def test_smoke_job_checks_the_same_sentence():
    assert f'"{_PHRASE}"' in (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
