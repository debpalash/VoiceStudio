"""Startup defaults for compile paths unavailable on Windows."""

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
VARIABLES = (
    "TORCH_COMPILE_DISABLE",
    "TORCHDYNAMO_DISABLE",
    "TORCHINDUCTOR_DISABLE",
)


def _windows_defaults_node():
    tree = ast.parse((ROOT / "backend/main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "sys.platform == 'win32'":
            return node
    raise AssertionError("Windows compile-default block is missing")


def _run_defaults(platform):
    module = ast.Module(body=[_windows_defaults_node()], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "backend/main.py", "exec"), {
        "os": os,
        "sys": SimpleNamespace(platform=platform),
    })


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_compile_defaults_are_windows_only(monkeypatch, platform):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    _run_defaults(platform)
    assert all(variable not in os.environ for variable in VARIABLES)


def test_windows_compile_defaults_preserve_explicit_values(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("TORCHDYNAMO_DISABLE", "0")

    _run_defaults("win32")

    assert os.environ["TORCH_COMPILE_DISABLE"] == "1"
    assert os.environ["TORCHDYNAMO_DISABLE"] == "0"
    assert os.environ["TORCHINDUCTOR_DISABLE"] == "1"
