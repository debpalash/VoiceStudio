"""The Python CI shards partition tests/ exactly: every test runs in one shard."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _collect(shard: str | None) -> set[str]:
    env = {**os.environ}
    env.pop("VOICESTUDIO_TEST_SHARD", None)
    if shard:
        env["VOICESTUDIO_TEST_SHARD"] = shard
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_ci_pass_cache.py", "tests/test_ci_pytest_parallel.py",
         "--collect-only", "-q", "-p", "no:xdist", "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout
    return {line for line in out.splitlines() if "::" in line}


def test_shards_are_a_disjoint_cover_of_the_full_collection():
    full = _collect(None)
    first, second = _collect("1/2"), _collect("2/2")
    assert first and second
    assert not first & second
    assert first | second == full


def test_workflow_shard_count_matches_the_matrix():
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["test-python"]
    shards = job["strategy"]["matrix"]["shard"]
    assert sorted(shards) == list(range(1, len(shards) + 1))
    pytest_step = next(s for s in job["steps"] if "pytest tests/" in s.get("run", ""))
    assert pytest_step["env"]["VOICESTUDIO_TEST_SHARD"] == "${{ matrix.shard }}/" + str(len(shards))
    # the serial backend session must run in exactly one shard
    backend = next(s for s in job["steps"] if "pytest backend/tests/" in s.get("run", ""))
    assert re.search(r"matrix\.shard == \d", backend["if"])


def test_a_nested_pytest_run_does_not_inherit_the_shard(tmp_path, monkeypatch):
    probe = tmp_path / "test_probe.py"
    probe.write_text("def test_ok():\n    pass\n")
    monkeypatch.setenv("VOICESTUDIO_TEST_SHARD", "2/2")
    # Sharding consumes the variable at collection, so a child started later
    # by a test in this session collects everything. Emulate that session.
    import importlib.util

    spec = importlib.util.spec_from_file_location("root_conftest_for_shard", ROOT / "conftest.py")
    root_conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root_conftest)

    class _Cfg:
        class hook:
            @staticmethod
            def pytest_deselected(items):
                pass

    root_conftest.pytest_collection_modifyitems(_Cfg, [])
    assert "VOICESTUDIO_TEST_SHARD" not in os.environ
    out = subprocess.run([sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
