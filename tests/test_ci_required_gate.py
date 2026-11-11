"""The required "Tests (backend + frontend)" check combines every CI job's result.

It is the only CI status the `main` ruleset requires, and auto-merge merges once
the required checks pass, so a job that does not feed it cannot block a merge.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
GATE = WORKFLOW["jobs"]["test"]


def test_the_required_gate_only_reads_job_results():
    # A merge moved the renderer smoke run into the gate, which has no checkout or
    # Node, so the required check failed with both test jobs green (#3283).
    assert GATE["name"] == "Tests (backend + frontend)"
    assert [step.get("uses") for step in GATE["steps"]] == [None]
    assert "electron/" not in GATE["steps"][0]["run"]
    runs = [step["run"] for step in WORKFLOW["jobs"]["electron-smokes"]["steps"] if "run" in step]
    assert any("node electron/tests/run-renderer-smokes.mjs" in run for run in runs)


def test_every_job_feeds_the_required_gate():
    # Only the gate is required: a job missing here (UI crawler, Desktop UI, the
    # platform smokes and the release smoke all were) fails without blocking a merge.
    assert set(GATE["needs"]) == set(WORKFLOW["jobs"]) - {"test", "approve-external"}
    assert GATE["if"] == "${{ !cancelled() }}"


RESULTS = {name: value for name, value in GATE["env"].items()
           if value.endswith(".result }}") and name != "CHANGES_RESULT"}
ELECTRON_VARS = {"ELECTRON", "SMOKES", "CRAWLER", "NATIVE_PACKAGE", "DESKTOP_UI", "RELEASE_SMOKE"}


def _gate(**overrides) -> int:
    env = {name: "success" for name in RESULTS}
    env |= {"RUNTIME_NEEDED": "true", "CHANGES_RESULT": "success", "ELECTRON_NEEDED": "true", "CLA_HOLD": ""}
    env |= overrides
    # `run:` on ubuntu is `bash -e {0}`.
    script = GATE["steps"][0]["run"]
    return subprocess.run(["bash", "-e", "-c", script], env={**os.environ, **env},
                          capture_output=True, text=True).returncode


def test_the_gate_reads_the_jobs_it_needs():
    assert ELECTRON_VARS < set(RESULTS)
    assert {value.split(".")[1] for value in RESULTS.values()} == set(GATE["needs"]) - {"changes"}


def test_the_gate_passes_only_when_every_job_passed():
    assert _gate() == 0
    for name in RESULTS:
        for result in ("failure", "cancelled", "skipped"):
            assert _gate(**{name: result}) != 0, (name, result)


def test_electron_jobs_may_be_skipped_only_when_detection_said_so():
    skipped = {name: "skipped" for name in ELECTRON_VARS}
    assert _gate(**skipped, ELECTRON_NEEDED="false") == 0
    assert _gate(**skipped, ELECTRON_NEEDED="true") != 0
    assert _gate(**skipped, ELECTRON_NEEDED="") != 0
    # The Python-side jobs never may.
    for name in set(RESULTS) - ELECTRON_VARS:
        assert _gate(**skipped, ELECTRON_NEEDED="false", **{name: "skipped"}) != 0, name


@pytest.mark.parametrize("cla", ["", "held"])
def test_a_documentation_only_change_passes_with_every_job_skipped(cla):
    skipped = {name: "skipped" for name in RESULTS}
    assert _gate(**skipped, RUNTIME_NEEDED="false", CLA_HOLD=cla) == 0
    # ...but not with a job that ran anyway, nor when detection itself failed.
    assert _gate(**(skipped | {"RELEASE_SMOKE": "success"}), RUNTIME_NEEDED="false") != 0
    assert _gate(**skipped, RUNTIME_NEEDED="false", CHANGES_RESULT="failure") != 0
