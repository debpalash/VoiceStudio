"""Regression for #2052: pedalboard 0.9.21+ Linux wheels SIGILL on import.

spotify/pedalboard#454 — 0.9.21+ manylinux wheels are compiled with
``-march=native``, so ``import pedalboard`` raises SIGILL on CPUs that
lack the builder host's SIMD set. VoiceStudio reported this as a Docker
first-synthesis crash; 0.9.20 is the last known-good wheel.

The specifier must keep the 0.9.14 floor (effects-chain API) and exclude
0.9.21+. ``uv.lock`` must resolve inside that range so ``uv sync`` /
Docker ``--frozen-lockfile`` cannot pull a crashing wheel.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

_ROOT = Path(__file__).resolve().parents[1]
_KNOWN_GOOD = "0.9.20"
_FIRST_BAD = "0.9.21"


def _pedalboard_requirement() -> Requirement:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(item) for item in data["project"]["dependencies"]]
    return next(req for req in requirements if req.name.lower() == "pedalboard")


def _locked_pedalboard_versions() -> list[Version]:
    lock = tomllib.loads((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return [
        Version(package["version"])
        for package in lock["package"]
        if package["name"].lower() == "pedalboard"
    ]


def test_specifier_keeps_known_good_and_excludes_sigill_wheels():
    pedalboard = _pedalboard_requirement()
    assert pedalboard.specifier.contains(_KNOWN_GOOD)
    assert not pedalboard.specifier.contains(_FIRST_BAD)
    assert not pedalboard.specifier.contains("0.9.24")


def test_lockfile_resolves_inside_the_safe_range():
    locked = _locked_pedalboard_versions()
    assert locked, "uv.lock must pin pedalboard"
    specifier = _pedalboard_requirement().specifier
    too_new = [str(version) for version in locked if version >= Version(_FIRST_BAD)]
    out_of_range = [str(version) for version in locked if not specifier.contains(version)]
    assert too_new == []
    assert out_of_range == []
