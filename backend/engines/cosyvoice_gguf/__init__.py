"""Opt-in CosyVoice3 GGUF backend backed by a managed native server."""

from __future__ import annotations

import atexit
import logging
import threading
import urllib.error
from pathlib import Path

from services.tts_backend import TTSBackend, TTSInputError

from engines.cosyvoice_gguf import bootstrap
from engines.cosyvoice_gguf.process import NativeServer
from engines.cosyvoice_gguf.prompt_cache import cached_prompt, prompt_cache_key
from engines.cosyvoice_gguf.server import build_speech_payload, decode_speech_response

logger = logging.getLogger("omnivoice.cosyvoice_gguf")


class CosyVoiceGGUFBackend(TTSBackend):
    id = "cosyvoice-gguf"
    display_name = "CosyVoice 3 GGUF (native Vulkan/CPU, 9 langs, reference clone)"
    gpu_compat = ("cpu", "vulkan")
    supports_cloning = True
    supports_voice_design = False
    # The speak payload carries model/voice/text/instruct/seed/speed only —
    # no quality knobs (#3654).
    supports_num_step = False
    supports_guidance_scale = False
    supports_generation_seed = True
    runs_out_of_process = True
    _is_subprocess_isolated = True

    def __init__(self):
        self._server = NativeServer()
        self._lock = threading.RLock()
        self._sample_rate = bootstrap.SAMPLE_RATE
        atexit.register(self.close)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            bootstrap.resolve_server()
            bootstrap.resolve_cli()
            bootstrap.resolve_model()
            bootstrap.frontend_dir()
            return True, "ready"
        except (OSError, RuntimeError) as exc:
            return False, str(exc)

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        try:
            selected, fallback = bootstrap.selected_backend()
        except (OSError, RuntimeError) as exc:
            return {
                "gpu_compat": cls.gpu_compat,
                "min_vram_gb": 0.0,
                "effective_device": "cpu",
                "routing_status": "unavailable",
                "routing_reason": str(exc),
                "runtime_backend": None,
                "runtime_device_index": None,
                "runtime_device_name": None,
                "runtime_hardware_family": None,
                "runtime_vram_gb": None,
                "runtime_device_verified": False,
            }
        accelerated = selected.target == "vulkan"
        return {
            "gpu_compat": cls.gpu_compat,
            "min_vram_gb": 8.0 if accelerated else 0.0,
            "effective_device": selected.target,
            "routing_status": "accelerated"
            if accelerated
            else ("cpu_fallback" if fallback else "cpu_only"),
            "routing_reason": fallback,
            "runtime_backend": selected.name,
            "runtime_device_index": None,
            "runtime_device_name": selected.device_name,
            "runtime_hardware_family": "amd"
            if "radeon" in selected.device_name.lower()
            else selected.target,
            "runtime_vram_gb": None,
            "runtime_device_verified": accelerated,
        }

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def supported_languages(self) -> list[str]:
        return ["zh", "en", "ja", "ko", "yue", "de", "es", "fr", "it"]

    def model_identity(self) -> str:
        return f"{bootstrap.HF_MODEL_REPO}@{bootstrap.HF_MODEL_REVISION}"

    def execution_evidence_loaded(self) -> bool:
        process = self._server.process
        return process is not None and process.poll() is None

    def generate(self, text: str, **kw):
        if not text or not text.strip():
            raise TTSInputError("CosyVoice GGUF: enter text to synthesize.")
        self._check_language(kw.get("language"))
        reference = kw.get("ref_audio")
        transcript = kw.get("ref_text")
        if not reference:
            raise TTSInputError(
                "CosyVoice GGUF requires a reference recording and its transcript."
            )
        if not Path(reference).is_file():
            raise TTSInputError("CosyVoice GGUF reference recording was not found.")
        if not transcript:
            from services.tts_backend import required_reference_transcript

            transcript = required_reference_transcript(reference, transcript)
        if not transcript:
            raise TTSInputError(
                "CosyVoice GGUF requires the reference transcript to prepare its voice prompt."
            )
        with self._lock:
            prompt = cached_prompt(reference, transcript)
            key = prompt_cache_key(reference, transcript)
            self._server.ensure(prompt, key)
            payload = build_speech_payload(
                model_id=self._server.model_id,
                voice="mira",
                text=text,
                instruct=kw.get("instruct"),
                seed=kw.get("seed"),
                speed=kw.get("speed", 1.0),
            )
            try:
                result = self._server.speak(payload)
            except urllib.error.HTTPError as exc:
                # The server answered: it is healthy, so keep it running.
                raise RuntimeError(
                    f"CosyVoice GGUF server rejected the request (HTTP {exc.code})."
                ) from None
            except (TimeoutError, OSError) as exc:
                self._server.stop()
                reason = getattr(exc, "reason", None)
                if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
                    raise RuntimeError(
                        "CosyVoice GGUF generation timed out; its managed server was stopped."
                    ) from None
                raise RuntimeError(
                    "CosyVoice GGUF server stopped responding; it was stopped and restarts on the next request."
                ) from None
        sample_rate, waveform = decode_speech_response(result)
        if sample_rate != bootstrap.SAMPLE_RATE:
            raise RuntimeError(
                f"CosyVoice GGUF returned {sample_rate} Hz; expected {bootstrap.SAMPLE_RATE} Hz."
            )
        import torch

        self._sample_rate = sample_rate
        return torch.from_numpy(waveform).float().unsqueeze(0)

    def close(self):
        with self._lock:
            self._server.stop()

    def unload(self):
        self.close()
        super().unload()


__all__ = ["CosyVoiceGGUFBackend"]
