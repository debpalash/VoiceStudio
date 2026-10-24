"""A malformed optional environment setting must not block backend import."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _import_config(tmp_path, **overrides):
    env = {**os.environ, **overrides}
    env["OMNIVOICE_DATA_DIR"] = str(tmp_path / "app-data")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "backend")
    completed = subprocess.run(
        [sys.executable, "-B", "-c",
         "import json; from core import config; "
         "print(json.dumps([config.IDLE_TIMEOUT_SECONDS, config.CPU_POOL_WORKERS]))"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize("name", ["OMNIVOICE_IDLE_TIMEOUT", "OMNIVOICE_CPU_POOL"])
@pytest.mark.parametrize("invalid", ["", "not-a-number", "-1", "1.5", "nan"])
def test_invalid_override_falls_back_without_breaking_import(tmp_path, name, invalid):
    overrides = {"OMNIVOICE_IDLE_TIMEOUT": "900", "OMNIVOICE_CPU_POOL": "0", name: invalid}
    assert _import_config(tmp_path, **overrides) == [900, min(8, os.cpu_count() or 4)]


@pytest.mark.parametrize(("idle", "pool"), [("0", "0"), ("42", "3"), (" 120 ", " 2 ")])
def test_valid_overrides_and_zero_semantics_are_preserved(tmp_path, idle, pool):
    assert _import_config(tmp_path, OMNIVOICE_IDLE_TIMEOUT=idle, OMNIVOICE_CPU_POOL=pool) == [
        int(idle), int(pool) or min(8, os.cpu_count() or 4),
    ]
