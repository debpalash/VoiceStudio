"""Uncertain inventory or another installed SDK family must never trigger repair."""
import importlib

import pytest


@pytest.mark.parametrize("arch,inventory,versions", [
    ("gfx1150", (), {}),
    ("gfx1150", ("",), {}),
    ("gfx1150", ("zzzz",), {}),
    ("gfx1150", ("73df",), {"torch": "2.13.0+rocm10.0.0", "rocm-sdk-core": "10.0.0"}),
    ("gfx1031", ("73df",), {"rocm-sdk-core": "7.2.1"}),
    ("gfx1031", ("73df",), {"rocm-sdk-core": "invalid"}),
    ("gfx1031", ("73df",), {"torch": "2.13.0+rocmunknown"}),
    ("gfx1031", ("73df",), {"rocm-sdk-core": "10.1.0"}),
    ("gfx1031", ("73df",), {"rocm-sdk-core": "10.0.0", "rocm": "7.2.1"}),
])
def test_unsafe_selection_stops_before_download_or_package_changes(monkeypatch, arch, inventory, versions):
    """Reject unsafe GPU or runtime selections before downloads or package mutations."""
    installer = importlib.import_module("core.windows_rocm.install")
    recipe = importlib.import_module("core.windows_rocm.recipe")
    gpu = importlib.import_module("core.gpu_inventory")
    failures = importlib.import_module("core.windows_rocm.failures")
    from importlib import metadata

    monkeypatch.setattr(installer.sys, "platform", "win32")
    selected = recipe.load_recipe(architecture=arch)
    monkeypatch.setattr(installer, "validate_target", lambda: None)
    monkeypatch.setattr(installer, "load_recipe", lambda: selected)
    monkeypatch.setattr(gpu, "detect_host_gpus", lambda **kw: tuple(
        gpu.HostGPU(vendor="amd", name="Radeon", pci_device_id=pci) for pci in inventory))

    def version(name):
        """Return simulated package versions or report the requested package as absent."""
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(metadata, "version", version)
    monkeypatch.setattr(installer, "verify_environment", lambda: (_ for _ in ()).throw(
        failures.VerificationFailure("package_version", "repairable package mismatch")))
    monkeypatch.setattr(installer, "download_archive", lambda *a: pytest.fail("unsafe download"))
    monkeypatch.setattr(installer, "run_install", lambda *a: pytest.fail("unsafe package changes"))
    with pytest.raises(failures.VerificationFailure, match="inventory|family|families|architecture"):
        installer.provision("uv")
