"""Completed GPU work, rather than enumeration alone, determines readiness."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def failed_gpu(monkeypatch):
    from core import gpu_self_test

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setattr(gpu_self_test, "_thread", None)
    monkeypatch.setattr(
        gpu_self_test, "_verdict",
        gpu_self_test.Verdict("failed", "RuntimeError: HIPBLAS_STATUS_ALLOC_FAILED"),
    )


@pytest.mark.parametrize("family", ["cuda", "rocm"])
def test_completed_self_test_failure_matches_actual_parent_loader(monkeypatch, failed_gpu, family):
    from core import device_caps
    from services import model_manager
    from services.engine_routing import resolve_routing

    caps = device_caps.HostCaps(family=family, available_families=(family, "cpu"))
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(device_caps, "arch_unsupported", lambda _: None)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(hip=None),
    ))
    assert model_manager.get_best_device() == "cpu"

    route = resolve_routing((family, "cpu"), caps)
    assert route["effective_device"] == "cpu"
    assert route["routing_status"] == "cpu_fallback"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in route["routing_reason"]


@pytest.mark.parametrize("vendor,backend", [("amd", "rocm"), ("nvidia", "cuda"), ("unknown", "cuda")])
@pytest.mark.parametrize("forced", [False, True])
def test_preflight_does_not_call_failed_gpu_ready(monkeypatch, tmp_path, failed_gpu, vendor, backend, forced):
    from api.routers.setup import wizard
    from services import media_tools

    if forced:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 32)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _: 100)
    monkeypatch.setattr(wizard, "hf_cache_dir", lambda: str(tmp_path / "hf"))
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "status": "pass", "detail": "Local test", "fix": None,
    })
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": vendor, "device_name": "Test GPU", "driver": "600.0",
        "backend": backend, "available": True, "notes": [],
    })
    monkeypatch.setattr(media_tools, "summary", lambda **_: {"ready": True})
    monkeypatch.setattr("services.tts_backend.gpu_routing_verdict", lambda: None)

    row = next(c for c in wizard.preflight()["checks"] if c["id"] == "gpu")
    assert row["status"] == "warn"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in row["detail"]
    assert ("explicitly forced" if forced else "running on CPU") in row["detail"]
    assert "ready" not in row["detail"]


@pytest.mark.parametrize("family", ["cuda", "rocm"])
def test_failed_parent_torch_does_not_downgrade_independent_runtime(failed_gpu, family):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps(family=family, available_families=(family, "cpu"))
    route = resolve_routing((family, "cpu"), caps, parent_torch=False)
    assert route["effective_device"] == family
    assert route["routing_status"] == "accelerated"


@pytest.mark.parametrize("status", [None, "passed", "inconclusive", "skipped", "no_gpu"])
def test_readiness_does_not_wait_or_start_another_gpu_test(monkeypatch, status):
    from core import gpu_self_test
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    class PendingTest:
        def join(self, *args):
            pytest.fail("readiness must never wait for a GPU self-test")

    monkeypatch.setattr(gpu_self_test, "_thread", PendingTest())
    monkeypatch.setattr(gpu_self_test, "_verdict", None if status is None else gpu_self_test.Verdict(status))
    monkeypatch.setattr(gpu_self_test, "_run_child", lambda: pytest.fail("readiness must not launch GPU work"))
    route = resolve_routing(("cuda", "cpu"), HostCaps("cuda", ("cuda", "cpu")))
    assert route["routing_status"] == "accelerated"
    assert route["routing_reason"] is None


def test_force_cuda_preserves_explicit_gpu_choice(monkeypatch, failed_gpu):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    route = resolve_routing(("cuda", "cpu"), HostCaps("cuda", ("cuda", "cpu")))
    assert route["routing_status"] == "accelerated"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in route["routing_reason"]


def test_gpu_only_parent_engine_explains_failed_work(failed_gpu):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    route = resolve_routing(("cuda",), HostCaps("cuda", ("cuda", "cpu")))
    assert route["routing_status"] == "unavailable"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in (route["routing_reason"] or "")


def test_same_interpreter_child_does_not_inherit_parent_verdict(failed_gpu):
    from core.device_caps import HostCaps
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend

    caps = HostCaps("cuda", ("cuda", "cpu"))
    assert OmniVoiceSubprocessBackend.shares_parent_torch() is True
    route = OmniVoiceSubprocessBackend.runtime_compute_profile(caps)
    assert route["routing_status"] == "accelerated"
    assert route["effective_device"] == "cuda"


def test_completed_verdict_updates_routing_without_refreshing_host_caps(monkeypatch):
    from core import gpu_self_test
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps("cuda", ("cuda", "cpu"))
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setattr(gpu_self_test, "_verdict", None)
    assert resolve_routing(("cuda", "cpu"), caps)["routing_status"] == "accelerated"
    monkeypatch.setattr(gpu_self_test, "_verdict", gpu_self_test.Verdict("failed", "GPU test failure"))
    assert resolve_routing(("cuda", "cpu"), caps)["routing_status"] == "cpu_fallback"

