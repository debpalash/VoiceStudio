"""`docs/STRUCTURE.md` carries counts of the trees it describes.

Counts rot silently: the doc shipped "79 modules of business logic" when
`backend/services/` held 78, and nothing failed. A number in a doc that no
test reads is a number that is wrong the week after it is written, so the
counts are pinned here rather than trusted to review attention.

Per CLAUDE.md's token-economy rule, mechanical claims like this belong in a
deterministic test, not in agent or reviewer effort. When you add a router or
a service, update the doc — this test tells you which line.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STRUCTURE = REPO / "docs" / "STRUCTURE.md"


def _modules(directory: Path) -> set:
    """Importable modules directly under `directory`: flat `*.py` files plus
    subpackages. `__init__.py` is packaging, not a module of its own."""
    names = {p.stem for p in directory.glob("*.py") if p.stem != "__init__"}
    names |= {p.name for p in directory.iterdir() if (p / "__init__.py").is_file()}
    return names


def _documented(pattern: str) -> int:
    text = STRUCTURE.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"docs/STRUCTURE.md no longer states a count for {pattern!r}"
    return int(match.group(1))


def test_router_count_matches_the_tree():
    actual = len(_modules(REPO / "backend" / "api" / "routers"))
    assert _documented(r"(\d+) routers, auto-included") == actual, (
        f"docs/STRUCTURE.md says N routers; backend/api/routers/ has {actual}"
    )


def test_service_count_matches_the_tree():
    actual = len(_modules(REPO / "backend" / "services"))
    assert _documented(r"(\d+) modules of business logic") == actual, (
        f"docs/STRUCTURE.md says N services; backend/services/ has {actual}"
    )


def test_every_engine_adapter_is_listed():
    engines = REPO / "backend" / "engines"
    actual = {p.name for p in engines.iterdir() if (p / "__init__.py").is_file()}
    text = STRUCTURE.read_text(encoding="utf-8")
    listed = text[text.index("per-engine adapters:") :]
    listed = re.split(r"\n│   [├└]──", listed, maxsplit=1)[0]
    missing = sorted(name for name in actual if name not in listed)
    assert not missing, f"docs/STRUCTURE.md does not list engine adapter(s): {missing}"
