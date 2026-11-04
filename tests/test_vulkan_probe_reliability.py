"""Regressions for native GPU verification when hidden full reports hang."""
import importlib
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SUMMARY = """GPU0:
    deviceName = AMD Radeon RX 6700 XT
    deviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
    vendorID = 0x1002
    deviceUUID = gpu-0
"""


def module():
    return importlib.import_module("engines.omnivoice_gguf.vulkan_auto")


def test_hidden_full_report_timeout_uses_summary(monkeypatch):
    auto = module()
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _: "vulkaninfo")

    def run(args, **kwargs):
        calls.append(args)
        if "--summary" not in args:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return SimpleNamespace(stdout=SUMMARY, returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    assert auto._run_vulkaninfo() == SUMMARY
    assert len(calls) == 2


def test_failed_probe_output_is_not_verified(monkeypatch):
    auto = module()
    monkeypatch.setattr(shutil, "which", lambda _: "vulkaninfo")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout=SUMMARY, returncode=1))
    assert auto._run_vulkaninfo() is None


@pytest.mark.parametrize("vendor,name,memory,expected", [
    ("amd", "AMD Radeon RX 6700 XT", 12, 12),
    ("intel", "AMD Radeon RX 6700 XT", 16, 0),
    ("amd", "AMD Radeon RX 7600", 8, 0),
])
def test_verified_summary_uses_only_matching_os_memory(monkeypatch, vendor, name, memory, expected):
    auto = module()
    inventory = importlib.import_module("core.gpu_inventory")
    monkeypatch.setattr(auto, "_run_vulkaninfo", lambda: SUMMARY)
    monkeypatch.setattr(auto, "system_ram_gb", lambda: 64)
    monkeypatch.setattr(inventory, "detect_host_gpus", lambda: (
        SimpleNamespace(vendor=vendor, name=name, vram_gb=memory, discrete=True),))
    auto.detect_vulkan_profile.cache_clear()
    try:
        result = auto.detect_vulkan_profile()
        assert result.source == "vulkaninfo"
        assert result.best().dedicated_vram_gb == expected
        assert result.best().bf16 is None
    finally:
        auto.detect_vulkan_profile.cache_clear()


def test_duplicate_inventory_names_do_not_guess_memory(monkeypatch):
    auto = module()
    inventory = importlib.import_module("core.gpu_inventory")
    monkeypatch.setattr(auto, "_run_vulkaninfo", lambda: SUMMARY)
    monkeypatch.setattr(auto, "system_ram_gb", lambda: 64)
    monkeypatch.setattr(inventory, "detect_host_gpus", lambda: tuple(
        SimpleNamespace(vendor="amd", name="AMD Radeon RX 6700 XT", vram_gb=size, discrete=True)
        for size in (8, 12)))
    auto.detect_vulkan_profile.cache_clear()
    try:
        assert auto.detect_vulkan_profile().best().dedicated_vram_gb == 0
    finally:
        auto.detect_vulkan_profile.cache_clear()


def test_windows_native_build_cannot_succeed_without_vulkan(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/build-omnivoice-tts.yml").read_text())
    steps = workflow["jobs"]["build-omnivoice-tts"]["steps"]
    gate = next(step for step in steps if step.get("name") == "Check the Vulkan GPU module was built")
    git_bash = Path(os.environ.get("ProgramFiles", "")) / "Git/bin/bash.exe"
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None:
        pytest.skip("Bash required for the existing native-build workflow")
    (tmp_path / "bin").mkdir()
    result = subprocess.run([bash, "-c", gate["run"]], cwd=tmp_path,
                            env={**os.environ, "PLATFORM": "windows-x86_64"},
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode != 0, result.stdout + result.stderr



def test_physical_ram_probe_preserves_shared_memory_budget(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=64 * 1024 ** 3))
    assert module().system_ram_gb() == 64


@pytest.mark.parametrize("second_uuid,expected", [("gpu-0", 12), ("gpu-1", 0)])
def test_summary_counts_physical_gpus_not_duplicate_drivers(monkeypatch, second_uuid, expected):
    auto = module()
    inventory = importlib.import_module("core.gpu_inventory")
    other = SUMMARY.replace("GPU0:", "GPU1:").replace("gpu-0", second_uuid)
    monkeypatch.setattr(auto, "_run_vulkaninfo", lambda: SUMMARY + other)
    monkeypatch.setattr(auto, "system_ram_gb", lambda: 64)
    monkeypatch.setattr(inventory, "detect_host_gpus", lambda: (
        SimpleNamespace(vendor="amd", name="AMD Radeon RX 6700 XT", vram_gb=12, discrete=True),))
    auto.detect_vulkan_profile.cache_clear()
    try:
        assert auto.detect_vulkan_profile().best().dedicated_vram_gb == expected
    finally:
        auto.detect_vulkan_profile.cache_clear()


@pytest.mark.parametrize("mode,count,expected", [
    ("full", 1, SUMMARY), ("summary", 2, SUMMARY), ("failed", 2, None),
])
def test_probe_is_bounded_hidden_and_stops_after_success(monkeypatch, mode, count, expected):
    auto = module()
    monkeypatch.setattr(shutil, "which", lambda _: "vulkaninfo")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        success = mode == "full" or (mode == "summary" and "--summary" in args)
        return SimpleNamespace(stdout=SUMMARY if success else "", returncode=0 if success else 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert auto._run_vulkaninfo() == expected
    assert len(calls) == count
    for (_, options), timeout in zip(calls, (10, 3)):
        assert options["timeout"] == timeout
        assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
