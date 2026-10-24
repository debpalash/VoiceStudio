from types import SimpleNamespace

from api.routers import system


def test_windows_gpu_fallback_prefers_discrete_adapter(monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "win32")
    monkeypatch.setattr(system.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="Microsoft Basic Display Adapter\nIntel UHD Graphics 770\nAMD Radeon RX 7900 XTX\n"
        ),
    )

    assert system._detect_os_gpu_name() == "AMD Radeon RX 7900 XTX"


def test_linux_gpu_fallback_reads_lspci_machine_output(monkeypatch):
    monkeypatch.setattr(system.sys, "platform", "linux")
    monkeypatch.setattr(system.shutil, "which", lambda name: "/usr/bin/lspci")
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=(
                '00:02.0 "VGA compatible controller" "Intel Corporation" "UHD Graphics"\n'
                '03:00.0 "3D controller" "NVIDIA Corporation" "GeForce RTX 4090"\n'
            )
        ),
    )

    assert system._detect_os_gpu_name() == "NVIDIA Corporation GeForce RTX 4090"


def _npu_backend(*, name="Ascend 910B", total_gb=64.0, allocated_gb=1.5, reserved_gb=2.0):
    """A torch.npu-shaped stub: the surface ``_active_accelerator`` callers use."""
    return SimpleNamespace(
        is_available=lambda: True,
        get_device_name=lambda index: name,
        get_device_properties=lambda index: SimpleNamespace(
            total_memory=int(total_gb * 1024 ** 3)
        ),
        current_device=lambda: 0,
        memory_allocated=lambda: int(allocated_gb * 1024 ** 3),
        memory_reserved=lambda: int(reserved_gb * 1024 ** 3),
    )


def _torch_stub(**accelerators):
    """A torch-module stub carrying the attributes the router touches.

    ``backends`` is always present (like the real torch) so the MPS probe in
    ``flush_memory`` reads False instead of raising on attribute access.
    """
    return SimpleNamespace(backends=SimpleNamespace(), **accelerators)


def _cpu_only_flags(monkeypatch):
    monkeypatch.setattr(system, "_is_mac", False)
    monkeypatch.setattr(system, "_is_cuda", False)
    monkeypatch.setattr(system, "_is_xpu", False)


def test_detect_gpu_reports_ascend_npu(monkeypatch):
    _cpu_only_flags(monkeypatch)
    monkeypatch.setattr(system, "_is_npu", True)
    monkeypatch.setattr(system, "torch", _torch_stub(npu=_npu_backend()))

    assert system._detect_gpu() == ("Ascend 910B", 64.0)


def test_detect_gpu_prefers_cuda_over_npu(monkeypatch):
    monkeypatch.setattr(system, "_is_mac", False)
    monkeypatch.setattr(system, "_is_cuda", True)
    monkeypatch.setattr(system, "_is_xpu", False)
    monkeypatch.setattr(system, "_is_npu", True, raising=False)
    cuda = SimpleNamespace(
        get_device_name=lambda index: "NVIDIA RTX 4090",
        get_device_properties=lambda index: SimpleNamespace(total_memory=24 * 1024 ** 3),
    )
    monkeypatch.setattr(system, "torch", _torch_stub(cuda=cuda, npu=_npu_backend()))

    assert system._detect_gpu() == ("NVIDIA RTX 4090", 24.0)


def test_detect_gpu_cpu_host_still_falls_back_to_os_probe(monkeypatch):
    _cpu_only_flags(monkeypatch)
    monkeypatch.setattr(system, "_is_npu", False, raising=False)
    monkeypatch.setattr(system, "torch", _torch_stub())
    monkeypatch.setattr(system, "_detect_os_gpu_name", lambda: "Intel UHD Graphics 770")

    assert system._detect_gpu() == ("Intel UHD Graphics 770", 0.0)


def test_sysinfo_reports_npu_memory(monkeypatch):
    _cpu_only_flags(monkeypatch)
    monkeypatch.setattr(system, "_is_npu", True)
    monkeypatch.setattr(system, "_GPU_NAME", "Ascend 910B")
    monkeypatch.setattr(
        system, "torch", _torch_stub(npu=_npu_backend(total_gb=64.0, allocated_gb=3.0))
    )
    monkeypatch.setattr(
        system,
        "psutil",
        SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5,
            cpu_count=lambda logical=True: 8,
            cpu_freq=lambda: SimpleNamespace(current=2400.0),
            virtual_memory=lambda: SimpleNamespace(used=8 * 1024 ** 3, total=32 * 1024 ** 3),
        ),
    )

    info = system.get_sys_info()

    assert info["vram"] == 3.0
    assert info["total_vram"] == 64.0
    assert info["gpu_active"] is True


def test_flush_memory_snapshot_reads_npu(monkeypatch):
    import asyncio

    from services import model_manager

    _cpu_only_flags(monkeypatch)
    monkeypatch.setattr(system, "_is_npu", True)
    monkeypatch.setattr(
        system,
        "torch",
        _torch_stub(npu=_npu_backend(allocated_gb=0.5, reserved_gb=1.25)),
    )
    monkeypatch.setattr(model_manager, "free_vram", lambda: None)
    monkeypatch.setattr(
        system,
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(used=8 * 1024 ** 3, total=32 * 1024 ** 3)
        ),
    )

    result = asyncio.run(system.flush_memory(unload_model=False))

    assert result["vram_after"] == 0.5
    assert result["vram_reserved"] == 1.25
