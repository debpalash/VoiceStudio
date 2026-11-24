"""Integrated Intel Arc XPUs are unified-memory devices, not dedicated-VRAM
devices.

A single Intel Arc XPU (e.g. Lunar Lake 130V/140V) advertises a tiny
``local_mem_size`` in the kilobytes while allocating weights in system RAM.
On those boxes ``offload_tts_for_asr()`` must release the TTS model through
``_offload_unified_memory()`` and ``restore_tts_after_asr()`` must leave it
released, instead of moving it CPU<->XPU via ``_relocate_shared_model()``.

Discrete Arc cards, CUDA, NPU, multi-XPU boxes and unreadable XPU properties
keep the existing dedicated path. The probe never raises.
"""

import pytest

import services.model_manager as mm

_TINY_LOCAL = 128 * 1024  # 128 KiB, like Lunar Lake integrated Arc
_DISCRETE_LOCAL = 12 * 1024 ** 3  # 12 GiB, like a discrete Arc card


class _FakeProps:
    def __init__(self, name: str, local_mem_size):
        self.name = name
        self.local_mem_size = local_mem_size


class _FakeXPU:
    def __init__(self, *, available=True, count=1, props=None, probe_error=False):
        self._available = available
        self._count = count
        self._props = props
        self._probe_error = probe_error

    def is_available(self):
        return self._available

    def device_count(self):
        return self._count

    def get_device_properties(self, index):
        if self._probe_error:
            raise RuntimeError("XPU property probe failed")
        return self._props


class _FakeCuda:
    def __init__(self, available=False):
        self._available = available

    def is_available(self):
        return self._available


class _FakeNPU:
    def __init__(self, available=False):
        self._available = available

    def is_available(self):
        return self._available


class _FakeTorch:
    def __init__(self, xpu=None, cuda=None, npu=None):
        self.xpu = xpu
        self.cuda = cuda if cuda is not None else _FakeCuda()
        self.npu = npu if npu is not None else _FakeNPU()


def _no_accelerator_torch():
    return _FakeTorch(
        xpu=_FakeXPU(available=False, count=0),
        cuda=_FakeCuda(),
        npu=_FakeNPU(),
    )


def _integrated_intel_arc_torch():
    return _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Intel(R) Arc(TM) Graphics", _TINY_LOCAL),
        )
    )


def _discrete_intel_arc_torch():
    return _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Intel(R) Arc Pro B70", _DISCRETE_LOCAL),
        )
    )


def _patch_torch(monkeypatch, fake):
    monkeypatch.setattr(mm, "_lazy_torch", lambda: fake)


# ── Detection ─────────────────────────────────────────────────────────


def test_single_integrated_intel_arc_is_unified_and_not_dedicated(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    assert mm._xpu_is_unified_memory() is True
    assert mm._has_dedicated_vram() is False


def test_single_discrete_intel_arc_stays_dedicated(monkeypatch):
    _patch_torch(monkeypatch, _discrete_intel_arc_torch())
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_multi_xpu_box_stays_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=2,
            props=_FakeProps("Intel(R) Arc(TM) Graphics", _TINY_LOCAL),
        )
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_non_intel_xpu_with_tiny_local_memory_stays_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Other Accelerator", _TINY_LOCAL),
        )
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_missing_local_mem_size_stays_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Intel(R) Arc(TM) Graphics", None),
        )
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_unreadable_props_fall_back_to_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(available=True, count=1, props=None, probe_error=True)
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_no_accelerator_is_not_dedicated(monkeypatch):
    _patch_torch(monkeypatch, _no_accelerator_torch())
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is False


def test_cuda_remains_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(available=False, count=0),
        cuda=_FakeCuda(available=True),
    )
    _patch_torch(monkeypatch, fake)
    assert mm._has_dedicated_vram() is True


def test_npu_remains_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(available=False, count=0),
        npu=_FakeNPU(available=True),
    )
    _patch_torch(monkeypatch, fake)
    assert mm._has_dedicated_vram() is True


# ── Offload/restore path selection ────────────────────────────────────


def test_integrated_intel_arc_offload_uses_unified_release(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    model = object()
    monkeypatch.setattr(mm, "model", model)

    unified_calls = []
    relocate_calls = []
    monkeypatch.setattr(mm, "_offload_unified_memory", lambda: unified_calls.append(1) or True)
    monkeypatch.setattr(
        mm,
        "_relocate_shared_model",
        lambda *_a, **_k: relocate_calls.append(1),
    )

    mm.offload_tts_for_asr()

    assert unified_calls == [1]
    assert relocate_calls == []


def test_integrated_intel_arc_restore_does_not_relocate(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    relocate_calls = []
    monkeypatch.setattr(
        mm,
        "_relocate_shared_model",
        lambda *_a, **_k: relocate_calls.append(1),
    )

    mm.restore_tts_after_asr()

    assert relocate_calls == []


def test_discrete_intel_arc_offload_relocates_to_cpu(monkeypatch):
    _patch_torch(monkeypatch, _discrete_intel_arc_torch())
    model = object()
    monkeypatch.setattr(mm, "model", model)
    monkeypatch.setattr(mm, "_acquire_exclusive_placement", lambda _wait: True)
    monkeypatch.setattr(mm, "_release_exclusive_placement", lambda: None)

    relocate_calls = []
    monkeypatch.setattr(
        mm,
        "_relocate_shared_model",
        lambda m, device: relocate_calls.append((m, device)),
    )

    mm.offload_tts_for_asr()

    assert relocate_calls == [(model, "cpu")]


def test_discrete_intel_arc_restore_relocates_back_to_xpu(monkeypatch):
    _patch_torch(monkeypatch, _discrete_intel_arc_torch())
    model = object()
    monkeypatch.setattr(mm, "model", model)
    monkeypatch.setattr(mm, "_acquire_exclusive_placement", lambda _wait: True)
    monkeypatch.setattr(mm, "_release_exclusive_placement", lambda: None)
    monkeypatch.setattr(mm, "get_best_device", lambda: "xpu")

    relocate_calls = []
    monkeypatch.setattr(
        mm,
        "_relocate_shared_model",
        lambda m, device: relocate_calls.append((m, device)),
    )

    mm.restore_tts_after_asr()

    assert relocate_calls == [(model, "xpu")]

