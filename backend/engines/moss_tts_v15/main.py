"""MOSS-TTS-v1.5 sidecar entry point (issue #498).

Runs inside ``engines/moss_tts_v15/.venv`` (or the user's existing
``${OMNIVOICE_MOSS_TTS_V15_DIR}/.venv``) with ``transformers==5.0.0``,
isolated from the OmniVoice parent process which pins ``transformers>=5.3``.
Same isolation rationale as the IndexTTS sidecar.

Stdlib-only at import time. The model + transformers + torch are imported
lazily on the first synthesize op so the sidecar emits its ``ready`` frame
inside the parent's 30 s spawn handshake even on a cold filesystem (an 8B
model takes well over 30 s to cold-load).

Wire protocol — length-prefixed JSON over stdin/stdout, byte-identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow:
    1. Sidecar -> parent: {"op": "ready", "engine": "moss-tts-v15",
                           "sample_rate": 24000}
    2. parent -> sidecar: {"op": "ping"} -> {"op": "pong", "vram_mb": N}
    3. parent -> sidecar: {"op": "synthesize", "text": "...",
                            "ref_audio": "/path/spk.wav", "language": "fr",
                            "tokens": 325, "max_new_tokens": 4096}
       -> {"op": "progress", ...} (cold load only) then
       -> {"op": "audio", "audio_pcm_b64": "...", "sample_rate": 24000,
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

#: Native sample rate MOSS-TTS-v1.5 emits. Advertised in the ready frame so
#: the parent doesn't have to import MOSS just to learn the rate. Confirmed
#: via ``processor.model_config.sampling_rate`` (the real value is read from
#: the loaded model at synthesize time; this is the handshake default).
MOSS_SAMPLE_RATE = 24000

#: HF repo id for the weights, overridable for air-gapped / mirror installs.
_DEFAULT_REPO = "OpenMOSS-Team/MOSS-TTS-v1.5"

#: ISO-639-1 → MOSS language name. MOSS's ``build_user_message`` takes a
#: language *name* ("French"), not a code. Unknown codes are omitted so the
#: model auto-detects. Covers the high-traffic subset of MOSS's 31 langs.
_ISO_TO_NAME = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
    "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "cs": "Czech", "el": "Greek",
    "he": "Hebrew", "fa": "Persian", "uk": "Ukrainian", "sv": "Swedish",
}


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
    """This sidecar's own GPU memory in MB (MM2-08). The parent can't see a
    child's VRAM, so we self-report it in the pong. 0 on CPU. Never raises."""
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


# ── model loading (lazy, on first synthesize) ─────────────────────────────


# Module-level singleton — populated on the first synthesize op and reused.
# Holds (processor, model, device, sample_rate).
_state = None


def _load_model(stdout):
    """Cold-construct the MOSS-TTS-v1.5 processor + model.

    Device selection is CUDA-or-CPU only — MOSS's upstream documents no MPS
    path and the custom ``trust_remote_code`` modelling code is untested on
    Apple Silicon, so we never route to MPS where it might crash. dtype is
    bf16 on CUDA, fp32 on CPU (bf16 CPU ops are spotty). Emits progress
    frames so the parent can surface the multi-GB cold-load latency.
    """
    global _state
    if _state is not None:
        return _state

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    import torch
    from transformers import AutoModel, AutoProcessor

    repo = os.environ.get("OMNIVOICE_MOSS_TTS_V15_MODEL", _DEFAULT_REPO)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    # "sdpa" works on CUDA + CPU and needs no extra dep. flash_attention_2
    # (Ampere+ CUDA, optional flash-attn) is opt-in via env.
    attn = os.environ.get("OMNIVOICE_MOSS_TTS_V15_ATTN", "sdpa")

    processor = AutoProcessor.from_pretrained(repo, trust_remote_code=True)
    # The audio tokenizer is a separate sub-module that must be moved to the
    # device independently (easy to miss — see upstream README).
    processor.audio_tokenizer = processor.audio_tokenizer.to(device)

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})

    model = AutoModel.from_pretrained(
        repo,
        trust_remote_code=True,
        attn_implementation=attn,
        torch_dtype=dtype,
    ).to(device)
    model.eval()

    sample_rate = int(getattr(processor.model_config, "sampling_rate", MOSS_SAMPLE_RATE))
    _state = (processor, model, device, sample_rate)

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _state


def _tensor_to_pcm_b64(audio, sample_rate: int) -> tuple[str, int, int]:
    """Convert a torch waveform tensor to base64 int16 PCM.

    MOSS returns a float tensor in [-1, 1] (1-D or (1, N)); we squeeze to
    mono, clip, scale to int16, and base64 so the wire frame stays JSON-safe.
    """
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


def _resolve_language(raw):
    """Map OmniVoice's language value to a MOSS language name, or None.

    Accepts an ISO-639-1 code or a full name. Unknown / empty / "auto"
    values return None so MOSS auto-detects."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.lower() == "auto":
        return None
    if s.lower() in _ISO_TO_NAME:
        return _ISO_TO_NAME[s.lower()]
    # Already a language name (or an unknown code) — pass it through; MOSS
    # ignores a language it doesn't recognise.
    return s


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    import torch

    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    processor, model, device, sample_rate = _load_model(stdout)

    user_kwargs: dict = {"text": text}

    ref_audio = msg.get("ref_audio")
    if ref_audio:
        # Zero-shot voice cloning: the reference audio alone is enough in
        # MOSS's clone mode (ref_text is not consumed here). The processor's
        # audio tokenizer encodes the reference into the prompt.
        user_kwargs["reference"] = [ref_audio]

    language = _resolve_language(msg.get("language"))
    if language:
        user_kwargs["language"] = language

    tokens = msg.get("tokens")
    if tokens is not None:
        user_kwargs["tokens"] = int(tokens)

    max_new_tokens = int(msg.get("max_new_tokens", 4096))

    conversations = [[processor.build_user_message(**user_kwargs)]]

    with torch.no_grad():
        batch = processor(conversations, mode="generation")
        outputs = model.generate(
            input_ids=batch["input_ids"].to(device),
            attention_mask=batch["attention_mask"].to(device),
            max_new_tokens=max_new_tokens,
        )

    decoded = processor.decode(outputs)
    audio = decoded[0].audio_codes_list[0]

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

    # Ready handshake fires BEFORE any heavy import — nothing above this line
    # touches transformers/torch, so we make the 30 s spawn window even cold.
    _send(stdout, {
        "op": "ready",
        "engine": "moss-tts-v15",
        "sample_rate": MOSS_SAMPLE_RATE,
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
            # Per-op failure is recoverable — emit the error frame and stay
            # alive so the parent can retry without paying the respawn +
            # multi-GB model-load cost again.
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
