"""Model loading keeps the caller's tqdm environment intact."""

import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Stdout:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


ROOT = Path(__file__).resolve().parents[1]


def _load_harness(observed, stdout, failure=None):
    class Parent:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            observed.append(os.environ.get("TQDM_DISABLE"))
            if failure:
                raise failure
            return SimpleNamespace()

    tree = ast.parse((ROOT / "omnivoice/models/omnivoice.py").read_text(encoding="utf-8"))
    model_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OmniVoice"
    )
    method = next(
        node for node in model_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "from_pretrained"
    )
    harness = ast.ClassDef(
        name="Harness",
        bases=[ast.Name(id="Parent", ctx=ast.Load())],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.Module(body=[harness], type_ignores=[])
    namespace = {
        "Parent": Parent,
        "_DEFAULT_ASR_MODEL": "unused-asr",
        "logging": logging,
        "os": os,
        "sys": SimpleNamespace(stdout=stdout),
    }
    code = compile(
        ast.fix_missing_locations(module),
        "omnivoice/models/omnivoice.py",
        "exec",
    )
    exec(code, namespace)
    return namespace["Harness"]


@pytest.mark.parametrize("fails", [False, True])
def test_non_tty_disables_tqdm_only_during_load(monkeypatch, fails):
    monkeypatch.delenv("TQDM_DISABLE", raising=False)
    observed = []
    failure = RuntimeError("load stopped") if fails else None
    harness = _load_harness(observed, _Stdout(False), failure)

    if fails:
        with pytest.raises(RuntimeError, match="load stopped"):
            harness.from_pretrained("unused", train=True)
    else:
        harness.from_pretrained("unused", train=True)

    assert observed == ["1"]
    assert "TQDM_DISABLE" not in os.environ


@pytest.mark.parametrize("stdout", [_Stdout(True), None])
def test_tty_or_missing_stdout_does_not_change_tqdm(monkeypatch, stdout):
    monkeypatch.setenv("TQDM_DISABLE", "user-choice")
    observed = []
    harness = _load_harness(observed, stdout)

    harness.from_pretrained("unused", train=True)

    assert observed == ["user-choice"]
    assert os.environ["TQDM_DISABLE"] == "user-choice"


def test_non_tty_restores_a_prior_user_value(monkeypatch):
    monkeypatch.setenv("TQDM_DISABLE", "keep-this")
    observed = []
    harness = _load_harness(observed, _Stdout(False))

    harness.from_pretrained("unused", train=True)

    assert observed == ["1"]
    assert os.environ["TQDM_DISABLE"] == "keep-this"
