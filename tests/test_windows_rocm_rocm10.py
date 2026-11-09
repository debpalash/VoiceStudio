"""Windows ROCm 10 (RDNA 1 and 2) uses pinned native kernels and safe precision."""

import importlib
import json
import re
import types
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def application_modules():
    """Bind canonical modules for each test after any sys.modules replacement."""
    global device_caps, install, recipe
    device_caps = importlib.import_module("core.device_caps")
    install = importlib.import_module("core.windows_rocm.install")
    recipe = importlib.import_module("core.windows_rocm.recipe")


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


def _recipe_with_ct2(tmp_path, **fields):
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    data["rocm10"]["ctranslate2"].update(fields)
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


_PINS = dict(archive_size=1, archive_sha256="a" * 64, wheel_sha256="b" * 64, binary_sha256="c" * 64)
# A synthetic recipe whose in-repo build is not published yet: the real recipe is
# pinned, but a future rebuild (new tag) passes through this state again.
_UNPINNED = dict.fromkeys(_PINS)
# The build published by .github/workflows/ct2-rocm-windows.yml and pinned in the recipe.
_PUBLISHED = dict(
    archive_url="https://github.com/debpalash/VoiceStudio/releases/download/ct2-rocm-4.8.4-78b1e17/rocm-python-wheels-Windows.zip",
    archive_size=21865556,
    archive_sha256="ef69c6d68628996faa2eec16007cdba1ec2f670ef914ddee883663e18bf7d8be",
    wheel_member="ctranslate2-4.8.4-cp312-cp312-win_amd64.whl",
    wheel_sha256="87b06d60c994afbce73c360f8117a5905884e65c06935c864c074261d3016625",
    binary_sha256="43d4ee10347f78b1be91c1cc715d698bc6d67c1b0a0f6d7f25c206d83e43013c",
)
_RDNA2 = ["gfx1030", "gfx1031", "gfx1032"]


@pytest.mark.parametrize("gfx", _RDNA2)
def test_rdna2_selects_the_pinned_in_repo_hip_ct2(gfx):
    """The real recipe routes RDNA 2 to the published HIP build with its exact pins."""
    selected = recipe.load_recipe(architecture=gfx)
    assert recipe.ct2_supported(selected)
    ct2 = selected["ctranslate2"]
    assert {key: ct2[key] for key in _PUBLISHED} == _PUBLISHED
    assert ct2["version"] == "4.8.4"
    assert "gpu_architectures" not in ct2
    assert recipe.expected_versions(selected)["ctranslate2"] == "4.8.4"


@pytest.mark.parametrize("gfx", _RDNA2)
def test_rdna2_keeps_cpu_ct2_while_the_in_repo_build_is_unpinned(tmp_path, gfx):
    """An unpublished build (pins unset) must route like gfx1010, never download."""
    selected = recipe.load_recipe(_recipe_with_ct2(tmp_path, **_UNPINNED), architecture=gfx)
    assert not recipe.ct2_supported(selected)
    assert "ctranslate2" not in recipe.expected_versions(selected)
    assert "not published" in selected["ctranslate2"]["reason"]


@pytest.mark.parametrize("gfx", _RDNA2)
def test_rdna2_devices_receive_the_overlay_hip_ct2_once_pinned(tmp_path, gfx):
    selected = recipe.load_recipe(_recipe_with_ct2(tmp_path, **_PINS), architecture=gfx)
    assert recipe.ct2_supported(selected)
    assert selected["ctranslate2"]["version"] == "4.8.4"
    assert "gpu_architectures" not in selected["ctranslate2"]
    assert recipe.expected_versions(selected)["ctranslate2"] == "4.8.4"


@pytest.mark.parametrize("unset", list(_PINS))
def test_partially_pinned_ct2_build_is_rejected(tmp_path, unset):
    path = _recipe_with_ct2(tmp_path, **{unset: None})
    with pytest.raises(RuntimeError, match="CT2 recipe"):
        recipe.load_recipe(path, architecture="gfx1031")


def test_partially_pinned_unpublished_ct2_build_is_rejected(tmp_path):
    path = _recipe_with_ct2(tmp_path, **{**_UNPINNED, "archive_sha256": "a" * 64})
    with pytest.raises(RuntimeError, match="CT2 recipe"):
        recipe.load_recipe(path, architecture="gfx1031")


def test_ct2_build_must_come_from_this_repository(tmp_path):
    fork = "https://github.com/dutch2005/VoiceStudio/releases/download/ct2-rocm-4.8.4-9a7a074/rocm-python-wheels-Windows.zip"
    with pytest.raises(RuntimeError, match="unapproved source"):
        recipe.load_recipe(_recipe_with_ct2(tmp_path, archive_url=fork, **_PINS), architecture="gfx1031")


def _repair(monkeypatch, selected, downloads):
    """Run provision() for ``selected`` with a repairable failure and no real I/O."""
    failures = importlib.import_module("core.windows_rocm.failures")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "load_recipe", lambda: selected)
    monkeypatch.setattr(install, "validate_repair_selection", lambda recipe: None)

    def verify():
        raise failures.VerificationFailure("package_missing", "missing")

    monkeypatch.setattr(install, "verify_environment", verify)
    monkeypatch.setattr(install, "download_archive", lambda *args: downloads.append(args))
    monkeypatch.setattr(install, "checked_wheel", lambda archive, folder, ct2: Path(folder) / ct2["wheel_member"])
    monkeypatch.setattr(install, "locked_ct2_requirement", lambda uv: "ctranslate2==4.4.0 --hash=sha256:" + "a" * 64)
    commands = []
    monkeypatch.setattr(install, "run_install", lambda command: commands.append(command))
    with pytest.raises(failures.VerificationFailure):
        install.provision("uv-custom")
    return commands


def test_unpinned_rdna2_installs_the_locked_cpu_ct2_without_downloading(monkeypatch, tmp_path):
    selected = recipe.load_recipe(_recipe_with_ct2(tmp_path, **_UNPINNED), architecture="gfx1031")
    downloads = []
    commands = _repair(monkeypatch, selected, downloads)
    assert downloads == []
    assert len(commands) == 2
    assert str(commands[1][-1]).endswith("cpu-ctranslate2.txt")


@pytest.mark.parametrize("gfx", _RDNA2)
def test_pinned_rdna2_installs_the_in_repo_hip_ct2(monkeypatch, gfx):
    selected = recipe.load_recipe(architecture=gfx)
    downloads = []
    commands = _repair(monkeypatch, selected, downloads)
    assert [(url, size) for url, _, size in downloads] == [(_PUBLISHED["archive_url"], _PUBLISHED["archive_size"])]
    assert len(commands) == 2
    assert any(str(part).endswith(_PUBLISHED["wheel_member"]) for part in commands[1])
    assert not any(str(part).endswith("cpu-ctranslate2.txt") for command in commands for part in command)


def test_gfx1010_installs_the_locked_cpu_ct2_without_downloading(monkeypatch):
    """The pinned RDNA 2 build has no gfx1010 kernels and is never fetched for it."""
    downloads = []
    commands = _repair(monkeypatch, recipe.load_recipe(architecture="gfx1010"), downloads)
    assert downloads == []
    assert str(commands[1][-1]).endswith("cpu-ctranslate2.txt")


def test_gfx1010_shares_the_overlay_but_not_its_hip_ct2():
    """The RDNA 2 CT2 build has no gfx1010 kernels; RDNA 1 keeps CT2 on the CPU."""
    selected = recipe.load_recipe(architecture="gfx1010")
    assert selected["torch"] == recipe.load_recipe(architecture="gfx1031")["torch"]
    assert not recipe.ct2_supported(selected)
    assert "ctranslate2" not in recipe.expected_versions(selected)


def test_a_ct2_build_for_an_undeclared_device_is_rejected(tmp_path):
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    data["rocm10"]["ctranslate2"]["gpu_architectures"].append("gfx1012")
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="undeclared device"):
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
