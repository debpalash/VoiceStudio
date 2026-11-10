"""MOSS-SoundEffect v2.0 sidecar entry point (issue #3577).

Runs inside the installer-made venv (``numpy==1.26.4``, ``transformers==4.57.1``,
torch 2.9 CUDA), isolated from the VoiceStudio parent. Stdlib-only at import
time; torch and ``moss_soundeffect_v2`` load lazily on the first synthesize so
the ``ready`` frame fits the parent's spawn handshake.

Wire protocol: length-prefixed JSON over stdin/stdout, identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

    sidecar -> parent  {"op": "ready", "engine": "moss-soundeffect", "sample_rate": 48000}
    parent  -> sidecar {"op": "ping"}                    -> {"op": "pong", "vram_mb": N}
    parent  -> sidecar {"op": "synthesize", "text": "<prompt>", "seconds": 10.0,
                        "seed": 0, "num_steps": 100, "cfg_scale": 4.0}
                       -> {"op": "progress", ...} (cold load) then
                       -> {"op": "audio", "audio_pcm_b64": "...", "sample_rate": 48000,
                           "n_samples": N}
    parent  -> sidecar {"op": "shutdown"}                -> exit 0

No imports from VoiceStudio parent code (different venv); no logging of
``os.environ``.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import traceback

# Mirrors backend/services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024
SFX_SAMPLE_RATE = 48000

#: Mirrors the limits the parent enforces (engines/moss_sfx/__init__.py).
_MAX_SECONDS = 30.0

#: Recommended inference hyperparameters from the model card.
_DEFAULT_STEPS = 100
_DEFAULT_CFG = 4.0
_MAX_STEPS = 200

#: Pinned weights come from the installer (<install dir>/checkpoints).
_INSTALL_ENV = "OMNIVOICE_MOSS_SFX_DIR"
_WEIGHTS_SUBDIR = "checkpoints"


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _recv(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None  # EOF
    (n,) = struct.unpack("!I", header)
    if n > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {n}")
    body = bytearray()
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            raise IOError("short read")
        body.extend(chunk)
    return json.loads(bytes(body).decode("utf-8"))


def _measure_vram_mb() -> float:
    """This sidecar's own GPU memory in MB. 0 until torch is loaded. Never raises."""
    try:
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


_pipeline = None


def _weights_dir() -> str:
    base = os.environ.get(_INSTALL_ENV)
    if not base:
        raise RuntimeError("Sound effects are not installed (no install directory is set).")
    path = os.path.join(base, _WEIGHTS_SUBDIR)
    if not os.path.isfile(os.path.join(path, "model_index.json")):
        raise RuntimeError(
            "The sound-effects weights are missing or incomplete. Repair the "
            "install from Tools > Sound effects."
        )
    return path


def _load_pipeline(stdout):
    """Cold-construct the pipeline from the pinned local checkpoint directory."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    # The pipeline wraps its DiT in torch.compile + Triton CUDA graphs: minutes
    # of first-call compile, and no Triton at all on Windows. Skip it unless the
    # user opts in; this is a speed choice only, the output is the same.
    if os.environ.get("OMNIVOICE_SFX_COMPILE", "0") != "1":
        os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

    import torch
    from moss_soundeffect_v2 import MossSoundEffectPipeline  # type: ignore[import-not-found]

    if not torch.cuda.is_available():
        raise RuntimeError("No CUDA GPU is available to this sidecar; sound effects need one.")

    path = _weights_dir()
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})
    _pipeline = MossSoundEffectPipeline.from_pretrained(
        path, torch_dtype=torch.bfloat16, device="cuda",
    )
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _pipeline


def _to_pcm_b64(audio, sample_rate: int) -> tuple:
    """(B, C, T) or (C, T) float tensor in [-1, 1] -> base64 int16 mono PCM.

    Done with numpy so the sidecar never touches torchaudio.save (which needs
    torchcodec in torchaudio 2.9).
    """
    import numpy as np

    arr = audio.detach().to("cpu").float().numpy()
    arr = np.asarray(arr, dtype=np.float32)
    while arr.ndim > 1:
        # Average over the channel axis (the smallest one), never over time.
        arr = arr.mean(axis=int(np.argmin(arr.shape)))
    arr = np.nan_to_num(np.clip(arr, -1.0, 1.0))
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sample_rate), int(arr.shape[0])


def _handle_synthesize(msg: dict, stdout) -> None:
    prompt = msg.get("text")
    if not prompt or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("synthesize: missing or empty prompt")
    seconds = float(msg.get("seconds", 10.0))
    if not 0.0 < seconds <= _MAX_SECONDS:
        raise ValueError(f"seconds must be between 0 and {_MAX_SECONDS:g}")
    steps = max(1, min(int(msg.get("num_steps", _DEFAULT_STEPS)), _MAX_STEPS))

    pipe = _load_pipeline(stdout)
    audio = pipe(
        prompt=prompt.strip(),
        seconds=seconds,
        num_inference_steps=steps,
        cfg_scale=float(msg.get("cfg_scale", _DEFAULT_CFG)),
        seed=int(msg.get("seed", 0)),
    )
    pcm_b64, sr, n_samples = _to_pcm_b64(audio, getattr(pipe, "sample_rate", SFX_SAMPLE_RATE))
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a private fd and fd 1 is pointed at stderr (#1428): the
    # libraries this sidecar loads print to fd 1 and would corrupt the frames.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    _send(stdout, {"op": "ready", "engine": "moss-soundeffect", "sample_rate": SFX_SAMPLE_RATE})

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:  # noqa: BLE001
            _send(stdout, {
                "op": "error", "stage": "recv",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1
        if msg is None:
            return 0

        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                _send(stdout, {"op": "pong", "vram_mb": _measure_vram_mb()})
            elif op == "synthesize":
                _handle_synthesize(msg, stdout)
            elif op == "shutdown":
                return 0
            else:
                _send(stdout, {"op": "error", "stage": "dispatch", "message": f"unknown op: {op!r}"})
        except Exception as exc:  # noqa: BLE001 - report, keep the sidecar alive
            _send(stdout, {
                "op": "error", "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
