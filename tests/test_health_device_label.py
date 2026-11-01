"""``/health`` names the device speech is generated on, not just torch's.

Native sidecars choose their own accelerator: CosyVoice GGUF runs on Vulkan
while this process's PyTorch has no GPU at all, and ``/health`` used to report
"cpu" for it. Remote clients read that label to decide whether a slow job is a
cold start or a host without acceleration, so it has to name the engine's real
device (vulkan, cuda, rocm, xpu, mps or cpu) and the engine it belongs to, and
the two must always describe the same engine.

Fail-before: the probe asked torch only, so the Vulkan case answered "cpu" and
the body carried no ``engine``.
"""

import asyncio
import importlib
import os
import sys
import threading
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)


@pytest.fixture()
def main(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    module = importlib.import_module("main")
    monkeypatch.setattr(module, "_health_label", None, raising=False)
    monkeypatch.setattr(module, "_health_device_at", 0.0, raising=False)
    monkeypatch.setattr(module, "_health_device_thread", None, raising=False)
    # torch on this host is irrelevant to every engine-profile case below.
    monkeypatch.setattr(module, "_torch_device_label", lambda: "torch-fallback")
    return module


def _caps(family="cpu", name=""):
    from core import device_caps

    return device_caps.HostCaps(
        family=family, available_families=(family, "cpu"), device_name=name
    )


@pytest.fixture()
def engine(monkeypatch):
    """A valid baseline: the active engine runs on Vulkan with a named GPU."""
    from core import device_caps
    from services import engine_routing, tts_backend

    state = {
        "id": "cosyvoice-gguf",
        "caps": _caps(),
        "profile": {
            "effective_device": "vulkan",
            "routing_status": "ok",
            "runtime_device_name": "AMD Radeon RX 6700 XT",
        },
        "profiled": [],
    }
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: state["id"])
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda backend_id: backend_id)

    def _profile(backend, caps):
        state["profiled"].append(backend)
        if isinstance(state["profile"], Exception):
            raise state["profile"]
        return state["profile"]

    monkeypatch.setattr(engine_routing, "runtime_compute_profile", _profile)
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: state["caps"])
    return state


def _resolved_body(main):
    main._resolve_health_device()
    return asyncio.run(main.health())


def test_a_vulkan_sidecar_is_reported_as_vulkan_not_cpu(main, engine):
    body = _resolved_body(main)
    assert body["device"] == "vulkan (AMD Radeon RX 6700 XT)"
    assert body["engine"] == "cosyvoice-gguf"


def test_an_engine_on_cpu_says_cpu_even_when_torch_has_a_gpu(main, engine):
    engine["profile"] = {**engine["profile"], "effective_device": "cpu"}
    assert _resolved_body(main)["device"] == "cpu"


def test_a_torch_engine_on_rocm_takes_the_host_gpu_name(main, engine):
    engine["caps"] = _caps("rocm", "AMD Radeon PRO W7900")
    engine["profile"] = {
        "effective_device": "rocm",
        "routing_status": "ok",
        "runtime_device_name": None,
    }
    assert _resolved_body(main)["device"] == "rocm (AMD Radeon PRO W7900)"


def test_an_accelerator_without_a_known_name_is_reported_bare(main, engine):
    engine["profile"] = {**engine["profile"], "runtime_device_name": None}
    assert _resolved_body(main)["device"] == "vulkan"


def test_an_unavailable_engine_falls_back_to_the_torch_device(main, engine):
    engine["profile"] = {**engine["profile"], "routing_status": "unavailable"}
    assert _resolved_body(main)["device"] == "torch-fallback"


def test_a_failing_engine_profile_falls_back_to_the_torch_device(main, engine):
    engine["profile"] = RuntimeError("native probe crashed")
    body = _resolved_body(main)
    assert body["status"] == "ok"
    assert body["device"] == "torch-fallback"


def test_the_device_is_resolved_for_the_engine_that_is_reported(main, engine, monkeypatch):
    # The active engine changes between two reads (a Settings switch racing
    # the resolver). Reading it once keeps the reported pair consistent.
    from services import tts_backend

    switching = iter(["cosyvoice-gguf", "omnivoice"])
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: next(switching))
    body = _resolved_body(main)
    assert engine["profiled"] == [body["engine"]]


def test_a_stale_label_is_refreshed_after_an_engine_switch(main, engine, monkeypatch):
    _resolved_body(main)
    release = threading.Event()
    real_probe = main._probe_health_device

    def _slow_probe(engine_id):
        release.wait(10)
        return real_probe(engine_id)

    monkeypatch.setattr(main, "_probe_health_device", _slow_probe)
    engine["id"] = "omnivoice"
    engine["profile"] = {"effective_device": "cpu", "routing_status": "ok"}
    main._health_device_at -= main._HEALTH_DEVICE_TTL_S + 1

    served = asyncio.run(main.health())
    # While the new engine's device is still being probed, the old pair is
    # served whole: never the new engine with the old engine's device.
    assert (served["engine"], served["device"]) == (
        "cosyvoice-gguf",
        "vulkan (AMD Radeon RX 6700 XT)",
    )
    release.set()
    main._health_device_thread.join(timeout=10)
    refreshed = asyncio.run(main.health())
    assert (refreshed["engine"], refreshed["device"]) == ("omnivoice", "cpu")


def test_a_fresh_label_never_starts_another_resolver(main, engine):
    _resolved_body(main)
    asyncio.run(main.health())
    assert main._health_device_thread is None


@pytest.mark.parametrize(
    ("hip", "expected"),
    [(None, "cuda (Test GPU)"), ("6.4.0", "rocm (Test GPU)")],
)
def test_the_torch_fallback_tells_rocm_from_cuda(monkeypatch, tmp_path, hip, expected):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    module = importlib.import_module("main")
    fake = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda i: "Test GPU"),
        version=types.SimpleNamespace(hip=hip),
        backends=types.SimpleNamespace(),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert module._torch_device_label() == expected
