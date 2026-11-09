"""whisper.cpp binary probe and model resolution.

whisper.cpp (ggerganov/whisper.cpp) is a high-performance C/C++ implementation
of OpenAI's Whisper model, supporting GGML/GGUF quantization and native GPU
backends including Vulkan, CUDA, and Metal.

Probe order for the binary:
    1. ${OMNIVOICE_WHISPER_CPP_BIN} — absolute path to the binary itself.
    2. ${OMNIVOICE_WHISPER_CPP_DIR}/whisper-cli[.exe] — user-managed install dir.
    3. backend/engines/whisper_cpp/bin/whisper-cli[.exe] — package-local copy.
    4. System PATH lookup via shutil.which().

Local-first: no automatic network downloads of executable binaries.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path, PureWindowsPath

logger = logging.getLogger("omnivoice.whisper_cpp.bootstrap")

#: Pinned default model identifier
DEFAULT_MODEL_NAME = "ggml-base.bin"

#: Hugging Face repo serving the GGML/GGUF models
HF_MODEL_REPO = "ggerganov/whisper.cpp"

#: Environment variable overrides
BIN_ENV = "OMNIVOICE_WHISPER_CPP_BIN"
DIR_ENV = "OMNIVOICE_WHISPER_CPP_DIR"
MODEL_ENV = "OMNIVOICE_WHISPER_CPP_MODEL"
BACKEND_ENV = "OMNIVOICE_WHISPER_CPP_BACKEND"
DEVICE_ENV = "OMNIVOICE_WHISPER_CPP_DEVICE"

#: Package-local binary and model directories
_PKG_DIR = Path(__file__).resolve().parent
_PKG_BIN_DIR = _PKG_DIR / "bin"
_PKG_MODELS_DIR = _PKG_DIR / "models"

#: Candidate binary names
_CANDIDATE_NAMES = (
    ("whisper-cli.exe", "main.exe")
    if sys.platform == "win32"
    else ("whisper-cli", "main")
)


def _usable_binary(path: Path) -> bool:
    return (
        path.is_file()
        and path.stem.lower() not in ("whisper-server", "server", "whisper")
        and (sys.platform == "win32" or os.access(path, os.X_OK))
    )


def locate_binary() -> Path | None:
    """Find a usable whisper.cpp binary.

    Returns the absolute Path if found, otherwise None.
    """
    # 1. Direct environment variable override
    direct = os.environ.get(BIN_ENV, "").strip()
    if direct:
        cand = Path(direct)
        if _usable_binary(cand):
            return cand.resolve()
        logger.warning("%s points to a missing executable", BIN_ENV)

    # 2. Directory override
    dir_env = os.environ.get(DIR_ENV, "").strip()
    if dir_env:
        dir_path = Path(dir_env)
        if dir_path.is_dir():
            for name in _CANDIDATE_NAMES:
                cand = dir_path / name
                if _usable_binary(cand):
                    return cand.resolve()

    # 3. Package-owned binary directory
    if _PKG_BIN_DIR.is_dir():
        for name in _CANDIDATE_NAMES:
            cand = _PKG_BIN_DIR / name
            if _usable_binary(cand):
                return cand.resolve()

    # 4. System PATH
    for name in _CANDIDATE_NAMES[:1]:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()

    return None


def _model_path(path: Path) -> Path:
    """Absolute path to a model file that keeps the file's own name.

    ``Path.resolve()`` canonicalises the final component, so a Hugging Face
    cache entry — a symlink from ``snapshots/<rev>/ggml-<name>.bin`` into
    ``blobs/<sha256>`` — comes back named after its content hash. The
    checkpoint's identity (English-only ``.en``, Turbo, Distil) then cannot
    be told from the hash, and the translation guard lets those checkpoints
    translate. The parent directory is still resolved, so the result stays
    absolute and free of ``..`` segments.
    """
    return path.parent.resolve() / path.name


def resolve_model(model_name: str | None = None) -> Path | None:
    """Resolve the path to a GGML/GGUF Whisper model file.

    Checks:
    1. Direct file path if model_name (or MODEL_ENV) points to an existing file.
    2. Package-local models directory: `backend/engines/whisper_cpp/models/`.
    3. User whisper cache: `~/.cache/whisper/`.
    4. HuggingFace hub cache if present.

    The returned path keeps the checkpoint's own file name, so a cached
    model's identity survives the symlink into the content-addressed blob.
    """
    target = (model_name or os.environ.get(MODEL_ENV, "")).strip() or DEFAULT_MODEL_NAME

    # Direct existing file path
    direct = Path(target)
    if direct.is_file():
        return _model_path(direct)
    if "/" in target or "\\" in target or PureWindowsPath(target).drive:
        # An explicit path that is missing must not silently load a
        # same-named model from another cache; only bare names are searched.
        return None

    filename = direct.name if direct.suffix in (".bin", ".gguf") else f"ggml-{target}.bin"

    # In package models dir
    pkg_cand = _PKG_MODELS_DIR / filename
    if pkg_cand.is_file():
        return _model_path(pkg_cand)

    # In user cache dir
    cache_cand = Path.home() / ".cache" / "whisper" / filename
    if cache_cand.is_file():
        return _model_path(cache_cand)

    # In huggingface hub cache (safe check, no network)
    try:
        from huggingface_hub import try_to_load_from_cache

        hf_cached = try_to_load_from_cache(HF_MODEL_REPO, filename)
        if hf_cached and isinstance(hf_cached, (str, Path)) and Path(hf_cached).is_file():
            return _model_path(Path(hf_cached))
    except Exception:  # noqa: BLE001
        logger.debug("No local whisper.cpp HF cache entry")

    return None


def resolve_model_target(model_name: str | None = None) -> Path | None:
    """The physical file a resolved model points at.

    A cached model is a symlink into a content-addressed blob, and a user may
    symlink their own copy into a cache directory. The repository that
    actually holds those bytes is the one whose terms apply, so repository
    and licence classification follow the physical target. The checkpoint's
    capabilities follow the logical file name instead (see
    :func:`resolve_model`).
    """
    model = resolve_model(model_name)
    if model is None:
        return None
    try:
        return model.resolve()
    except OSError:  # pragma: no cover - unreadable link
        return model


def is_installed() -> bool:
    """Return True if a usable whisper.cpp binary is located."""
    return locate_binary() is not None
