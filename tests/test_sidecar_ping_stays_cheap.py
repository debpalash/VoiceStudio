"""A sidecar's ping must not import torch just to report VRAM (#2837).

``health_check`` spawns the sidecar and pings it under a 60 s deadline. Every
sidecar answered the ping with ``_measure_vram_mb()``, which imported torch, so
on a cold start the engine "Test" could time out and report the engine
unavailable although the model loaded and generated fine moments later. Until
the model is loaded nothing is allocated, so the answer is 0 without the import.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from pathlib import Path

import pytest

ENGINES = Path(__file__).resolve().parents[1] / "backend" / "engines"
SIDECARS = sorted(
    p for p in ENGINES.glob("*/main.py") if "def _measure_vram_mb" in p.read_text(encoding="utf-8")
)


class _WatchTorch(importlib.abc.MetaPathFinder):
    """Records torch import attempts (the probes swallow exceptions, so raising
    here would hide the very regression this test exists to catch)."""

    def __init__(self):
        self.attempts = []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "torch":
            self.attempts.append(name)
        return None


def test_sidecars_were_discovered():
    assert len(SIDECARS) >= 8


@pytest.mark.parametrize("script", SIDECARS, ids=[p.parent.name for p in SIDECARS])
def test_ping_vram_probe_never_imports_torch(script, monkeypatch):
    spec = importlib.util.spec_from_file_location(f"_ping_probe_{script.parent.name}", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    watcher = _WatchTorch()
    monkeypatch.setattr(sys, "meta_path", [watcher, *sys.meta_path])

    assert module._measure_vram_mb() == 0.0
    assert watcher.attempts == []


def test_failed_health_check_keeps_its_reason_in_the_local_log(monkeypatch, caplog):
    from services.subprocess_backend import SubprocessBackend

    class Broken(SubprocessBackend):
        id = "_broken"
        sample_rate = 24000
        supported_languages = ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        @classmethod
        def venv_python(cls):
            return Path(sys.executable)

        @classmethod
        def sidecar_script(cls):
            return Path("missing-sidecar.py")

    backend = Broken()
    monkeypatch.setattr(backend, "_spawn", lambda: (_ for _ in ()).throw(RuntimeError("boom ready")))
    with caplog.at_level("WARNING"):
        ok, message = backend.health_check()
    assert not ok and "boom ready" in message
    assert any("[_broken] health check failed" in r.getMessage() and "boom ready" in r.getMessage()
               for r in caplog.records)
