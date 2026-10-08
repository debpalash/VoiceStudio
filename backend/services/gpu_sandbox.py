"""
GPU crash sandbox — subprocess isolation for GPU-intensive operations.

Wraps TTS generation in a subprocess so a GPU crash (CUDA OOM, MPS fault,
driver segfault) kills the worker process but NOT the main backend server.
The parent process catches the crash and returns a 503 with a clear error
instead of the entire application dying.

Usage:
    from services.gpu_sandbox import sandboxed_generate

    result = await sandboxed_generate(
        text="Hello world",
        profile_id="voice_123",
        timeout=60,
    )
    # result is a dict with either {"audio_path": ...} or {"error": ...}

Architecture:
    Main Process ──fork──► Worker Process (GPU ops)
                  ◄─pipe── {"audio_path": "/tmp/xxx.wav"} or {"error": "..."}

    If the worker dies (segfault, OOM), the pipe closes and the main
    process returns a clean error response.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import sys
import tempfile

logger = logging.getLogger("omnivoice.sandbox")


def _worker(conn, request: dict):
    """Run in a subprocess — does the actual GPU work."""
    try:
        # Prevent CUDA from inheriting contexts from parent
        os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")

        # Add backend to path
        backend_dir = os.path.join(os.path.dirname(__file__), "..")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)

        from services.audio_io import _safe_torchaudio_save
        from services.model_manager import _load_model_sync
        from services.audio_dsp import apply_mastering, normalize_audio

        model = _load_model_sync()

        # Build generation kwargs
        gen_kw = {
            "text": request["text"],
            "language": request.get("language"),
            "ref_audio": request.get("ref_audio"),
            "ref_text": request.get("ref_text"),
            "instruct": request.get("instruct"),
            "num_step": request.get("num_step", 16),
            "speed": request.get("speed", 1.0),
            "guidance_scale": request.get("guidance_scale", 2.0),
        }

        audios = model.generate(**gen_kw)
        audio_out = audios[0]

        sr = getattr(model, "sampling_rate", 24000)
        mastered = apply_mastering(audio_out, sample_rate=sr)
        final = normalize_audio(mastered, target_dBFS=-2.0)

        # Write to temp file and return path
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        # torchaudio.save needs TorchCodec on 2.9+; the helper falls back to soundfile.
        _safe_torchaudio_save(tmp.name, final, sr, format="wav")
        tmp.close()

        conn.send({"audio_path": tmp.name, "sample_rate": sr})

    except Exception as e:
        import traceback
        conn.send({
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        })
    finally:
        conn.close()


async def sandboxed_generate(
    text: str,
    timeout: float = 120,
    **gen_kwargs,
) -> dict:
    """Run TTS generation in a sandboxed subprocess.

    Returns:
        {"audio_path": str, "sample_rate": int} on success
        {"error": str} on failure (GPU crash, timeout, etc.)
    """
    parent_conn, child_conn = multiprocessing.Pipe()

    request = {"text": text, **gen_kwargs}

    proc = multiprocessing.Process(
        target=_worker,
        args=(child_conn, request),
        daemon=True,
    )
    proc.start()

    loop = asyncio.get_running_loop()

    def _wait():
        proc.join(timeout=timeout)
        if proc.is_alive():
            logger.warning("Sandbox worker timed out after %.0fs — killing", timeout)
            proc.kill()
            proc.join(timeout=5)
            return {"error": f"GPU operation timed out after {timeout}s"}

        if proc.exitcode != 0:
            # Worker crashed (segfault, CUDA OOM, etc.)
            return {
                "error": f"GPU worker crashed (exit code {proc.exitcode}). "
                         f"This usually means a CUDA OOM or driver fault. "
                         f"Try reducing num_step or restarting the server."
            }

        if parent_conn.poll(timeout=1):
            return parent_conn.recv()

        return {"error": "Worker completed but returned no data"}

    result = await loop.run_in_executor(None, _wait)

    # Clean up
    parent_conn.close()

    if result.get("error"):
        logger.error("Sandbox error: %s", result["error"])
    else:
        logger.info("Sandbox success: %s", result.get("audio_path", "?"))

    return result


def is_sandbox_available() -> tuple[bool, str]:
    """Check if sandboxing is feasible on this platform."""
    try:
        method = multiprocessing.get_start_method()
        if method == "fork":
            return True, "fork-based sandbox available"
        elif method == "spawn":
            return True, "spawn-based sandbox available (slower cold start)"
        return True, f"sandbox available (start method: {method})"
    except Exception as e:
        return False, f"multiprocessing not available: {e}"


# ── Phase 4 GGUF-01: hardware-capability probe ─────────────────────────────
#
# The probe itself lives in ``engines.omnivoice_gguf.hardware_probe`` (it's
# tightly coupled to the GGUF engine's quant_map.json) but it is a
# *backend-tier* responsibility per RESEARCH.md "Architectural
# Responsibility Map" — anything that needs to know "is this machine
# CUDA / MPS / CPU and how much VRAM does it have?" should import it from
# this module so we have a single entry point.
#
# Re-exported lazily via ``__getattr__`` so importing ``gpu_sandbox`` for
# the existing CUDA-crash sandbox doesn't drag in the GGUF engine package
# (which transitively pulls ``huggingface_hub`` + ``soundfile``).


def __getattr__(name: str):  # pragma: no cover - exercised via tests
    if name in ("detect_capabilities", "HardwareCapabilities", "ComputeClass"):
        from engines.omnivoice_gguf import hardware_probe

        return getattr(hardware_probe, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
