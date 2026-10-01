"""A Radeon owner on Windows must be told why the GPU is idle (Discord report,
RX 9070 XT; #2468).

The shipped Windows runtime is the NVIDIA-CUDA PyTorch wheel. On an AMD-only
machine ``torch.cuda.is_available()`` is False, so every PyTorch engine runs on
the CPU - and, before this change, three surfaces said something false or
nothing at all:

* ``why_no_gpu`` blamed a missing/too-old *NVIDIA driver* on a machine with no
  NVIDIA card;
* routing called the host a plain ``cpu_only`` machine, indistinguishable from
  one with no GPU, so no synth-time notice fired;
* nothing joined "what hardware is here" to "what the installed torch can do".

Every host shape is built from fakes (registry / sysfs / torch), so these run on
any CI OS.
"""
from __future__ import annotations

import types

import pytest

from core.device_caps import HostCaps, UNUSABLE_GPU_MARKER
from core.gpu_inventory import HostGPU
from services.engine_routing import resolve_routing


def _m(name: str):
    """Resolve a module per call, not at import: other suites purge
    ``sys.modules``, and a stale import would patch an object nothing else uses
    (same trap as tests/test_why_no_gpu_1274.py)."""
    import importlib

    return importlib.import_module(name)

RADEON = HostGPU(vendor="amd", name="AMD Radeon RX 9070 XT", vram_gb=16.0, pci_device_id="7550")
GEFORCE = HostGPU(vendor="nvidia", name="NVIDIA GeForce RTX 4070", vram_gb=12.0)
IGPU = HostGPU(vendor="intel", name="Intel(R) UHD Graphics", vram_gb=0.0)


def _torch(*, hip=None, cuda=None):
    return types.SimpleNamespace(version=types.SimpleNamespace(hip=hip, cuda=cuda))


def _cpu_caps(*notes: str, **kw) -> HostCaps:
    return HostCaps(
        family="cpu", available_families=("cpu",), notes=tuple(notes), **kw,
    )


# ── inventory readers ─────────────────────────────────────────────────────


class _FakeWinreg:
    """Just enough of ``winreg`` for the display-adapter class key."""

    HKEY_LOCAL_MACHINE = object()

    def __init__(self, subkeys: dict[str, dict]):
        self._subkeys = subkeys

    class _Ctx:
        def __init__(self, obj):
            self.obj = obj

        def __enter__(self):
            return self.obj

        def __exit__(self, *a):
            return False

    def OpenKey(self, parent, name):
        if parent is self.HKEY_LOCAL_MACHINE:
            return self._Ctx(("class", None))
        return self._Ctx(("adapter", self._subkeys[name]))

    def EnumKey(self, key, index):
        names = list(self._subkeys)
        if index >= len(names):
            raise OSError
        return names[index]

    def QueryValueEx(self, key, name):
        values = key[1]
        if name not in values:
            raise OSError
        return (values[name], 1)


def test_windows_registry_finds_radeon_and_skips_virtual_adapters():
    reg = _FakeWinreg({
        "0000": {
            "DriverDesc": "AMD Radeon RX 9070 XT",
            "MatchingDeviceId": "pci\\ven_1002&dev_7550&subsys_00000000",
            "HardwareInformation.qwMemorySize": 16 * 1024 ** 3,
        },
        "0001": {
            "DriverDesc": "Microsoft Hyper-V Video",
            "MatchingDeviceId": "vmbus\\{da0a7802-e377-4aac-8e77-0558eb1073f8}",
        },
        "0002": {"DriverDesc": "Microsoft Basic Display Adapter", "MatchingDeviceId": ""},
        "Configuration": {"DriverDesc": "ignored"},
    })
    gpus = _m("core.gpu_inventory")._read_windows(reg)
    assert [(g.vendor, g.name, g.vram_gb, g.pci_device_id) for g in gpus] == [
        ("amd", "AMD Radeon RX 9070 XT", 16.0, "7550"),
    ]


def test_windows_registry_vram_blob_from_older_drivers():
    reg = _FakeWinreg({
        "0000": {
            "DriverDesc": "NVIDIA GeForce GTX 1070",
            "MatchingDeviceId": "PCI\\VEN_10DE&DEV_1B81",
            "HardwareInformation.qwMemorySize": (8 * 1024 ** 3).to_bytes(8, "little"),
        },
    })
    (gpu,) = _m("core.gpu_inventory")._read_windows(reg)
    assert (gpu.vendor, gpu.vram_gb) == ("nvidia", 8.0)


def test_linux_sysfs_lists_cards_not_connectors(tmp_path):
    for entry, vendor, vram in (
        ("card0", "0x1002", str(16 * 1024 ** 3)),
        ("card1", "0x10de", ""),
        ("card0-DP-1", "0x1002", ""),   # connector, not a device
        ("renderD128", "0x1002", ""),
        ("card2", "0x1414", ""),        # Hyper-V synthetic video: not a candidate
    ):
        dev = tmp_path / entry / "device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text(vendor + "\n")
        (dev / "device").write_text("0x7550\n")
        if vram:
            (dev / "mem_info_vram_total").write_text(vram)
    gpus = _m("core.gpu_inventory")._read_linux(str(tmp_path))
    assert [(g.vendor, g.vram_gb) for g in gpus] == [("amd", 16.0), ("nvidia", 0.0)]


def test_inventory_never_raises_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DISABLE_GPU_INVENTORY", "1")
    assert _m("core.gpu_inventory").refresh() == ()
    assert _m("core.gpu_inventory")._read_linux("/definitely/not/here") == ()


def test_plain_intel_igpu_is_not_a_candidate():
    assert _m("core.gpu_inventory").discrete_candidates((IGPU,)) == ()
    arc = HostGPU(vendor="intel", name="Intel Arc A770", vram_gb=16.0, discrete=True)
    assert _m("core.gpu_inventory").discrete_candidates((IGPU, arc)) == (arc,)


# ── the misleading "NVIDIA driver" message ────────────────────────────────


def test_cuda_wheel_on_amd_only_host_does_not_blame_nvidia_driver():
    msg = " ".join(_m("core.device_caps").why_no_gpu(_torch(cuda="12.8"), gpus=(RADEON,)))
    assert "NVIDIA driver" not in msg
    assert UNUSABLE_GPU_MARKER in msg
    assert "Radeon RX 9070 XT" in msg
    assert "CUDA 12.8" in msg


def test_cuda_wheel_with_an_nvidia_card_keeps_the_driver_advice():
    msg = " ".join(_m("core.device_caps").why_no_gpu(_torch(cuda="12.8"), gpus=(GEFORCE,)))
    assert "NVIDIA driver is missing or too old" in msg


def test_cuda_wheel_on_gpu_less_host_keeps_the_driver_advice():
    msg = " ".join(_m("core.device_caps").why_no_gpu(_torch(cuda="12.8"), gpus=()))
    assert "NVIDIA driver is missing or too old" in msg


# ── the probe + routing ───────────────────────────────────────────────────


def _probe_with_gpus(monkeypatch, gpus, *, cuda=None, hip=None):
    from unittest.mock import patch

    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: False, device_count=lambda: 0,
        ),
        version=types.SimpleNamespace(cuda=cuda, **({"hip": hip} if hip else {})),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
        xpu=types.SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setattr("core.gpu_inventory.detect_host_gpus", lambda: gpus)
    try:
        with patch.dict("sys.modules", {"torch": torch}):
            return _m("core.device_caps").refresh()
    finally:
        # refresh() cached a fake-torch probe process-wide; don't leak it into
        # later tests (the patches above are still active here, so clear only).
        _m("core.device_caps").detect_host_caps.cache_clear()


def test_probe_flags_amd_card_that_the_cuda_wheel_cannot_drive(monkeypatch):
    caps = _probe_with_gpus(monkeypatch, (RADEON,), cuda="12.8")
    assert caps.family == "cpu"
    marked = [n for n in caps.notes if UNUSABLE_GPU_MARKER in n]
    assert len(marked) == 1 and "Radeon" in marked[0]  # one note, not two
    assert not any("NVIDIA driver" in n for n in caps.notes)


def test_probe_is_quiet_on_a_gpu_less_host(monkeypatch):
    caps = _probe_with_gpus(monkeypatch, (), cuda="12.8")
    assert not any(UNUSABLE_GPU_MARKER in n for n in caps.notes)


def test_probe_ignores_an_intel_igpu(monkeypatch):
    caps = _probe_with_gpus(monkeypatch, (IGPU,), cuda="12.8")
    assert not any(UNUSABLE_GPU_MARKER in n for n in caps.notes)


def test_gpu_capable_engine_is_a_cpu_fallback_on_that_host_not_cpu_only():
    note = f"AMD Radeon RX 9070 XT (AMD) {UNUSABLE_GPU_MARKER}: this is a CUDA 12.8 PyTorch build"
    caps = _cpu_caps(note)
    r = resolve_routing(("cuda", "cpu"), caps)
    assert r["routing_status"] == "cpu_fallback"
    assert "Radeon" in r["routing_reason"]


def test_cpu_native_engine_stays_benign_on_that_host():
    note = f"AMD Radeon (AMD) {UNUSABLE_GPU_MARKER}: x"
    r = resolve_routing(("cpu",), _cpu_caps(note))
    assert r["routing_status"] == "cpu_only"


def test_gpu_less_host_is_still_benign_cpu_only():
    assert resolve_routing(("cuda", "cpu"), _cpu_caps())["routing_status"] == "cpu_only"


# ── the report ────────────────────────────────────────────────────────────


def _row(eid, compat, status, device="cpu", reason=None, available=True):
    return {
        "id": eid, "display_name": eid.title(), "available": available,
        "gpu_compat": list(compat), "routing_status": status,
        "effective_device": device, "routing_reason": reason,
    }


def _report(caps, gpus, kind, tts=(), asr=(), platform="win32"):
    return _m("core.gpu_report").build_gpu_report(
        caps, tuple(gpus), {"kind": kind, "version": "2.8.0+cu128", "runtime": "12.8"},
        list(tts), list(asr), platform=platform,
    )


def test_windows_amd_on_cuda_wheel_state_and_honest_options():
    rep = _report(_cpu_caps(), [RADEON], "cuda", platform="win32")
    assert rep["state"] == "amd_cuda_build"
    assert rep["params"]["gpu"] == "AMD Radeon RX 9070 XT"
    # Vulkan engines and the manual ROCm route are the only honest options;
    # DirectML is deliberately not offered (needs torch 2.4).
    assert rep["options"] == ["vulkan_engines", "rocm_windows_manual"]


def test_linux_amd_on_cuda_wheel_points_at_the_rocm_variant():
    rep = _report(_cpu_caps(), [RADEON], "cuda", platform="linux")
    assert rep["state"] == "amd_cuda_build"
    assert rep["options"][0] == "rocm_variant_linux"


def test_rocm_build_without_a_device_is_its_own_state():
    assert _report(_cpu_caps(), [RADEON], "rocm", platform="linux")["state"] == "amd_rocm_no_device"


def test_nvidia_host_states():
    assert _report(_cpu_caps(), [GEFORCE], "cpu")["state"] == "nvidia_cpu_build"
    assert _report(_cpu_caps(), [GEFORCE], "cuda")["state"] == "nvidia_cuda_unavailable"


def test_working_gpu_and_gpu_less_and_pinned_states():
    gpu_caps = HostCaps(family="cuda", available_families=("cuda", "cpu"), device_name="RTX 4070")
    assert _report(gpu_caps, [GEFORCE], "cuda")["state"] == "accelerated"
    assert _report(_cpu_caps(), [], "cpu")["state"] == "no_gpu"
    pinned = HostCaps(
        family="cpu", available_families=("cuda", "cpu"), requested_family="cpu",
    )
    assert _report(pinned, [GEFORCE], "cuda")["state"] == "pinned_cpu"
    assert _report(_cpu_caps(probe_ok=False), [], "unknown")["state"] == "probe_failed"


def test_kernel_risk_note_marks_accelerated_with_caveat():
    caps = HostCaps(
        family="cuda", available_families=("cuda", "cpu"),
        notes=("GPU (sm_120) not in this torch build's archs - may fail at kernel launch",),
    )
    assert _report(caps, [GEFORCE], "cuda")["state"] == "accelerated_caveat"


def test_engine_verdicts_on_windows_amd_host():
    note = f"AMD Radeon RX 9070 XT (AMD) {UNUSABLE_GPU_MARKER}: x"
    caps = _cpu_caps(note)
    rep = _report(
        caps, [RADEON], "cuda",
        tts=[
            _row("omnivoice", ("cuda", "rocm", "mps", "cpu"), "cpu_fallback", reason=note),
            _row("cosyvoice", ("cuda", "cpu"), "cpu_fallback", reason=note),
            _row("pockettts", ("cpu",), "cpu_only"),
            # audio.cpp runs its own Vulkan runtime: the Radeon IS used.
            _row("audiocpp", ("cpu", "vulkan"), "accelerated", device="vulkan"),
            # runtime not installed: no verdict is claimed.
            _row("audiocpp2", ("cpu",), "unavailable", available=False),
        ],
        asr=[_row("faster-whisper", ("cuda", "cpu"), "cpu_fallback", reason=note)],
    )
    by_id = {e["id"]: e for e in rep["engines"]}
    assert by_id["omnivoice"]["code"] == "host_gpu_unusable_rocm"
    assert by_id["cosyvoice"]["code"] == "host_gpu_unusable_no_amd_path"
    assert by_id["faster-whisper"]["code"] == "host_gpu_unusable_no_amd_path"
    assert by_id["pockettts"]["code"] == "cpu_by_design"
    assert by_id["audiocpp"]["code"] == "gpu"
    assert by_id["audiocpp"]["params"] == {"device": "vulkan"}
    assert by_id["audiocpp2"]["code"] == "not_installed"
    assert {e["kind"] for e in rep["engines"]} == {"tts", "asr"}


def test_collect_never_raises_when_registries_explode(monkeypatch):
    monkeypatch.setattr("core.gpu_report.detect_host_gpus", lambda: (RADEON,))

    def boom():
        raise RuntimeError("registry broken")

    monkeypatch.setattr("services.tts_backend.list_backends", boom)
    monkeypatch.setattr("services.asr_backend.list_backends", boom)
    rep = _m("core.gpu_report").collect_gpu_report()
    assert rep["engines"] == []
    assert "state" in rep


def test_report_reasons_are_scrubbed(monkeypatch):
    monkeypatch.setattr("core.gpu_report.detect_host_gpus", lambda: ())
    row = _row("x", ("cuda", "cpu"), "cpu_fallback", reason="failed at C:\\Users\\alice\\secret")
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [row])
    monkeypatch.setattr("services.asr_backend.list_backends", lambda: [])
    rep = _m("core.gpu_report").collect_gpu_report()
    assert "alice" not in (rep["engines"][0]["reason"] or "")


def _fake_venv(tmp_path, version_py: str):
    site = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages" / "torch"
    site.mkdir(parents=True, exist_ok=True)
    (site / "version.py").write_text(version_py)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")


def test_indextts_does_not_claim_rocm_when_its_venv_holds_a_cuda_torch(tmp_path, monkeypatch):
    """PR #2423 review: gpu_compat claims ROCm because the installer provisions a
    ROCm torch - but a venv that predates that (or a user-managed clone) still
    has the CUDA wheel, which sees no AMD GPU. Routing must not report
    acceleration for it."""
    from engines.indextts import IndexTTS2Backend, bootstrap

    rocm_caps = HostCaps(family="rocm", available_families=("rocm", "cpu"), device_name="RX 6800 XT")
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_resolved_python", None)

    _fake_venv(tmp_path, "__version__ = '2.8.0+cu128'\ncuda = '12.8'\nhip = None\n")
    cuda_only = IndexTTS2Backend.runtime_compute_profile(rocm_caps)
    assert cuda_only["routing_status"] == "cpu_fallback"
    assert "rocm" not in cuda_only["gpu_compat"]

    _fake_venv(tmp_path, "__version__ = '2.8.0+rocm6.4'\ncuda = None\nhip = '6.4.43482'\n")
    rocm = IndexTTS2Backend.runtime_compute_profile(rocm_caps)
    assert rocm["routing_status"] == "accelerated"
    assert rocm["effective_device"] == "rocm"


def test_indextts_keeps_its_claim_when_the_venv_cannot_be_inspected(tmp_path, monkeypatch):
    from engines.indextts import IndexTTS2Backend, bootstrap

    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(tmp_path))  # no .venv at all
    monkeypatch.setattr(bootstrap, "_resolved_python", None)
    rocm_caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))
    assert IndexTTS2Backend.runtime_compute_profile(rocm_caps)["routing_status"] == "accelerated"
    # Never narrowed off a ROCm host either.
    cuda_caps = HostCaps(family="cuda", available_families=("cuda", "cpu"))
    assert IndexTTS2Backend.runtime_compute_profile(cuda_caps)["routing_status"] == "accelerated"


def test_indextts_inspects_the_managed_default_venv_too(tmp_path, monkeypatch):
    """Review: with no OMNIVOICE_INDEXTTS_DIR, bootstrap falls back to the
    package's own venv - a CUDA torch there must not be reported as GPU use."""
    from engines.indextts import IndexTTS2Backend, bootstrap

    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    monkeypatch.setattr(bootstrap, "_resolved_python", None)
    monkeypatch.setattr(bootstrap, "_ENGINES_VENV_DIR", tmp_path / ".venv")
    _fake_venv(tmp_path, "__version__ = '2.8.0+cu128'\ncuda = '12.8'\nhip = None\n")
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))
    assert IndexTTS2Backend.runtime_compute_profile(caps)["routing_status"] == "cpu_fallback"


def test_linux_intel_arc_is_found_without_a_vram_figure(tmp_path, symlink_or_skip):
    """i915/xe expose no mem_info_vram_total, so Arc used to read 0 GB and be
    dropped as noise. A card behind a bridge is discrete; the iGPU at 00:02.0
    is not."""
    inv = _m("core.gpu_inventory")
    pci = tmp_path / "pci"
    for name in ("0000:00:02.0", "0000:03:00.0"):
        (pci / name).mkdir(parents=True)
    for card, bdf in (("card0", "0000:00:02.0"), ("card1", "0000:03:00.0")):
        (tmp_path / card).mkdir()
        symlink_or_skip(tmp_path / card / "device", pci / bdf, target_is_directory=True)
        (pci / bdf / "vendor").write_text("0x8086\n")
        (pci / bdf / "device").write_text("0x56a0\n")
    gpus = inv._read_linux(str(tmp_path))
    assert [(g.vram_gb, g.discrete) for g in gpus] == [(0.0, False), (0.0, True)]
    assert [g.discrete for g in inv.discrete_candidates(gpus)] == [True]


def test_mixed_radeon_geforce_host_is_diagnosed_against_the_build(monkeypatch):
    """Review: with both cards and a CUDA build that found nothing, the NVIDIA
    path is the broken one - recommending ROCm is the wrong diagnosis."""
    both = (RADEON, GEFORCE)
    assert _report(_cpu_caps(), both, "cuda")["state"] == "nvidia_cuda_unavailable"
    assert _report(_cpu_caps(), both, "rocm", platform="linux")["state"] == "amd_rocm_no_device"
    assert _report(_cpu_caps(), both, "cpu")["state"] == "nvidia_cpu_build"
    note = _m("core.device_caps")._unusable_gpu_note(_torch(cuda="12.8"), both)
    assert "GeForce" in note


def test_engine_missing_its_runtime_is_never_counted_as_using_the_gpu():
    row = _row("audiocpp", ("cpu", "vulkan"), "accelerated", device="vulkan", available=False)
    rep = _report(_cpu_caps(), [RADEON], "cuda", tts=[row])
    assert rep["engines"][0]["code"] == "not_installed"


@pytest.mark.parametrize("driver", ["N/A", "[Not Supported]", "", "abc.def"])
def test_unparseable_nvidia_driver_metadata_skips_the_floor_check(monkeypatch, driver):
    """CodeRabbit: an unreadable driver string must not fail a working GPU."""
    import sys as _sys
    import platform as _p

    if _sys.platform == "darwin" and _p.machine() == "arm64":
        pytest.skip("apple-silicon branch returns before nvidia-smi")
    wizard = _m("api.routers.setup.wizard")
    monkeypatch.setattr(
        wizard, "_run_cmd",
        lambda args, timeout=2.0: (0, f"{driver}, NVIDIA GeForce RTX 4070\n") if args[0] == "nvidia-smi" else (-1, ""),
    )
    info = wizard._detect_gpu()
    assert info["vendor"] == "nvidia"
    assert not any("below" in n for n in info["notes"])
    assert wizard._driver_tuple(driver) is None


def test_self_check_names_the_card_instead_of_saying_no_gpu(monkeypatch):
    diagnose = _m("core.diagnose")

    note = f"AMD Radeon RX 9070 XT (AMD) {UNUSABLE_GPU_MARKER}: this is a CUDA 12.8 PyTorch build"
    monkeypatch.setattr("services.model_manager.get_best_device", lambda: "cpu")
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: _cpu_caps(note))
    check = diagnose._check_device()
    assert check["status"] == "warn"
    assert "Radeon RX 9070 XT" in check["detail"]
    assert "no GPU acceleration detected" not in check["detail"]

    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: _cpu_caps())
    assert "no GPU acceleration detected" in diagnose._check_device()["detail"]


def test_settings_route_serves_the_report(monkeypatch):
    from api.routers import settings

    monkeypatch.setattr("core.gpu_report.detect_host_gpus", lambda: (RADEON,))
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [])
    monkeypatch.setattr("services.asr_backend.list_backends", lambda: [])
    rep = settings.get_gpu_report()
    assert rep["gpus"][0]["vendor"] == "amd"
    assert {"state", "options", "engines", "torch", "platform"} <= set(rep)
    assert any(r.path == "/api/settings/gpu-report" for r in settings.router.routes)


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_report_is_json_serialisable(platform):
    import json

    json.dumps(_report(_cpu_caps(), [RADEON], "cuda", platform=platform))
