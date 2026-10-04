"""ASR backends now carry an explicit ``gpu_compat`` (mirroring TTSBackend) so
engine_routing can surface the effective device per host. Verifies the ABC
default and every subclass's declared tuple, plus the IndexTTS2 fix.

Backend classes are resolved at RUNTIME (inside each test, via the registry)
rather than imported at module scope — other suites purge ``services.*`` from
``sys.modules`` for DB isolation, which would otherwise leave this module
holding stale class objects depending on collection/run order.
"""
from __future__ import annotations

import pytest

# id → declared gpu_compat. nemo-parakeet was CUDA-gated until 2026-07-02,
# when parakeet-tdt-0.6b-v3 was measured at RTF 0.08–0.23 on an M2 CPU —
# every ASR engine now has a cpu path.
_EXPECTED = {
    "whisperx": ("cuda", "cpu"),
    "faster-whisper": ("cuda", "cpu"),
    "mlx-whisper": ("mps", "cpu"),
    "pytorch-whisper": ("cuda", "rocm", "mps", "cpu"),
    "nemo-parakeet": ("cuda", "cpu"),
    # MLX runs on Apple Silicon's unified-memory GPU only; is_available
    # hard-gates on mlx_supported(), so claiming cpu would be false.
    "parakeet-mlx": ("mps",),
    "moonshine": ("cpu",),
    "funasr": ("cuda", "cpu"),
    # Crash-isolated sidecar wraps the same CTranslate2 engine as
    # faster-whisper — same device support (#730 residual B).
    "faster-whisper-isolated": ("cuda", "cpu"),
}

# Engines that legitimately have NO cpu path (hard platform/GPU gate in
# is_available — e.g. parakeet-mlx gates on Apple Silicon via mlx_supported()).
_GPU_ONLY: set[str] = {"parakeet-mlx"}

_VALID = {"cuda", "rocm", "mps", "xpu", "npu", "cpu"}


def _cls(engine_id):
    from services.asr_backend import _REGISTRY
    return _REGISTRY[engine_id]


def test_abc_default_is_cpu_only():
    from services.asr_backend import ASRBackend
    assert ASRBackend.gpu_compat == ("cpu",)


@pytest.mark.parametrize("engine_id,expected", list(_EXPECTED.items()))
def test_subclass_gpu_compat(engine_id, expected):
    assert _cls(engine_id).gpu_compat == expected


@pytest.mark.parametrize("engine_id", list(_EXPECTED))
def test_compat_values_are_valid(engine_id):
    compat = _cls(engine_id).gpu_compat
    assert compat, "gpu_compat must be non-empty"
    assert set(compat) <= _VALID
    # Every engine has a cpu path EXCEPT the known hard-GPU-gated ones.
    if engine_id not in _GPU_ONLY:
        assert "cpu" in compat
    else:
        assert "cpu" not in compat  # would be a false claim — is_available gates on CUDA


def test_only_verified_asr_engines_claim_unconditional_rocm_support():
    declared_rocm_engines = {
        engine_id for engine_id in _EXPECTED
        if "rocm" in _cls(engine_id).gpu_compat
    }
    assert declared_rocm_engines == {"pytorch-whisper"}


def test_nemo_parakeet_has_no_cuda_gate(monkeypatch):
    """Regression (CPU un-gating, 2026-07-02): on a CUDA-less host,
    is_available() must never claim a GPU is required — availability is a
    pure nemo_toolkit dependency check now."""
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    ok, reason = _cls("nemo-parakeet").is_available()
    assert "NVIDIA GPU" not in reason
    if not ok:  # env without nemo_toolkit — the only legitimate blocker
        assert "nemo_toolkit" in reason


def test_indextts2_overrides_cpu_only_default():
    from engines.indextts import IndexTTS2Backend
    # ROCm is claimable since the one-click installer gives the sidecar venv
    # a ROCm torch on ROCm hosts (#2371); see sidecar_install.uses_rocm_index.
    assert IndexTTS2Backend.gpu_compat == ("cuda", "rocm", "cpu")
    # must NOT be the inherited TTSBackend default
    assert IndexTTS2Backend.gpu_compat != ("cpu",)
