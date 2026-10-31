"""Windows ROCm 10 uses pinned native kernels and safe precision."""
import json
import re
import types

import pytest

from core import device_caps
from core.windows_rocm import install, recipe


def test_recipe_has_a_native_windows_rocm10_variant():
    selected = recipe.load_recipe(architecture="gfx1031")
    assert selected["gpu_architectures"] == ["gfx1031"]
    assert selected["torch"]["hip_prefix"] == "7.15."
    assert "amd-torch-device-gfx1031==2.13.0+rocm10.0.0" in selected["runtime"]["packages"]
    assert all("gfx1030" not in p and "gfx1032" not in p for p in selected["runtime"]["packages"])


def test_overlay_urls_are_allowlisted_and_hashed():
    selected = recipe.load_recipe(architecture="gfx1031")
    for line in install.hashed_requirements(selected).splitlines():
        url, digest = line.split(" --hash=sha256:")
        assert url.startswith("https://stable.repo.amd.com/rocm/whl-next/")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_missing_overlay_hash_is_rejected(tmp_path):
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    device = data["rocm10"]["devices"]["gfx1031"]
    del device["wheel_sha256"][device["wheel_files"][0]]
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="sha256"):
        recipe.load_recipe(path, architecture="gfx1031")


def test_unapproved_overlay_source_is_rejected(tmp_path):
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    data["rocm10"]["torch"]["find_links"] = "https://unapproved.example/"
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="source"):
        recipe.load_recipe(path, architecture="gfx1031")


def test_windows_rocm10_only_reports_installed_architectures(monkeypatch):
    monkeypatch.setattr(device_caps.sys, "platform", "win32")
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    fake = types.SimpleNamespace(
        __version__="2.13.0+rocm10.0.0", version=types.SimpleNamespace(hip="7.15"),
        cuda=types.SimpleNamespace(is_available=lambda: True,
            get_arch_list=lambda: ["gfx1030", "gfx1031"],
            get_device_properties=lambda index: types.SimpleNamespace(gcnArchName="gfx1031")))
    monkeypatch.setattr(device_caps, "_installed_rocm_arches", lambda version: {"gfx1031"})
    assert device_caps.arch_unsupported(fake) is None
    assert device_caps.build_arch_list(fake) == ["gfx1031"]
    assert device_caps.rocm_override_target("gfx1031", device_caps.build_arch_list(fake)) is None

@pytest.mark.parametrize("pci,gfx", [("73bf", "gfx1030"), ("73df", "gfx1031"), ("73ff", "gfx1032")])
def test_pci_device_selects_the_exact_recipe(monkeypatch, pci, gfx):
    from core import gpu_inventory
    monkeypatch.setattr(recipe.sys, "platform", "win32")
    monkeypatch.setattr(gpu_inventory, "detect_host_gpus", lambda: (
        gpu_inventory.HostGPU(vendor="amd", name="Radeon", pci_device_id=pci),))
    assert recipe.load_recipe()["gpu_architectures"] == [gfx]


def test_mixed_amd_architectures_fail_closed(monkeypatch):
    from core import gpu_inventory
    monkeypatch.setattr(recipe.sys, "platform", "win32")
    monkeypatch.setattr(gpu_inventory, "detect_host_gpus", lambda: (
        gpu_inventory.HostGPU(vendor="amd", name="Radeon", pci_device_id="73df"),
        gpu_inventory.HostGPU(vendor="amd", name="Radeon", pci_device_id="744c")))
    with pytest.raises(RuntimeError, match="Multiple AMD"):
        recipe.load_recipe()


@pytest.mark.parametrize("gfx", ["gfx1030", "gfx1031", "gfx1032"])
def test_each_selected_artifact_is_pinned(gfx):
    selected = recipe.load_recipe(architecture=gfx)
    lines = install.hashed_requirements(selected).splitlines()
    assert len(lines) == 10
    assert all(re.search(r" --hash=sha256:[0-9a-f]{64}$", line) for line in lines)
    assert sum(f"device-{gfx}/" in line for line in lines) == 3


def test_the_amd_kernel_and_sdk_packages_are_both_required(monkeypatch):
    from importlib import metadata
    class Dist:
        metadata = {"Name": "amd-torch-device-gfx1031"}
        version = "2.13.0+rocm10.0.0"
    monkeypatch.setattr(metadata, "distributions", lambda: [Dist()])
    def missing_sdk(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing_sdk)
    assert device_caps._installed_rocm_arches(Dist.version) == set()


def test_native_gfx1031_is_reported_as_accelerated():
    from core.gpu_report import build_gpu_report
    caps = device_caps.HostCaps(family="rocm", available_families=("rocm", "cpu"), device_name="Radeon")
    report = build_gpu_report(caps, (), {"kind": "rocm", "version": "2.13.0+rocm10.0.0"}, [], [], platform="win32")
    assert report["state"] == "accelerated"
    assert report["options"] == []


def test_windows_never_sets_hsa_override(monkeypatch):
    from services import model_manager
    monkeypatch.setattr(model_manager.sys, "platform", "win32")
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    fake = types.SimpleNamespace(
        version=types.SimpleNamespace(hip="7.15"),
        cuda=types.SimpleNamespace(get_device_name=lambda i: "Radeon",
            get_device_properties=lambda i: types.SimpleNamespace(gcnArchName="gfx1031"),
            get_arch_list=lambda: ["gfx1030"]))
    model_manager._configure_rocm_if_needed(fake)
    assert "HSA_OVERRIDE_GFX_VERSION" not in model_manager.os.environ


def test_rocm10_dll_search_excludes_stale_rocm7_libraries(monkeypatch, tmp_path):
    from core.windows_rocm import runtime
    for package in ("torch", "ctranslate2", "_rocm_sdk_core", "_rocm_sdk_libraries", "_rocm_sdk_libraries_custom"):
        (tmp_path / package / ("lib" if package == "torch" else "" if package == "ctranslate2" else "bin")).mkdir(parents=True)
    monkeypatch.setattr(runtime.metadata, "version", lambda name: "2.13.0+rocm10.0.0")
    monkeypatch.setattr(runtime, "package_root", lambda name: tmp_path / name)
    directories = runtime.dll_directories()
    assert (tmp_path / "_rocm_sdk_libraries" / "bin").resolve() in directories
    assert (tmp_path / "_rocm_sdk_libraries_custom" / "bin").resolve() not in directories


def test_overlay_refuses_an_hsa_override_before_imports(monkeypatch):
    from core.windows_rocm import probe
    selected = recipe.load_recipe(architecture="gfx1031")
    monkeypatch.setattr(probe, "validate_target", lambda: None)
    monkeypatch.setattr(probe, "load_recipe", lambda: selected)
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "10.3.0")
    monkeypatch.setattr(probe.metadata, "version", lambda name: pytest.fail("unexpected package probe"))
    with pytest.raises(RuntimeError, match="Remove HSA_OVERRIDE_GFX_VERSION"):
        probe.probe()


def test_missing_rocm10_kernel_metadata_fails_closed(monkeypatch):
    monkeypatch.setattr(device_caps.sys, "platform", "win32")
    fake = types.SimpleNamespace(__version__="2.13.0+rocm10.0.0",
        version=types.SimpleNamespace(hip="7.15"),
        cuda=types.SimpleNamespace(is_available=lambda: True,
            get_arch_list=lambda: ["gfx1031"],
            get_device_properties=lambda i: types.SimpleNamespace(gcnArchName="gfx1031")))
    monkeypatch.setattr(device_caps, "_installed_rocm_arches", lambda version: (_ for _ in ()).throw(OSError("metadata")))
    assert device_caps.arch_unsupported(fake) is not None


def test_recipe_rejects_a_requirements_file_injection(tmp_path):
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    section = data["rocm10"]["torch"]
    name = section["wheel_files"][0]
    injected = name + "\nhttps://unapproved.example/wheel.whl"
    section["wheel_files"][0] = injected
    section["wheel_sha256"][injected] = section["wheel_sha256"].pop(name)
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsafe artifact"):
        recipe.load_recipe(path, architecture="gfx1031")
