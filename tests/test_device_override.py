"""The user compute-device override (Settings → Performance / OMNIVOICE_DEVICE).

The override is applied at the single choke point — `_probe()`'s family
selection — so routing, `get_best_device()`, and every UI badge inherit it.
Contract pinned here: an override steers, it cannot invent hardware (a family
this host lacks is noted and ignored); "cpu" is always honorable; env beats
the persisted Settings pick; unknown values normalize to "auto"; and the
running process reports what it actually applied (`requested_family`) so the
Settings panel can show restart-required truthfully.
"""
from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from core import device_caps


def _torch_mock(*, cuda_available=False, mps_available=False):
    """Minimal torch mock — just enough accelerator shape for the override
    tests (test_device_caps.py owns the full degradation matrix)."""
    cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: 1,
        get_device_name=lambda i: "NVIDIA RTX 4090",
        mem_get_info=lambda: (12 * 1024**3, 24 * 1024**3),
        get_device_capability=lambda i: (8, 9),
        _get_arch_list=lambda: [],
    )
    backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_available)
    )
    xpu = types.SimpleNamespace(
        is_available=lambda: False, get_device_name=lambda i: ""
    )
    return types.SimpleNamespace(
        cuda=cuda, version=types.SimpleNamespace(), backends=backends, xpu=xpu
    )


def _probe_with(modules):
    with patch.dict("sys.modules", modules):
        return device_caps.refresh()


def _cleanup():
    # Leave the process-wide cache in its no-override state for other tests.
    # The env var must go FIRST: this runs inside the test (before
    # monkeypatch teardown), so a refresh with OMNIVOICE_DEVICE still set
    # would cache the overridden caps for every later test in the session.
    import os

    os.environ.pop("OMNIVOICE_DEVICE", None)
    device_caps.refresh()


def test_no_override_is_auto_and_keeps_priority_pick(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_DEVICE", raising=False)
    monkeypatch.setattr("core.prefs.resolve", lambda key, **kw: kw.get("default"))
    try:
        caps = _probe_with({"torch": _torch_mock(cuda_available=True, mps_available=True)})
        assert caps.family == "cuda"
        assert caps.requested_family == "auto"
    finally:
        _cleanup()


def test_cpu_override_forces_cpu_on_an_accelerated_host(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    try:
        caps = _probe_with({"torch": _torch_mock(cuda_available=True)})
        assert caps.family == "cpu"
        assert caps.requested_family == "cpu"
        # The pin is explained, not silent — a CUDA host showing CPU chips
        # without a note reads as a broken install.
        assert any("pinned" in n for n in caps.notes)
    finally:
        _cleanup()


def test_override_for_a_missing_family_is_noted_and_ignored(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cuda")
    try:
        caps = _probe_with({"torch": _torch_mock(mps_available=True)})
        assert caps.family == "mps"  # auto pick survives
        assert caps.requested_family == "cuda"
        assert any("not available" in n for n in caps.notes)
    finally:
        _cleanup()


def test_override_matching_the_auto_pick_adds_no_note(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cuda")
    try:
        caps = _probe_with({"torch": _torch_mock(cuda_available=True)})
        assert caps.family == "cuda"
        assert not any("pinned" in n for n in caps.notes)
    finally:
        _cleanup()


def test_env_beats_the_persisted_settings_pick(monkeypatch, tmp_path):
    # Same resolution order as engine selection (#981): a power-user's env
    # pin must not be silently undone by the UI's stored choice.
    from core import prefs

    monkeypatch.setattr(prefs, "_PREFS_PATH", tmp_path / "prefs.json")
    prefs.set_("compute_device", "cuda")
    try:
        monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
        assert device_caps.requested_device_override() == "cpu"
        monkeypatch.delenv("OMNIVOICE_DEVICE")
        assert device_caps.requested_device_override() == "cuda"
    finally:
        _cleanup()


def test_unknown_values_normalize_to_auto(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DEVICE", "quantum")
    try:
        assert device_caps.requested_device_override() == "auto"
    finally:
        _cleanup()


def test_torch_unimportable_still_reports_the_request(monkeypatch):
    # Even a degraded CPU-only probe carries the requested family so the
    # Settings panel renders the user's pick instead of resetting to auto.
    # (A CPU pick never imports torch at all: see the no-GPU-probe test below.)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cuda")
    real_import = __import__

    def _no_torch(name, *a, **kw):
        if name == "torch":
            raise ImportError("no torch here")
        return real_import(name, *a, **kw)

    try:
        with patch.dict("sys.modules"):
            import sys

            sys.modules.pop("torch", None)
            with patch("builtins.__import__", side_effect=_no_torch):
                caps = device_caps.refresh()
        assert caps.probe_ok is False
        assert caps.requested_family == "cuda"
    finally:
        _cleanup()


# Every GPU runtime call a probe made. Probes swallow exceptions, so raising
# alone would not prove anything: the calls are recorded and must stay empty.
_GPU_CALLS: list[str] = []


def _exploding_torch():
    """A torch whose every accelerator probe is recorded and fails."""

    def boom(*_a, **_kw):
        _GPU_CALLS.append("torch")
        raise AssertionError("a GPU runtime was probed on a host pinned to CPU")

    accel = types.SimpleNamespace(
        is_available=boom, device_count=boom, get_device_name=boom,
        mem_get_info=boom, get_device_capability=boom, get_device_properties=boom,
    )
    return types.SimpleNamespace(
        cuda=accel, xpu=accel, npu=accel, version=types.SimpleNamespace(cuda="12.8"),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=boom)),
    )


def _exploding_directml():
    def boom(*_a, **_kw):
        _GPU_CALLS.append("directml")
        raise AssertionError("DirectML was opened on a host pinned to CPU")

    return types.SimpleNamespace(device_count=boom, device=boom)


@pytest.mark.parametrize("pin_by", ["prefs", "env"])
def test_cpu_choice_never_probes_a_gpu_runtime(monkeypatch, pin_by):
    """Restart on CPU after a graphics driver crash (#4002): no startup path may
    initialise CUDA, MPS, XPU or DirectML, or it could hit the same driver."""
    import importlib

    import importlib

    from services import model_manager

    # Resolved per test: other tests may replace these sys.modules entries.
    main = importlib.import_module("main")
    system = importlib.import_module("api.routers.system")

    if pin_by == "env":
        monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    else:
        monkeypatch.delenv("OMNIVOICE_DEVICE", raising=False)
        monkeypatch.setattr(
            "core.prefs.resolve",
            lambda key, **kw: "cpu" if key == "compute_device" else kw.get("default"),
        )
    live_caps = importlib.import_module("core.device_caps")
    _GPU_CALLS.clear()
    torch = _exploding_torch()
    modules = {"torch": torch, "torch_directml": _exploding_directml()}
    try:
        with patch.dict("sys.modules", modules):
            caps = live_caps.refresh()
            assert caps.family == "cpu"
            assert caps.requested_family == "cpu"
            assert caps.available_families == ("cpu",)
            assert any("pinned" in n for n in caps.notes)

            monkeypatch.setattr(model_manager, "_lazy_torch", lambda: torch)
            assert model_manager.get_best_device() == "cpu"

            assert system._probe_accelerators(torch, True) == (False, False, False, False)
            assert main._torch_device_label() == "cpu"
            # Background paths: /system/notifications, the capture warm-up's
            # GPU pool sizing, and allocator cache release.
            assert model_manager.check_device_compatibility() == (True, None)
            monkeypatch.delenv("OMNIVOICE_GPU_WORKERS", raising=False)
            assert model_manager._pick_gpu_workers() == 1
            model_manager.release_device_cache()
        assert _GPU_CALLS == []
    finally:
        _cleanup()
        live_caps.detect_host_caps.cache_clear()


def test_cpu_choice_reports_as_the_users_choice_not_a_gpu_failure():
    """A host pinned to CPU is no longer probed, so its families are unknown.
    The GPU report must say the user chose CPU (Settings offers Auto to go
    back), not that CUDA failed and the driver needs an update (#4006)."""
    from core import gpu_report
    from core.gpu_inventory import HostGPU

    pinned = device_caps.HostCaps(
        family="cpu",
        available_families=("cpu",),
        notes=(device_caps.CPU_PINNED_NOTE,),
        requested_family="cpu",
        gpu_probe_skipped=True,
    )
    nvidia = (HostGPU(vendor="nvidia", name="NVIDIA GeForce RTX 4070"),)
    cuda_build = {"kind": "cuda", "version": "2.10.0+cu128"}
    assert gpu_report.classify_host(pinned, nvidia, cuda_build) == ("pinned_cpu", {})
    assert gpu_report.classify_host(pinned, (), cuda_build) == ("pinned_cpu", {})
    # A probed host with no CPU choice is never reported as pinned.
    probed = device_caps.HostCaps(family="cpu", available_families=("cpu",))
    assert gpu_report.classify_host(probed, nvidia, cuda_build)[0] != "pinned_cpu"


def test_status_page_without_a_cpu_choice_still_finds_an_extension_npu(monkeypatch):
    """/system/info decides whether to probe from the choice alone. Reading the
    capability snapshot there cached it before torch_npu was imported, so an
    Ascend NPU whose extension does not autoload vanished for the process."""
    import importlib

    system = importlib.import_module("api.routers.system")
    monkeypatch.delenv("OMNIVOICE_DEVICE", raising=False)
    monkeypatch.setattr("core.prefs.resolve", lambda key, **kw: kw.get("default"))
    snapshots: list[str] = []
    # Patch the module the router imports now, not this file's collection-time copy.
    monkeypatch.setattr(
        importlib.import_module("core.device_caps"),
        "detect_host_caps",
        lambda: snapshots.append("caps") or None,
    )
    assert system._cpu_pinned() is False
    assert snapshots == [], "the choice check must not snapshot capabilities"

    # torch.npu only exists once the Ascend extension is imported.
    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        xpu=types.SimpleNamespace(is_available=lambda: False),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
    )
    real_import, real_find = system.importlib.import_module, system.importlib.util.find_spec

    def fake_import(name, *args):
        if name == "torch_npu":
            torch.npu = types.SimpleNamespace(is_available=lambda: True)
            return types.SimpleNamespace()
        return real_import(name, *args)

    monkeypatch.setattr(system.importlib, "import_module", fake_import)
    monkeypatch.setattr(
        system.importlib.util,
        "find_spec",
        lambda name, *a: object() if name == "torch_npu" else real_find(name, *a),
    )
    assert system._probe_accelerators(torch, system._cpu_pinned()) == (False, False, False, True)
