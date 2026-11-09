"""Structured pre-install and measured disk costs for TTS engines."""
from __future__ import annotations

import os
import threading
import time
from functools import lru_cache
from pathlib import Path

_GIB = 1024**3
_CACHE_TTL_SECONDS = 10.0
_measurement_cache: dict[str, tuple[float, dict]] = {}
_measurement_lock = threading.Lock()

# Catalogue/build estimates. ``None`` is deliberate: unknown costs must stay
# visible instead of being silently treated as zero.
_ESTIMATES: dict[str, dict] = {
    "omnivoice": {
        "package_download_bytes": None,
        "unique_installed_bytes": None,
        "potentially_shared_bytes": None,
        "temporary_free_bytes": None,
        "confidence": "estimated",
        "destination": "hf_model_cache",
        "deduplication": None,
    },
    "kittentts": {
        "package_download_bytes": None,
        "unique_installed_bytes": None,
        "potentially_shared_bytes": None,
        "temporary_free_bytes": None,
        "confidence": "estimated",
        "destination": "hf_model_cache",
        "deduplication": None,
    },
    "audiocpp": {
        "package_download_bytes": None,
        "unique_installed_bytes": None,
        "potentially_shared_bytes": None,
        "temporary_free_bytes": None,
        "confidence": "estimated",
        "destination": "hf_model_cache",
        "deduplication": None,
    },
    "audiocpp-dots": {
        "package_download_bytes": None,
        "unique_installed_bytes": None,
        "potentially_shared_bytes": None,
        "temporary_free_bytes": None,
        "confidence": "estimated",
        "destination": "hf_model_cache",
        "deduplication": None,
    },
}
_MODEL_REPOS = {
    "omnivoice": "k2-fsa/OmniVoice",
    "kittentts": "KittenML/kitten-tts-mini-0.8",
    "audiocpp": "audio-cpp/audio.cpp-gguf",
    # Catalogue identity of a package in the shared audio.cpp repo (#2665).
    "audiocpp-dots": "audio-cpp/audio.cpp-gguf:dots-tts-soar-q8_0",
}


def _volume_root(path: Path) -> str:
    """Mount point/drive containing a possibly not-yet-created destination."""
    try:
        current = path.expanduser().resolve()
        while not current.exists() and current.parent != current:
            current = current.parent
        device = current.stat().st_dev
        while current.parent != current and current.parent.stat().st_dev == device:
            current = current.parent
        return str(current)
    except OSError:
        return "unknown"


def _hf_cache_path() -> Path:
    configured = (
        os.environ.get("HF_HUB_CACHE")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("HF_HOME")
    )
    return Path(configured) if configured else Path.home() / ".cache" / "huggingface"


@lru_cache(maxsize=None)
def _catalog_model_bytes(engine_id: str) -> int | None:
    """Resolve the weight estimate from config/models.yaml, its source of truth."""
    repo_id = _MODEL_REPOS.get(engine_id)
    if repo_id is None:
        return None
    try:
        import yaml

        catalog_path = Path(__file__).resolve().parents[1] / "config" / "models.yaml"
        entries = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))["models"]
        model = next(item for item in entries if (item.get("id") or item["repo_id"]) == repo_id)
        return round(float(model["size_gb"]) * _GIB)
    except (OSError, KeyError, StopIteration, TypeError, ValueError):
        return None


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for filename in files:
                try:
                    total += os.path.getsize(os.path.join(root, filename))
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _sidecar_estimate(engine_id: str) -> dict | None:
    try:
        from services.sidecar_install import get_spec, managed_root

        spec = get_spec(engine_id)
    except Exception:
        return None
    if spec is None:
        return None
    model_bytes = spec.weights_bytes
    dependency_bytes = spec.dependency_bytes
    return {
        "model_download_bytes": model_bytes,
        "package_download_bytes": dependency_bytes,
        "unique_installed_bytes": spec.required_bytes,
        "potentially_shared_bytes": spec.potentially_shared_bytes,
        "temporary_free_bytes": spec.temporary_free_bytes,
        "confidence": spec.disk_confidence,
        "destination": "engine_data",
        "destination_volume": _volume_root(managed_root(spec)),
        "deduplication": "uv_same_volume",
    }


def estimate_for(engine_id: str) -> dict:
    estimate = _sidecar_estimate(engine_id) or _ESTIMATES.get(engine_id)
    if estimate is not None:
        return {
            "model_download_bytes": _catalog_model_bytes(engine_id),
            "destination_volume": _volume_root(_hf_cache_path()),
            **estimate,
        }
    return {
        "model_download_bytes": None,
        "package_download_bytes": None,
        "unique_installed_bytes": None,
        "potentially_shared_bytes": None,
        "temporary_free_bytes": None,
        "confidence": "unknown",
        "destination": "unknown",
        "destination_volume": "unknown",
        "deduplication": None,
    }


def _measure_sidecar(engine_id: str) -> dict | None:
    try:
        from services.sidecar_install import get_spec, managed_checkout, managed_root

        spec = get_spec(engine_id)
    except Exception:
        return None
    if spec is None:
        return None
    checkout = managed_checkout(spec)
    if not checkout.is_dir():
        return None
    model = _dir_size(checkout / spec.weights_subdir)
    environment = _dir_size(checkout / ".venv")
    total = _dir_size(managed_root(spec))
    shared_cache = _dir_size(managed_root(spec).parent / ".uv-cache")
    return {
        "model_bytes": model,
        "environment_bytes": environment,
        "cache_bytes": shared_cache,
        "total_owned_bytes": total,
        "confidence": "measured",
    }


def _measure_model_cache(engine_id: str) -> dict | None:
    key = _MODEL_REPOS.get(engine_id)
    if key is None:
        return None
    try:
        from services.model_catalogue import catalogue_entry, package_cache_usage, shares_repo

        entry = catalogue_entry(key)
        if entry is not None and shares_repo(entry):
            # Only this entry's package: the repo also holds other entries' files.
            usage = package_cache_usage(entry)
            size = int(usage["size_on_disk"]) if usage else 0
        else:
            from huggingface_hub import scan_cache_dir

            repo = next((item for item in scan_cache_dir().repos if item.repo_id == key), None)
            size = int(repo.size_on_disk) if repo is not None else 0
    except Exception:
        return None
    if size <= 0:
        return None
    return {
        "model_bytes": size,
        # The model lives in this cache; cache overhead is not separately
        # attributable without double-counting the same hardlinked blobs.
        "environment_bytes": None,
        "cache_bytes": 0,
        "total_owned_bytes": size,
        "confidence": "measured",
    }


def actual_for(engine_id: str) -> dict:
    now = time.monotonic()
    cached = _measurement_cache.get(engine_id)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return dict(cached[1])
    # A cache miss can recursively walk a sidecar and the shared uv cache.
    # Coalesce concurrent requests so callers cannot multiply that work.
    with _measurement_lock:
        now = time.monotonic()
        cached = _measurement_cache.get(engine_id)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return dict(cached[1])
        actual = _measure_sidecar(engine_id) or _measure_model_cache(engine_id) or {
            "model_bytes": None,
            "environment_bytes": None,
            "cache_bytes": None,
            "total_owned_bytes": None,
            "confidence": "unknown",
        }
        _measurement_cache[engine_id] = (now, actual)
        return dict(actual)


def disk_usage_for(engine_id: str) -> dict:
    """Stable API shape consumed by the engine catalogue."""
    return {"estimate": estimate_for(engine_id), "actual": actual_for(engine_id)}


def disk_summary_for(engine_id: str) -> dict:
    """Cheap list payload; measurement is deferred until the row is opened."""
    return {
        "estimate": estimate_for(engine_id),
        "actual": {
            "model_bytes": None,
            "environment_bytes": None,
            "cache_bytes": None,
            "total_owned_bytes": None,
            "confidence": "unknown",
        },
    }
