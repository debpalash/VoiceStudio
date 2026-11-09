"""pedalboard must stay below the 0.9.21 wheels that crash on older CPUs (#2052)."""
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _specifier() -> SpecifierSet:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    reqs = [Requirement(d) for d in deps]
    found = [r for r in reqs if r.name.lower() == "pedalboard"]
    assert len(found) == 1, "pedalboard must be declared exactly once"
    return found[0].specifier


@pytest.mark.parametrize("version,allowed", [
    ("0.9.14", True), ("0.9.20", True),
    ("0.9.13", False), ("0.9.21", False), ("0.9.24", False),
])
def test_specifier_window(version, allowed):
    assert (Version(version) in _specifier()) is allowed


def test_locked_pedalboard_versions_obey_the_pin():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [Version(p["version"]) for p in lock["package"] if p["name"] == "pedalboard"]
    assert versions, "uv.lock has no pedalboard entry"
    spec = _specifier()
    for v in versions:
        assert v in spec
        assert v < Version("0.9.21")
