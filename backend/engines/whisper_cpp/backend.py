"""whisper.cpp ASR backend implementation (Phase 3).

Native C/C++ speech recognition via whisper.cpp. Provides GPU-accelerated
transcription on AMD GPUs via the Vulkan runtime without requiring CTranslate2
or a PyTorch wheel swap.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from services.asr_backend import ASR_TRANSCRIBE_TIMEOUT_S, ASRBackend

from engines.whisper_cpp.audio import _ensure_wav_16k
from engines.whisper_cpp.bootstrap import (
    BACKEND_ENV,
    DEVICE_ENV,
    locate_binary,
    resolve_model,
)
from engines.whisper_cpp.output import parse_transcript
from engines.whisper_cpp.process import run_transcription

logger = logging.getLogger("omnivoice.whisper_cpp")


class WhisperCppBackend(ASRBackend):
    """ASR backend powered by whisper.cpp with native Vulkan acceleration."""

    id: str = "whisper-cpp"
    display_name: str = "whisper.cpp"
    # Vulkan first, followed by CUDA and CPU.
    gpu_compat: tuple[str, ...] = ("vulkan", "cuda", "mps", "cpu")
    requires_full_audio_for_speaker_consistency: bool = False
    _is_subprocess_isolated: bool = True

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        """Installed CLI providers are independent of the Python torch build."""
        return {
            "gpu_compat": cls.gpu_compat,
            "min_vram_gb": 0.0,
            "runtime_backend": None,
            "runtime_device_index": None,
            "runtime_device_name": None,
            "runtime_hardware_family": None,
            "runtime_vram_gb": None,
            "runtime_device_verified": False,
            "effective_device": "cpu",
            "routing_status": "cpu_only",
            "routing_reason": "Native CLI acceleration is unverified; using CPU timeout estimates.",
        }

    def supports_translation(self) -> bool:
        from services.asr_backend import whisper_checkpoint_translates

        model = resolve_model()
        return (
            model is not None
            and ".en" not in model.stem.lower().split(".bin")[0]
            and whisper_checkpoint_translates(model.stem)
        )


    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        binary = locate_binary()
        if not binary:
            return (
                False,
                ("whisper.cpp binary not found. Set OMNIVOICE_WHISPER_CPP_BIN or "
                 "place whisper-cli in backend/engines/whisper_cpp/bin/."),
            )
        model = resolve_model()
        if not model:
            return (
                False,
                ("whisper.cpp model not found. Set OMNIVOICE_WHISPER_CPP_MODEL or "
                 "download a ggml-*.bin model."),
            )
        return True, "ok"

    def transcribe(
        self,
        audio_path: str,
        *,
        word_timestamps: bool = True,
        language: str | None = None,
        initial_prompt: str | None = None,
        temperature: float | None = None,
        task: str = "transcribe",
    ) -> dict:
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        binary = locate_binary()
        if not binary:
            raise RuntimeError("whisper.cpp binary is not available")

        model_path = resolve_model()
        if not model_path:
            raise RuntimeError("whisper.cpp model file could not be resolved")

        if task == "translate" and not self.supports_translation():
            raise ValueError("whisper.cpp translation requires a multilingual non-Turbo, non-Distil model")

        with tempfile.TemporaryDirectory(prefix="vs_whisper_cpp_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            wav_path = _ensure_wav_16k(audio_path, tmp_dir)
            out_prefix = tmp_dir / "out"

            cmd = [
                str(binary),
                "-m", str(model_path),
                "-f", str(wav_path),
                "--output-json-full" if word_timestamps else "-oj",
                "-of", str(out_prefix),
            ]

            cmd.extend(["-l", (language or "").strip() or "auto"])

            if task == "translate":
                cmd.append("--translate")

            if initial_prompt:
                cmd.extend(["--prompt", initial_prompt])

            if temperature is not None:
                cmd.extend(["--temperature", str(temperature)])

            backend_override = os.environ.get(BACKEND_ENV, "").strip().lower()
            device_override = os.environ.get(DEVICE_ENV, "").strip()

            from core.device_caps import requested_device_override
            if requested_device_override() == "cpu" or backend_override == "cpu":
                cmd.append("-ng")
            elif device_override:
                cmd.extend(["-dev", device_override])

            logger.info("Executing local whisper.cpp transcription")
            data = run_transcription(cmd, tmp_dir / "out.json", ASR_TRANSCRIBE_TIMEOUT_S)

        return parse_transcript(data, word_timestamps)
