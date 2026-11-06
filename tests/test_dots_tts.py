"""Tests for the dots.tts engine (issue #498).

dots.tts runs in a dedicated subprocess venv (transformers==4.57.0), so
these tests never import dots.tts itself. They exercise the parent-side
wiring that ships in the default install: registry resolution, subprocess
isolation, the Windows-unsupported gate (cross-platform parity rule),
hardware honesty, and the generate() kwarg arbitration. No network, no
optional deps, no subprocess spawn.
"""
from __future__ import annotations

import importlib
import sys

import pytest


# ── registry wiring ────────────────────────────────────────────────────────


def test_registry_contains_dots_tts():
    from services.tts_backend import _REGISTRY, get_backend_class

    assert "dots-tts" in _REGISTRY, (
        "_REGISTRY is missing 'dots-tts'; check _LAZY_REGISTRY in "
        "services/tts_backend.py"
    )
    cls = _REGISTRY["dots-tts"]
    assert cls.__name__ == "DotsTTSBackend"
    assert get_backend_class("dots-tts") is cls
    assert getattr(cls, "_is_subprocess_isolated", False), (
        "DotsTTSBackend should be subprocess-isolated"
    )
    for name in ("is_available", "generate", "sample_rate", "supported_languages"):
        assert hasattr(cls, name), f"DotsTTSBackend missing {name!r}"


def test_pep562_lazy_import():
    mod = importlib.import_module("services.tts_backend")
    cls = mod._REGISTRY["dots-tts"]
    assert cls.__name__ == "DotsTTSBackend"


def test_install_hint_present():
    from services.tts_backend import _INSTALL_HINTS

    hint = _INSTALL_HINTS.get("dots-tts", "")
    assert "OMNIVOICE_DOTS_TTS_DIR" in hint
    assert "rednote-hilab" in hint


def test_sidecar_script_ships():
    from engines.dots_tts.bootstrap import DOTS_TTS_SIDECAR_SCRIPT

    assert DOTS_TTS_SIDECAR_SCRIPT.name == "main.py"
    assert DOTS_TTS_SIDECAR_SCRIPT.is_file()


# ── hardware + platform honesty ────────────────────────────────────────────


def test_gpu_compat_cuda_rocm_cpu_no_mps():
    from engines.dots_tts import DotsTTSBackend

    # ROCm through a HIP torch in the sidecar venv; routing drops the claim
    # when the venv carries a CUDA/CPU wheel (SubprocessBackend).
    assert DotsTTSBackend.gpu_compat == ("cuda", "rocm", "cpu")


def test_sample_rate_is_48k():
    from engines.dots_tts import DotsTTSBackend

    b = DotsTTSBackend()
    assert b.sample_rate == 48000
    assert b.supported_languages == ["multi"]


def test_windows_is_gated_off(monkeypatch):
    """Cross-platform rule: dots.tts upstream is Linux/macOS-only, so on
    Windows is_available() must refuse cleanly (not advertise a dead engine)."""
    monkeypatch.setattr(sys, "platform", "win32")
    from engines.dots_tts import DotsTTSBackend

    ok, msg = DotsTTSBackend.is_available()
    assert ok is False
    assert "Windows" in msg
    assert "mps" not in msg.lower()


def test_is_available_not_installed_is_honest(monkeypatch):
    """On a supported OS without the venv, gate cleanly with an actionable
    hint and never claim MPS."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "engines.dots_tts.bootstrap.is_dots_tts_installed",
        lambda: False,
    )
    from engines.dots_tts import DotsTTSBackend

    ok, msg = DotsTTSBackend.is_available()
    assert ok is False
    assert "OMNIVOICE_DOTS_TTS_DIR" in msg
    assert "docs/engines/dots-tts.md" in msg  # actionable pointer
    # No-MPS honesty is enforced by gpu_compat; the message may disclaim MPS.


# ── generate() parent-side arbitration ─────────────────────────────────────


def test_clone_with_transcript_and_overrides(monkeypatch):
    """ref_audio+ref_text → continuation cloning; num_step/guidance forwarded."""
    captured: dict = {}

    def fake_super_generate(self, text, **kw):
        import torch
        captured["text"] = text
        captured.update(kw)
        return torch.zeros(1, 8)

    from engines.dots_tts import DotsTTSBackend
    # Patch the exact SubprocessBackend in this backend's MRO (not via a
    # module-path string): survives the sys.modules['services.*'] reloads
    # other tests perform, so super().generate() hits the fake instead of
    # dispatching to the real class and trying to spawn a sidecar. (#498)
    _sub = next(c for c in DotsTTSBackend.__mro__ if c.__name__ == "SubprocessBackend")
    monkeypatch.setattr(_sub, "generate", fake_super_generate)

    DotsTTSBackend().generate(
        "speak this",
        ref_audio="/tmp/ref.wav",
        ref_text="the reference transcript",
        language="en",
        num_step=20,
        guidance_scale=1.5,
    )
    assert captured["ref_audio"] == "/tmp/ref.wav"
    assert captured["ref_text"] == "the reference transcript"
    assert captured["language"] == "en"
    assert captured["num_steps"] == 20
    assert captured["guidance_scale"] == 1.5


def test_orphan_ref_text_dropped_and_dots_defaults(monkeypatch):
    """ref_text without ref_audio is dropped (upstream would raise), and the
    dots-appropriate defaults (num_steps=10, guidance=1.2) apply."""
    captured: dict = {}

    def fake_super_generate(self, text, **kw):
        import torch
        captured.update(kw)
        return torch.zeros(1, 8)

    from engines.dots_tts import DotsTTSBackend
    # Patch the exact SubprocessBackend in this backend's MRO (not via a
    # module-path string): survives the sys.modules['services.*'] reloads
    # other tests perform, so super().generate() hits the fake instead of
    # dispatching to the real class and trying to spawn a sidecar. (#498)
    _sub = next(c for c in DotsTTSBackend.__mro__ if c.__name__ == "SubprocessBackend")
    monkeypatch.setattr(_sub, "generate", fake_super_generate)

    DotsTTSBackend().generate("no reference", ref_text="orphan transcript")
    assert "ref_text" not in captured
    assert "ref_audio" not in captured
    assert captured["num_steps"] == 10
    assert captured["guidance_scale"] == 1.2
