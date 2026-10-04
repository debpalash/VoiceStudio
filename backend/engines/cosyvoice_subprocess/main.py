"""cosyvoice sidecar: CosyVoice in the engine's own venv (one-click install).

Launched as ``<engine venv python> main.py`` by CosyVoiceSubprocessBackend.
The venv holds only CosyVoice's own dependencies, so this file imports nothing
from the app. CosyVoice is not a package: like upstream's own ``example.py``,
this puts the checkout and its ``third_party/Matcha-TTS`` on ``sys.path``.

The mode mapping mirrors the in-process CosyVoiceBackend. For a CosyVoice 3
model, prompts also take the system-prompt prefix upstream's v3 examples use
(``You are a helpful assistant.<|endofprompt|>``), and with no reference clip
the model speaks in the voice of upstream's own sample prompt, because v3
ships no built-in speakers.

Wire protocol: identical to the other sidecars (engines/pockettts/main.py).
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import struct
import subprocess
import sys
import threading
import traceback

# Mirrors services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024
#: The rate the engine reports; a model's output is resampled to it if needed.
COSYVOICE_SAMPLE_RATE = 24_000
_HEARTBEAT_S = 5.0
#: ref_audio must be a local file path, not a URL (local-first; no SSRF).
_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
#: Where the installer puts the CosyVoice 3 weights, inside the checkout.
_MANAGED_MODEL_SUBDIR = ("pretrained_models", "Fun-CosyVoice3-0.5B")
#: Upstream's sample prompt, the default voice for a v3 model.
_DEFAULT_PROMPT_CLIP = ("asset", "zero_shot_prompt.wav")
_ENDOFPROMPT = "<|endofprompt|>"
_SYSTEM_PROMPT = "You are a helpful assistant."
#: Cross-lingual language tags for v1/v2 models (the in-process mapping).
_LANG_TAGS = {
    "zh": "<|zh|>", "en": "<|en|>", "ja": "<|ja|>",
    "ko": "<|ko|>", "yue": "<|yue|>", "de": "<|de|>",
    "es": "<|es|>", "fr": "<|fr|>", "it": "<|it|>",
    "ru": "<|ru|>",
}

_MODEL = None

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
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated()) / (1024 * 1024)
    except Exception:  # noqa: BLE001 — a probe, never fatal
        pass
    return 0.0


@contextlib.contextmanager
def _heartbeat(stdout, stage: str):
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


# -- loading -----------------------------------------------------------------


def _checkout() -> str:
    path = os.environ.get("OMNIVOICE_COSYVOICE_DIR")
    if not path:
        raise RuntimeError(
            "OMNIVOICE_COSYVOICE_DIR is not set. Reinstall CosyVoice from "
            "Model Catalogue → Engines."
        )
    return path


def _model_dir(checkout: str) -> str:
    override = os.environ.get("OMNIVOICE_COSYVOICE_MODEL")
    if not override:
        return os.path.join(checkout, *_MANAGED_MODEL_SUBDIR)
    if not os.path.isdir(override):
        # Falling back to the installed model would synthesize with a model
        # and voice the user did not choose, while model_identity() still
        # named theirs. Say what is wrong instead.
        raise RuntimeError(
            f"OMNIVOICE_COSYVOICE_MODEL points at {override}, which is not a "
            "folder. Point it at a CosyVoice model folder, or clear it to use "
            "the model the one-click install downloaded."
        )
    return override


@contextlib.contextmanager
def _qwen_full_precision_load():
    """Load trained fp32 weights before any explicit upstream fp16 conversion.

    Transformers 5 otherwise constructs Qwen in the blank checkpoint's bf16
    dtype. Loading llm.pt into it rounds trained weights irreversibly; calling
    float() afterwards cannot recover them. This sidecar loads one model at a
    time, and restores the inherited classmethod even when loading fails.
    """
    import torch
    import transformers

    Qwen2ForCausalLM = getattr(transformers, "Qwen2ForCausalLM", None)
    if Qwen2ForCausalLM is None:
        yield  # Legacy CosyVoice 1 environments do not use Qwen.
        return
    owned = Qwen2ForCausalLM.__dict__.get("from_pretrained")
    original = Qwen2ForCausalLM.from_pretrained

    def load(cls, *args, **kwargs):
        # torch_dtype also supports existing user-managed Transformers 4 envs.
        if "dtype" not in kwargs and "torch_dtype" not in kwargs:
            kwargs["torch_dtype"] = torch.float32
        return original(*args, **kwargs)

    Qwen2ForCausalLM.from_pretrained = classmethod(load)
    try:
        yield
    finally:
        if owned is None:
            del Qwen2ForCausalLM.from_pretrained
        else:
            Qwen2ForCausalLM.from_pretrained = owned


def _qwen_attention_mask(xs, masks, cache):
    """Include cached prompt tokens in upstream's unpadded incremental mask."""
    if cache is None or masks.shape[-1] != xs.shape[1]:
        return masks
    length = cache.get_seq_length() if hasattr(cache, "get_seq_length") else cache[0][0].shape[-2]
    if not length:
        return masks
    import torch

    prefix = masks.new_ones((*masks.shape[:-1], length))
    return torch.cat((prefix, masks), dim=-1)


def _repair_qwen_cache(model):
    from cosyvoice.llm import llm as llm_module

    Qwen2Encoder = getattr(llm_module, "Qwen2Encoder", None)
    if Qwen2Encoder is None:
        return
    components = getattr(model, "model", model)
    encoder = getattr(getattr(components, "llm", None), "llm", None)
    if not isinstance(encoder, Qwen2Encoder):
        return  # CosyVoice 1 uses a different encoder.
    original = encoder.forward_one_step

    def forward_one_step(xs, masks, cache=None):
        return original(xs, _qwen_attention_mask(xs, masks, cache), cache)

    # Modern Transformers interprets a one-token mask literally, hiding the
    # cached text/voice prompt. Adapt only this instance, preserving full masks.
    encoder.forward_one_step = forward_one_step


def _windows_rocm() -> bool:
    if sys.platform != 'win32':
        return False
    import torch

    return bool(torch.version.hip)


def _decode_windows_rocm_audio(uri):
    import imageio_ffmpeg
    import numpy as np
    import torch

    if isinstance(uri, (str, bytes, os.PathLike)):
        source = os.path.abspath(os.fsdecode(uri))
        input_audio = None
    elif hasattr(uri, 'read') and hasattr(uri, 'seek'):
        uri.seek(0)
        source = 'pipe:0'
        input_audio = uri.read()
    else:
        raise TypeError('CosyVoice reference audio must be a local file or readable stream')
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
         '-protocol_whitelist', 'file,pipe', '-i', source,
         '-map', '0:a:0', '-vn', '-ar', '24000', '-ac', '1', '-f', 'f32le', 'pipe:1'],
        input=input_audio, capture_output=True, timeout=120, check=False,
    )
    if result.returncode or not result.stdout or len(result.stdout) % 4:
        error = result.stderr.decode('utf-8', errors='replace')[-500:]
        raise RuntimeError(f'CosyVoice reference audio could not be decoded by FFmpeg: {error}')
    samples = np.frombuffer(result.stdout, dtype='<f4').copy()
    return torch.from_numpy(samples).reshape(1, -1), 24000


def _install_windows_rocm_audio_adapter() -> None:
    if not _windows_rocm():
        return
    import soundfile
    import torch
    import torchaudio

    original_load = torchaudio.load
    if getattr(original_load, '_cosyvoice_rocm_audio', False):
        return

    def load(uri, *, backend=None, **kwargs):
        if backend not in (None, 'soundfile') or kwargs:
            raise ValueError('CosyVoice Windows ROCm audio load does not support these options')
        try:
            audio = soundfile.SoundFile(uri)
        except soundfile.SoundFileError:
            return _decode_windows_rocm_audio(uri)
        with audio:
            samples = torch.from_numpy(audio.read(dtype='float32', always_2d=True).T.copy())
            return samples, audio.samplerate

    load._cosyvoice_rocm_audio = True
    torchaudio.load = load


def _verify_windows_rocm_audio() -> None:
    import imageio_ffmpeg
    import tempfile
    import torchaudio

    formats = {
        'wav': ('wav', 'pcm_s16le'), 'mp3': ('mp3', 'libmp3lame'),
        'm4a': ('ipod', 'aac'), 'flac': ('flac', 'flac'),
        'ogg': ('ogg', 'libvorbis'), 'oga': ('ogg', 'libvorbis'),
        'opus': ('opus', 'libopus'), 'aac': ('adts', 'aac'),
        'webm': ('webm', 'libopus'),
    }
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    raw_audio = struct.pack('<h', 8192) * 12000 + struct.pack('<h', -8192) * 12000
    _install_windows_rocm_audio_adapter()
    with tempfile.TemporaryDirectory(prefix='cosyvoice-rocm-audio-') as folder:
        for extension, (container, codec) in formats.items():
            path = os.path.join(folder, f'reference.{extension}')
            result = subprocess.run(
                [ffmpeg, '-nostdin', '-v', 'error', '-f', 's16le', '-ar', '24000',
                 '-ac', '1', '-i', 'pipe:0', '-c:a', codec, '-f', container, path],
                input=raw_audio, capture_output=True, timeout=30, check=False,
            )
            if result.returncode:
                error = result.stderr.decode('utf-8', errors='replace')[-500:]
                raise RuntimeError(f'FFmpeg could not create {extension} reference: {error}')
            samples, rate = torchaudio.load(path, backend='soundfile')
            if rate < 16000 or samples.numel() < 2400 or not samples.abs().max().item():
                raise RuntimeError(f'{extension} reference did not decode to usable audio')


def _verify_windows_rocm_dependencies() -> None:
    import importlib
    import importlib.metadata
    import torch

    checkout = _checkout()
    for path in (os.path.join(checkout, 'third_party', 'Matcha-TTS'), checkout):
        if path not in sys.path:
            sys.path.insert(0, path)
    for module in ('cosyvoice.cli.cosyvoice', 'cosyvoice.dataset.processor', 'matcha.utils'):
        importlib.import_module(module)

    versions = {
        'torch': '2.9.1+rocm7.2.1',
        'torchaudio': '2.9.1+rocm7.2.1',
        'torchvision': '0.24.1+rocm7.2.1',
        'rocm': '7.2.1',
        'imageio-ffmpeg': '0.6.0',
    }
    if any(importlib.metadata.version(name) != version for name, version in versions.items()):
        raise RuntimeError('CosyVoice Windows ROCm dependencies do not match the reviewed recipe')
    if not _windows_rocm() or not torch.cuda.is_available():
        raise RuntimeError('CosyVoice Windows ROCm GPU is not available')
    tensor = torch.ones((4, 4), device='cuda')
    if (tensor @ tensor)[0, 0].item() != 4:
        raise RuntimeError('CosyVoice Windows ROCm GPU kernel failed')
    _verify_windows_rocm_audio()


def _verify_windows_rocm_model_device(model) -> None:
    components = getattr(model, 'model', None)
    for name in ('llm', 'flow', 'hift'):
        component = getattr(components, name, None)
        parameter = next(component.parameters(), None) if component is not None else None
        if parameter is None or parameter.device.type != 'cuda':
            raise RuntimeError(f'CosyVoice {name} weights are not on the Radeon GPU')


def _load_model(stdout):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    checkout = _checkout()
    model_dir = _model_dir(checkout)
    # Never hand AutoModel a folder that is missing: it would treat the name as
    # a ModelScope id and start a download the user never asked for.
    if not os.path.isdir(model_dir):
        raise RuntimeError(
            f"CosyVoice model folder is missing ({model_dir}). Reinstall "
            "CosyVoice from Model Catalogue → Engines."
        )
    for path in (os.path.join(checkout, "third_party", "Matcha-TTS"), checkout):
        if path not in sys.path:
            sys.path.insert(0, path)
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})
    with _heartbeat(stdout, "loading_model"):
        _install_windows_rocm_audio_adapter()
        from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore[import-not-found]  # noqa: PLC0415

        import torch  # noqa: PLC0415

        with _qwen_full_precision_load():
            model = AutoModel(model_dir=model_dir)
        _repair_qwen_cache(model)
        if not torch.cuda.is_available():
            # Upstream's Qwen weights may load as bf16 even though its CPU
            # token inputs are fp32. Keep CUDA's chosen precision unchanged.
            components = getattr(model, "model", model)
            for name in ("llm", "flow", "hift"):
                component = getattr(components, name, None)
                if component is not None and hasattr(component, "float"):
                    component.float()
        if _windows_rocm():
            _verify_windows_rocm_model_device(model)
        _MODEL = model
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _MODEL


# -- synthesis ---------------------------------------------------------------


def _is_v3(model) -> bool:
    return type(model).__name__ == "CosyVoice3"


def _v3_prompt(text: str) -> str:
    """v3 wants the system prompt ahead of the prompt transcript or text."""
    return text if _ENDOFPROMPT in text else f"{_SYSTEM_PROMPT}{_ENDOFPROMPT}{text}"


def _instruct(text: str, v3: bool) -> str:
    if not text.endswith(_ENDOFPROMPT):
        text = f"{text}{_ENDOFPROMPT}"
    if v3 and not text.startswith(_SYSTEM_PROMPT):
        text = f"{_SYSTEM_PROMPT} {text}"
    return text


def _lang_tag(language) -> str:
    if not language:
        return ""
    full = str(language).lower()
    return _LANG_TAGS.get(full) or _LANG_TAGS.get(full[:2], "")


def _run_model(model, msg: dict):
    text = msg.get("text")
    ref_audio = msg.get("ref_audio") or None
    ref_text = msg.get("ref_text") or None
    instruct = msg.get("instruct") or None
    v3 = _is_v3(model)
    if not ref_audio and v3:
        ref_audio = os.path.join(_checkout(), *_DEFAULT_PROMPT_CLIP)
        ref_text = None  # the sample's transcript is not ours to supply
    if instruct and ref_audio:
        return model.inference_instruct2(text, _instruct(instruct, v3), ref_audio, stream=False)
    if ref_audio and ref_text:
        prompt_text = _v3_prompt(ref_text) if v3 else ref_text
        return model.inference_zero_shot(text, prompt_text, ref_audio, stream=False)
    if ref_audio:
        tts_text = _v3_prompt(text) if v3 else f"{_lang_tag(msg.get('language'))}{text}"
        return model.inference_cross_lingual(tts_text, ref_audio, stream=False)
    speakers = model.list_available_spks()
    if not speakers:
        raise ValueError("This CosyVoice model has no built-in voices; pass a reference clip.")
    return model.inference_sft(text, speakers[0], stream=False)


def _mono_pcm_b64(chunks, sample_rate: int) -> tuple[str, int]:
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415

    pieces = [c["tts_speech"] for c in chunks if c.get("tts_speech") is not None]
    if not pieces:
        raise RuntimeError("CosyVoice produced no audio")
    wav = torch.cat([torch.as_tensor(p, dtype=torch.float32).reshape(-1) for p in pieces])
    if sample_rate != COSYVOICE_SAMPLE_RATE:
        import torchaudio  # noqa: PLC0415

        wav = torchaudio.functional.resample(wav, sample_rate, COSYVOICE_SAMPLE_RATE)
    arr = np.clip(wav.detach().cpu().numpy(), -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(arr.shape[-1])


def _handle_synthesize(msg: dict, stdout) -> None:
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")
    ref_audio = msg.get("ref_audio") or None
    if ref_audio and _URL_RE.match(str(ref_audio)):
        raise ValueError(
            "ref_audio must be a local file path; URLs are not accepted (local-first)."
        )
    model = _load_model(stdout)
    chunks = list(_run_model(model, msg))
    sample_rate = int(getattr(model, "sample_rate", COSYVOICE_SAMPLE_RATE) or COSYVOICE_SAMPLE_RATE)
    pcm_b64, n_samples = _mono_pcm_b64(chunks, sample_rate)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": COSYVOICE_SAMPLE_RATE,
        "n_samples": n_samples,
    })


def _verify_windows_rocm_install() -> None:
    import io
    import torch

    _verify_windows_rocm_dependencies()
    output = io.BytesIO()
    _handle_synthesize({'text': 'Hello from CosyVoice.', 'op': 'synthesize'}, output)
    output.seek(0)
    audio = None
    while (message := _recv(output)) is not None:
        if message.get('op') == 'audio':
            audio = message
    if audio is None or audio['sample_rate'] != 24000 or audio['n_samples'] < 4800:
        raise RuntimeError('CosyVoice Windows ROCm produced no usable speech')
    if not any(base64.b64decode(audio['audio_pcm_b64'], validate=True)):
        raise RuntimeError('CosyVoice Windows ROCm speech was silent')
    torch.cuda.synchronize()


# -- main loop ---------------------------------------------------------------


def main() -> int:
    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428): the
    # libraries this loads print to fd 1, and those bytes would otherwise
    # interleave with the length-prefixed frames.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")

    _send(stdout, {"op": "ready", "engine": "cosyvoice", "sample_rate": COSYVOICE_SAMPLE_RATE})

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
    if sys.argv[1:] == ['--verify-windows-rocm-deps']:
        _verify_windows_rocm_dependencies()
    elif sys.argv[1:] == ['--verify-windows-rocm']:
        _verify_windows_rocm_install()
    else:
        sys.exit(main())
