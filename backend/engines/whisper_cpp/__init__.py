"""whisper.cpp engine — native C++ GGUF inference for ASR with Vulkan GPU acceleration."""
from __future__ import annotations

from engines.whisper_cpp.backend import WhisperCppBackend
from engines.whisper_cpp.bootstrap import (
    BACKEND_ENV,
    BIN_ENV,
    DEVICE_ENV,
    DIR_ENV,
    MODEL_ENV,
    is_installed,
    locate_binary,
    resolve_model,
    resolve_model_target,
)

__all__ = [
    "BACKEND_ENV",
    "BIN_ENV",
    "DEVICE_ENV",
    "DIR_ENV",
    "MODEL_ENV",
    "WhisperCppBackend",
    "is_installed",
    "locate_binary",
    "resolve_model",
    "resolve_model_target",
]
