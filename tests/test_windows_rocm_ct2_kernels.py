"""CT2 discovery must not certify a binary whose GPU kernels cannot run."""
import importlib
import json
import sys
from types import SimpleNamespace

import pytest


def _pinned_rdna2(tmp_path, recipe):
    """The RDNA 2 selection once the in-repo build's pins are filled in."""
    data = json.loads(recipe.RECIPE_PATH.read_text(encoding="utf-8"))
    data["rocm10"]["ctranslate2"].update(
        archive_size=1, archive_sha256="a" * 64, wheel_sha256="b" * 64, binary_sha256="c" * 64)
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return recipe.load_recipe(path, architecture="gfx1031")


def test_rdna2_selects_the_expanded_target_binary_once_pinned(tmp_path):
    recipe = importlib.import_module("core.windows_rocm.recipe")
    rdna2 = _pinned_rdna2(tmp_path, recipe)
    base = recipe.load_recipe(architecture="gfx1150")
    assert recipe.expected_versions(rdna2)["ctranslate2"] == "4.8.4"
    assert rdna2["ctranslate2"]["binary_sha256"] == "c" * 64
    assert base["ctranslate2"]["version"] == "4.8.2"


def test_discovery_only_report_is_rejected(tmp_path):
    recipe = importlib.import_module("core.windows_rocm.recipe")
    verify = importlib.import_module("core.windows_rocm.verify")
    selected = _pinned_rdna2(tmp_path, recipe)
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
