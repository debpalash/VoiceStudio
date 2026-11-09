"""Preset validation contains native imports and fails closed on worker failure."""
import builtins
import importlib
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def probe(monkeypatch):
    module = importlib.import_module("services.performance_probe")
    monkeypatch.setattr(module, "_CACHE", {})
    monkeypatch.setattr(module, "_PACKAGES", ())
    return module


def fake_child(monkeypatch, probe, *, payload=None, code=0, timeout=False):
    """Install a deterministic child transport without loading native engines."""
    observed = {}

    class Child:
        killed = False

        def wait(self, timeout=None):
            """Simulate child exit or a timeout until the fake child is killed."""
            observed["wait_timeout"] = timeout
            if observed.get("timeout") and not self.killed:
                raise subprocess.TimeoutExpired("probe", timeout)
            return code

        def kill(self):
            self.killed = True

    child = Child()

    def spawn(argv, **kwargs):
        """Record child launch options and optionally write its readiness result."""
        observed.update(argv=argv, kwargs=kwargs, timeout=timeout)
        if payload is not None:
            Path(argv[-1]).write_text(payload, encoding="utf-8")
        return child

    monkeypatch.setattr(probe, "spawn_owned", spawn)
    return child, observed


def result(**changes):
    """Return one matching worker response with optional corrupt fields."""
    data = dict(schema=1, family="asr", requested="faster-whisper",
                engine="faster-whisper", ready=True)
    return json.dumps(dict(data, **changes))


@pytest.mark.parametrize("payload,code,timeout", [
    (None, 0, False), ("not JSON", 0, False), ("[]", 0, False),
    (result(ready="true"), 0, False), (result(family="tts"), 0, False),
    (result(requested="other"), 0, False), (result(engine="other"), 0, False),
    (result(), 3, False), (result(), 0, True), (" " * 4097, 0, False),
])
def test_invalid_or_failed_worker_cannot_authorize_selection(monkeypatch, probe, payload, code, timeout):
    child, _ = fake_child(monkeypatch, probe, payload=payload, code=code, timeout=timeout)
    assert not probe.selection_ready("asr", "faster-whisper")
    assert child.killed == timeout
    monkeypatch.setattr(probe, "spawn_owned", lambda *a, **kw: pytest.fail("Failed probe respawned"))
    assert probe._query("asr", "faster-whisper") is None


def test_ready_worker_is_offline_bounded_and_does_not_import_parent_runtime(monkeypatch, probe):
    """Keep readiness imports in an offline child with a bounded wait."""
    _, observed = fake_child(monkeypatch, probe, payload=result())
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        """Reject heavy runtime imports in the parent while delegating other imports."""
        if name.split(".")[0] in {"transformers", "faster_whisper", "ctranslate2", "omnivoice"}:
            pytest.fail("Runtime imported in parent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert probe.selection_ready("asr", "faster-whisper")
    kwargs = observed["kwargs"]
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
    assert kwargs["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert observed["argv"][1].endswith("_performance_probe.py")
    assert observed["timeout"] is False
    assert observed["wait_timeout"] == 120


def test_explicit_engine_does_not_probe_and_unresolved_auto_is_honest(monkeypatch, probe):
    prefs = importlib.import_module("core.prefs")
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "get", lambda key: "pytorch-whisper")
    monkeypatch.setattr(probe, "_query", lambda *args: pytest.fail("Explicit pin probed"))
    assert probe.active_asr_selection() == "pytorch-whisper"
    monkeypatch.setattr(prefs, "get", lambda key: None)
    monkeypatch.setattr(probe, "_query", lambda *args: None)
    assert probe.active_asr_selection() == "auto"


@pytest.mark.parametrize("ready", [True, False, None])
def test_readiness_cache_preserves_results_and_expires(monkeypatch, probe, ready):
    """Bound repeated success, rejection and timeout checks without hiding later repair."""
    child, _ = fake_child(monkeypatch, probe, payload=result(ready=ready), timeout=ready is None)
    clock = [100.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    first = probe._query("asr", "faster-whisper")
    assert first == (None if ready is None else json.loads(result(ready=ready)))
    assert child.killed == (ready is None)
    monkeypatch.setattr(probe, "spawn_owned", lambda *a, **kw: pytest.fail("Warm probe respawned"))
    assert probe._query("asr", "faster-whisper") == first
    if first is not None:
        first["ready"] = not ready
        assert probe.selection_ready("asr", "faster-whisper") == ready
    clock[0] += 31
    fake_child(monkeypatch, probe, payload=result())
    assert probe.selection_ready("asr", "faster-whisper")


@pytest.mark.parametrize("ready", [True, False, None])
@pytest.mark.parametrize("change", ["device", "package", "broken", "interpreter"])
def test_cache_matches_device_packages_and_parent_failures(monkeypatch, probe, tmp_path, change, ready):
    """Invalidate cached readiness when device, packages, failures, or Python change."""
    from types import SimpleNamespace

    version = ["1.0"]
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})
    monkeypatch.setattr(probe, "_PACKAGES", ("torch",))
    monkeypatch.setattr(probe.metadata, "distribution", lambda name: SimpleNamespace(
        version=version[0], locate_file=lambda name: tmp_path))
    fake_child(monkeypatch, probe, payload=result(ready=ready), timeout=ready is None)
    assert probe.selection_ready("asr", "faster-whisper") == bool(ready)
    if change == "device":
        monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    elif change == "package":
        version[0] = "2.0"
    elif change == "broken":
        asr._DEEP_IMPORT_BROKEN["faster-whisper"] = "unloadable"
    else:
        monkeypatch.setattr(probe.sys, "executable", "different-python")
    _, observed = fake_child(monkeypatch, probe, payload=result(ready=not ready))
    assert probe.selection_ready("asr", "faster-whisper") == (not ready)
    if change == "broken":
        assert json.loads(observed["argv"][-2]) == ["faster-whisper"]


def test_failed_probe_cache_is_bounded(monkeypatch, probe):
    """Retire negative identities instead of growing memory indefinitely."""
    fake_child(monkeypatch, probe, code=3)
    for index in range(33):
        assert probe._query("asr", f"missing-{index}") is None
    fake_child(monkeypatch, probe, payload=result())
    assert probe.selection_ready("asr", "faster-whisper")
    assert len(probe._CACHE) <= 32
