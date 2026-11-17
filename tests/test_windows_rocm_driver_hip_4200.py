"""Issue #4200: prefer the driver's newer amdhip64 over the wheel's crashing copy."""
import importlib
import json
import subprocess
from pathlib import Path

import pytest


def module(name):
    return importlib.import_module(f"core.windows_rocm.{name}")


def layout(tmp_path, driver=True):
    bundled = tmp_path / "site" / "_rocm_sdk_core" / "bin"
    bundled.mkdir(parents=True)
    (bundled / "amdhip64_7.dll").write_bytes(b"old")
    system = tmp_path / "System32"
    system.mkdir()
    if driver:
        (system / "amdhip64_7.dll").write_bytes(b"new")
    return bundled, system


def versions(by_parent):
    return lambda path: by_parent[Path(path).parent.name]


def test_newer_driver_runtime_is_chosen(tmp_path):
    runtime = module("runtime")
    bundled, system = layout(tmp_path)
    got, reason = runtime.driver_hip_choice(
        bundled, system, versions({"bin": (10, 0, 3581, 0), "System32": (10, 0, 3679, 0)}))
    assert got == system / "amdhip64_7.dll" and "newer" in reason


@pytest.mark.parametrize("driver_version", [(10, 0, 3581, 0), (10, 0, 3000, 0), None])
def test_not_newer_or_unreadable_keeps_bundled(tmp_path, driver_version):
    runtime = module("runtime")
    bundled, system = layout(tmp_path)
    got, reason = runtime.driver_hip_choice(
        bundled, system, versions({"bin": (10, 0, 3581, 0), "System32": driver_version}))
    assert got is None and reason


def test_missing_driver_dll_and_version_errors_fall_back(tmp_path):
    runtime = module("runtime")
    bundled, system = layout(tmp_path, driver=False)
    assert runtime.driver_hip_choice(bundled, system, lambda p: (9,))[0] is None
    bundled, system = layout(tmp_path / "x")

    def boom(path):
        raise OSError("no version.dll")

    got, reason = runtime.driver_hip_choice(bundled, system, boom)
    assert got is None and "could not read" in reason


def test_env_opt_out(tmp_path, monkeypatch):
    runtime = module("runtime")
    bundled, system = layout(tmp_path)
    monkeypatch.setenv(runtime.DRIVER_HIP_ENV, "0")
    assert runtime.driver_hip_choice(bundled, system, lambda p: (99,))[0] is None


def test_preload_failure_falls_back_and_logs(tmp_path, monkeypatch, caplog):
    runtime = module("runtime")
    bundled, system = layout(tmp_path)
    monkeypatch.setattr(runtime, "_PRELOADED", {})
    monkeypatch.setattr(runtime, "driver_hip_choice", lambda core: (system / "amdhip64_7.dll", "newer"))

    def refuse(path):
        raise OSError("load failed")

    monkeypatch.setattr(runtime.ctypes, "WinDLL", refuse, raising=False)
    with caplog.at_level("WARNING"):
        assert runtime.preload_driver_hip([bundled]) is None
    assert "using bundled runtime" in caplog.text


def test_non_rocm_has_no_directories_so_no_preload(monkeypatch):
    runtime = module("runtime")
    monkeypatch.setattr(runtime, "_PRELOADED", {})
    assert runtime.preload_driver_hip([]) is None


def test_crashing_probe_retries_with_bundled_runtime(monkeypatch):
    verify = module("verify")
    runtime = module("runtime")
    monkeypatch.delenv(runtime.DRIVER_HIP_ENV, raising=False)
    seen = []

    def run(cmd, **kw):
        seen.append(kw["env"].get(runtime.DRIVER_HIP_ENV))
        if len(seen) == 1:
            return subprocess.CompletedProcess(cmd, 0xC0000005, "", "")
        return subprocess.CompletedProcess(cmd, 1, json.dumps({"ready": False, "scope": "model-free",
                                                               "category": "device_runtime", "error": "x"}), "")

    monkeypatch.setattr(verify.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="x"):
        verify.verify_environment()
    assert seen == [None, "0"]
