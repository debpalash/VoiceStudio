"""The macOS install guide must keep the Intel support statement.

The macOS Intel smoke job in .github/workflows/ci.yml asserts this sentence,
but only on that runner; this keeps the contract visible on every platform.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PHRASE = "Intel Macs are not supported"


def test_macos_guide_states_intel_support():
    assert _PHRASE in (_ROOT / "docs" / "install" / "macos.md").read_text(encoding="utf-8")


def test_smoke_job_checks_the_same_sentence():
    assert f'"{_PHRASE}"' in (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


# #3074: a page that lists "Apple Silicon (M1–M5)" tells the owner of the next
# chip that their Mac is unsupported. Support is decided by CPU architecture
# (electron/src/main/backend.ts isUnsupportedPlatform), never by chip name.
_CLOSED_CHIP_RANGE = re.compile(r"\bM[1-9]\s*(?:[–-]|to|through)\s*M[1-9]\b")


def test_install_docs_never_cap_the_apple_silicon_chip_range():
    pages = [_ROOT / "README.md", *sorted((_ROOT / "docs" / "install").glob("*.md"))]
    offenders = [
        f"{page.relative_to(_ROOT)}:{number}"
        for page in pages
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1)
        if _CLOSED_CHIP_RANGE.search(line)
    ]
    assert not offenders, offenders
