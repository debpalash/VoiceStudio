"""Unit tests for whisper.cpp ASR backend with Vulkan support (Phase 3)."""
from __future__ import annotations

pytest_plugins = ["whisper_cpp_fixtures"]


def test_autodetect_keeps_default_without_verified_native_gpu(monkeypatch, m):
    monkeypatch.setattr(m.ab, "_mps_available", lambda: False)
    monkeypatch.setattr(m.ab, "_cuda_reported_available", lambda: False)

    # Stub probe_available: whisper-cpp is available, whisperx is available
    monkeypatch.setattr(
        m.ab,
        "_probe_available",
        lambda cls: cls.id in ("whisper-cpp", "whisperx", "faster-whisper"),
    )

    assert m.ab._auto_detect() == "whisperx"

def test_autodetect_falls_back_when_whisper_cpp_unavailable(monkeypatch, m):
    monkeypatch.setattr(m.ab, "_mps_available", lambda: False)
    monkeypatch.setattr(m.ab, "_cuda_reported_available", lambda: False)

    # whisper-cpp is not available, whisperx is available
    monkeypatch.setattr(
        m.ab,
        "_probe_available",
        lambda cls: cls.id in ("whisperx", "faster-whisper"),
    )

    assert m.ab._auto_detect() == "whisperx"

def test_autodetect_prefers_cuda_whisperx_on_nvidia_host(monkeypatch, m):
    monkeypatch.setattr(m.ab, "_mps_available", lambda: False)
    monkeypatch.setattr(m.ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(m.ab, "_rocm_torch", lambda: False)

    # Both whisperx and whisper-cpp are available
    monkeypatch.setattr(
        m.ab,
        "_probe_available",
        lambda cls: cls.id in ("whisper-cpp", "whisperx", "faster-whisper"),
    )

    assert m.ab._auto_detect() == "whisperx"
