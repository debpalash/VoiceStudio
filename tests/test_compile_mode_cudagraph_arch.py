from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


class CudaProbe:
    def __init__(self, *, compute=(8, 0), present=True, error=None):
        self.compute = compute
        self.present = present
        self.error = error

    def is_available(self):
        return self.present

    def get_device_capability(self, _device=0):
        if self.error:
            raise self.error
        return self.compute

    def get_device_name(self, _device=0):
        return "Fixture adapter"


@pytest.fixture
def compile_policy(monkeypatch):
    module = importlib.import_module("services.model_manager")
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDAGRAPH", raising=False)

    def resolve(probe, requested=None):
        monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=probe))
        if requested is None:
            return module._resolved_compile_mode()
        return module._resolved_compile_mode(requested)

    return module, resolve


@pytest.mark.parametrize("compute", [(6, 1), (7, 5), (7, 9)])
def test_pre_ampere_adapters_drop_graph_capture(compile_policy, compute):
    module, resolve = compile_policy
    chosen = resolve(CudaProbe(compute=compute))
    assert chosen == "default"
    assert chosen not in module._CUDAGRAPH_COMPILE_MODES


@pytest.mark.parametrize("compute", [(8, 0), (8, 9), (9, 0), (12, 1)])
def test_supported_adapters_retain_requested_mode(compile_policy, compute):
    module, resolve = compile_policy
    assert resolve(CudaProbe(compute=compute)) == module._TORCH_COMPILE_MODE


def test_modes_without_cuda_graphs_bypass_hardware_detection(compile_policy):
    _module, resolve = compile_policy
    assert resolve(CudaProbe(error=AssertionError("must not probe")), "default") == "default"


def test_explicit_override_wins_on_an_older_adapter(compile_policy, monkeypatch):
    module, resolve = compile_policy
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDAGRAPH", " YES ")
    assert resolve(CudaProbe(compute=(5, 2))) == module._TORCH_COMPILE_MODE


@pytest.mark.parametrize(
    "probe",
    [CudaProbe(present=False), CudaProbe(error=RuntimeError("driver unavailable"))],
)
def test_indeterminate_hardware_preserves_existing_mode(compile_policy, probe):
    module, resolve = compile_policy
    assert resolve(probe) == module._TORCH_COMPILE_MODE


def test_absent_torch_module_preserves_existing_mode(compile_policy, monkeypatch):
    module, _resolve = compile_policy
    monkeypatch.setitem(sys.modules, "torch", None)
    assert module._resolved_compile_mode() == module._TORCH_COMPILE_MODE
