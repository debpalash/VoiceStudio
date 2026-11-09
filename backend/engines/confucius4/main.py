"""Confucius4-TTS sidecar entry point (issue #590).

Runs inside ``engines/confucius4/.venv`` (or the user's
``${OMNIVOICE_CONFUCIUS4_TTS_DIR}/.venv``), isolated from the OmniVoice parent.
Same isolation rationale as the IndexTTS / MOSS-TTS-v1.5 / dots.tts sidecars.

Stdlib-only at import time; ``confuciustts`` + torch are imported lazily on the
first synthesize op so the ``ready`` frame fits inside the parent's 30 s spawn
handshake.

Wire protocol — length-prefixed JSON over stdin/stdout, byte-identical to
``backend/services/subprocess_backend.py``::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow: ready → ping/pong → synthesize (→ progress, → audio) → shutdown.

Status (#590): the model API below
(``confuciustts.cli.inference.ConfuciusTTS(config_path=…, device=…)`` and
``model.generate(text=, lang=, prompt_wav=)`` → audio tensor, ``model.sample_rate``)
is **validated end-to-end** (2026-07-02, Apple Silicon, CPU): live generate()
produced audible speech at 22 050 Hz. This sidecar's pure logic is unit-tested
in ``tests/test_confucius4_sidecar.py``. Opt-in, so it affects no one until
enabled.

Restrictions: NO imports from OmniVoice parent code. NO logging of os.environ.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import traceback

MAX_FRAME_BYTES = 64 * 1024 * 1024

#: Upstream BigVGAN vocoder rate — ``target_sample_rate: 22050`` in
#: ``config/inference_config.yaml``, confirmed by a live end-to-end run
#: (2026-07-02). The real value is still re-read from ``model.sample_rate``
#: on each generate() so a future upstream change can't corrupt audio.
CONFUCIUS_SAMPLE_RATE = 22050


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


_model = None


def _config_path() -> str:
    """Locate Confucius4's inference config (``config/inference_config.yaml``)
    under the clone, or an explicit override."""
    explicit = os.environ.get("OMNIVOICE_CONFUCIUS4_CONFIG")
    if explicit:
        return explicit
    clone = os.environ.get("OMNIVOICE_CONFUCIUS4_TTS_DIR", "")
    return os.path.join(clone, "config", "inference_config.yaml")


def _ensure_clone_on_sys_path() -> None:
    """Make ``import confuciustts`` resolve from the user's clone.

    Upstream Confucius4-TTS is **not pip-installable** (no pyproject.toml /
    setup.py as of 2026-07); its own ``example.py`` sys.path-inserts the repo
    root instead. Mirror that here so the sidecar works from a plain
    ``uv pip install -r requirements.txt`` venv. Inserted at position 0 so the
    clone the user pointed at always wins over any stale installed copy.
    """
    clone = os.environ.get("OMNIVOICE_CONFUCIUS4_TTS_DIR", "")
    if clone and clone not in sys.path:
        sys.path.insert(0, clone)


def _chdir_to_clone_if_available() -> None:
    """Switch the sidecar's cwd to the Confucius4 clone if one is configured.

    Upstream's ``config/inference_config.yaml`` ships with paths relative to
    the clone root (``./checkpoints``, ``./checkpoints/wav2vec2bert_stats.pt``).
    Without chdir, those paths resolve against whatever directory the parent
    was started from — producing ``FileNotFoundError`` on the model file and,
    potentially selecting a different cache when a relative HF cache override
    is configured (#2099). Anchoring cwd once at start-up keeps the
    rest of the sidecar's relative-path behaviour identical to upstream.
    """
    clone = os.environ.get("OMNIVOICE_CONFUCIUS4_TTS_DIR", "").strip()
    if not clone:
        return
    clone_path = os.path.abspath(os.path.expanduser(clone))
    # These values were relative to the parent launch directory. Canonicalize
    # before chdir so imports, explicit config, cache reuse, and retries agree.
    os.environ["OMNIVOICE_CONFUCIUS4_TTS_DIR"] = clone_path
    for name in (
        "OMNIVOICE_CONFUCIUS4_CONFIG", "HF_HOME", "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME",
    ):
        value = os.environ.get(name)
        if value and not os.path.isabs(value):
            os.environ[name] = os.path.abspath(os.path.expanduser(value))
    if os.path.isdir(clone_path):
        try:
            os.chdir(clone_path)
        except OSError:
            # Permission / read-only filesystem — non-fatal; upstream's paths
            # will then fail loudly and the user will see a clear error.
            pass


def _patch_xpu_mem_get_info(torch) -> None:
    """Pinned copy of ``backend/core/xpu_compat.py`` (this sidecar cannot
    import parent code): Intel Arc drivers on Windows refuse the free-memory
    query that transformers/accelerate call while loading onto XPU, so fall
    back to ``total - reserved``. Kept in step by
    tests/test_xpu_mem_get_info_shim.py."""
    xpu = getattr(torch, "xpu", None)
    original = getattr(xpu, "mem_get_info", None)
    if original is None or getattr(original, "_omnivoice_fallback", False):
        return

    def mem_get_info(device=None):
        try:
            return original(device)
        except RuntimeError:
            if device is None:
                idx = int(xpu.current_device())
            elif isinstance(device, int):
                idx = device
            else:
                idx = torch.device(device).index
                idx = int(xpu.current_device()) if idx is None else int(idx)
            total = int(xpu.get_device_properties(idx).total_memory)
            return max(total - int(xpu.memory_reserved(idx)), 0), total

    mem_get_info._omnivoice_fallback = True
    xpu.mem_get_info = mem_get_info


def _load_model(stdout):
    """Cold-construct using an available torch accelerator, with CPU fallback."""
    global _model
    if _model is not None:
        return _model

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    _chdir_to_clone_if_available()
    _ensure_clone_on_sys_path()
    import torch
    from confuciustts.cli.inference import ConfuciusTTS  # type: ignore[import-not-found]

    try:
        # Existing manually provisioned venvs may predate torch.accelerator.
        current_accelerator = getattr(getattr(torch, "accelerator", None), "current_accelerator", None)
        if current_accelerator is None:
            device = torch.device("cuda") if torch.cuda.is_available() else None
        else:
            device = current_accelerator(check_available=True)
        device = device.type if device is not None else "cpu"  # 'cuda', 'npu', 'mps', 'xpu', 'cpu'
    except Exception:
        device = "cpu"  # Broken accelerator drivers must not block CPU loading.
    if device == "mps":
        device = "cpu"  # MPS was slower than CPU in the existing validation run
    if device == "xpu":
        _patch_xpu_mem_get_info(torch)
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 50})

    _model = ConfuciusTTS(config_path=_config_path(), device=device)

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _model


def _tensor_to_pcm_b64(audio, sample_rate: int) -> tuple[str, int, int]:
    import numpy as np
    arr = audio.detach().to("cpu").float().numpy() if hasattr(audio, "detach") else np.asarray(audio)
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
    """Confucius4 expects an ISO-ish language code (e.g. 'en', 'zh'). Empty /
    'auto' → 'en' as a safe default (the API requires a lang)."""
    if not raw or not isinstance(raw, str):
        return "en"
    s = raw.strip().lower()
    if not s or s == "auto":
        return "en"
    return s[:2] if (len(s) >= 2 and s[:2].isalpha()) else s


def _handle_synthesize(msg: dict, stdout) -> None:
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    ref_audio = msg.get("ref_audio")
    if not isinstance(ref_audio, str) or not ref_audio.strip():
        raise ValueError(
            "Confucius4-TTS requires a reference audio for voice cloning "
            "(prompt_wav). Pass ref_audio= with a path to a speaker reference clip."
        )
    model = _load_model(stdout)

    gen_kwargs: dict = {
        "text": text, "lang": _normalize_language(msg.get("language")),
        "prompt_wav": ref_audio,
    }

    audio = model.generate(**gen_kwargs)
    sample_rate = int(getattr(model, "sample_rate", CONFUCIUS_SAMPLE_RATE))

    pcm_b64, sr, n_samples = _tensor_to_pcm_b64(audio, sample_rate)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


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

    _send(stdout, {
        "op": "ready",
        "engine": "confucius4-tts",
        "sample_rate": CONFUCIUS_SAMPLE_RATE,
    })

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
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
                _send(stdout, {"op": "error", "stage": "dispatch",
                               "message": f"unknown op: {op!r}"})
        except Exception as exc:
            _send(stdout, {
                "op": "error", "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
