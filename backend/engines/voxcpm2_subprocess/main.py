"""voxcpm2 sidecar: VoxCPM2 in the engine's own venv (one-click install).

Launched as ``<engine venv python> main.py`` by VoxCPM2SubprocessBackend. It
imports nothing from the app: the venv holds only ``voxcpm`` and what it
depends on (torch, torchaudio, numpy), so this file must stay importable with
the standard library plus those. The parent prepares the reference clip and
trims the output's silent tail, exactly as the in-process engine does; this
process only loads the model and synthesizes.

Wire protocol: length-prefixed JSON over stdio, identical to the other
sidecars (engines/pockettts/main.py). A ``ready`` frame comes first, then one
``audio`` (or ``error``) frame per ``synthesize``, with ``progress`` frames
while a cold load runs so the parent's watchdog stays armed.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import struct
import sys
import threading
import time
import traceback

# Mirrors services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024
#: VoxCPM2's studio output rate; the in-process engine assumes the same.
VOXCPM2_SAMPLE_RATE = 48_000
#: Emit a progress frame at least this often during a cold load (a multi-GB
#: first download) so the parent's recv watchdog doesn't kill a healthy sidecar.
_HEARTBEAT_S = 5.0
#: ref_audio must be a local file path, not a URL (local-first; no SSRF).
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
#: A download failure worth retrying (the HF cache resumes, so a retry
#: continues rather than restarts). Anything else propagates at once.
_TRANSIENT_MARKERS = (
    "connection", "timed out", "timeout", "peer closed", "incomplete",
    "remoteprotocolerror", "temporarily unavailable",
)

_MODEL = None

# -- wire protocol -----------------------------------------------------------

#: Serializes _send across threads (the cold-load heartbeat + the main loop) so
#: concurrent length+body writes can't interleave and corrupt the framing.
_send_lock = threading.Lock()


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    with _send_lock:
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
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / (1024 * 1024)
    except Exception:  # noqa: BLE001 — a probe, never fatal
        pass
    return 0.0


# -- model loading (lazy, on the first synthesize) ---------------------------


def _with_retries(load):
    """Run ``load``, retrying a transient download failure with a short
    backoff, the way the app's own loader does for in-process engines."""
    try:
        attempts = max(1, int(os.environ.get("OMNIVOICE_MODEL_LOAD_RETRIES", "3")))
    except ValueError:
        attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            return load()
        except Exception as exc:  # noqa: BLE001 — classified below
            text = f"{type(exc).__name__}: {exc}".lower()
            if attempt == attempts or not any(m in text for m in _TRANSIENT_MARKERS):
                raise
            time.sleep(2.0 * attempt)
    raise AssertionError("unreachable")


@contextlib.contextmanager
def _heartbeating(stdout, stage: str):
    """Emit ``progress`` frames while a long native call runs.

    The parent's recv watchdog is re-armed by every frame, so a healthy sidecar
    that is simply slow - a multi-GB cold download, or a CPU / small-GPU host
    synthesising a long passage - is never killed for being quiet, while a
    sidecar that truly died (no frames at all) is still caught quickly. The
    request's own generate budget bounds the total either way.
    """
    stop = threading.Event()

    def _beat() -> None:
        pct = 1
        while not stop.wait(_HEARTBEAT_S):
            pct = min(pct + 1, 99)
            _send(stdout, {"op": "progress", "stage": stage, "percent": pct})

    thread = threading.Thread(target=_beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_HEARTBEAT_S + 1)


def _load_model(stdout):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    with _heartbeating(stdout, "loading_model"):
        from voxcpm import VoxCPM  # type: ignore[import-not-found]  # noqa: PLC0415

        checkpoint = os.environ.get("OMNIVOICE_VOXCPM_MODEL", "openbmb/VoxCPM2")
        _MODEL = _with_retries(
            lambda: VoxCPM.from_pretrained(
                checkpoint, load_denoiser=False, optimize=False
            )
        )
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _MODEL


def _sample_rate(model) -> int:
    for owner in (model, getattr(model, "tts_model", None)):
        sr = getattr(owner, "sample_rate", None)
        if isinstance(sr, int) and sr > 0:
            return sr
    return VOXCPM2_SAMPLE_RATE


def _at_engine_rate(wav, sample_rate: int):
    """The waveform at VOXCPM2_SAMPLE_RATE. The parent reads the PCM at that
    fixed rate (it trims the tail and labels the audio with it), so a model
    reporting another rate is resampled here rather than mislabelled."""
    if sample_rate == VOXCPM2_SAMPLE_RATE:
        return wav
    import torch  # noqa: PLC0415
    import torchaudio  # noqa: PLC0415

    tensor = torch.as_tensor(wav.detach().cpu() if hasattr(wav, "detach") else wav,
                             dtype=torch.float32).reshape(-1)
    return torchaudio.functional.resample(tensor, sample_rate, VOXCPM2_SAMPLE_RATE)


def _to_pcm_b64(wav, audio_format="s16le") -> tuple[str, int]:
    """A float waveform in [-1, 1] (numpy or torch) as base64 int16 PCM."""
    import numpy as np  # noqa: PLC0415

    if hasattr(wav, "detach"):
        wav = wav.detach().float().cpu().numpy()
    arr = np.asarray(wav, dtype=np.float32).squeeze()
    if arr.ndim > 1:
        raise ValueError(f"expected mono audio (1-D after squeeze), got shape {arr.shape}")
    arr = np.clip(arr, -1.0, 1.0)
    if audio_format not in ("s16le", "f32le"):
        raise ValueError("Unsupported audio transport format")
    pcm = (arr.astype("<f4").tobytes() if audio_format == "f32le"
           else (arr * 32767.0).astype("<i2").tobytes())
    return base64.b64encode(pcm).decode("ascii"), int(arr.shape[-1])


def generation_kwargs(text: str, **options) -> dict:
    """Map app controls to VoxCPM 2.0.3's three native generation modes.

    Shared by the in-process adapter; keep this mapping stdlib-only so the
    standalone sidecar does not depend on the application's environment.
    """
    ref_audio = options.get("ref_audio") or None
    controls = []
    if not ref_audio and options.get("description"):
        controls.append(options["description"])
    if options.get("instruct"):
        controls.append(options["instruct"])
    # Native inline instructions also select controllable cloning. A saved
    # profile's transcript must not silently switch them into continuation.
    inline = re.match(r"^\s*\(([^()]*)\)\s*(.*)$", text, re.DOTALL)
    if inline:
        controls.append(inline.group(1))
        text = inline.group(2)
    # Match the upstream demo: parentheses inside a control must not break
    # the single '(control)text' prefix.
    control = ", ".join(part.strip() for part in controls if part.strip())
    control = " ".join(control.replace("(", " ").replace(")", " ").split())
    ref_text = (options.get("ref_text") or "").strip()
    continuation = bool(ref_audio and ref_text and not control)
    # Bound work per request. VoxCPM's default token cap (4096) can keep one
    # desktop request running for many minutes on a short line, so scale the
    # cap with utterance size. 6 tokens per character is deliberately generous
    # (CJK is the densest case), so a legitimate render is never truncated.
    max_len = min(4096, max(256, len(text) * 6))
    # Steps come from the UI's sampling slider (1-64 for VoxCPM2); keep the
    # upstream default of 10 when a caller sends none. The requested value is
    # honoured: silently running fewer steps than the quality preset the user
    # picked would change the output without telling them.
    try:
        inference_timesteps = int(options.get("num_step", 10))
    except (TypeError, ValueError):
        inference_timesteps = 10
    inference_timesteps = min(64, max(1, inference_timesteps))
    return {
        "text": f"({control}){text}" if control else text,
        "cfg_value": options.get("guidance_scale", 2.0),
        "max_len": max_len,
        "inference_timesteps": inference_timesteps,
        # Upstream retries a "bad case" up to three whole generations, which
        # could keep one desktop request running for many minutes.
        "retry_badcase": False,
        # One attempt, no bad-case retries. VoxCPM runs `while attempts <
        # max_times`, so 0 skipped generation entirely and crashed with
        # UnboundLocalError: 'latent_pred' on every request.
        "retry_badcase_max_times": 1,
        "reference_wav_path": ref_audio,
        "prompt_wav_path": ref_audio if continuation else None,
        "prompt_text": ref_text if continuation else None,
    }


def _handle_synthesize(msg: dict, stdout) -> None:
    """One synthesize request. The mapping mirrors VoxCPM2Backend.generate."""
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    ref_audio = msg.get("ref_audio") or None
    if ref_audio and _URL_RE.match(str(ref_audio)):
        raise ValueError(
            "ref_audio must be a local file path; URLs are not accepted (local-first)."
        )
    model = _load_model(stdout)
    if msg.get("seed") is not None:
        import torch  # noqa: PLC0415

        torch.manual_seed(int(msg["seed"]))
    options = {key: value for key, value in msg.items() if key != "text"}
    with _heartbeating(stdout, "generating"):
        wav = model.generate(**generation_kwargs(text, **options))
    audio_format = msg.get("audio_format", "s16le")
    pcm_b64, n_samples = _to_pcm_b64(_at_engine_rate(wav, _sample_rate(model)), audio_format)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "audio_format": audio_format,
        "sample_rate": VOXCPM2_SAMPLE_RATE,
        "n_samples": n_samples,
    })


# -- main loop ---------------------------------------------------------------


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428): the
    # libraries this loads print to fd 1 (tqdm, native torch output), and those
    # bytes would otherwise interleave with the length-prefixed frames.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    # Ready handshake fires BEFORE any heavy import.
    _send(stdout, {"op": "ready", "engine": "voxcpm2", "sample_rate": VOXCPM2_SAMPLE_RATE})

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:  # noqa: BLE001
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
                _send(stdout, {"op": "error", "stage": "dispatch", "message": f"unknown op: {op!r}"})
        except Exception as exc:  # noqa: BLE001
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
