"""CI runs tests/ in parallel (pytest-xdist); the plugin must be a declared dev dependency-group member.

`-n auto` fails with "unrecognized arguments" when the plugin is missing, which
would break every CI run, so the workflow flag and the dependency move together.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_parallel_flag_requires_the_declared_plugin():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    uses_parallel = re.search(r"pytest tests/ [^\n]*-n (auto|\d+)", workflow) is not None
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "dependency-groups"
    ]["dev"]
    declared = any(dep.lower().startswith("pytest-xdist") for dep in dev)
    assert declared or not uses_parallel, "ci.yml uses -n but pytest-xdist is not in the dev dependency group"


def test_backend_suite_stays_serial_until_its_order_dependence_is_fixed():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    line = next(l for l in workflow.splitlines() if "pytest backend/tests/" in l)
    assert " -n " not in line


def test_python_suite_installs_electron_dependencies_before_packaging_tests():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test-python"]["steps"]
    install = next(i for i, step in enumerate(steps)
                   if step.get("run") == "bun install --frozen-lockfile")
    pytest = next(i for i, step in enumerate(steps)
                  if "pytest tests/" in step.get("run", ""))
    assert install < pytest, "Packaging regressions load the installed electron-builder during pytest"
    assert "if" not in steps[install], "A clean runner must always install Electron dependencies"
