"""Precision the TTS model loads in, keyed on the device string.

Stdlib-only so the backend (cycle-free) and the CLIs share one policy.

fp16/bf16 halve VRAM and use tensor cores, but a plain CPU has no fast
half-precision GEMM (torch falls back to a scalar path an order of magnitude
slower than float32 on most laptops, and some kernels are unimplemented for
Half), so a CPU-only host loaded the model and then crawled. Every non-CPU
device string (``cuda``, ``xpu``, ``mps``, DirectML ``privateuseone:N``) keeps
float16.
"""
from __future__ import annotations

import os

CPU_DTYPE_CHOICES: tuple[str, ...] = ("float32", "bfloat16", "float16")


def tts_dtype_name(device: object) -> str:
    """The torch dtype *name* the TTS model should load in on ``device``.

    Power users on CPUs with native bf16 (AVX512-BF16 / AMX) or short on RAM
    can opt into ``OMNIVOICE_CPU_DTYPE=bfloat16`` (halves resident weights);
    unknown values are ignored rather than raising — this runs on the model-load
    path.
    """
    if str(device or "cpu").strip().lower() != "cpu":
        return "float16"
    override = os.environ.get("OMNIVOICE_CPU_DTYPE", "").strip().lower()
    return override if override in CPU_DTYPE_CHOICES else "float32"
