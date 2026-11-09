"""Strict child-native readiness, not inference certification."""
import importlib
import json
import subprocess
import sys

import pytest


def module(name):
    return importlib.import_module(f"core.windows_rocm.{name}")


def good_report():
    recipe = module("recipe")
    return {
        "ready": True, "scope": "model-free", "python": sys.executable,
        "architecture": "win-amd64", "abi": "cp312", "hip": "7.2.53211",
        "packages": recipe.expected_versions(recipe.load_recipe()),
        "torch_gpu": True, "kernel_result": 4.0, "gpu_architecture": "gfx1100",
        "ct2_gpu_count": 1, "ct2_compute_types": ["float16"],
        "ct2_binary_sha256": recipe.load_recipe()["ctranslate2"]["binary_sha256"],
    }


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired("probe", 90), OSError("failed to launch"),
])
def test_probe_timeout_or_crash_is_failure(monkeypatch, error):
    verify = module("verify")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(verify.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="probe"):
        verify.verify_environment()


@pytest.mark.parametrize("field,value", [
    ("hip", None), ("torch_gpu", False), ("kernel_result", 0),
    ("ct2_gpu_count", 0), ("ct2_compute_types", ["float32"]),
    ("abi", "cp311"), ("architecture", "win-arm64"),
    ("python", "/another/python"), ("ct2_binary_sha256", "stock"),
    ("packages", {"torch": "2.9.1+cu128"}), ("ready", False),
    ("gpu_architecture", "gfx1031"),
])
def test_probe_rejects_cpu_fallback_wrong_abi_and_unpinned_packages(monkeypatch, field, value):
    verify = module("verify")
    report = good_report()
    report[field] = value
    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps(report), ""))
    with pytest.raises(RuntimeError):
        verify.verify_environment()


def test_probe_success_requires_bounded_same_interpreter_child(monkeypatch):
    verify = module("verify")

    def run(command, **kwargs):
        assert command[:3] == [sys.executable, "-m", "backend.core.windows_rocm.probe"]
        assert kwargs["timeout"] == 90
        return subprocess.CompletedProcess(command, 0, json.dumps(good_report()), "")

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert verify.verify_environment()["scope"] == "model-free"


@pytest.mark.parametrize("code,output", [(1, "{}"), (0, "garbage"), (-1, "{}")])
def test_bad_child_results_fail(monkeypatch, code, output):
    verify = module("verify")
    monkeypatch.setattr(verify.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, code, output, "diagnostic"))
    with pytest.raises(RuntimeError):
        verify.verify_environment()


def test_lifetime_dll_handles_reused_and_non_windows_noop(monkeypatch, tmp_path):
    runtime = module("runtime")
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "dll_directories", lambda: [tmp_path, tmp_path])
    monkeypatch.setattr(runtime, "_DLL_HANDLES", {})
    calls = []
    handle = object()
    # Windows-only API: absent on the macOS/Linux runners that run this test.
    monkeypatch.setattr(runtime.os, "add_dll_directory", lambda path: calls.append(path) or handle, raising=False)
    runtime.initialize_dll_search()
    runtime.initialize_dll_search()
    assert list(runtime._DLL_HANDLES.values()) == [handle]
    assert len(calls) == 1
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    runtime.initialize_dll_search()
    assert len(calls) == 1
