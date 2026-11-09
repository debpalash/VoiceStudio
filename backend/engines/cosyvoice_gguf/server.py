"""Speech request contracts and WAV decoder for the local native server."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf


def build_speech_payload(
    *,
    model_id: str,
    voice: str,
    text: str,
    instruct: str | None,
    seed: int | None,
    speed: float = 1.0,
) -> dict:
    payload = {
        "model": model_id,
        "voice": voice,
        "input": text,
        "response_format": "wav",
    }
    if instruct:
        payload["instructions"] = instruct
    if seed is not None:
        payload["seed"] = int(seed)
    if speed != 1.0:
        payload["speed"] = float(speed)
    return payload


def decode_speech_response(response: bytes) -> tuple[int, np.ndarray]:
    if not isinstance(response, bytes) or not response.startswith(b"RIFF"):
        raise ValueError("CosyVoice GGUF did not return a WAV response")
    try:
        audio, sample_rate = sf.read(io.BytesIO(response), dtype="float32")
    except Exception as exc:
        raise ValueError("CosyVoice GGUF returned invalid WAV audio") from exc
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("CosyVoice GGUF returned empty or invalid audio")
    return int(sample_rate), np.asarray(audio, dtype=np.float32)
