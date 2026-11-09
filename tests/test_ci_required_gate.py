"""The required "Tests (backend + frontend)" check only combines the test jobs' results."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_the_required_gate_only_reads_job_results():
    # A merge moved the renderer smoke run into the gate, which has no checkout or
    # Node, so the required check failed with both test jobs green (#3283).
    gate = WORKFLOW["jobs"]["test"]
    assert gate["name"] == "Tests (backend + frontend)"
    assert [step.get("uses") for step in gate["steps"]] == [None]
    assert "electron/" not in gate["steps"][0]["run"]
    runs = [step["run"] for step in WORKFLOW["jobs"]["electron-smokes"]["steps"] if "run" in step]
    assert any("node electron/tests/run-renderer-smokes.mjs" in run for run in runs)
