"""Keep dynamic HIP compatibility alongside native executable provider hooks."""

import pytest

from core.device_caps import HostCaps
from services.engine_routing import runtime_compute_profile


class DynamicEngine:
    gpu_compat = ("cuda", "cpu")

    @classmethod
    def runtime_gpu_compat(cls):
        return ("cuda", "rocm", "cpu")


def test_dynamic_hip_compatibility_is_used_by_shared_profile():
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))

    profile = runtime_compute_profile(DynamicEngine, caps)

    assert profile["effective_device"] == "rocm"
    assert profile["routing_status"] == "accelerated"
    assert profile["gpu_compat"] == ("cuda", "rocm", "cpu")


def test_explicit_cpu_retains_dynamic_compatibility():
    caps = HostCaps(family="cpu", available_families=("rocm", "cpu"), requested_family="cpu")

    profile = runtime_compute_profile(DynamicEngine, caps)

    assert profile["effective_device"] == "cpu"
    assert "rocm" in profile["gpu_compat"]


@pytest.mark.parametrize("family", ["cuda", "cpu", "mps"])
def test_static_engines_keep_existing_routing(family):
    class StaticEngine:
        gpu_compat = ("cuda", "mps", "cpu")

    caps = HostCaps(family=family, available_families=(family, "cpu"))

    profile = runtime_compute_profile(StaticEngine, caps)

    assert profile["effective_device"] == family
    assert profile["gpu_compat"] == StaticEngine.gpu_compat


def test_native_executable_profile_remains_authoritative():
    expected = {"gpu_compat": ("vulkan",), "effective_device": "rocm", "runtime_backend": "vulkan"}

    class NativeEngine:
        @classmethod
        def runtime_compute_profile(cls, caps):
            return expected

        @classmethod
        def runtime_gpu_compat(cls):
            pytest.fail("native provider profile must remain authoritative")

    assert runtime_compute_profile(NativeEngine, HostCaps(family="cpu", available_families=("cpu",))) is expected


def test_faster_whisper_inventory_preserves_dynamic_radeon_profile(monkeypatch):
    from core import device_caps
    from core.windows_rocm import ct2
    from services import asr_backend

    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(ct2, "gpu_compatible", lambda: ("cuda", "rocm", "cpu"))
    monkeypatch.setattr(asr_backend.FasterWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(asr_backend, "_REGISTRY", {"faster-whisper": asr_backend.FasterWhisperBackend})
    for name in ("_DEEP_IMPORT_BROKEN", "_LAST_ERRORS", "_RUNTIME_EVIDENCE", "_RUNTIME_INSTANCES", "_ISOLATED_INSTANCES"):
        monkeypatch.setattr(asr_backend, name, {})

    rows = asr_backend.list_backends()

    assert len(rows) == 1
    assert rows[0]["effective_device"] == "rocm"
    assert rows[0]["routing_status"] == "accelerated"
    assert "rocm" in rows[0]["gpu_compat"]
