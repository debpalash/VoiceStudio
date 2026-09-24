"""Device-cache release reaches every accelerator an engine can select.

The engine sidecars resolve their device through ``torch.accelerator``, so an
Ascend NPU host synthesizes on ``npu`` and an Intel Arc host on ``xpu`` (see
``tests/test_moss_tts_v15.py``). The dubbing and generation recovery paths
open-coded the CUDA/MPS pair instead, which made the flush a silent no-op on
those hosts: the allocator kept the blocks it had just been asked to hand back,
and the next allocation failed with the memory still counted as "in use".

These tests pin the shared primitive to all four backends, to the accelerator
the engines actually resolve (``torch.accelerator``, and the probe chain on
builds too old to have it), and to the two properties the call sites depend on —
the direct recovery calls never raise, and ``free_vram()`` still surfaces a
failed flush to its unload callers.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

BACKENDS = ("cuda", "mps", "xpu", "npu")


def _fake_torch(active: str, *, npu_present: bool = True, engine_accelerator: str | None = None):
    """A torch stub whose only available accelerator is ``active``.

    ``engine_accelerator`` mirrors what ``torch.accelerator.current_accelerator``
    answers — i.e. the device the engine sidecars would synthesize on. Left
    ``None`` the stub has no ``torch.accelerator`` at all, like a pre-2.6 build.
    """
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
    if engine_accelerator is not None:
        accel = SimpleNamespace(type=engine_accelerator)
        torch.accelerator = SimpleNamespace(current_accelerator=lambda **_kw: accel)
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


def test_release_device_cache_follows_the_engines_accelerator(monkeypatch):
    """Hybrid host: CUDA probes as available, but the engines run on npu.

    ``torch.accelerator.current_accelerator`` is the resolution the engine
    sidecars use (``engines/moss_tts_v15``), so the flush has to follow it
    instead of stopping at the first backend that merely probes as available —
    otherwise the active allocator keeps the blocks and the recovery retry hits
    the same OOM.
    """
    from services import model_manager as mm

    torch, backends = _fake_torch("cuda", engine_accelerator="npu")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()

    assert backends["npu"].empty_cache.call_count == 1
    assert backends["cuda"].empty_cache.call_count == 0


def test_release_device_cache_uses_explicit_caller_device(monkeypatch):
    """NLLB may run on CUDA while the default engine accelerator is NPU."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu", engine_accelerator="npu")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    mm.release_device_cache(device="cuda:0")
    assert backends["cuda"].empty_cache.call_count == 1
    assert backends["npu"].empty_cache.call_count == 0
    mm.release_device_cache(device="cpu")
    assert backends["npu"].empty_cache.call_count == 0


def test_release_device_cache_ignores_a_cpu_answer(monkeypatch):
    """A cpu answer must not end the search — probe the shipped backends."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu", engine_accelerator="cpu")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()

    assert backends["npu"].empty_cache.call_count == 1


def test_release_device_cache_falls_back_without_torch_accelerator(monkeypatch):
    """A pre-2.6 build has no ``torch.accelerator`` — the probe chain still runs."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu")  # no accelerator attribute at all
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()

    assert backends["npu"].empty_cache.call_count == 1


def test_release_device_cache_never_raises(monkeypatch):
    """Freeing memory is best-effort — a broken backend must not fail a request."""
    from services import model_manager as mm

    torch, backends = _fake_torch("npu")
    backends["npu"].empty_cache.side_effect = RuntimeError("driver went away")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)

    mm.release_device_cache()  # must not raise


def test_free_vram_still_propagates_a_flush_failure(monkeypatch):
    """``free_vram()`` keeps its original contract: unload callers report a
    failed flush (``model_lifecycle`` records ``success: False, reason: …``)."""
    from services import model_manager as mm

    torch, backends = _fake_torch("cuda")
    backends["cuda"].empty_cache.side_effect = RuntimeError("driver went away")
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    monkeypatch.setattr(mm, "_clear_cublas_workspaces", lambda torch: None)

    with pytest.raises(RuntimeError):
        mm.free_vram()


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
