"""pockettts sidecar entry point (#1306).

Runs Kyutai PocketTTS in a child process under the parent's own interpreter
(same pins), so a wedged generate can be hard-killed by the parent to reclaim
memory. Mirrors engines/omnivoice_subprocess/main.py.

Wire protocol: length-prefixed JSON over stdin/stdout, byte-identical to
services/subprocess_backend.py::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow:
    1. sidecar -> parent: {"op":"ready","engine":"pockettts","sample_rate":24000}
    2. parent -> sidecar: {"op":"ping"} -> {"op":"pong","vram_mb":0}
    3. parent -> sidecar: {"op":"synthesize","text":"...",
                            "ref_audio":"/path/to/ref.wav",
                            "language":"it"}
       -> {"op":"progress",...} (cold load) then
       -> {"op":"audio","audio_pcm_b64":"...","sample_rate":24000,
           "n_samples":N}
    4. parent -> sidecar: {"op":"shutdown"} -> exit 0

Stdlib-only at import time; torch + pocket_tts are imported lazily on the first
synthesize so the ready frame fits the parent's 30s spawn handshake even on a
cold filesystem.

Languages: PocketTTS ships one model per language (en/fr/de/pt/it/es), selected
by ``language``. The first synth in a given language cold-loads + caches that
model; later calls reuse it. (The HF model card's "English only at the moment"
line is stale; the GitHub README and pocket-tts 2.1.0 confirm six languages.)

Note: ``TTSModel.load_model(language=...)`` pulls the gated kyutai weights from
HuggingFace, so it needs HF auth + the access agreement accepted. A failure here
currently surfaces as a raw error frame; the typed "weights are gated" preflight
(condition 6) is built on top of this shape, not in it.
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import sys
import threading
import traceback
from collections import OrderedDict

# Mirrors services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024

#: PocketTTS emits 24 kHz mono. Re-read from the loaded model on each generate.
POCKETTTS_SAMPLE_RATE = 24_000

#: OmniVoice language (ISO code, name, or sentinel) -> pocket-tts model language.
#: "auto"/"multi"/"na"/None default to english (the library default).
_LANG_MAP = {
    "en": "english", "eng": "english", "english": "english",
    "fr": "french", "fra": "french", "french": "french",
    "de": "german", "deu": "german", "german": "german",
    "pt": "portuguese", "por": "portuguese", "portuguese": "portuguese",
    "it": "italian", "ita": "italian", "italian": "italian",
    "es": "spanish", "esp": "spanish", "spanish": "spanish",
}

#: Default preset voice per language when no reference clip is supplied (public
#: presets from kyutai/tts-voices; voice source does not affect synth speed).
_DEFAULT_VOICE_BY_LANG = {
    "english": "alba",
    "italian": "giovanni",
    "spanish": "lola",
    "german": "juergen",
    "portuguese": "rafael",
    "french": "estelle",
}

#: Emit a progress frame at least this often during a cold load so the parent's
#: recv watchdog doesn't kill a healthy sidecar on a slow first download.
_HEARTBEAT_S = 5.0

#: ref_audio must be a local file path, not a URL (local-first; no SSRF).
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

#: Bound the per-(language, voice) voice-state cache (LRU) so a long session
#: with many distinct reference clips can't grow memory without limit.
_VOICE_CACHE_MAX = 8

# Per-language model cache: load_model(language=...) is slow and PocketTTS ships
# one model per language, so cache each. Bounded by the distinct languages used
# in a session (at most six).
_MODELS: dict[str, object] = {}

# (language, voice) -> voice_state, LRU-bounded to _VOICE_CACHE_MAX entries.
# get_state_for_audio_prompt is relatively slow, so cache per (language, voice)
# to avoid re-encoding on every call.
_voice_cache: OrderedDict[str, object] = OrderedDict()


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
    """CPU-only engine: always 0. Kept for protocol parity with the parent."""
    return 0.0


# -- model loading (lazy, on first synthesize per language) ------------------


def _pocket_language(raw) -> str:
    """Map an OmniVoice language value to a pocket-tts model language. A specific
    but unsupported language raises rather than silently fall back to English and
    mispronounce; empty / "auto" / "multi" / "na" default to English."""
    if not raw:
        return "english"
    s = str(raw).strip().lower()
    if s in ("", "auto", "multi", "na"):
        return "english"
    if s in _LANG_MAP:
        return _LANG_MAP[s]
    raise ValueError(
        f"PocketTTS does not support language {raw!r}; supported: en, fr, de, pt, it, es."
    )


def _load_model(stdout, language: str):
    """Cold-construct the PocketTTS model for ``language`` (cached per language).
    Emits progress frames for the parent watchdog. Raises on failure (e.g.
    gated-weights access without HF auth); the caller emits an error frame and
    stays alive for a retry."""
    model = _MODELS.get(language)
    if model is not None:
        return model

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    # Heartbeat: a cold load (gated weights download) can outlast the parent's
    # recv timeout. Emit a progress frame every few seconds while it runs so the
    # parent's watchdog sees activity and does not kill a healthy sidecar.
    stop = threading.Event()

    def _heartbeat() -> None:
        pct = 1
        while not stop.wait(_HEARTBEAT_S):
            pct = min(pct + 1, 99)
            _send(stdout, {"op": "progress", "stage": "loading_model", "percent": pct})

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        from pocket_tts import TTSModel  # type: ignore[import-not-found]  # noqa: PLC0415

        model = TTSModel.load_model(language=language)
        _MODELS[language] = model
    finally:
        stop.set()
        hb.join(timeout=_HEARTBEAT_S + 1)
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return model


def _voice_state(model, language: str, ref_audio):
    """Return a (cached, LRU-bounded) voice state for ``ref_audio`` (a local
    file path) or the language's default preset voice when none is given. URLs
    are rejected to keep the sidecar local-first (no SSRF)."""
    if ref_audio and _URL_RE.match(ref_audio):
        raise ValueError(
            "ref_audio must be a local file path; URLs are not accepted (local-first)."
        )
    voice = ref_audio or _DEFAULT_VOICE_BY_LANG.get(language, "alba")
    # For a local file ref, fold mtime+size into the cache key so a file replaced
    # at the same path does not return a stale voice from the previous contents.
    fingerprint = ""
    if ref_audio:
        try:
            st = os.stat(ref_audio)
            fingerprint = f"|m{st.st_mtime_ns}s{st.st_size}"
        except OSError:
            fingerprint = ""
    key = f"{language}|{voice}{fingerprint}"
    state = _voice_cache.get(key)
    if state is not None:
        _voice_cache.move_to_end(key)
        return state
    state = model.get_state_for_audio_prompt(voice)
    _voice_cache[key] = state
    if len(_voice_cache) > _VOICE_CACHE_MAX:
        _voice_cache.popitem(last=False)  # evict oldest
    return state


def _tensor_to_pcm_b64(audio, sample_rate: int) -> tuple[str, int, int]:
    """Convert a float waveform in [-1, 1] to base64 int16 PCM."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).squeeze()
    if arr.ndim > 1:
        raise ValueError(
            f"expected mono audio (1-D after squeeze), got shape {arr.shape}; "
            f"PocketTTS returns mono, so a multi-channel array means an upstream change."
        )
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sample_rate), int(arr.shape[-1])


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    language = _pocket_language(msg.get("language"))
    model = _load_model(stdout, language)
    ref_audio = msg.get("ref_audio") or None
    voice_state = _voice_state(model, language, ref_audio)

    audio = model.generate_audio(voice_state, text)
    sample_rate = int(getattr(model, "sample_rate", POCKETTTS_SAMPLE_RATE))

    pcm_b64, sr, n_samples = _tensor_to_pcm_b64(audio, sample_rate)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


# -- main loop ---------------------------------------------------------------


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
        "engine": "pockettts",
        "sample_rate": POCKETTTS_SAMPLE_RATE,
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
