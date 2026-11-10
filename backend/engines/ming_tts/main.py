"""Ming-3.0-tts sidecar entry point (issue #3793).

Runs inside ``engines/ming_tts/.venv`` (or the user's
``${OMNIVOICE_MING_TTS_DIR}/.venv``) with upstream's ``transformers==4.46.3`` /
``torch==2.6.0``, isolated from the OmniVoice parent (``transformers>=5.3``).

Stdlib-only at import time; ``ming_tts`` + torch are imported lazily on the
first synthesize op so the ``ready`` frame fits inside the parent's 30 s spawn
handshake.

Wire protocol — length-prefixed JSON over stdin/stdout, byte-identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow: ready -> ping/pong -> synthesize (-> progress, -> audio) -> shutdown.

Upstream API (inclusionAI/Ming-3.0-tts ``ming_tts.py``, read 2026-11-10; not run
here): ``MingTTS(model_path, device="cuda:0", text_normalization=True)`` and
``synthesize(task="TTS", text=, language_id=, prompt_wav_path=, prompt_text=,
use_spk_emb=, cfg=)`` -> ``(1, samples)`` waveform at ``model.sample_rate``
(44.1 kHz per the checkpoint config). Upstream is **CUDA-tested only** and
loads bf16 straight onto the device, so this sidecar refuses to run without a
CUDA device instead of guessing at CPU/MPS behaviour.

Restrictions: NO imports from OmniVoice parent code. NO logging of os.environ.
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


#: Ming-3.0-tts-0.5b audio tokenizer rate (checkpoint config.json
#: ``audio_tokenizer_config.sample_rate``); re-read from ``model.sample_rate``.
MING_SAMPLE_RATE = 44100

_DEFAULT_MODEL = "inclusionAI/Ming-3.0-tts-0.5b"
#: Reviewed immutable revision of the default repo (kept equal to the pin in
#: backend/config/model_licenses.json by tests/test_ming_tts.py).
_DEFAULT_REVISION = "7247f4c94a5b2e987404ed28a5cfec41b1a4ca61"


def _model_source() -> str:
    """Local checkpoint path, or the pinned snapshot of a Hub repo.

    A local directory is used as given. A Hub repo id is resolved to a local
    snapshot at an immutable commit, never a branch: the default repo uses the
    reviewed revision, any other needs ``OMNIVOICE_MING_TTS_REVISION``.
    """
    import re

    model = os.environ.get("OMNIVOICE_MING_TTS_MODEL", "").strip() or _DEFAULT_MODEL
    if os.path.isdir(model):
        return model
    revision = _DEFAULT_REVISION if model == _DEFAULT_MODEL else (
        os.environ.get("OMNIVOICE_MING_TTS_REVISION", "").strip())
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError(
            "A custom Ming model needs OMNIVOICE_MING_TTS_REVISION=<40-character "
            "commit SHA>; branches and tags are mutable. A local directory "
            "needs no revision."
        )
    from huggingface_hub import snapshot_download

    # Generation never downloads: weights arrive only from the Model Catalogue
    # row the user starts, which fills this same cache at this revision.
    try:
        return snapshot_download(model, revision=revision, local_files_only=True)
    except Exception as exc:
        raise RuntimeError(
            f"Ming-3.0-tts weights ({model}) are not installed. Download them "
            "from Model Catalogue (Ming-3.0-tts weights row), then try again. "
            "Nothing is downloaded during generation."
        ) from exc

#: ISO code -> the ``language_id`` name upstream's README passes. Upstream
#: lists exactly these seven (tts_tasks._TARGET_LANGUAGE_NAMES).
_LANGUAGE_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "fr": "French", "de": "German", "es": "Spanish",
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
    """This sidecar's own GPU memory in MB (MM2-08). 0 on CPU. Never raises."""
    try:
        # A ping must stay cheap: importing torch here can take a long time on a cold
        # start (#2837). Until the model has loaded, nothing is allocated.
        torch = sys.modules.get("torch")
        if torch is None:
            return 0.0
        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated() / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


# ── model loading (lazy, on first synthesize) ─────────────────────────────


_model = None


def _clone_dir() -> str:
    raw = os.environ.get("OMNIVOICE_MING_TTS_DIR", "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else ""


def _prepare_clone() -> None:
    """Make ``import ming_tts`` resolve from the clone and run from its root.

    Upstream is a flat set of modules (``ming_tts.py``, ``tts_tasks.py``,
    ``frontend/`` ...) with no packaging metadata, so the clone root must be on
    ``sys.path`` (position 0 beats any stale copy). Its relative assets are
    resolved from the clone, so the cwd moves there too; cache variables that
    were relative to the launch directory are made absolute first so an existing
    Hugging Face cache keeps its meaning.
    """
    clone = _clone_dir()
    if not clone:
        return
    os.environ["OMNIVOICE_MING_TTS_DIR"] = clone
    for name in (
        "OMNIVOICE_MING_TTS_MODEL", "HF_HOME", "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME",
    ):
        value = os.environ.get(name)
        if value and not os.path.isabs(value) and (
                name != "OMNIVOICE_MING_TTS_MODEL" or os.path.exists(value)):
            os.environ[name] = os.path.abspath(os.path.expanduser(value))
    if clone not in sys.path:
        sys.path.insert(0, clone)
    if os.path.isdir(clone):
        try:
            os.chdir(clone)
        except OSError:
            pass  # read-only / permission: upstream's own errors will say so


def _load_model(stdout):
    """Cold-construct ``MingTTS`` on CUDA. Raises a clear error otherwise."""
    global _model
    if _model is not None:
        return _model

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    _prepare_clone()
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "Ming-3.0-tts needs an NVIDIA CUDA GPU: upstream only tests CUDA "
            "(bf16) and this venv's PyTorch sees none. Pick another engine, "
            "or see docs/engines/ming-tts.md."
        )
    from ming_tts import MingTTS  # type: ignore[import-not-found]

    # Text normalization needs pynini, which has no universal wheel; upstream
    # loads it lazily and documents running without it. Use it when present.
    try:
        import pynini  # noqa: F401
        normalize = True
    except Exception:
        normalize = False

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})
    model_path = _model_source()
    _model = MingTTS(model_path, device="cuda:0", text_normalization=normalize)

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _model


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
    """Map OmniVoice's language value to upstream's ``language_id``.

    Returns the name for one of the seven supported languages, ``None`` for an
    empty / "auto" request (upstream then sends no target-language line), and
    raises ``ValueError`` for anything else so the user gets a language error,
    never audio in the wrong language.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower().replace("_", "-")
    if not s or s == "auto":
        return None
    code = s.split("-", 1)[0]
    if code in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[code]
    for name in _LANGUAGE_NAMES.values():
        if s == name.lower():
            return name
    raise ValueError(
        f"Ming-3.0-tts supports zh, en, ja, ko, fr, de and es; got {raw!r}."
    )


def _handle_synthesize(msg: dict, stdout) -> None:
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    ref_audio = msg.get("ref_audio")
    if not isinstance(ref_audio, str) or not ref_audio.strip():
        raise ValueError(
            "Ming-3.0-tts clones a voice from a reference clip. Pass ref_audio= "
            "with a path to a speaker reference."
        )
    language = _normalize_language(msg.get("language"))  # before any model load
    model = _load_model(stdout)

    kwargs: dict = {
        "task": "TTS",
        "text": text,
        "prompt_wav_path": ref_audio,
        "use_spk_emb": True,
        "cfg": float(msg.get("guidance_scale", 2.0)),
    }
    if language:
        kwargs["language_id"] = language
    # Upstream only uses the acoustic prompt together with its transcript; a
    # clip alone still conditions the speaker embedding.
    ref_text = msg.get("ref_text")
    if isinstance(ref_text, str) and ref_text.strip():
        kwargs["prompt_text"] = ref_text.strip()

    audio = model.synthesize(**kwargs)
    sample_rate = int(getattr(model, "sample_rate", MING_SAMPLE_RATE))
    if sample_rate != MING_SAMPLE_RATE:
        raise RuntimeError(
            f"Ming model sample_rate {sample_rate} differs from the declared "
            f"{MING_SAMPLE_RATE}; refusing to emit mislabelled audio."
        )

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
        "engine": "ming-tts",
        "sample_rate": MING_SAMPLE_RATE,
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
