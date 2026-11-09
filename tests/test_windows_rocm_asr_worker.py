"""Device admission and unaffected defaults without native execution."""
import builtins
import importlib
import sys

import pytest


@pytest.mark.parametrize("parent,override,verified,want", [
    ("cpu", "auto", True, "cpu"),
    ("cuda", "cpu", True, "cpu"),
    ("cuda", "auto", False, "cpu"),
    ("cuda", "rocm", True, "cuda"),
    ("cuda", "not-a-device", True, "cuda"),
    ("cuda", "mps", True, "cuda"),  # unavailable overrides retain auto on HIP
])
def test_worker_never_imports_torch_and_requires_its_own_gpu_gate(
        monkeypatch, parent, override, verified, want):
    """Require child GPU verification while honoring CPU overrides without importing Torch."""
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    policy = importlib.import_module("core.windows_rocm.isolation_policy")
    ct2 = importlib.import_module("core.windows_rocm.ct2")
    monkeypatch.setattr(policy.sys, "platform", "win32")
    monkeypatch.setattr(policy.metadata, "version", lambda name: "2.13.0+rocm10.0.0")
    monkeypatch.setenv("OMNIVOICE_DEVICE", override)
    def gate():
        """Reject GPU verification for CPU selections and return the simulated GPU result."""
        if parent == "cpu" or override == "cpu":
            pytest.fail("CPU selected but GPU verification ran")
        return verified
    monkeypatch.setattr(ct2, "hip_ct2_available", gate)
    original = builtins.__import__
    def no_torch(name, *args, **kwargs):
        """Reject Torch imports during device selection and delegate unrelated imports."""
        if name == "torch" or name.startswith("torch."):
            pytest.fail("inference device selection imported Torch")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_torch)
    assert isolation.worker_device(parent) == want


@pytest.mark.parametrize("platform,version", [
    ("win32", "2.9.1+rocm7.2.1"), ("win32", "2.8.0+cu128"),
    ("win32", "2.8.0+cpu"), ("linux", "2.13.0+rocm10.0.0"),
    ("darwin", "2.8.0"),
])
def test_other_runtimes_retain_in_process_execution(monkeypatch, platform, version):
    isolation = importlib.import_module("core.windows_rocm.asr_isolation")
    policy = importlib.import_module("core.windows_rocm.isolation_policy")
    ab = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(policy.metadata, "version", lambda name: version)
    assert not isolation.isolation_required()
    assert isolation.worker_device() is None
    assert isolation.availability_status() is None
    assert not ab.FasterWhisperBackend.runtime_runs_out_of_process()
