"""Install guides must not repeat a section: a duplicated copy drifts out of date."""
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_GUIDES = sorted((_ROOT / "docs" / "install").glob("*.md")) + [_ROOT / "README.md"]


@pytest.mark.parametrize("path", _GUIDES, ids=lambda p: p.name)
def test_no_repeated_top_level_sections(path):
    headings = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## ")]
    repeated = [h for h, n in Counter(headings).items() if n > 1]
    assert not repeated, f"{path.name} repeats {repeated}"
