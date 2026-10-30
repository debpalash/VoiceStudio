"""WhisperX VRAM preflight (#723).

On an 8 GB card with the TTS model resident, loading whisper large-v3 fp16
dies as a *native* CUDA OOM abort — the backend process is killed outright,
no Python exception fires, and the UI reports "Can't reach the local
backend". The load-time fp16→int8 / OOM→CPU fallbacks in `_ensure_asr` never
run because nothing is raised. The only defense is a preflight: re-check the
device pick against actually-free VRAM (`torch.cuda.mem_get_info`) right
before loading, degrading fp16 → int8_float16 → int8 → CPU.

Backend classes are resolved at RUNTIME (see test_asr_gpu_compat.py rationale).
"""
from __future__ import annotations

import pytest


def _backend():
    from services.asr_backend import _REGISTRY
    b = _REGISTRY["whisperx"].__new__(_REGISTRY["whisperx"])  # skip __init__ (no torch probe)
    b._model_name = "large-v3"
    return b


def _degrade(b, free_gb, device="cuda", compute="float16"):
    b._free_vram_gb = lambda: free_gb
    return b._degrade_for_vram(device, compute)


# ── The #723 crash scenario: TTS resident, ~2 GB free, fp16 requested ──────

def test_starved_card_falls_back_to_cpu():
    assert _degrade(_backend(), 2.0) == ("cpu", "int8")


def test_mid_vram_degrades_to_int8_on_cuda():
    # 3.5 GB free: can't hold fp16 (5.0) or int8_float16 (3.5 is not > needed
    # headroom boundary — equal passes), int8 (3.0) certainly fits.
    dev, ct = _degrade(_backend(), 3.2)
    assert (dev, ct) == ("cuda", "int8")


def test_ample_vram_keeps_fp16():
    assert _degrade(_backend(), 7.0) == ("cuda", "float16")


# ── Preflight must never *break* ASR ────────────────────────────────────────

def test_unknown_vram_is_left_alone():
    assert _degrade(_backend(), None) == ("cuda", "float16")


def test_cpu_pick_is_untouched():
    b = _backend()
    b._free_vram_gb = lambda: 0.5
    assert b._degrade_for_vram("cpu", "int8") == ("cpu", "int8")


def test_small_models_not_over_evicted():
    # A 2 GB-free card comfortably runs whisper-small fp16 (5.0 * 0.25 budget);
    # the large-v3 budgets must not evict smaller models from CUDA.
    b = _backend()
    b._model_name = "small"
    assert _degrade(b, 2.0) == ("cuda", "float16")


def test_env_opt_out(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_VRAM_PREFLIGHT", "0")
    assert _degrade(_backend(), 0.5) == ("cuda", "float16")


# ── Wiring: _ensure_asr must preflight BEFORE whisperx.load_model ──────────

def test_ensure_asr_applies_preflight_before_load(monkeypatch):
    import sys, types

    monkeypatch.setattr("core.windows_rocm.pipelines.windows_hip_installed", lambda: False)
    calls = {}

    fake_whisperx = types.ModuleType("whisperx")
    def _load_model(name, device=None, compute_type=None, **kw):
        calls["load"] = (device, compute_type)
        return object()
    fake_whisperx.load_model = _load_model
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)

    b = _backend()
    b._asr = None
    b._device, b._compute_type = "cuda", "float16"
    b._free_vram_gb = lambda: 2.0          # the #723 card state
    b._allow_vad_pickle_globals = lambda: None

    b._ensure_asr()
    assert calls["load"] == ("cpu", "int8")   # degraded BEFORE the load call
