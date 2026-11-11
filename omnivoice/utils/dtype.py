"""Precision the TTS model loads in, keyed on the device string.

Stdlib-only so the backend (cycle-free) and the CLIs share one policy.

fp16/bf16 halve VRAM and use tensor cores, but a plain CPU has no fast
half-precision GEMM (torch falls back to a scalar path an order of magnitude
slower than float32 on most laptops, and some kernels are unimplemented for
Half), so a CPU-only host loaded the model and then crawled. Accelerators
(``cuda``, ``mps``, DirectML ``privateuseone:N``) keep float16.

Intel XPU loads in bfloat16: on an Arc 140V (Lunar Lake, Windows 11) float16
renders were audibly choppier than the CPU's float32 ones, while bfloat16 kept
the 16-bit (XMX) speed and sounded clean. float32 is no fallback there — the
XMX units only accelerate 16-bit types, and a 3-minute script ran past the
generate budget.
"""
from __future__ import annotations

import os

CPU_DTYPE_CHOICES: tuple[str, ...] = ("float32", "bfloat16", "float16")
XPU_DTYPE_CHOICES: tuple[str, ...] = CPU_DTYPE_CHOICES


def _override(env: str, choices: tuple[str, ...], default: str) -> str:
    """``env``'s value when it names one of ``choices``, else ``default``."""
    value = os.environ.get(env, "").strip().lower()
    return value if value in choices else default


def tts_dtype_name(device: object) -> str:
    """The torch dtype *name* the TTS model should load in on ``device``.

    Power users on CPUs with native bf16 (AVX512-BF16 / AMX) or short on RAM
    can opt into ``OMNIVOICE_CPU_DTYPE=bfloat16`` (halves resident weights);
    ``OMNIVOICE_XPU_DTYPE`` does the same for Intel XPU. Unknown values are
    ignored rather than raising — this runs on the model-load path.
    """
    name = str(device or "cpu").strip().lower()
    if name == "cpu":
        return _override("OMNIVOICE_CPU_DTYPE", CPU_DTYPE_CHOICES, "float32")
    if name == "xpu" or name.startswith("xpu:"):
        return _override("OMNIVOICE_XPU_DTYPE", XPU_DTYPE_CHOICES, "bfloat16")
    return "float16"
