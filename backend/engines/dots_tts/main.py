"""dots.tts sidecar entry point (issue #498).

Runs inside ``engines/dots_tts/.venv`` (or the user's existing
``${OMNIVOICE_DOTS_TTS_DIR}/.venv``) with ``transformers==4.57.0``, isolated
from the OmniVoice parent (``transformers>=5.3``). Same isolation rationale
as the IndexTTS / MOSS-TTS-v1.5 sidecars.

Stdlib-only at import time; ``dots_tts`` + torch are imported lazily on the
first synthesize op so the ``ready`` frame fits inside the parent's 30 s
spawn handshake even on a cold filesystem.

Wire protocol — length-prefixed JSON over stdin/stdout, byte-identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow:
    1. Sidecar -> parent: {"op": "ready", "engine": "dots-tts",
                           "sample_rate": 48000}
    2. parent -> sidecar: {"op": "ping"} -> {"op": "pong", "vram_mb": N}
    3. parent -> sidecar: {"op": "synthesize", "text": "...",
                            "ref_audio": "/path/ref.wav",
                            "ref_text": "transcript", "language": "EN",
                            "num_steps": 10, "guidance_scale": 1.2}
       -> {"op": "progress", ...} (cold load) then
       -> {"op": "audio", "audio_pcm_b64": "...", "sample_rate": 48000,
           "n_samples": N}
    4. parent -> sidecar: {"op": "shutdown"} -> exit 0

Restrictions: NO imports from OmniVoice parent code (different venv). NO
logging of ``os.environ`` contents. Single-frame DoS cap matches the
parent's ``MAX_FRAME_BYTES``.
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

#: dots.tts emits 48 kHz (checkpoint vocoder.sample_rate). Advertised in the
#: ready frame; the real value is re-read from each generate() result.
DOTS_SAMPLE_RATE = 48000

#: Default checkpoint. ``-soar`` is the best-cloning variant; ``-mf`` is the
#: fastest (use num_steps=4). Overridable for air-gapped / mirror installs.
_DEFAULT_REPO = "rednote-hilab/dots.tts-soar"


# ── wire protocol ─────────────────────────────────────────────────────────


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
    """This sidecar's own GPU memory in MB (MM2-08). 0 on CPU. Never raises."""
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


# ── model loading (lazy, on first synthesize) ─────────────────────────────


# Module-level singleton — (runtime,). Device is auto-selected inside the
# dots.tts runtime (cuda-or-cpu, no MPS); we don't pass a device.
_runtime = None


def _load_runtime(stdout):
    """Cold-construct the dots.tts runtime.

    ``DotsTtsRuntime.from_pretrained`` auto-selects cuda-or-cpu internally
    (no MPS path). precision is bf16 on CUDA; on CPU we fall back to fp32
    (bf16 CPU kernels are spotty). Both overridable via env.
    """
    global _runtime
    if _runtime is not None:
        return _runtime

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    import torch
    from dots_tts.runtime import DotsTtsRuntime  # type: ignore[import-not-found]

    repo = os.environ.get("OMNIVOICE_DOTS_TTS_MODEL", _DEFAULT_REPO)
    default_precision = "bfloat16" if torch.cuda.is_available() else "float32"
    precision = os.environ.get("OMNIVOICE_DOTS_TTS_PRECISION", default_precision)
    optimize = os.environ.get("OMNIVOICE_DOTS_TTS_OPTIMIZE", "0") == "1"

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})

    _runtime = DotsTtsRuntime.from_pretrained(
        repo,
        precision=precision,
        optimize=optimize,
    )

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _runtime


def _tensor_to_pcm_b64(audio, sample_rate: int) -> tuple[str, int, int]:
    """Convert a torch waveform tensor (1, N) in [-1, 1] to base64 int16 PCM."""
    import numpy as np

    arr = audio.detach().to("cpu").float().numpy()
    arr = np.asarray(arr, dtype=np.float32).squeeze()
    while arr.ndim > 1:
        # Downmix along whichever axis is the channel axis. Hardcoded to axis 0
        # this averaged across TIME for a channels-last (N, 2) array -- every
        # output sample became the mean of two neighbouring samples, which is
        # not a downmix but a destroyed waveform. (#1328)
        arr = arr.mean(axis=int(np.argmin(arr.shape)))
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sample_rate), int(arr.shape[0])


def _normalize_language(raw):
    """Map OmniVoice's language value to what dots.tts accepts, or None.

    dots.tts accepts None/"auto_detect", ISO codes upper-cased ("EN"/"ZH"),
    or names ("english"). A 2-letter ISO code is upper-cased; anything else
    is passed through; empty / "auto" → None (auto-detect)."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.lower() == "auto":
        return None
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return s


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    runtime = _load_runtime(stdout)

    gen_kwargs: dict = {
        "text": text,
        "num_steps": int(msg.get("num_steps", 10)),
        "guidance_scale": float(msg.get("guidance_scale", 1.2)),
    }

    ref_audio = msg.get("ref_audio")
    if ref_audio:
        gen_kwargs["prompt_audio_path"] = ref_audio
        ref_text = msg.get("ref_text")
        if ref_text:
            # continuation cloning — upstream requires prompt_audio_path when
            # prompt_text is set (the parent already enforces this).
            gen_kwargs["prompt_text"] = ref_text

    language = _normalize_language(msg.get("language"))
    if language:
        gen_kwargs["language"] = language

    result = runtime.generate(**gen_kwargs)
    audio = result["audio"]
    sample_rate = int(result.get("sample_rate", DOTS_SAMPLE_RATE))

    pcm_b64, sr, n_samples = _tensor_to_pcm_b64(audio, sample_rate)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


# ── main loop ─────────────────────────────────────────────────────────────


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428).
    #
    # This sidecar's protocol is length-prefixed binary on stdout, but it is
    # not the only thing writing there: the libraries it loads print freely to
    # fd 1 — wetextprocessing's FST logs, tqdm bars, native prints from torch
    # and ONNX runtime. Those bytes interleave with frames, and the parent
    # then reads four bytes of log text as a length prefix, which is how a
    # generation dies with `OSError: frame too large: 1044258881` (that number
    # is ASCII). Worse, it desyncs the stream, so every later request on the
    # same sidecar reads stale bytes and no retry can recover.
    #
    # Duplicating fd 1 first keeps a clean channel only this module can write
    # to; redirecting fd 1 to fd 2 sends the library noise to stderr, which
    # the parent already drains into its own log (through the HF-token
    # redactor). Nothing is lost and the frame stream cannot be corrupted.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    # Ready handshake fires BEFORE any heavy import.
    _send(stdout, {
        "op": "ready",
        "engine": "dots-tts",
        "sample_rate": DOTS_SAMPLE_RATE,
    })

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": "recv",
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
                _send(stdout, {
                    "op": "error",
                    "stage": "dispatch",
                    "message": f"unknown op: {op!r}",
                })
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
