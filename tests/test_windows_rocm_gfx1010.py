"""gfx1010 (RDNA 1 / RX 5700 class) on the Windows ROCm 10 overlay.

What this suite pins down, and why each case exists:

* the PCI ids that select the gfx1010 overlay, taken from AMD's rocminfo
  ``rocm_agent_enumerator`` rather than guessed from a product name;
* that the gfx1010 device wheels are pinned by content like every other
  artifact, and that a gfx1030 id does not leak a gfx1030 wheel into it;
* that the ROCm 10 overlay reports the absence of a verified HIP CTranslate2
  instead of inheriting the 7.2.1 one. This is the load-bearing difference:
  the 7.2.1 HIP binary *loads* on ROCm 10 and reports float16, but cannot
  launch a kernel (verified on an RX 5700: ``hipErrorInvalidImage``), so a
  recipe that assumed CT2 worked would advertise GPU ASR and then fail it.
"""
import json
import re

import pytest

from core.windows_rocm import install, recipe


GFX1010_PCI_IDS = ("7310", "7312", "7318", "7319", "731a", "731b", "731e", "731f")


def _host_gpu(pci_device_id):
    from core import gpu_inventory

    return gpu_inventory.HostGPU(vendor="amd", name="Radeon", pci_device_id=pci_device_id)


def test_gfx1010_selects_the_native_overlay():
    selected = recipe.load_recipe(architecture="gfx1010")
    assert selected["gpu_architectures"] == ["gfx1010"]
    assert selected["torch"]["hip_prefix"] == "7.15."
    assert "amd-torch-device-gfx1010==2.13.0+rocm10.0.0" in selected["runtime"]["packages"]
    assert "rocm-sdk-device-gfx1010==10.0.0" in selected["runtime"]["packages"]
    # A gfx1010 machine must not be handed another architecture's kernels.
    assert not [p for p in selected["runtime"]["packages"] if re.search(r"gfx10(3|1[12])", p)]


@pytest.mark.parametrize("pci", GFX1010_PCI_IDS)
def test_each_navi10_device_id_selects_gfx1010(monkeypatch, pci):
    from core import gpu_inventory

    monkeypatch.setattr(recipe.sys, "platform", "win32")
    monkeypatch.setattr(gpu_inventory, "detect_host_gpus", lambda: (_host_gpu(pci),))
    assert recipe.load_recipe()["gpu_architectures"] == ["gfx1010"]


def test_navi14_and_navi12_ids_fall_back_to_the_stock_recipe(monkeypatch):
    # gfx1011/gfx1012 are RDNA 1 siblings with no hardware validation here, so
    # they must keep the 7.2.1 stock recipe (and therefore CPU routing) rather
    # than silently borrow gfx1010 kernels.
    from core import gpu_inventory

    for pci in ("7360", "7340"):
        monkeypatch.setattr(recipe.sys, "platform", "win32")
        monkeypatch.setattr(gpu_inventory, "detect_host_gpus", lambda p=pci: (_host_gpu(p),))
        assert recipe.load_recipe()["torch"]["hip_prefix"] == "7.2."


def test_gfx1010_and_gfx1030_together_fail_closed(monkeypatch):
    from core import gpu_inventory

    monkeypatch.setattr(recipe.sys, "platform", "win32")
    monkeypatch.setattr(
        gpu_inventory,
        "detect_host_gpus",
        lambda: (_host_gpu("731f"), _host_gpu("73bf")),
    )
    with pytest.raises(RuntimeError, match="Multiple AMD"):
        recipe.load_recipe()


def test_gfx1010_artifacts_are_all_content_pinned():
    selected = recipe.load_recipe(architecture="gfx1010")
    lines = install.hashed_requirements(selected).splitlines()
    assert len(lines) == 10
    assert sum("device-gfx1010/" in line for line in lines) == 3
    assert all(re.search(r" --hash=sha256:[0-9a-f]{64}$", line) for line in lines)


def test_gfx1010_reuses_the_shared_rocm10_runtime_hashes():
    # The SDK/runtime wheels are architecture independent, so gfx1010 must pin
    # exactly the same bytes as the other ROCm 10 devices rather than a copy
    # that could drift.
    gfx1010 = recipe.load_recipe(architecture="gfx1010")
    gfx1030 = recipe.load_recipe(architecture="gfx1030")
    for name in ("rocm/rocm-10.0.0.tar.gz",
                 "rocm-sdk-core/rocm_sdk_core-10.0.0-py3-none-win_amd64.whl",
                 "rocm-sdk-libraries/rocm_sdk_libraries-10.0.0-py3-none-win_amd64.whl"):
        assert gfx1010["runtime"]["wheel_sha256"][name] == gfx1030["runtime"]["wheel_sha256"][name]


def test_rocm10_overlay_reports_no_verified_ct2():
    selected = recipe.load_recipe(architecture="gfx1010")
    assert recipe.ct2_supported(selected) is False
    assert selected["ctranslate2"]["supported"] is False
    assert selected["ctranslate2"]["reason"]
    # The CT2 wheel hashes must not survive into a recipe that ships no CT2.
    assert "ctranslate2" not in recipe.expected_versions(selected)


def test_stock_recipe_keeps_its_verified_ct2():
    stock = recipe.load_recipe(architecture=None)
    assert recipe.ct2_supported(stock) is True
    assert recipe.expected_versions(stock)["ctranslate2"] == stock["ctranslate2"]["version"]


def test_a_probe_report_must_match_the_recipes_ct2_claim(monkeypatch):
    from core.windows_rocm import verify

    # A ROCm 10 report that still claims a verified CT2 contradicts its recipe.
    report = {
        "ready": True, "scope": "model-free", "python": verify.sys.executable,
        "architecture": "win-amd64", "abi": "cp312", "hip": "7.15.26333",
        "packages": recipe.expected_versions(recipe.load_recipe(architecture="gfx1010")),
        "torch_gpu": True, "kernel_result": 4.0, "gpu_architecture": "gfx1010",
        "ct2_supported": True, "ct2_gpu_count": 1,
        "ct2_compute_types": ["float16"], "ct2_binary_sha256": "pinned",
    }
    with pytest.raises(RuntimeError):
        verify.validate_report(report, recipe.load_recipe(architecture="gfx1010"))


def test_a_gfx1010_report_without_ct2_is_ready(monkeypatch):
    from core.windows_rocm import verify

    report = {
        "ready": True, "scope": "model-free", "python": verify.sys.executable,
        "architecture": "win-amd64", "abi": "cp312", "hip": "7.15.26333",
        "packages": recipe.expected_versions(recipe.load_recipe(architecture="gfx1010")),
        "torch_gpu": True, "kernel_result": 4.0, "gpu_architecture": "gfx1010",
        "ct2_supported": False, "ct2_gpu_count": 0,
        "ct2_compute_types": [], "ct2_binary_sha256": None,
    }
    assert verify.validate_report(report, recipe.load_recipe(architecture="gfx1010"))["ready"] is True


def test_installer_skips_the_ct2_archive_but_restores_the_locked_cpu_build(monkeypatch):
    """The overlay must leave a working CPU CTranslate2 behind.

    ``windowsRocmSyncArgs()`` excludes ``ctranslate2`` from the frozen sync, so
    skipping the HIP archive without a replacement would leave a fresh ROCm 10
    environment with no CTranslate2 at all: ``faster_whisper`` cannot import, the
    app's own import check fails, and even the CPU fallback disappears.
    """
    import importlib

    installer = importlib.import_module("core.windows_rocm.install")
    failures = importlib.import_module("core.windows_rocm.failures")

    selected = recipe.load_recipe(architecture="gfx1010")
    monkeypatch.setattr(installer, "validate_target", lambda: None)
    monkeypatch.setattr(installer, "load_recipe", lambda: selected)

    def verify():
        raise failures.VerificationFailure("package_missing", "missing")

    monkeypatch.setattr(installer, "verify_environment", verify)
    monkeypatch.setattr(
        installer, "download_archive",
        lambda *a: pytest.fail("ROCm 10 must not download the 7.2.1 CT2 archive"),
    )
    monkeypatch.setattr(installer, "checked_wheel", lambda *a: pytest.fail("unexpected CT2 wheel"))

    commands = []
    monkeypatch.setattr(installer, "run_install", lambda command: commands.append(command))
    with pytest.raises(failures.VerificationFailure):
        installer.provision("uv-custom")

    # Two passes: the overlay itself, then the locked CPU CTranslate2.
    assert len(commands) == 2
    overlay, cpu_ct2 = commands
    assert "-r" in overlay
    # The CPU build must come from the lock/index, never from a pinned URL, and
    # it must be reinstalled so a stale HIP binary is actually replaced.
    assert "ctranslate2" in cpu_ct2
    assert not any(str(arg).startswith("http") for arg in cpu_ct2)
    assert "--reinstall-package" in cpu_ct2 and "ctranslate2" in cpu_ct2


def test_a_recipe_with_a_hip_ct2_never_adds_the_cpu_pass(monkeypatch):
    """The 7.2.1 recipe keeps its pinned HIP wheel and must not also install PyPI CT2."""
    import importlib

    installer = importlib.import_module("core.windows_rocm.install")
    failures = importlib.import_module("core.windows_rocm.failures")

    selected = {**recipe.load_recipe(architecture="gfx1010")}
    selected["ctranslate2"] = {
        "version": "4.8.2",
        "archive_url": "https://github.com/OpenNMT/CTranslate2/releases/download/x.zip",
        "archive_size": 1,
        "archive_sha256": "0" * 64,
        "wheel_member": "w/ctranslate2.whl",
        "wheel_sha256": "0" * 64,
        "binary_sha256": "0" * 64,
    }
    monkeypatch.setattr(installer, "validate_target", lambda: None)
    monkeypatch.setattr(installer, "load_recipe", lambda: selected)

    def verify():
        raise failures.VerificationFailure("package_missing", "missing")

    monkeypatch.setattr(installer, "verify_environment", verify)
    monkeypatch.setattr(installer, "download_archive", lambda *a: None)
    monkeypatch.setattr(installer, "checked_wheel", lambda *a: "wheel.whl")
    commands = []
    monkeypatch.setattr(installer, "run_install", lambda command: commands.append(command))
    with pytest.raises(failures.VerificationFailure):
        installer.provision("uv-custom")

    assert len(commands) == 2
    assert commands[1][-1] == "wheel.whl"  # the verified HIP wheel, not a bare name



def test_gfx1010_window_opens_no_hsa_override(monkeypatch):
    from core.windows_rocm import probe

    selected = recipe.load_recipe(architecture="gfx1010")
    monkeypatch.setattr(probe, "validate_target", lambda: None)
    monkeypatch.setattr(probe, "load_recipe", lambda: selected)
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "10.3.0")
    monkeypatch.setattr(probe.metadata, "version", lambda name: pytest.fail("unexpected package probe"))
    with pytest.raises(RuntimeError, match="Remove HSA_OVERRIDE_GFX_VERSION"):
        probe.probe()


def test_gfx1010_urls_stay_on_the_amd_index():
    selected = recipe.load_recipe(architecture="gfx1010")
    assert selected["torch"]["find_links"] == "https://stable.repo.amd.com/rocm/whl-next/"
    for line in install.hashed_requirements(selected).splitlines():
        url, digest = line.split(" --hash=sha256:")
        assert url.startswith("https://stable.repo.amd.com/rocm/whl-next/")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_the_recipe_names_gfx1010_and_its_validated_siblings():
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    assert set(data["rocm10"]["devices"]) == {"gfx1010", "gfx1030", "gfx1031", "gfx1032"}
    # The stock (7.2.1) matrix is untouched by this overlay.
    assert "gfx1010" not in data["gpu_architectures"]


def test_availability_status_defers_to_the_cpu_path_on_the_rocm10_overlay(monkeypatch):
    """A recipe without HIP CT2 must not hide the CPU fallback.

    ``availability_status()`` answers the engine picker. Returning a failure here
    would mark both faster-whisper backends unavailable even though a perfectly
    good CPU CTranslate2 is installed, leaving the user no way to pick them
    without disabling GPU use for the whole app.
    """
    from core.windows_rocm import ct2

    monkeypatch.setattr(ct2, "windows_hip_installed", lambda: True)
    monkeypatch.setattr(ct2, "hip_ct2_available", lambda: pytest.fail("must not probe HIP CT2"))
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: type("Caps", (), {"family": "rocm"})(),
    )
    monkeypatch.setattr(ct2, "recipe_has_hip_ct2", lambda: False)
    assert ct2.availability_status() is None


def test_availability_status_still_reports_a_broken_hip_ct2(monkeypatch):
    """On a recipe that DOES pin HIP CT2, a failed verification stays a failure."""
    from core.windows_rocm import ct2

    monkeypatch.setattr(ct2, "windows_hip_installed", lambda: True)
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: type("Caps", (), {"family": "rocm"})(),
    )
    monkeypatch.setattr(ct2, "recipe_has_hip_ct2", lambda: True)
    monkeypatch.setattr(ct2.util, "find_spec", lambda name: object())
    monkeypatch.setattr(ct2, "hip_ct2_available", lambda: False)
    status = ct2.availability_status()
    assert status is not None
    assert status[0] is False
    assert "verification failed" in status[1].lower()


def test_gpu_device_available_stays_false_on_the_rocm10_overlay(monkeypatch):
    """The ASR worker must pick cpu for CT2 on this overlay, never a fake cuda."""
    from core.windows_rocm import ct2

    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: type("Caps", (), {"family": "rocm"})(),
    )
    monkeypatch.setattr(ct2, "hip_ct2_available", lambda: False)
    assert ct2.gpu_device_available() is False
