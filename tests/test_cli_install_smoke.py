"""The CLI install smoke has to be wired to a job that can run it.

`scripts/cli_install_smoke.py` installs the bundled command with real uv in
temporary tool and config directories, replaces an existing desktop wrapper and
checks that the wrapper's environment survives. It was referenced by nothing, so
it could rot without anybody noticing; it now runs on the macOS and Linux legs of
the install smoke, which is the job that already has uv.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cli_install_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "install-smoke.yml"


def _workflow() -> dict:
    """The parsed workflow, with `on:` normalised (YAML 1.1 reads it as `True`)."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {**data, "on": data.get("on", data.get(True))}


def _step() -> dict:
    steps = _workflow()["jobs"]["install"]["steps"]
    step = next((item for item in steps if "cli_install_smoke.py" in str(item.get("run", ""))), None)
    assert step is not None, "no install-smoke step runs scripts/cli_install_smoke.py"
    return step


def _script():
    spec = importlib.util.spec_from_file_location("cli_install_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_install_smoke_runs_the_cli_smoke_on_the_posix_legs():
    condition = str(_step().get("if", ""))
    assert "Windows" in condition, "the step replaces a POSIX launcher, so it must exclude Windows"
    # An inverted guard (`runner.os == 'Windows'`) would also mention Windows.
    assert "!=" in condition and "==" not in condition.replace("!=", ""), (
        f"the step must be skipped on Windows, not run there: {condition}"
    )


def test_the_workflow_triggers_for_the_files_the_step_reads():
    """Changing the script or the bundled CLI has to start the job that runs it."""
    triggers = _workflow()["on"]
    for entry in ("cli/**", "scripts/cli_install_smoke.py"):
        assert entry in triggers["pull_request"]["paths"], f"{entry} does not trigger the smoke"
        assert entry in triggers["push"]["paths"], f"{entry} does not trigger the smoke on main"


def test_the_windows_branch_returns_with_a_skip(monkeypatch, capsys):
    """Calling the script on Windows must be informative, not an error."""
    module = _script()
    monkeypatch.setattr(os, "name", "nt")
    assert module.main() is None
    assert "SKIP" in capsys.readouterr().out


def test_the_script_does_not_abort_when_it_skips():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "POSIX-only" in text, "the Windows branch should say why it is skipped"
    assert "raise SystemExit" not in text, "the Windows branch must not abort the run"