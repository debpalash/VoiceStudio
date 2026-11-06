"""Pure Torch ASR may declare verified Windows HIP without depending on CT2."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture(params=[False, True], ids=["windows-source", "linux-parity-integration"])
def hip(monkeypatch, request):
    """Provide pinned RDNA 2 Torch capabilities while forbidding CT2 capability probes."""
    ab = importlib.import_module("services.asr_backend")
    if request.param:
        monkeypatch.setattr(ab.PyTorchWhisperBackend, "gpu_compat", ("cuda", "rocm", "mps", "cpu"))
    recipe = importlib.import_module("core.windows_rocm.recipe")
    caps = importlib.import_module("core.device_caps")
    ct2 = importlib.import_module("core.windows_rocm.ct2")
    selected = recipe.load_recipe(architecture="gfx1031")
    pins = recipe.expected_versions(selected)
    pins.pop("ctranslate2")
    monkeypatch.setattr(recipe.sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(recipe, "load_recipe", lambda: selected)
    monkeypatch.setattr(recipe, "validate_target", lambda: None)
    monkeypatch.setattr(caps, "detect_host_caps", lambda: SimpleNamespace(
        family="rocm", probe_ok=True, notes=()))
    def version(name):
        """Return pinned Torch package versions and reject any CT2 metadata query."""
        if name == "ctranslate2":
            pytest.fail("Pure Torch ASR capability checked CT2")
        if name not in pins:
            raise ct2.metadata.PackageNotFoundError(name)
        return pins[name]
    monkeypatch.setattr(ct2.metadata, "version", version)
    monkeypatch.setattr(ct2, "hip_ct2_available", lambda: pytest.fail("Pure Torch ASR probed CT2"))
    return ab.PyTorchWhisperBackend, recipe, caps, pins


def test_exact_opt_in_windows_torch_recipe_advertises_rocm_independent_of_ct2(hip):
    backend, _recipe, _caps, _pins = hip
    assert backend.runtime_gpu_compat() == ("cuda", "rocm", "mps", "cpu")


@pytest.mark.parametrize("package", ["torch", "rocm-sdk-core", "amd-torch-device-gfx1031"])
def test_mismatched_native_packages_do_not_advertise_verified_rocm(hip, package):
    backend, _recipe, _caps, pins = hip
    pins[package] = "wrong-version"
    assert "rocm" not in backend.runtime_gpu_compat()


@pytest.mark.parametrize("family,ok,notes", [
    ("cpu", True, ()), ("rocm", False, ()),
    ("rocm", True, ("GPU may fail at kernel launch",)),
])
def test_cpu_override_or_unsafe_host_never_claims_verified_gpu(hip, monkeypatch, family, ok, notes):
    backend, _recipe, caps, _pins = hip
    monkeypatch.setattr(caps, "detect_host_caps", lambda: SimpleNamespace(
        family=family, probe_ok=ok, notes=notes))
    assert "rocm" not in backend.runtime_gpu_compat()


@pytest.mark.parametrize("platform,opt_in", [("linux", "rocm"), ("darwin", "rocm"), ("win32", "")])
def test_default_and_other_platform_compatibility_stays_unchanged(hip, monkeypatch, platform, opt_in):
    backend, recipe, caps, _pins = hip
    monkeypatch.setattr(recipe.sys, "platform", platform)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", opt_in)
    monkeypatch.setattr(caps, "detect_host_caps", lambda: pytest.fail("Default path probed HIP"))
    expected = backend.gpu_compat if platform != "win32" else ("cuda", "mps", "cpu")
    assert backend.runtime_gpu_compat() == expected
