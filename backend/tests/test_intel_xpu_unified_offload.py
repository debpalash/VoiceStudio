"""Integrated Intel Arc XPUs are unified-memory devices, not dedicated-VRAM
devices.

A single integrated Intel Arc XPU (e.g. Lunar Lake 130V/140V, reported via
the device properties' ``is_integrated_gpu`` flag) allocates weights in system
RAM.
On those boxes ``offload_tts_for_asr()`` must release the TTS model through
``_offload_unified_memory()`` and ``restore_tts_after_asr()`` must leave it
released, instead of moving it CPU<->XPU via ``_relocate_shared_model()``.

Discrete Arc cards, CUDA, NPU, multi-XPU boxes and unreadable XPU properties
keep the existing dedicated path. The probe never raises.
"""

import pytest

import services.model_manager as mm

_INTEGRATED = True
_DISCRETE = False


class _FakeProps:
    def __init__(self, name: str, is_integrated_gpu):
        self.name = name
        # local_mem_size is per-work-group memory: deliberately tiny on both
        # integrated and discrete parts, so it must not drive detection.
        self.local_mem_size = 64 * 1024
        if is_integrated_gpu is not None:
            self.is_integrated_gpu = is_integrated_gpu


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
            props=_FakeProps("Intel(R) Arc(TM) Graphics", _INTEGRATED),
        )
    )


def _discrete_intel_arc_torch():
    return _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Intel(R) Arc Pro B70", _DISCRETE),
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
            props=_FakeProps("Intel(R) Arc(TM) Graphics", _INTEGRATED),
        )
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_non_intel_integrated_xpu_stays_dedicated(monkeypatch):
    fake = _FakeTorch(
        xpu=_FakeXPU(
            available=True,
            count=1,
            props=_FakeProps("Other Accelerator", _INTEGRATED),
        )
    )
    _patch_torch(monkeypatch, fake)
    assert mm._xpu_is_unified_memory() is False
    assert mm._has_dedicated_vram() is True


def test_missing_is_integrated_gpu_attribute_stays_dedicated(monkeypatch):
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
    monkeypatch.setattr(mm, "_offload_unified_memory", lambda **_kw: unified_calls.append(1) or True)
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


def test_integrated_intel_arc_offload_waits_for_exclusive_placement(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    events = []
    monkeypatch.setattr(
        mm, "_acquire_exclusive_placement", lambda _w: events.append("acquire") or True
    )
    monkeypatch.setattr(
        mm, "_release_exclusive_placement", lambda: events.append("release")
    )
    monkeypatch.setattr(
        mm, "_offload_unified_memory", lambda **_k: events.append("unload") or True
    )

    mm.offload_tts_for_asr(force_unified_release=False)

    assert events == ["acquire", "unload", "release"]


def test_integrated_intel_arc_offload_skips_while_generation_active(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_acquire_exclusive_placement", lambda _w: False)
    unloaded = []
    monkeypatch.setattr(
        mm, "_offload_unified_memory", lambda **_k: unloaded.append(1) or True
    )

    mm.offload_tts_for_asr(force_unified_release=True)

    assert unloaded == []


def test_unified_offload_force_releases_despite_headroom(monkeypatch):
    from services import memory_budget as mb

    monkeypatch.setattr(
        mb,
        "available_memory",
        lambda: {"ram_available_gb": 13.0},
    )
    monkeypatch.setattr(mm, "model", object())
    unloaded = []
    monkeypatch.setattr(
        mm,
        "unload_shared_model",
        lambda: unloaded.append(1) or True,
    )

    assert mm._offload_unified_memory() is False
    assert mm._offload_unified_memory(force=True) is True
    assert unloaded == [1]


# ── Forced clone release is gated to integrated Intel Arc ────────────


def test_forced_release_engages_on_integrated_intel_arc(monkeypatch):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())

    seen = []
    monkeypatch.setattr(
        mm,
        "_offload_unified_memory",
        lambda **kw: seen.append(kw) or True,
    )

    mm.offload_tts_for_asr(force_unified_release=True)

    assert seen == [{"force": True}]


def test_forced_release_does_not_force_on_non_integrated_unified(monkeypatch):
    # A no-accelerator box is unified-but-not-integrated-Arc: the old
    # headroom window must keep deciding, so the clone path cannot turn
    # into an unconditional unload on Mac/CUDA-absent hosts.
    _patch_torch(monkeypatch, _no_accelerator_torch())
    monkeypatch.setattr(mm, "model", object())

    seen = []
    monkeypatch.setattr(
        mm,
        "_offload_unified_memory",
        lambda **kw: seen.append(kw) or True,
    )

    mm.offload_tts_for_asr(force_unified_release=True)

    assert seen == [{"force": False}]



# ── Forced release holds placement for the whole reference ASR ───────


@pytest.fixture
def _clean_hold(monkeypatch):
    monkeypatch.setattr(mm, "_unified_asr_holds", 0)
    monkeypatch.setattr(mm, "_tts_loads_in_flight", 0)
    monkeypatch.setattr(mm, "_tts_unloads_in_flight", 0)
    monkeypatch.setattr(mm, "_placement_exclusive", False)
    mm._asr_hold_tls.tokens = 0
    yield


def test_forced_release_holds_placement_until_restore(monkeypatch, _clean_hold):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    events = []
    monkeypatch.setattr(
        mm, "_acquire_exclusive_placement", lambda _w: events.append("acquire") or True
    )
    monkeypatch.setattr(
        mm, "_release_exclusive_placement", lambda: events.append("release")
    )
    monkeypatch.setattr(
        mm, "_offload_unified_memory", lambda **_k: events.append("unload") or True
    )

    assert mm.offload_tts_for_asr(force_unified_release=True) is True
    assert events == ["acquire", "unload"]  # still held during ASR
    monkeypatch.setattr(mm, "model", None)  # the release unloaded it
    mm.restore_tts_after_asr()
    assert events == ["acquire", "unload", "release"]


def test_refused_forced_release_returns_false(monkeypatch, _clean_hold):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_acquire_exclusive_placement", lambda _w: False)
    assert mm.offload_tts_for_asr(force_unified_release=True) is False


def _run_in_thread(fn):
    import threading

    t = threading.Thread(target=fn)
    t.start()
    t.join(5)
    assert not t.is_alive()


@pytest.mark.parametrize("first_ends", ["a", "b"])
def test_overlapping_ops_hold_until_both_end(monkeypatch, _clean_hold, first_ends):
    import threading, time

    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_offload_unified_memory", lambda **_k: True)
    # op b is admitted while the model is already unloaded
    ops = {}

    def admit(name, model):
        monkeypatch.setattr(mm, "model", model)
        ops[name] = threading.Event()
        res = []

        def run():
            res.append(mm.offload_tts_for_asr(force_unified_release=True))
            ops[name].wait(5)
            mm.restore_tts_after_asr()

        th = threading.Thread(target=run)
        th.start()
        time.sleep(0.1)
        assert res == [True]
        return th

    ta = admit("a", object())
    tb = admit("b", None)
    assert mm._unified_asr_holds == 2

    woke = threading.Event()
    waiter = threading.Thread(
        target=lambda: (mm._begin_tts_load(5), mm._end_tts_load(), woke.set())
    )
    waiter.start()

    ops[first_ends].set()
    time.sleep(0.2)
    assert mm._unified_asr_holds == 1
    assert not woke.is_set() and mm._placement_exclusive is True

    ops["b" if first_ends == "a" else "a"].set()
    ta.join(2); tb.join(2); waiter.join(2)
    assert woke.is_set()
    assert mm._unified_asr_holds == 0 and mm._placement_exclusive is False


def test_restore_without_token_is_a_noop(monkeypatch, _clean_hold):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_unified_asr_holds", 1)
    monkeypatch.setattr(mm, "_placement_exclusive", True)
    mm.restore_tts_after_asr()
    assert mm._unified_asr_holds == 1 and mm._placement_exclusive is True


def test_wait_is_bounded(monkeypatch, _clean_hold):
    monkeypatch.setattr(mm, "_unified_asr_holds", 1)
    mm._begin_tts_load(0.05)  # returns instead of hanging
    mm._end_tts_load()


def test_failed_forced_release_leaves_hold_and_returns_false(monkeypatch, _clean_hold):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    monkeypatch.setattr(mm, "_offload_unified_memory", lambda **_k: False)
    assert mm.offload_tts_for_asr(force_unified_release=True) is False
    assert mm._unified_asr_holds == 0 and mm._placement_exclusive is False
    mm.restore_tts_after_asr()  # nothing left to release
    assert mm._unified_asr_holds == 0


def test_admission_waits_for_inflight_unload(monkeypatch, _clean_hold):
    import threading, time

    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", object())
    gate = threading.Event()
    monkeypatch.setattr(mm, "release_tts_side_caches", lambda: gate.wait(5))
    monkeypatch.setattr(mm, "free_vram", lambda: None)

    unloader = threading.Thread(target=mm.unload_shared_model)
    unloader.start()
    time.sleep(0.1)
    assert mm.model is None  # reference cleared, unload still running

    res = []
    admit = threading.Thread(
        target=lambda: res.append(mm.offload_tts_for_asr(force_unified_release=True))
    )
    admit.start()
    time.sleep(0.2)
    assert res == []  # admission is waiting on the slow unload
    gate.set()
    unloader.join(2); admit.join(2)
    assert res == [True]
    mm.restore_tts_after_asr()


def test_admission_times_out_on_stuck_unload_and_leaves_hold(monkeypatch, _clean_hold):
    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_ASR_PLACEMENT_WAIT_S", 0.1)
    monkeypatch.setattr(mm, "_tts_unloads_in_flight", 1)
    assert mm.offload_tts_for_asr(force_unified_release=True) is False
    assert mm._unified_asr_holds == 0 and mm._placement_exclusive is False


def test_admission_waits_for_inflight_load(monkeypatch, _clean_hold):
    import threading, time

    _patch_torch(monkeypatch, _integrated_intel_arc_torch())
    monkeypatch.setattr(mm, "model", None)
    mm._begin_tts_load(1)  # a cold load already running
    res = []
    th = threading.Thread(
        target=lambda: res.append(mm.offload_tts_for_asr(force_unified_release=True))
    )
    th.start()
    time.sleep(0.2)
    assert res == []
    mm._end_tts_load()
    th.join(2)
    assert res == [True]
    mm.restore_tts_after_asr()
