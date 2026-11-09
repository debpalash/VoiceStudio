"""Pinned runtime discovery and verified native-device selection."""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 -- fixed probe arguments
from dataclasses import dataclass, replace
from pathlib import Path

from core.config import DATA_DIR
from services.hf_revisions import revision_for

ENGINE_ID = "cosyvoice-gguf"
SOURCE_REVISION = "67d45feb2f654bbc66cca25bff78464b3ec08fa1"
HF_MODEL_REPO = "Lourdle/Fun-CosyVoice3-0.5B-2512-GGUF"
# One pin for downloads, preflight and this engine: the central registry.
HF_MODEL_REVISION = revision_for(HF_MODEL_REPO)
MODEL_FILE = "CosyVoice3-2512_Q4_K_M.gguf"
FRONTEND_FILES = (
    "frontend-onnx/campplus.int8.onnx",
    "frontend-onnx/speech_tokenizer_v3.int8.onnx",
)
DIR_ENV = "OMNIVOICE_COSYVOICE_GGUF_DIR"
BIN_ENV = "OMNIVOICE_COSYVOICE_GGUF_SERVER"
CLI_ENV = "OMNIVOICE_COSYVOICE_GGUF_CLI"
MODEL_ENV = "OMNIVOICE_COSYVOICE_GGUF_MODEL"
FRONTEND_ENV = "OMNIVOICE_COSYVOICE_GGUF_FRONTEND"
BACKEND_ENV = "OMNIVOICE_COSYVOICE_GGUF_BACKEND"
MODEL_ID = "cosyvoice3-2512"
SAMPLE_RATE = 24000


@dataclass(frozen=True)
class NativeBackend:
    name: str
    device_name: str
    target: str
    fallback_reason: str | None = None


def managed_dir() -> Path:
    return Path(DATA_DIR) / "engines" / ENGINE_ID / "runtime"


def _binary(kind: str) -> Path:
    env = BIN_ENV if kind == "server" else CLI_ENV
    name = f"cosyvoice-{kind}.exe" if os.name == "nt" else f"cosyvoice-{kind}"
    if os.environ.get(env):
        override = Path(os.environ[env])
        if override.is_file():
            return override.resolve()
        raise RuntimeError(
            f"{env} points to a missing CosyVoice GGUF binary: {override}"
        )
    candidates = []
    root = Path(os.environ.get(DIR_ENV, managed_dir()))
    candidates.extend((root / "bin" / name, root / name))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        f"CosyVoice GGUF {kind} binary not found; build pinned source {SOURCE_REVISION} and set {DIR_ENV}."
    )


def resolve_server() -> Path:
    return _binary("server")


def resolve_cli() -> Path:
    return _binary("cli")


def resolve_model() -> Path:
    direct = os.environ.get(MODEL_ENV, "").strip()
    if direct:
        # An explicit choice never silently falls back to the pinned cache.
        if Path(direct).is_file():
            return Path(direct).resolve()
        raise RuntimeError(f"{MODEL_ENV} points to a missing CosyVoice GGUF model file: {direct}")
    try:
        from huggingface_hub import snapshot_download

        root = Path(
            snapshot_download(
                HF_MODEL_REPO, revision=HF_MODEL_REVISION, local_files_only=True
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"Install {HF_MODEL_REPO} from Model Catalogue or set {MODEL_ENV}; no network download is attempted."
        ) from exc
    model = root / MODEL_FILE
    if not model.is_file():
        raise RuntimeError(f"CosyVoice GGUF model file missing: {model}")
    return model


def frontend_dir() -> Path:
    override = os.environ.get(FRONTEND_ENV, "").strip()
    if override:
        # An explicit choice never silently mixes with the pinned cache.
        root = Path(override)
        missing = [item for item in FRONTEND_FILES if not (root / item).is_file()]
        if missing:
            raise RuntimeError(
                f"{FRONTEND_ENV} is missing CosyVoice GGUF frontend files: {', '.join(missing)}"
            )
        return root
    try:
        from huggingface_hub import snapshot_download

        cache = Path(
            snapshot_download(
                HF_MODEL_REPO, revision=HF_MODEL_REVISION, local_files_only=True
            )
        )
        if all((cache / item).is_file() for item in FRONTEND_FILES):
            return cache
    except Exception as exc:  # Hugging Face offline/cache errors vary by version
        raise RuntimeError(
            f"Unable to inspect the local CosyVoice frontend cache: {exc}"
        ) from exc
    raise RuntimeError(
        f"CosyVoice GGUF frontend ONNX files missing; install the model assets or set {FRONTEND_ENV}."
    )


def parse_backends(output: str) -> list[NativeBackend]:
    rows = []
    for line in output.splitlines():
        match = re.match(r"\s*([A-Za-z][\w.-]*)\s+-\s+(.+?)\s*$", line)
        if match:
            name, device = match.groups()
            rows.append(
                NativeBackend(
                    name,
                    device,
                    "vulkan"
                    if name.lower().startswith("vulkan")
                    else "cpu"
                    if name.upper() == "CPU"
                    else name.lower(),
                )
            )
    return rows


def probe_backends(cli: Path | None = None) -> list[NativeBackend]:
    try:
        result = subprocess.run(
            [str(cli or resolve_cli()), "--list-backends"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("CosyVoice GGUF backend probe timed out after 30 s") from None
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"CosyVoice GGUF backend probe failed (exit code {exc.returncode})"
        ) from None
    devices = parse_backends(result.stdout + "\n" + result.stderr)
    if not devices:
        raise RuntimeError("Native CosyVoice runtime returned no usable backends")
    return devices


def choose_backend(rows: list[NativeBackend], requested: str = "auto") -> NativeBackend:
    requested = requested.strip()
    if requested.lower() == "auto":
        gpu = next((row for row in rows if row.target == "vulkan"), None)
        if gpu:
            return gpu
        cpu = next((row for row in rows if row.name.upper() == "CPU"), None)
        if cpu:
            return replace(
                cpu,
                fallback_reason="No Vulkan backend was reported by the native runtime; using CPU.",
            )
        return _missing(requested)
    for row in rows:
        if row.name.casefold() == requested.casefold():
            return row
    raise RuntimeError(
        f"Requested CosyVoice backend {requested!r} is unavailable; detected: {', '.join(row.name for row in rows)}"
    )


def _missing(requested: str):
    raise RuntimeError(f"No supported native backend for selection {requested!r}")


def selected_backend() -> tuple[NativeBackend, str | None]:
    rows = probe_backends()
    requested = os.environ.get(BACKEND_ENV, "auto")
    selected = choose_backend(rows, requested)
    return selected, selected.fallback_reason
