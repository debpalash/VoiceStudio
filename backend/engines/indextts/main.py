"""IndexTTS-2 sidecar entry point (Phase 2 Plan 02-03).

Runs inside ``engines/indextts/.venv`` (or the user's existing
``${OMNIVOICE_INDEXTTS_DIR}/.venv``) with ``transformers<5``, isolated
from the OmniVoice parent process which pins ``transformers>=5.3``.
Closes issue #42 — the canonical ``OffloadedCache`` ImportError that
results from running both libraries inside one Python interpreter.

This script is stdlib-only at import time. It imports the indextts
library lazily on the first synthesize op so the sidecar can emit a
``ready`` frame within the parent's 30 s spawn handshake even when the
model itself takes ~20 s cold-load (RESEARCH.md Pitfall 8). Any import
failure surfaces as an ``error`` frame with full traceback before the
sidecar exits 1 — the parent's stderr drain + the operator's logs will
also have the underlying ImportError text.

Wire protocol — length-prefixed JSON over stdin/stdout, byte-identical
to ``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow expected by the parent:

    1. Sidecar -> parent: {"op": "ready", "engine": "indextts2",
                           "sample_rate": 24000}
       (Model NOT yet loaded — that happens on the first synthesize op
       per Pitfall 8. The ready frame is just the handshake.)

    2. Optional: parent -> sidecar: {"op": "ping"} ->
                   sidecar -> parent: {"op": "pong"}

    3. Parent -> sidecar: {"op": "synthesize", "text": "...",
                            "ref_audio": "/path/to/spk.wav",
                            "emo_vector": [..], "emo_audio": "...",
                            "emo_text": "...", "emo_alpha": 1.0,
                            "use_random": false, "duration": 3.4}
       Sidecar emits one or more {"op": "progress",
                                  "stage": "loading_model",
                                  "percent": N} frames during the cold
       model construction, then:
                   sidecar -> parent: {"op": "audio",
                                       "audio_pcm_b64": "<base64 int16>",
                                       "sample_rate": 24000,
                                       "n_samples": N}

    4. Parent -> sidecar: {"op": "shutdown"} -> exit 0
    5. Unknown op -> {"op": "error", "stage": "dispatch",
                      "message": "unknown op: <op>"} and continue.

Restrictions:

  * NO imports from ``backend.services``, ``backend.engines`` (other
    than this package), or any OmniVoice parent code. The sidecar runs
    under a venv where those modules may not resolve.
  * NO logging of ``os.environ`` contents or env-var values. Defense in
    depth against accidental token-bytes-on-stderr (T-02-08); the
    parent's stderr drainer additionally pipes everything through the
    Phase 1 ``HFTokenRedactor`` filter.
  * Single-frame DoS cap matches the parent's ``MAX_FRAME_BYTES`` so a
    malformed inbound frame surfaces as a clean IOError instead of an
    OOM.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import tempfile
import traceback


# Mirrors backend/services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024


def _measure_vram_mb() -> float:
    """This sidecar's own GPU memory in MB, for the loaded-models panel
    (MM2-08). The parent can't see a child's VRAM, so we self-report it in the
    pong. Degrades to 0 on CPU / when torch isn't loaded yet — never raises."""
    try:
        import torch  # already a dep inside the indextts venv
        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            drv = getattr(torch.mps, "driver_allocated_memory", None)
            if drv:
                return round(drv() / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0

# Sample rate IndexTTS-2 emits natively. Advertised in the ready frame so
# the parent doesn't have to import IndexTTS just to learn the rate.
INDEXTTS_SAMPLE_RATE = 24000

# Allowlist of kwargs we forward to ``IndexTTS2.infer``. Mirrors the old
# in-process ``IndexTTS2Backend.generate`` body at
# ``backend/services/tts_backend.py::IndexTTS2Backend.generate`` so the
# emotion / duration / random kwargs survive the migration verbatim.
# Anything not in this set is silently dropped before the call.
EMOTION_KWARGS_ALLOWLIST = frozenset({
    "emo_vector",       # list[float] len=8
    "emo_audio_prompt", # path to emotion ref wav
    "emo_alpha",        # float, emotion blend strength
    "emo_text",         # str, natural-language emotion
    "use_emo_text",     # bool — set by parent when emo_text supplied
    "use_random",       # bool
    "target_tokens",    # int — duration control
})


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


# ── model loading (lazy, on first synthesize) ─────────────────────────────


# Module-level singleton — populated on the first synthesize op and reused
# for every subsequent request in this sidecar's lifetime.
_model = None


def _load_model(stdout) -> object:
    """Cold-construct IndexTTS2 from OMNIVOICE_INDEXTTS_DIR/checkpoints/.

    Emits ``progress`` frames at 0/50/100% so the parent can surface the
    20+ second model-load latency in the Compat Matrix UI (T-02-10). On
    failure raises — the caller emits an ``error`` frame for the
    in-flight synthesize op and continues the dispatch loop (the next
    request retries the load).
    """
    global _model
    if _model is not None:
        return _model

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    # Imported lazily so a missing dep doesn't block the ready handshake.
    from indextts.infer_v2 import IndexTTS2  # type: ignore[import-not-found]

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})

    repo_dir = os.environ.get("OMNIVOICE_INDEXTTS_DIR", ".")
    cfg_path = os.path.join(repo_dir, "checkpoints", "config.yaml")
    model_dir = os.path.join(repo_dir, "checkpoints")
    use_fp16 = os.environ.get("OMNIVOICE_INDEXTTS_FP16", "1") == "1"

    _model = IndexTTS2(
        cfg_path=cfg_path,
        model_dir=model_dir,
        use_fp16=use_fp16,
        use_cuda_kernel=False,
        use_deepspeed=False,
    )

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _model


def _wav_to_pcm_b64(wav_path: str) -> tuple[str, int, int]:
    """Read a WAV file, downmix to mono, return base64 int16 PCM.

    Returns (b64_pcm, sample_rate, n_samples). Uses torchaudio because
    the sidecar's venv already has torch as a dep of indextts — no extra
    install cost.
    """
    import numpy as np
    import torchaudio  # type: ignore[import-not-found]

    wav, sr = torchaudio.load(wav_path)
    # Downmix multi-channel to mono.
    if wav.ndim == 2 and wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)

    # Resample to IndexTTS's advertised rate if the model emitted something
    # different (it shouldn't, but defensive — the parent caches our
    # advertised sample_rate from the ready frame and decodes accordingly).
    if int(sr) != INDEXTTS_SAMPLE_RATE:
        wav = torchaudio.functional.resample(wav, sr, INDEXTTS_SAMPLE_RATE)
        sr = INDEXTTS_SAMPLE_RATE

    arr = wav.squeeze(0).cpu().numpy()
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sr), int(arr.shape[0])


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    text = msg.get("text")
    if not text:
        raise ValueError("synthesize: missing 'text' field")
    ref_audio = msg.get("ref_audio")
    if not ref_audio:
        raise ValueError(
            "synthesize: IndexTTS2 requires a 'ref_audio' path for voice cloning"
        )

    model = _load_model(stdout)

    # Build infer_kwargs by filtering through the allowlist. The parent
    # has already done any vector-vs-audio-vs-text emotion priority
    # arbitration; we just forward whichever keys it sent.
    infer_kw: dict = {
        "spk_audio_prompt": ref_audio,
        "text": text,
        "verbose": False,
    }
    for k, v in msg.items():
        if k in EMOTION_KWARGS_ALLOWLIST and v is not None:
            infer_kw[k] = v

    # IndexTTS2.infer() writes to a file; we route through tempfile so
    # cleanup is automatic on success and on exit.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        infer_kw["output_path"] = tmp_path
        model.infer(**infer_kw)
        pcm_b64, sr, n_samples = _wav_to_pcm_b64(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            # T-02-11 — failure to unlink is logged-as-debug at most; the
            # OS will reap the temp file at process exit. Never break the
            # response on a cleanup error.
            pass

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

    # The ready handshake fires BEFORE any heavy import. SubprocessBackend's
    # SPAWN_READY_TIMEOUT_S is 30 s; we comfortably make that even on a
    # cold filesystem because nothing above this line touches indextts.
    _send(stdout, {
        "op": "ready",
        "engine": "indextts2",
        "sample_rate": INDEXTTS_SAMPLE_RATE,
    })

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            # Wire-level failure — we can't trust further reads. Surface
            # the error frame, then exit 1 so the parent respawns next time.
            _send(stdout, {
                "op": "error",
                "stage": "recv",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1
        if msg is None:
            # Clean EOF — parent closed stdin (shutdown path bypassed
            # because the shutdown op already triggered our return).
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
            # Per-op failure is recoverable — emit the error frame and
            # stay alive so the parent can retry without paying the
            # ~20 s respawn cost.
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
