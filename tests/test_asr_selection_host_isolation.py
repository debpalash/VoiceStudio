"""ASR selection tests must not inherit the hardware of the machine running them (#3549).

A selection test that pins only some predicates passed on a plain runner and
failed on Apple Silicon or a Windows ROCm install, where ``_auto_detect``
answered from the real MPS/HIP state before the test's stubs mattered.
"""
import importlib

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]

_PREDICATES = ("_mps_available", "_cuda_reported_available", "_rocm_torch")


@pytest.fixture
def every_accelerator_host(monkeypatch):
    """Apple Silicon, CUDA, ROCm Torch and a verified Windows HIP install at once,
    plus a warm transcription engine left behind by an earlier test."""
    hip = importlib.import_module("core.windows_rocm.ct2")
    ab = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(hip, "windows_hip_installed", lambda: True)
    monkeypatch.setattr(hip, "hip_ct2_available", lambda: True)
    for name in _PREDICATES:
        monkeypatch.setattr(ab, name, lambda: True)
    monkeypatch.setattr(ab, "_transcribe_backend", object())
    monkeypatch.setattr(ab, "_transcribe_backend_key", ("stale",))
    monkeypatch.setattr(ab, "_transcribe_last_used", 0.0)


def test_shared_fixture_starts_from_a_cpu_only_host(every_accelerator_host, m, monkeypatch):
    monkeypatch.setattr(m.ab, "_probe_available", lambda cls: True)
    assert m.ab._auto_detect() == "whisperx"
    hip = importlib.import_module("core.windows_rocm.ct2")
    assert not hip.windows_hip_installed() and not hip.hip_ct2_available()
    assert m.ab.warm_transcription_backend() is None


def test_a_test_still_opts_in_to_its_hardware(m, monkeypatch):
    monkeypatch.setattr(m.ab, "_probe_available", lambda cls: True)
    monkeypatch.setattr(m.ab, "_mps_available", lambda: True)
    assert m.ab._auto_detect() == "mlx-whisper"
