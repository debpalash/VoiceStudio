"""Quant picker + weight prefetch for the OmniVoice GGUF engine.

The engine picks a quant per host (``compute.automatic_compute_class`` ×
``quant_map.json``) and downloads the selected pair on the first generate.
This module gives Settings › Models the rest of that loop:

* :func:`quant_status` — what the engine will run on (a verified Vulkan GPU,
  or the unverified/CPU route), which quant that recommends, the user's pin,
  the pair generation would actually load, and what is installed;
* :func:`select_quant` — persist ``auto`` or a specific quant (validated
  against ``quant_map.json``);
* :func:`start_download` — fetch a pair ahead of time at the pinned revision
  into the app's Hugging Face cache, with byte progress in :func:`quant_status`.

A download only ever starts from an explicit request (the Download button) or
the first generate, never on its own: nothing leaves the machine without the
user's action. ``huggingface_hub`` handles mirrors, resumption and SHA checks.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .backend import (
    _HF_REPO_ID,
    QUANT_ENV,
    _automatic_entry,
    _load_quant_map,
    _walk_quant_entries,
    env_quant_override,
    _persisted_quant_override,
)
from .quant_cache import installed_pair

logger = logging.getLogger("omnivoice.gguf.quants")

_MIB = 1024 * 1024
#: Fixed text for the UI: exception messages can carry local paths or URLs.
_DOWNLOAD_FAILED = (
    "The download did not finish. Check your connection and free disk space, "
    "then try again."
)
_lock = threading.Lock()
_job: dict = {
    "state": "idle", "quant": None, "error": None,
    "bytes_total": 0, "started_at": None, "finished_at": None,
}


def tag(base: str) -> str:
    """``omnivoice-base-Q8_0.gguf`` → ``Q8_0``."""
    return base.removeprefix("omnivoice-base-").removesuffix(".gguf")


def _entries(quant_map: dict) -> list[dict]:
    """Distinct quants (cpu and low-vram share one), smallest first."""
    seen: dict[str, dict] = {}
    for _key, entry in _walk_quant_entries(quant_map):
        seen.setdefault(entry["base"], entry)
    return sorted(seen.values(), key=lambda e: int(e.get("approx_size_mb") or 0))


def entry_for(quant_map: dict, value: str) -> Optional[dict]:
    """The entry a base filename or a tag (``BF16``) names."""
    for entry in _entries(quant_map):
        if value in (entry["base"], tag(entry["base"])):
            return entry
    return None


def _in_use(revision: str) -> tuple[Optional[str], Optional[str]]:
    """``(base, problem)``: the pair generation would load right now without
    downloading, or why it cannot."""
    try:
        from .backend import _make_backend_class

        _base, _tok, entry = _make_backend_class()()._resolve_quant_paths(download=False)
        return entry.get("base"), None
    except Exception:  # noqa: BLE001 - reported as a code, not raised
        # The exception text can carry local paths; the API returns a fixed
        # code and the details stay in the log.
        logger.debug("OmniVoice GGUF weights not resolvable", exc_info=True)
        return None, "weights_unavailable"


def _incomplete_bytes() -> int:
    """Bytes of the in-flight download (``*.incomplete`` blobs of the repo)."""
    try:
        from huggingface_hub import constants

        blobs = Path(constants.HF_HUB_CACHE) / ("models--" + _HF_REPO_ID.replace("/", "--")) / "blobs"
        return sum(p.stat().st_size for p in blobs.glob("*.incomplete"))
    except Exception:  # noqa: BLE001
        return 0


def _hardware() -> dict:
    """The adapter the engine will open, as the picker shows it. Never raises."""
    from .compute import cpu_selection_explicit, selected_vulkan_adapter
    from .vulkan_auto import detect_vulkan_profile

    try:
        adapter = selected_vulkan_adapter()
        cpu_pinned = cpu_selection_explicit()
    except Exception:  # noqa: BLE001 - an invalid override is reported by generate
        adapter, cpu_pinned = None, False
    profile = detect_vulkan_profile()
    return {
        "backend": "vulkan" if adapter else "cpu" if cpu_pinned else "unverified",
        "cpu_pinned": cpu_pinned,
        "device": adapter.ggml_name if adapter else None,
        "gpu_name": adapter.name if adapter else None,
        "vendor": adapter.vendor if adapter else None,
        "device_type": adapter.device_type if adapter else None,
        "dedicated_vram_gb": adapter.dedicated_vram_gb if adapter else None,
        "effective_vram_gb": adapter.effective_vram_gb if adapter else None,
        "shared_memory": adapter.shared_memory if adapter else False,
        "native_bf16": adapter.bf16 if adapter else None,
        "system_ram_gb": profile.system_ram_gb,
        "probe_source": profile.source,
    }


_RUNTIME_CACHE: dict[tuple, dict] = {}


def _runtime() -> dict:
    """Is the native binary usable — kept apart from the weights so the UI
    can say which of the two files is missing.

    ``_binary_status`` hashes the binary against its manifest, and the picker
    polls this every 1.5 s during a download, so the answer is cached per
    binary (path, size, mtime): replacing the binary invalidates it.
    """
    from .backend import _binary_path, _file_identity

    try:
        path = _binary_path()
        key = (str(path), _file_identity(path))
    except Exception:  # noqa: BLE001 - an unresolvable path is not cached
        key = None
    if key is not None and key in _RUNTIME_CACHE:
        return dict(_RUNTIME_CACHE[key])
    result = _runtime_uncached()
    # Only a ready runtime is cached: the fixes a problem asks for (allowing a
    # quarantined file, repairing a library) do not change the binary's
    # identity, so a cached failure would outlive them.
    if key is not None and result["ready"]:
        _RUNTIME_CACHE.clear()
        _RUNTIME_CACHE[key] = result
    return dict(result)


def _runtime_uncached() -> dict:
    """Classify ``_binary_status`` into a problem code the UI can translate."""
    from .backend import _make_backend_class
    from .compute import binary_has_vulkan_module

    ok, reason = _make_backend_class()._binary_status()
    text = (reason or "").lower()
    if ok:
        problem = None
    elif "binary missing" in text:
        problem = "missing"
    elif "not a usable executable" in text:
        problem = "placeholder"
    elif "checksum" in text or "could not be verified" in text:
        problem = "integrity"
    elif "quarantined" in text:
        problem = "quarantined"
    else:
        problem = "other"
    return {"ready": ok, "problem": problem, "vulkan_module": binary_has_vulkan_module()}


def quant_status() -> dict:
    """Everything the quant picker renders. No network, no binary spawn."""
    q = _load_quant_map()
    revision = q["_meta"]["source_commit_sha"]
    from .compute import ComputeConfigError

    try:
        recommended = _automatic_entry(q)
    except ComputeConfigError:
        # An invalid OMNIVOICE_GGUF_BACKEND / _DEVICE: generation reports it;
        # the picker still renders, on the conservative tier.
        recommended = q["cpu"]
    env_pin = env_quant_override()
    pin = env_pin or _persisted_quant_override()
    pin_entry = entry_for(q, pin) if pin else None
    # An unknown pin is shown as itself, not as the automatic pick:
    # generation refuses it rather than silently using another precision.
    selected_base = pin_entry["base"] if pin_entry else (pin or recommended["base"])
    in_use, problem = _in_use(revision)
    with _lock:
        job = dict(_job)
    if job["state"] == "running":
        job["bytes_done"] = _incomplete_bytes()
    options = []
    for entry in _entries(q):
        options.append({
            "quant": entry["base"],
            "tag": tag(entry["base"]),
            "approx_size_mb": int(entry.get("approx_size_mb") or 0),
            "rationale": entry.get("rationale", ""),
            "recommended": entry["base"] == recommended["base"],
            "selected": entry["base"] == selected_base,
            "in_use": entry["base"] == in_use,
            "installed": installed_pair(_HF_REPO_ID, revision, entry) is not None,
        })
    return {
        "runtime": _runtime(),
        "hardware": _hardware(),
        "recommended": recommended["base"],
        "selection": pin or "auto",
        "selection_source": "env" if env_pin else ("settings" if pin else "auto"),
        "env_var": QUANT_ENV,
        "selected": selected_base,
        "selection_valid": not pin or pin_entry is not None,
        "in_use": in_use,
        "problem": None if in_use else problem,
        "options": options,
        "download": job,
    }


def select_quant(value: str) -> None:
    """Persist ``auto`` or a quant (base filename or tag). ``ValueError`` on
    anything quant_map.json does not list."""
    from services.settings_store import set_quant_override

    if value != "auto":
        entry = entry_for(_load_quant_map(), value)
        if entry is None:
            raise ValueError(f"Unknown OmniVoice GGUF quant {value!r}")
        value = entry["base"]
    set_quant_override(value)


def _download(entry: dict, revision: str) -> None:
    """Background worker: fetch the base + tokenizer pair, recording progress in ``_job``."""
    from huggingface_hub import hf_hub_download

    try:
        for filename in (entry["base"], entry["tokenizer"]):
            hf_hub_download(repo_id=_HF_REPO_ID, filename=filename, revision=revision)
        outcome = {"state": "done", "error": None}
    except Exception:  # noqa: BLE001 - reported to the UI, not raised
        logger.warning("OmniVoice GGUF weight download failed", exc_info=True)
        outcome = {"state": "failed", "error": _DOWNLOAD_FAILED}
    with _lock:
        _job.update(outcome, finished_at=time.time())


def approx_bytes(value: Optional[str]) -> int:
    """Download size of a quant pair (``None`` = the selected one)."""
    q = _load_quant_map()
    entry = entry_for(q, value) if value else entry_for(q, quant_status()["selected"])
    return int(entry.get("approx_size_mb") or 0) * _MIB if entry else 0


def start_download(value: Optional[str] = None) -> dict:
    """Download ``value`` (base filename or tag; ``None`` = the selected
    quant) in a background thread. Returns ``{status, ...job}``."""
    q = _load_quant_map()
    entry = entry_for(q, value) if value else entry_for(q, quant_status()["selected"])
    if entry is None:
        raise ValueError(f"Unknown OmniVoice GGUF quant {value!r}")
    revision = q["_meta"]["source_commit_sha"]
    if installed_pair(_HF_REPO_ID, revision, entry) is not None:
        with _lock:
            return {**_job, "status": "already_installed", "quant": entry["base"]}
    with _lock:
        if _job["state"] == "running":
            return {**_job, "status": "already_running"}
        _job.update(
            state="running", quant=entry["base"], error=None,
            bytes_total=int(entry.get("approx_size_mb") or 0) * _MIB,
            started_at=time.time(), finished_at=None,
        )
        snapshot = dict(_job)
    if os.environ.get("HF_HUB_OFFLINE", "").strip() in ("1", "true"):
        with _lock:
            _job.update(state="failed", error="HF_HUB_OFFLINE is set; downloads are disabled",
                        finished_at=time.time())
            return {**_job, "status": "failed"}
    threading.Thread(
        target=_download, args=(entry, revision),
        name="omnivoice-gguf-weights", daemon=True,
    ).start()
    return {**snapshot, "status": "started"}


__all__ = ["approx_bytes", "entry_for", "quant_status", "select_quant", "start_download", "tag"]
