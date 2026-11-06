"""ROCm sidecar provisioning must respect the user's exact index."""
import pytest
from services import sidecar_install as si


@pytest.mark.parametrize(
    ("url", "expected_tag"),
    [("https://mirror.example/whl/rocm6.4", "rocm7.2"),
     ("https://mirror.example/simple", "rocm7.2")],
)
def test_custom_index_is_not_rewritten_and_requires_hip_wheel(monkeypatch, url, expected_tag):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.setenv("OMNIVOICE_TORCH_INDEX", url)
    args = si._spec_rocm_torch_args(si.get_spec("voxcpm2"))
    assert args[args.index("--extra-index-url") + 1] == url
    assert f"torch==2.11.0+{expected_tag}" in args
    assert f"torchaudio==2.11.0+{expected_tag}" in args


def test_shared_stack_mirror_without_named_tail_requires_hip_wheel(monkeypatch):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.setenv("OMNIVOICE_TORCH_INDEX", "https://mirror.example/simple")
    args = si._spec_rocm_torch_args(si.get_spec("indextts2"))
    assert "torch==2.8.0+rocm6.4" in args
    assert "torchaudio==2.8.0+rocm6.4" in args


@pytest.mark.parametrize("engine", ["cosyvoice", "moss-tts-nano", "confucius4-tts"])
def test_custom_mirror_does_not_override_engine_rocm_version(monkeypatch, engine):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.setenv("OMNIVOICE_TORCH_INDEX", "https://mirror.example/whl/rocm6.4")
    args = si._spec_rocm_torch_args(si.get_spec(engine))
    assert "torch==2.7.0+rocm6.3" in args
    assert "torchaudio==2.7.0+rocm6.3" in args
    assert args[args.index("--extra-index-url") + 1] == "https://mirror.example/whl/rocm6.4"


def test_rocm_sidecar_preflight_accounts_for_large_torch_wheel(monkeypatch, tmp_path):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.delenv("OMNIVOICE_TORCH_INDEX", raising=False)
    monkeypatch.setattr(si, "managed_root", lambda spec: tmp_path)
    monkeypatch.setattr(si, "managed_checkout", lambda spec: tmp_path / "new")
    monkeypatch.setattr(si, "disk_free_bytes", lambda path: 15 * 1024**3)
    assert "Not enough disk space" in si.disk_space_error(si.get_spec("voxcpm2"))


@pytest.mark.parametrize(("version", "gib"), [
    ("2.7.0", 26), ("2.8.0", 15), ("2.11.0", 19), (None, 26),
])
def test_rocm_preflight_uses_the_selected_wheel_footprint(version, gib):
    from services.rocm_sidecar_index import rocm_minimum_install_bytes
    assert rocm_minimum_install_bytes(10 * 1024**3, 2 * 1024**3, version) == (8 + gib) * 1024**3


def test_voxcpm2_preflight_does_not_charge_for_a_larger_torch_wheel(monkeypatch, tmp_path):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.delenv("OMNIVOICE_TORCH_INDEX", raising=False)
    monkeypatch.setattr(si, "managed_root", lambda spec: tmp_path)
    monkeypatch.setattr(si, "managed_checkout", lambda spec: tmp_path / "new")
    monkeypatch.setattr(si, "disk_free_bytes", lambda path: 35 * 1024**3)
    assert si.disk_space_error(si.get_spec("voxcpm2")) is None


def test_remote_gpt_sovits_does_not_claim_unverified_rocm():
    from services.tts_backend import GPTSoVITSBackend
    assert "rocm" not in GPTSoVITSBackend.gpu_compat


def test_nemo_and_funasr_only_claim_rocm_for_non_windows_hosts():
    import os
    from services.asr_backend import FunASRBackend, NeMoASRBackend
    for engine in (FunASRBackend, NeMoASRBackend):
        assert ("rocm" in engine.gpu_compat) == (os.name != "nt")


def test_unresolved_sidecar_does_not_advertise_acceleration(monkeypatch):
    from core.device_caps import HostCaps
    from engines.confucius4 import Confucius4Backend
    monkeypatch.setattr(Confucius4Backend, "sidecar_python_hint", classmethod(lambda cls: None))
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"), vram_gb=12.0)
    result = Confucius4Backend.runtime_compute_profile(caps)
    assert result["effective_device"] == "unknown"
    assert result["routing_status"] == "unknown"


def test_unknown_route_gets_conservative_cpu_budget(monkeypatch):
    from core import device_caps
    from services import engine_routing, model_manager, tts_backend
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: object())
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda _: object())
    monkeypatch.setattr(engine_routing, "runtime_compute_profile", lambda *_: {"effective_device": "unknown"})
    assert model_manager._local_route_is_cpu("test") is True


def test_dots_preflight_reserves_rocm_space_before_source_download(monkeypatch, tmp_path):
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    monkeypatch.delenv("OMNIVOICE_TORCH_INDEX", raising=False)
    monkeypatch.setattr(si, "managed_root", lambda spec: tmp_path)
    monkeypatch.setattr(si, "managed_checkout", lambda spec: tmp_path / "not-fetched")
    monkeypatch.setattr(si, "disk_free_bytes", lambda path: 25 * 1024**3)
    assert "Not enough disk space" in si.disk_space_error(si.get_spec("dots-tts"))


@pytest.mark.parametrize(("universal", "cpu_explicit"), [(False, False), (False, True), (True, False), (True, True)])
def test_unknown_device_gets_server_cpu_timeout(monkeypatch, universal, cpu_explicit):
    from types import SimpleNamespace
    from core import device_caps
    from services import model_manager as mm
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: SimpleNamespace(family="rocm"))
    monkeypatch.setattr(mm, "_explicit_budget_flags", lambda: (universal, cpu_explicit))
    monkeypatch.setattr(mm, "CPU_JOB_TIMEOUT_S", 900.0)
    monkeypatch.setattr(mm, "GPU_JOB_TIMEOUT_S", 300.0)
    cpu = mm.generate_timeout_s("long sample" * 100, execution_device="cpu")
    unknown = mm.generate_timeout_s("long sample" * 100, execution_device="unknown")
    assert unknown == cpu


def test_gpu_report_does_not_describe_unknown_sidecar_as_no_gpu():
    from core.gpu_report import _engine_entry
    row = {"id": "confucius4-tts", "available": True, "gpu_compat": ["rocm", "cpu"],
           "effective_device": "unknown", "routing_status": "unknown"}
    assert _engine_entry(row, "tts", "accelerated")["code"] == "unknown"
