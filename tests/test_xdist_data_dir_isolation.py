"""Each pytest-xdist worker must get its own hermetic data dir (tests/conftest.py).

The controller exports the dir it created and workers inherit the environment,
so before the fix all workers shared one prefs.json/omnivoice.db. That made the
LLM skill tests fail in parallel CI runs (#3223) while passing serially.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PROBE_OUT = os.environ.get("XDIST_DATA_DIR_PROBE_OUT")


@pytest.mark.skipif(not _PROBE_OUT, reason="driven by test_each_xdist_worker_gets_its_own_data_dir")
@pytest.mark.parametrize("i", range(12))
def test_probe_records_worker_and_data_dir(i):
    with open(_PROBE_OUT, "a", encoding="utf-8") as out:
        data_dir = os.environ["OMNIVOICE_DATA_DIR"]
        env_file = os.environ["OMNIVOICE_ENV_FILE"]
        # Directory names only (mkdtemp names are unique), never absolute paths.
        out.write(
            f"{os.environ.get('PYTEST_XDIST_WORKER')}\t{os.path.basename(data_dir)}"
            f"\t{int(os.path.dirname(env_file) == data_dir)}\n"
        )


@pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="pytest-xdist not installed")
def test_each_xdist_worker_gets_its_own_data_dir(tmp_path):
    out = tmp_path / "probe.tsv"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OMNIVOICE_DATA_DIR", "OMNIVOICE_ENV_FILE", "_OMNIVOICE_TEST"))}
    env["XDIST_DATA_DIR_PROBE_OUT"] = str(out)
    subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n", "3",
         f"{Path(__file__)}::test_probe_records_worker_and_data_dir"],
        cwd=ROOT, env=env, check=True, capture_output=True, timeout=120,
    )
    rows = [line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()]
    by_worker = {}
    for worker, data_dir, env_in_data_dir in rows:
        by_worker.setdefault(worker, set()).add((data_dir, env_in_data_dir))
    assert len(by_worker) >= 2, "xdist ran everything on one worker; nothing to compare"
    assert all(len(v) == 1 for v in by_worker.values()), "a worker changed its data dir mid-run"
    dirs = [next(iter(v))[0] for v in by_worker.values()]
    assert len(set(dirs)) == len(dirs), f"workers share a data dir: {dirs}"
    for _, env_in_data_dir in (next(iter(v)) for v in by_worker.values()):
        assert env_in_data_dir == "1", "user-env file must live in the worker's own dir"
