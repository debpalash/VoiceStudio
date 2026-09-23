"""Device-cache release reaches every accelerator an engine can select.

The engine sidecars resolve their device through ``torch.accelerator``, so an
Ascend NPU host synthesizes on ``npu`` and an Intel Arc host on ``xpu`` (see
``tests/test_moss_tts_v15.py``). The dubbing and generation recovery paths
open-coded the CUDA/MPS pair instead, which made the flush a silent no-op on
those hosts: the allocator kept the blocks it had just been asked to hand back,
and the next allocation failed with the memory still counted as "in use".

These tests pin the shared primitive to all four backends, and to the two
properties the call sites depend on — it never raises, and it never needs a
vendor backend that this torch build does not ship.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

BACKENDS = ("cuda", "mps", "xpu", "npu")


def _fake_torch(active: str, *, npu_present: bool = True):
    """A torch stub whose only available accelerator is ``active``."""
    backends = {
        name: SimpleNamespace(
            is_available=lambda name=name: active == name,
            empty_cache=Mock(),
        )
        for name in BACKENDS
    }
    torch = SimpleNamespace(
        cuda=backends["cuda"],
        mps=backends["mps"],
        xpu=backends["xpu"],
        backends=SimpleNamespace(mps=backends["mps"]),
    )
    if npu_present:
        torch.npu = backends["npu"]
    return torch, backends


@pytest.mark.parametrize("active", ["cpu", *BACKENDS])
def test_release_device_cache_flushes_the_active_accelerator(monkeypatch, active):
    from services import model_manager as mm

    torch, backends = _fake_torch(active)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()

    for name, backend in backends.items():
        assert backend.empty_cache.call_count == int(active == name), name


def test_release_device_cache_skips_a_backend_this_torch_does_not_ship(monkeypatch):
    """A torch build without torch_npu has no ``npu`` attribute to probe."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu", npu_present=False)
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()

    assert all(backend.empty_cache.call_count == 0 for backend in backends.values())


def test_release_device_cache_never_raises(monkeypatch):
    """Freeing memory is best-effort — a broken backend must not fail a request."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu")
    backends["npu"].empty_cache.side_effect = RuntimeError("driver went away")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()  # must not raise


def test_free_vram_still_collects_cublas_and_flushes(monkeypatch):
    """``free_vram()`` keeps its gc + cuBLAS clear and now shares the flush."""
    from services import model_manager as mm

    torch, backends = _fake_torch("cuda")
    cleared = Mock()
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    monkeypatch.setattr(mm, "_clear_cublas_workspaces", cleared)

    mm.free_vram()

    assert cleared.call_count == 1
    assert backends["cuda"].empty_cache.call_count == 1


def test_recovery_paths_use_the_shared_flush():
    """No recovery path re-introduces a backend-specific flush.

    Mechanical rule, checked in source: these modules release memory only
    through the one primitive that knows about every backend, so a new call
    site cannot quietly skip NPU/XPU by copying a CUDA/MPS pair again.
    """
    from pathlib import Path

    routers = Path(__file__).resolve().parent.parent / "backend" / "api" / "routers"
    for name in ("generation", "dub_generate", "dub_translate", "dub_core"):
        source = (routers / f"{name}.py").read_text(encoding="utf-8")
        assert "release_device_cache" in source, name
        assert "torch.cuda.empty_cache()" not in source, name
        assert "torch.mps.empty_cache()" not in source, name
