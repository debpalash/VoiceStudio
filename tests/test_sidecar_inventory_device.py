"""Resident sidecars must report their engine's compute contract."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("family", ["cuda", "mps"])
@pytest.mark.parametrize("cpu_only", [True, False])
def test_loaded_sidecar_device_respects_engine_compatibility(monkeypatch, family, cpu_only):
    from core import device_caps
    from engines.supertonic3.backend import Supertonic3Backend
    from services import model_lifecycle as lifecycle, subprocess_backend as sidecars

    # Supertonic uses CPU ONNX Runtime even when the host exposes a GPU.
    backend_cls = Supertonic3Backend if cpu_only else type(
        "AcceleratedSidecar", (Supertonic3Backend,), {"gpu_compat": (family, "cpu")},
    )
    backend = backend_cls()
    backend._proc = SimpleNamespace(pid=12345, poll=lambda: None)
    monkeypatch.setattr(sidecars, "_LIVE_BACKENDS", [backend])
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: device_caps.HostCaps(
        family=family, available_families=(family, "cpu"),
    ))
    monkeypatch.setattr(lifecycle, "get_best_device", lambda: family)
    monkeypatch.setattr(lifecycle.mm, "model", None)
    monkeypatch.setattr(lifecycle.mm, "_diar_pipeline", None)
    try:
        rows = lifecycle.list_loaded()["models"]
        row = next(row for row in rows if row["id"] == f"sidecar:{backend.id}")
        assert row["device"] == ("cpu" if cpu_only else family)
    finally:
        backend._proc = None


def test_live_sidecar_stays_visible_when_compute_profile_fails(monkeypatch):
    from core import device_caps
    from services import subprocess_backend as sidecars

    def broken(caps):
        raise RuntimeError("device probe unavailable")

    backend = SimpleNamespace(
        id="unavailable-probe", _proc=SimpleNamespace(pid=12345, poll=lambda: None),
        idle_seconds=lambda: 0, runtime_compute_profile=broken,
    )
    monkeypatch.setattr(sidecars, "_LIVE_BACKENDS", [backend])
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: device_caps.HostCaps(
        family="cpu", available_families=("cpu",),
    ))
    row, = sidecars.list_live_sidecars()
    assert row["id"] == backend.id
    assert row["device"] == "unknown"
