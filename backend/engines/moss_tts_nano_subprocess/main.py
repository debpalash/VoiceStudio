"""moss-tts-nano sidecar: MOSS-TTS-Nano in the engine's own venv (one-click install).

Launched as ``<engine venv python> main.py`` by MossTTSNanoSubprocessBackend.
The venv holds the reviewed upstream checkout, installed editable, and its
pinned dependencies, so this file imports nothing from the app. It drives the
runtime that checkout ships, ``moss_tts_nano_runtime.NanoTTSService``.

Wire protocol: identical to the other sidecars (engines/pockettts/main.py).
Progress frames are sent while the model loads and through the first
synthesis, which is when upstream fetches its audio tokenizer. Later calls
send none, so the parent's watchdog still catches a generation that wedges.
"""
from __future__ import annotations

import base64
import contextlib
import inspect
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import threading
import time
import traceback
import weakref

# Mirrors services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024
#: The rate the engine reports; upstream's output is resampled to it if needed.
NANO_SAMPLE_RATE = 48_000
_HEARTBEAT_S = 5.0
#: ref_audio must be a local file path, not a URL (local-first; no SSRF).
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
#: A download failure worth retrying; anything else propagates at once.
_TRANSIENT_MARKERS = (
    "connection", "timed out", "timeout", "peer closed", "incomplete",
    "remoteprotocolerror", "temporarily unavailable",
)

_SERVICE = None
_WARM = False

# -- wire protocol -----------------------------------------------------------

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
        # A ping must stay cheap: importing torch here can take a long time on a cold
        # start (#2837). Until the model has loaded, nothing is allocated.
        torch = sys.modules.get("torch")
        if torch is None:
            return 0.0
        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / (1024 * 1024)
    except Exception:  # noqa: BLE001 — a probe, never fatal
        pass
    return 0.0


# -- loading -----------------------------------------------------------------


def _with_retries(action):
    """Run ``action``, retrying a transient download failure with a short
    backoff, the way the app's own loader does for in-process engines."""
    try:
        attempts = max(1, int(os.environ.get("OMNIVOICE_MODEL_LOAD_RETRIES", "3")))
    except ValueError:
        attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 — classified below
            text = f"{type(exc).__name__}: {exc}".lower()
            if attempt == attempts or not any(m in text for m in _TRANSIENT_MARKERS):
                raise
            time.sleep(2.0 * attempt)
    raise AssertionError("unreachable")


@contextlib.contextmanager
def _heartbeat(stdout, stage: str):
    """Progress frames every few seconds while a download may be running."""
    stop = threading.Event()

    def beat() -> None:
        pct = 1
        while not stop.wait(_HEARTBEAT_S):
            pct = min(pct + 1, 99)
            _send(stdout, {"op": "progress", "stage": stage, "percent": pct})

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_HEARTBEAT_S + 1)


def _configure_tokenizer_cache(model):
    """Keep cache roots stable and reuse a usable legacy tokenizer at load time."""
    if getattr(model, "_voicestudio_tokenizer_cache_configured", False):
        return model
    from huggingface_hub.constants import HF_HUB_CACHE  # noqa: PLC0415

    model._resolve_hf_cache_dir = lambda: HF_HUB_CACHE
    model._patch_hf_dynamic_module_cache_dir = lambda cache_dir: None
    legacy_cache = Path(inspect.getfile(type(model))).parent / ".cache" / "huggingface"
    if legacy_cache.is_dir() and callable(getattr(model, "_load_resolved_text_tokenizer", None)):
        # A strong reference to the bound loader would keep discarded GPU models
        # alive until cyclic GC runs. The active caller already owns the model.
        original_loader = weakref.WeakMethod(model._load_resolved_text_tokenizer)
        use_fast = bool(getattr(getattr(model, "config", None), "tokenizer_use_fast", True))

        def load_tokenizer_with_cache_fallback(resolved_path, cache_dir):
            """Try existing Hub caches offline before the usual first-use loader."""
            if not os.path.exists(os.path.expanduser(resolved_path)):
                from transformers import AutoTokenizer  # noqa: PLC0415

                for candidate in (cache_dir, str(legacy_cache)):
                    try:
                        tokenizer = AutoTokenizer.from_pretrained(
                            resolved_path, cache_dir=candidate, use_fast=use_fast,
                            trust_remote_code=True, local_files_only=True,
                        )
                        # Some backends construct an empty vocabulary from
                        # metadata alone; that cannot tokenize Nano's input.
                        if tokenizer.vocab_size > 0:
                            return tokenizer
                    except Exception:  # noqa: BLE001 — cache probes are best-effort
                        # The original loader below retains the normal failure
                        # and first-use download behavior if neither cache works.
                        pass
            loader = original_loader()
            if loader is None:
                raise RuntimeError("MOSS-TTS-Nano model has been unloaded")
            return loader(resolved_path=resolved_path, cache_dir=cache_dir)

        model._load_resolved_text_tokenizer = load_tokenizer_with_cache_fallback
    model._voicestudio_tokenizer_cache_configured = True
    return model


def _load_service(stdout):
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    with _heartbeat(stdout, "loading_model"):
        from moss_tts_nano.defaults import (  # type: ignore[import-not-found]  # noqa: PLC0415
            DEFAULT_AUDIO_TOKENIZER_PATH,
            DEFAULT_CHECKPOINT_PATH,
        )
        from moss_tts_nano_runtime import NanoTTSService  # type: ignore[import-not-found]  # noqa: PLC0415

        service = NanoTTSService(
            checkpoint_path=os.environ.get("OMNIVOICE_MOSS_TTS_MODEL", DEFAULT_CHECKPOINT_PATH),
            audio_tokenizer_path=os.environ.get(
                "OMNIVOICE_MOSS_TTS_TOKENIZER", DEFAULT_AUDIO_TOKENIZER_PATH
            ),
            # Upstream writes every synthesis to a file; keep them out of the
            # install folder (and see _handle_synthesize: one file, reused).
            output_dir=tempfile.mkdtemp(prefix="moss-tts-nano-"),
        )
        # Upstream's text-tokenizer loader creates another HF cache beside its
        # dynamically imported model file, then relocates HF_MODULES_CACHE into
        # it. This defeats the app's short Windows cache path (#3137) and makes
        # the tokenizer miss files already downloaded into the shared Hub cache.
        # Keep both cache roots chosen by Hugging Face, including user overrides.
        load_model = service._load_model_locked

        def load_model_with_shared_cache():
            """Preserve cache roots for every model instance, including reloads."""
            return _configure_tokenizer_cache(load_model())

        # Inference errors and dtype recovery can discard and reload the model.
        service._load_model_locked = load_model_with_shared_cache
        _with_retries(lambda: service.preload(load_model=True))
        _SERVICE = service
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _SERVICE


# -- synthesis ---------------------------------------------------------------


def _mono_pcm_b64(result: dict) -> tuple[str, int]:
    """Upstream's waveform, downmixed to mono at NANO_SAMPLE_RATE, as base64
    int16 PCM. The in-process engine downmixed the same way."""
    import numpy as np  # noqa: PLC0415

    wav = np.asarray(result["waveform_numpy"], dtype=np.float32)
    if wav.ndim == 2:  # (samples, channels): the layout upstream returns
        wav = wav.mean(axis=1)
    sr = int(result.get("sample_rate") or NANO_SAMPLE_RATE)
    if sr != NANO_SAMPLE_RATE:
        import torch  # noqa: PLC0415
        import torchaudio  # noqa: PLC0415

        wav = torchaudio.functional.resample(torch.from_numpy(wav), sr, NANO_SAMPLE_RATE).numpy()
    wav = np.clip(wav, -1.0, 1.0)
    pcm = (wav * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(wav.shape[-1])


def _handle_synthesize(msg: dict, stdout) -> None:
    global _WARM
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    ref_audio = msg.get("ref_audio") or None
    if ref_audio and _URL_RE.match(str(ref_audio)):
        raise ValueError(
            "ref_audio must be a local file path; URLs are not accepted (local-first)."
        )
    service = _load_service(stdout)
    kwargs = {
        "text": text,
        # Reference cloning, as the in-process engine did; with no clip,
        # upstream uses its default voice preset.
        "mode": "voice_clone",
        "prompt_audio_path": ref_audio,
        "output_audio_path": os.path.join(str(service.output_dir), "last.wav"),
    }
    if _WARM:
        result = service.synthesize(**kwargs)
    else:
        with _heartbeat(stdout, "loading_model"):
            result = _with_retries(lambda: service.synthesize(**kwargs))
        _WARM = True
    pcm_b64, n_samples = _mono_pcm_b64(result)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": NANO_SAMPLE_RATE,
        "n_samples": n_samples,
    })


# -- main loop ---------------------------------------------------------------


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428): the
    # libraries this loads print to fd 1, and those bytes would otherwise
    # interleave with the length-prefixed frames.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    _send(stdout, {"op": "ready", "engine": "moss-tts-nano", "sample_rate": NANO_SAMPLE_RATE})

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
