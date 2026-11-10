"""The shared ``cpu_host`` fixture makes native OmniVoice stubs host-independent (#3695).

On Apple Silicon the ``omnivoice`` id resolves to the crash-isolated MPS
sidecar, which never sees a test's in-process model stub.
"""
import importlib

import pytest


@pytest.fixture
def apple_silicon(monkeypatch):
    device_caps = importlib.import_module("core.device_caps")
    caps = device_caps.HostCaps(family="mps", available_families=("mps", "cpu"))
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)


def test_apple_silicon_routes_omnivoice_to_the_sidecar(apple_silicon):
    tts_backend = importlib.import_module("services.tts_backend")
    assert tts_backend.get_backend_class("omnivoice").__name__ == "OmniVoiceMPSSubprocessBackend"


def test_cpu_host_keeps_native_omnivoice_in_process(apple_silicon, cpu_host):
    tts_backend = importlib.import_module("services.tts_backend")
    assert tts_backend.get_backend_class("omnivoice").__name__ == "OmniVoiceBackend"
    assert importlib.import_module("core.device_caps").detect_host_caps() is cpu_host
