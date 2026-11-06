"""CT2 discovery must not certify a binary whose GPU kernels cannot run."""
import importlib
import sys
from types import SimpleNamespace

import pytest


def test_rdna2_selects_the_verified_expanded_target_binary():
    recipe = importlib.import_module("core.windows_rocm.recipe")
    rdna2 = recipe.load_recipe(architecture="gfx1031")
    base = recipe.load_recipe(architecture="gfx1150")
    assert recipe.expected_versions(rdna2)["ctranslate2"] == "4.8.4"
    assert rdna2["ctranslate2"]["binary_sha256"] == "dd4092e29b5dcf4337c2f6e8d4f9aece377b19c46ff11a8d7ec1a76608b150fa"
    assert base["ctranslate2"]["version"] == "4.8.2"


def test_discovery_only_report_is_rejected():
    recipe = importlib.import_module("core.windows_rocm.recipe")
    verify = importlib.import_module("core.windows_rocm.verify")
    selected = recipe.load_recipe(architecture="gfx1031")
    report = dict(ready=True, scope="model-free", python=sys.executable,
                  architecture="win-amd64", abi="cp312", hip="7.15.26333",
                  packages=recipe.expected_versions(selected), torch_gpu=True,
                  kernel_result=4.0, gpu_architecture="gfx1031", ct2_gpu_count=1,
                  ct2_compute_types=["float16"],
                  ct2_binary_sha256=selected["ctranslate2"]["binary_sha256"])
    with pytest.raises(RuntimeError, match="validation"):
        verify.validate_report(report, selected)


class View:
    def __init__(self, values, device="cpu", broken=False):
        self.values, self.device, self.broken = values, device, broken

    def to_device(self, device):
        return View(self.values, device, self.broken)

    def to(self, dtype):
        """Simulate a successful cast or the native invalid-image kernel failure."""
        if self.broken:
            raise RuntimeError("parallel_for failed: hipErrorInvalidImage")
        return self

    def __array__(self, dtype=None, copy=None):
        import numpy as np
        return np.asarray(self.values, dtype=dtype)


@pytest.mark.parametrize("broken", [False, True])
def test_native_cast_requires_an_executable_gpu_kernel(broken):
    """Require a successful native GPU cast rather than accepting allocation alone."""
    module = importlib.import_module("core.windows_rocm.ct2_kernel")
    ct2 = SimpleNamespace(
        StorageView=SimpleNamespace(from_array=lambda a: View(a, broken=broken)),
        Device=SimpleNamespace(cuda="cuda", cpu="cpu"),
        DataType=SimpleNamespace(float16="float16", float32="float32"))
    if broken:
        with pytest.raises(RuntimeError, match="hipErrorInvalidImage"):
            module.verify_ct2_kernel(ct2)
    else:
        assert module.verify_ct2_kernel(ct2) == [1.25, -2.5, 0.0, 8.0]


def test_cpu_fallback_cannot_pass_the_gpu_kernel_check():
    module = importlib.import_module("core.windows_rocm.ct2_kernel")
    ct2 = SimpleNamespace(
        StorageView=SimpleNamespace(from_array=lambda a: View(a)),
        Device=SimpleNamespace(cuda="cpu", cpu="cpu"),
        DataType=SimpleNamespace(float16="float16", float32="float32"))
    with pytest.raises(RuntimeError, match="GPU"):
        module.verify_ct2_kernel(ct2)
