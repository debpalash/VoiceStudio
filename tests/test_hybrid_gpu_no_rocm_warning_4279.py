"""A usable CUDA build plus an AMD iGPU/dGPU is a CUDA host, never "ROCm not configured" (#4279)."""
import sys
import types

from api.routers.setup import wizard


def _torch(cuda_ok, cuda="12.8", hip=None):
    t = types.SimpleNamespace()
    t.version = types.SimpleNamespace(cuda=cuda, hip=hip)
    t.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_ok, get_device_name=lambda i=0: "NVIDIA GeForce RTX 3060",
    )
    t.backends = types.SimpleNamespace()
    return t


def _hybrid(monkeypatch, torch_mod):
    # nvidia-smi missing/failing, Radeon iGPU visible via rocm-smi / sysfs.
    monkeypatch.setattr(wizard, "_run_cmd", lambda args, timeout=2.0: (
        (0, "AMD Radeon Graphics\n") if args[0] == "rocm-smi" else (-1, "")))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    import core.device_caps as dc
    monkeypatch.setattr(dc, "arch_unsupported", lambda t: None)


def test_hybrid_cuda_host_is_cuda_not_amd(monkeypatch):
    _hybrid(monkeypatch, _torch(True))
    info = wizard._detect_gpu()
    assert info["vendor"] == "nvidia" and info["available"] and info["backend"] == "cuda"
    assert not any("ROCm" in n for n in info["notes"])


def test_amd_only_host_keeps_rocm_advice(monkeypatch):
    _hybrid(monkeypatch, _torch(False))
    info = wizard._detect_gpu()
    assert info["vendor"] == "amd" and not info["available"]
    assert "OMNIVOICE_TORCH_VARIANT=rocm" in " ".join(info["notes"])
