"""CUDA Qwen3-TTS Base engine for a locally provisioned checkpoint."""
from collections import OrderedDict
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from threading import Lock

import torch

from services.tts_backend import TTSBackend, TTSInputError


class QwenCUDABackend(TTSBackend):
    id = "qwen3-tts"
    display_name = "Qwen3-TTS Base"
    gpu_compat = ("cuda",)
    supports_voice_design = False
    supports_generation_seed = True
    supports_num_step = False
    supports_guidance_scale = False
    sample_rate = 24000
    languages = dict(en="English", de="German", fr="French", es="Spanish",
                     it="Italian", pt="Portuguese", ru="Russian", zh="Chinese",
                     ja="Japanese", ko="Korean")

    def __init__(self):
        self._model = None
        self._prompts = OrderedDict()
        self._load_lock = Lock()
        self._lock = Lock()

    @property
    def supported_languages(self):
        return list(self.languages)

    @classmethod
    def is_available(cls):
        from importlib.util import find_spec

        path = os.environ.get("VOICESTUDIO_QWEN_CUDA_MODEL")
        if not path or not (Path(path) / "config.json").is_file():
            return False, "Set VOICESTUDIO_QWEN_CUDA_MODEL to a local Qwen3-TTS Base checkpoint."
        if not torch.cuda.is_available() or find_spec("faster_qwen3_tts") is None:
            return False, "Requires CUDA and faster-qwen3-tts."
        return True, "ready"

    def _ensure_loaded(self):
        with self._load_lock:
            if self._model is None:
                from faster_qwen3_tts import FasterQwen3TTS

                engine = FasterQwen3TTS.from_pretrained(
                    os.environ["VOICESTUDIO_QWEN_CUDA_MODEL"], local_files_only=True,
                )
                model = engine.model.model
                # The prompt encoder receives mel features in the main model
                # dtype. Keep its convolution weights and biases in that dtype.
                model.speaker_encoder.to(dtype=model.dtype)
                self._model = engine

    def unload(self):
        self._prompts.clear()
        super().unload()

    def can_stream(self, **kw):
        return kw.get("speed", 1.0) == 1.0 and kw.get("duration") is None

    def stream_audio(self, text, *, ref_audio=None, ref_text=None, language=None,
                     seed=None, **kw):
        import numpy as np
        import soundfile as sf

        if not ref_audio or not ref_text:
            raise TTSInputError("Qwen3-TTS Base requires a voice reference and transcript.")
        code = self._check_language(language)
        try:
            audio = Path(ref_audio).read_bytes()
        except OSError as exc:
            raise TTSInputError("The reference audio file is missing or unreadable.") from exc
        key = (sha256(audio).digest(), ref_text)
        self._ensure_loaded()
        # A native response can hold the engine across network writes.
        # Buffered callers must not park the shared GPU worker behind it.
        if not self._lock.acquire(blocking=False):
            from services.model_manager import GpuPoolBusyError

            raise GpuPoolBusyError("Qwen3-TTS Base is already generating speech.", retry_after=1)
        try:
            if key not in self._prompts:
                try:
                    samples, rate = sf.read(BytesIO(audio), dtype="float32", always_2d=True)
                except (ValueError, sf.LibsndfileError) as exc:
                    raise TTSInputError("The reference audio file could not be decoded.") from exc
                self._prompts[key] = self._model.model.create_voice_clone_prompt(
                    ref_audio=(samples.mean(axis=1), rate), ref_text=ref_text,
                )
                if len(self._prompts) > 8:
                    self._prompts.popitem(last=False)
            self._prompts.move_to_end(key)
            with torch.random.fork_rng(devices=[0], enabled=seed is not None):
                if seed is not None:
                    torch.manual_seed(seed)
                chunks = self._model.generate_voice_clone_streaming(
                    text=text, language=self.languages.get(code, "Auto"),
                    voice_clone_prompt=self._prompts[key],
                    chunk_size=8, non_streaming_mode=True,
                )
                with closing(chunks):
                    for audio, rate, _ in chunks:
                        if rate != self.sample_rate:
                            raise RuntimeError(f"Unexpected Qwen sample rate {rate}")
                        if len(audio):
                            yield torch.from_numpy(np.array(audio, dtype=np.float32, copy=True)).reshape(1, -1)
        finally:
            self._lock.release()

    def generate(self, text, **kw):
        if not self.can_stream(**kw):
            raise TTSInputError("Qwen3-TTS Base requires default speed and duration.")
        chunks = list(self.stream_audio(text, **kw))
        if not chunks:
            raise RuntimeError("Qwen3-TTS Base produced no audio.")
        return torch.cat(chunks, dim=-1)
