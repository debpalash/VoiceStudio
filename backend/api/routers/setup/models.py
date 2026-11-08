"""Model catalog, platform detection, and cache introspection.

Extracted from the monolithic ``setup.py`` to keep concerns separate:
- ``KNOWN_MODELS`` loaded from ``config/models.yaml``
- ``GET /models`` endpoint (with 10 s response cache)
- ``GET /setup/recommendations`` device-aware preset endpoint
- ``ModelCatalog`` dependency for use with ``Depends()``
"""
from __future__ import annotations

import logging
import os
import platform as _platform
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Depends
from core.browser_guard import reject_cross_site_get

logger = logging.getLogger("omnivoice.setup.models")
router = APIRouter()

# ── Model Catalog (loaded from YAML; services/model_catalogue.py) ─────────

from services.model_catalogue import (  # noqa: F401 — re-exported API
    KNOWN_MODELS,
    _hub_cache_roots,
    _repo_dir_name,
    _snapshot_dirs,
    hf_cache_dir,
    model_key,
    package_cache_usage,
    shares_repo,
    snapshot_is_complete,
)


# Back-compat tuple view for code that expects (repo_id, label) pairs.
REQUIRED_MODELS = [(model_key(m), m["label"]) for m in KNOWN_MODELS if m.get("required")]


# ── Dependency Injection ───────────────────────────────────────────────────
# Use `catalog: ModelCatalog = Depends(get_model_catalog)` in endpoint params
# for testable, mockable access to the model registry.

class ModelCatalog:
    """Injectable service wrapping the model catalog + cache scanner."""

    def __init__(self, models: list[dict] | None = None):
        self.models = models if models is not None else KNOWN_MODELS
        self._by_id = {model_key(m): m for m in self.models}
        self._required = [(model_key(m), m["label"]) for m in self.models if m.get("required")]

    def get(self, key: str) -> dict | None:
        """Entry by catalogue identity (the repo id for whole-repo entries)."""
        return self._by_id.get(key)

    @property
    def required(self) -> list[tuple[str, str]]:
        return self._required

    @property
    def all(self) -> list[dict]:
        return self.models

    def supported_on_host(self, model: dict) -> bool:
        return _model_supported(model)


# Singleton — shared across all requests.
_catalog = ModelCatalog()


def get_model_catalog() -> ModelCatalog:
    """FastAPI dependency — inject with ``Depends(get_model_catalog)``."""
    return _catalog


# ── Platform Detection ─────────────────────────────────────────────────────

def _current_platform_tags() -> list[str]:
    """Return platform tags that the current host supports.

    Beyond the OS/arch tags, emits the acceleration family so both the
    ``platforms`` gate and the ``curated_on`` recommendation field can key on
    it: ``cuda`` (NVIDIA — also present on ROCm hosts, where torch reports
    CUDA available, so existing ``platforms: [cuda]`` entries keep working),
    ``rocm`` (AMD HIP builds), and ``cpu`` (no GPU acceleration at all —
    Apple Silicon is NOT tagged cpu; it curates via ``darwin-arm64``).
    """
    tags = [sys.platform]
    arch = _platform.machine()
    tags.append(f"{sys.platform}-{arch}")
    has_gpu = False
    try:
        import torch
        if torch.cuda.is_available():
            tags.append("cuda")
            has_gpu = True
            # ROCm torch masquerades through the CUDA API (torch.version.hip
            # set, torch.cuda.is_available() True when the AMD GPU is usable).
            # Grant 'rocm' only when BOTH hold: a ROCm *build* on a host whose
            # AMD GPU isn't actually visible must curate as CPU, not as a
            # working ROCm host.
            if getattr(torch.version, "hip", None):
                tags.append("rocm")
    except Exception:
        pass
    is_apple_silicon = sys.platform == "darwin" and arch == "arm64"
    if not has_gpu and not is_apple_silicon:
        tags.append("cpu")
    return tags


def _model_supported(model: dict) -> bool:
    """Check if a model is supported on the current platform."""
    plats = model.get("platforms")
    if not plats:
        return True
    return bool(set(plats) & set(_current_platform_tags()))


def _model_curated(model: dict, tags: "set[str] | None" = None) -> bool:
    """True when this model is a curated "best for your system" pick here.

    Driven by the ``curated_on`` field in models.yaml (``all`` matches every
    host). Required models are always curated — the preset must include them.
    """
    if model.get("required"):
        return True
    curated_on = model.get("curated_on") or []
    if "all" in curated_on:
        return True
    if tags is None:
        tags = set(_current_platform_tags())
    # A ROCm host also carries the 'cuda' tag (HIP masquerades through the
    # CUDA API; the tag keeps `platforms: [cuda]` support-gates working). For
    # *curation* ignore it: `curated_on: [cuda]` means NVIDIA-tuned picks —
    # sweeping them into the AMD preset recommended models that are slow or
    # broken there. Entries that want AMD list 'rocm' explicitly (the CT2
    # large-v3 already does).
    if "rocm" in tags:
        tags = tags - {"cuda"}
    return bool(set(curated_on) & tags)


# ── HF Cache Helpers ───────────────────────────────────────────────────────

# ── Disk-space guard (shared, single-sourced) ──────────────────────────────
# MIN_FREE_GB is the headroom we insist on keeping free on the model-cache
# volume — the wizard's absolute pre-install floor AND the extra buffer the
# per-install check demands on top of the download itself, so an "Install all"
# can't fill the disk to the brim (setup/download.py). Lives here — the lowest
# module in the setup import graph — so the wizard, the /models header, and the
# install endpoint can't drift apart (mirrors the weight-floor single-sourcing).
_GIB = 1024 ** 3
MIN_FREE_GB = 10
# Headroom the per-install check keeps free on top of the download, and the
# preflight's hard floor. Smaller than MIN_FREE_GB (the comfortable-library
# warning level) so a small model still installs on a nearly full disk.
INSTALL_HEADROOM_GB = 2


def disk_free_bytes(path: "str | None" = None) -> int:
    """Free bytes on the volume backing *path* (defaults to the HF cache).

    Walks up to the nearest existing ancestor so a not-yet-created cache dir
    still probes the correct mount point. ``shutil.disk_usage`` is cross-platform
    (macOS/Windows/Linux) so this behaves identically everywhere. Never raises.
    """
    import shutil
    try:
        p = Path(path or hf_cache_dir()).resolve()
        while not p.exists():
            parent = p.parent
            if parent == p:  # reached the volume root
                break
            p = parent
        return int(shutil.disk_usage(str(p)).free)
    except Exception:
        return 0


def disk_space_error(to_download_bytes: "int | None", *, cache_dir: "str | None" = None) -> "str | None":
    """Actionable message when *to_download_bytes* (+ INSTALL_HEADROOM_GB) won't
    fit on the cache volume; ``None`` when it fits, the size is unknown, or the
    volume can't be probed (never block on missing information).

    Names the three numbers a user needs to act — needs X, headroom Y, have Z —
    so "Install all" can't silently overrun the disk (issue: no pre-install disk
    check). Platform-agnostic; applied identically on macOS/Windows/Linux.
    """
    if not to_download_bytes or to_download_bytes <= 0:
        return None  # unknown plan (older/gated repo, mirror without dry-run) → don't block
    cache = cache_dir or hf_cache_dir()
    free = disk_free_bytes(cache)
    if free <= 0:
        return None  # couldn't probe the volume → don't block on missing info
    required = int(to_download_bytes) + INSTALL_HEADROOM_GB * _GIB
    if free >= required:
        return None

    def _gb(n: int) -> str:
        return f"{n / _GIB:.1f} GB"

    return (
        f"Not enough disk space to install: this download needs {_gb(int(to_download_bytes))} "
        f"plus {INSTALL_HEADROOM_GB} GB free headroom ({_gb(required)} total), but only {_gb(free)} "
        f"is free at {cache}. Free up space (or move the model cache to a bigger volume) and retry."
    )


def disk_full_message(*, cache_dir: "str | None" = None) -> str:
    """Actionable text for a download that ran out of space *mid-flight*
    (the preflight guard can't see other writers or an unknown plan size)."""
    cache = cache_dir or hf_cache_dir()
    free = disk_free_bytes(cache)
    have = f"only {free / _GIB:.1f} GB is free" if free > 0 else "the disk is full"
    return (
        f"The disk filled up while downloading: {have} at {cache}. Free up space "
        "(or move the model cache to a bigger volume) and retry; the download "
        "resumes from the part that already finished."
    )


def cache_is_complete(model: dict) -> bool:
    """True when this model's on-disk cache is usable (not a truncated download).

    Config-only repos carry no weight file of their own. Their catalogue entry
    declares the small files that make the pipeline usable, so a README left by a
    gated 403 is not mistaken for a completed install. A weight-bearing repo is
    complete only if at least one snapshot has weights; if no snapshot directory
    is found, the size-based caller's cached result is preserved.
    """
    for dependency in model.get("dependencies") or ():
        snapshots = _snapshot_dirs(dependency["repo_id"])
        if not any(snapshot_is_complete(dependency, path) for path in snapshots):
            return False
    dirs = _snapshot_dirs(model["repo_id"])
    if not dirs:
        return True
    return any(snapshot_is_complete(model, snapshot) for snapshot in dirs)


def installed_snapshot_path(repo_id: str) -> "str | None":
    """Complete local snapshot directory of an installed catalogue repo, else None.

    Loading an installed model by *repo id* makes huggingface_hub ask the Hub
    which commit ``main`` is before touching the cache — with no timeout on that
    call, so a network that drops packets stalls the load, and a pinned-revision
    install (no ``refs/main``) cannot load offline at all (#2583). A concrete
    snapshot directory loads with no network. Prefers the recorded installed
    revision, then the newest complete snapshot. Never raises.
    """
    try:
        meta = get_model_catalog().get(repo_id)
        if meta is None:
            return None
        hub_repo = meta["repo_id"]
        snapshots = [path for path in _snapshot_dirs(hub_repo) if snapshot_is_complete(meta, path)]
        if not snapshots:
            return None
        try:
            from services.hf_cache_repair import hf_cache_home
            from services.hf_revisions import installed_revision
            pinned = installed_revision(hub_repo, hf_cache_home())
        except Exception:  # noqa: BLE001 — uncurated repo: newest snapshot wins
            pinned = None
        for path in snapshots:
            if os.path.basename(path) == pinned:
                return path
        return max(snapshots, key=os.path.getmtime)
    except Exception:  # noqa: BLE001 — callers keep loading by name
        return None


def _is_cached_on_disk(repo_id: str) -> bool:
    """Direct-filesystem fallback for is_cached when scan_cache_dir is unavailable.

    On Windows scan_cache_dir() can raise WinError 448 ('untrusted mount point');
    we then walk the canonical HF layout <root>/models--<org>--<name>/snapshots/
    <rev>/ and treat the repo as cached if any revision directory has files. This
    stops a present model from being mistaken for missing and re-downloaded
    (#117/#118).
    """
    name = _repo_dir_name(repo_id)
    for root in _hub_cache_roots():
        snaps = os.path.join(root, name, "snapshots")
        try:
            if not os.path.isdir(snaps):
                continue
            for rev in os.listdir(snaps):
                rev_dir = os.path.join(snaps, rev)
                if os.path.isdir(rev_dir):
                    # `with` so the dir handle is closed even when any() short-
                    # circuits — avoids handle leaks on repeated polls (Greptile).
                    with os.scandir(rev_dir) as it:
                        if any(it):
                            return True
        except OSError:
            continue
    return False


def _scan_cache_on_disk() -> dict[str, dict]:
    """Direct-filesystem equivalent of scan_cache_dir(), for the WinError-448
    fallback path. Returns {repo_id: {size_on_disk, last_accessed, nb_files}}."""
    out: dict[str, dict] = {}
    for root in _hub_cache_roots():
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not name.startswith("models--"):
                continue
            repo_id = name[len("models--"):].replace("--", "/")
            if repo_id in out:
                continue  # first root wins (HF_HUB_CACHE before the /hub probe)
            repo_root = os.path.join(root, name)
            if not os.path.isdir(os.path.join(repo_root, "snapshots")):
                continue
            size = 0
            nb = 0
            for dirpath, _dirs, files in os.walk(repo_root):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(dirpath, f))
                        nb += 1
                    except OSError:
                        # Skip files we can't stat (broken symlink, permission) —
                        # the count is best-effort for the UI's "installed" badge.
                        continue
            if nb > 0:
                out[repo_id] = {"size_on_disk": size, "last_accessed": None, "nb_files": nb}
    return out


def _cache_dir_missing(exc: Exception) -> bool:
    """Whether Hugging Face is reporting the normal empty-cache state.

    ``CacheNotFound`` is expected on a clean installation before the first
    download. Treating it like a damaged Windows cache makes every model probe
    perform a redundant filesystem fallback and fills the first-run log with
    warnings. Unexpected scan failures remain visible and recoverable below.
    """
    return type(exc).__name__ == "CacheNotFound"


def is_cached(repo_id: str) -> bool:
    """Best-effort check: does HF have this repo in its cache on disk?"""
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        for entry in info.repos:
            if entry.repo_id == repo_id and entry.size_on_disk > 0:
                return True
        return False
    except Exception as e:
        if _cache_dir_missing(e):
            return False
        # scan_cache_dir can raise on Windows (WinError 448 'untrusted mount
        # point'); fall back to a direct disk check so a cached model isn't
        # mistaken for missing and re-downloaded in a loop (#117/#118). Logged
        # at WARNING with the exception type (MM2-09) so this fallback isn't
        # invisible when triaging a Windows cache report — it previously logged
        # at DEBUG and never showed at the default level.
        logger.warning("is_cached: scan_cache_dir failed (%s: %s); using on-disk fallback for %s",
                       type(e).__name__, e, repo_id)
        return _is_cached_on_disk(repo_id)


# ── Response Cache ─────────────────────────────────────────────────────────
# Simple TTL dict cache to avoid re-scanning the HF cache directory on every
# frontend poll.  Entries expire after ``_CACHE_TTL`` seconds.

_CACHE_TTL = 10.0  # seconds
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float = _CACHE_TTL):
    """Return cached value if still valid, else None."""
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < ttl:
        return entry[1]
    return None


def _set_cache(key: str, value: object) -> None:
    _cache[key] = (time.monotonic(), value)


def invalidate_cache() -> None:
    """Called after install/delete to bust the models cache."""
    _cache.clear()


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/models/access/status", dependencies=[Depends(reject_cross_site_get)])
def model_access_status(repo_id: str = Query(...)):
    """Check gated Hub access without downloading model files.

    This route runs only after an explicit UI action. It never returns the
    token or a raw Hub exception; callers need only the per-repository verdict.
    """
    model = _catalog.get(repo_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown model")
    if not model.get("gated"):
        return {
            "repo_id": repo_id,
            "token_present": False,
            "ready": True,
            "repositories": [],
        }

    from services import token_resolver

    resolved = token_resolver.resolve()
    repositories = [model["repo_id"]]
    prerequisite = str(model.get("prerequisite_repo_id") or "").strip()
    if prerequisite:
        repositories.append(prerequisite)
    if not resolved:
        return {
            "repo_id": repo_id,
            "token_present": False,
            "ready": False,
            "repositories": [
                {"repo_id": current, "access": "token_missing"}
                for current in repositories
            ],
        }

    from huggingface_hub import get_hf_file_metadata, hf_hub_url
    from services.hf_auth import CANONICAL_ENDPOINT

    results = []
    for current in repositories:
        try:
            # Access is an account verdict on Hugging Face itself; a configured
            # mirror (HF_ENDPOINT) must never receive the token.
            url = hf_hub_url(current, filename=".gitattributes", endpoint=CANONICAL_ENDPOINT)
            get_hf_file_metadata(url, token=resolved.token, endpoint=CANONICAL_ENDPOINT)
            access = "granted"
        except Exception as exc:  # Hub exception types vary across releases.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            access = "required" if status in {401, 403, 404} else "unavailable"
        results.append({"repo_id": current, "access": access})
    return {
        "repo_id": repo_id,
        "token_present": True,
        "ready": all(item["access"] == "granted" for item in results),
        "repositories": results,
    }


def _model_license_info(repo_id: str) -> dict:
    from services.model_licenses import safe_disclosure
    return safe_disclosure(repo_id)


def _model_licence_acceptance(repo_id: str) -> dict:
    from services import model_acceptance
    return model_acceptance.status(repo_id)


def _engine_gate_covers_all_rows(engine_id: str) -> bool:
    """True when the engine's runtime licence gate checks every registry row
    recorded for it; False when it checks only the model actually loaded.

    Mirrors the runtime paths so the catalogue never demands acceptances the
    gate will not: TTS engines gate in ``tts_backend.ensure_engine_licence``
    with the class's ``configured_identity()`` - ``None`` for engines that
    ship a fixed model set (every recorded row is gated, e.g. ``omnivoice``
    with its tokenizer), the selected checkpoint for preference-dependent
    engines (``mlx-audio`` gates only the chosen model). ASR and diarisation
    engines gate per selected model (``asr_backend.ensure_backend_licence``),
    so a non-TTS engine id adds no dependencies.
    """
    try:
        from services import tts_backend
        cls = tts_backend.get_backend_class(engine_id)
    except Exception:  # noqa: BLE001 — not a TTS engine: per-model gate
        return False
    try:
        return cls.configured_identity() is None
    except Exception:  # noqa: BLE001 — the gate itself fails closed (identity=None) here
        return True


def _model_licence_dependencies(model: dict, own_key: str) -> list[dict]:
    """Acceptance states of the other gated repos this row's engines need.

    Only engines whose runtime gate covers every registry row recorded for
    them contribute (``_engine_gate_covers_all_rows``): generation then gates
    on all of them (``model_acceptance.repos_for_engine``), so a row whose
    licence dialog only offers its own licence leaves the engine blocked
    after accepting (#3855: OmniVoice also needs the Higgs tokenizer's
    licence, which has no catalogue row of its own). Engines that gate per
    selected model contribute nothing: running this row checks only the row
    itself. The row carries those dependent states so its dialog can list
    and clear all of them at once.
    """
    try:
        from services import model_acceptance
        hf_repo = model["repo_id"]
        seen: dict[str, dict] = {}
        for engine in model.get("engines") or []:
            if not _engine_gate_covers_all_rows(engine):
                continue
            for repo_id in model_acceptance.repos_for_engine(engine):
                if repo_id in (own_key, hf_repo) or repo_id in seen:
                    continue
                state = model_acceptance.status(repo_id)
                if state["required"]:
                    seen[repo_id] = state
        return list(seen.values())
    except Exception:  # noqa: BLE001 — the row's own licence flow must survive a registry read failure
        logger.warning("model licence dependency lookup failed", exc_info=True)
        return []


@router.get("/models")
def list_models():
    """Catalogue every known model + its on-disk install state.

    Uses a 10 s response cache to avoid repeated ``scan_cache_dir()`` disk
    walks when the frontend polls.
    """
    platform_tags = _current_platform_tags()
    cache_key = "models:local:" + ",".join(sorted(platform_tags))
    cached_response = _cached(cache_key)
    if cached_response is not None:
        return cached_response

    cached_by_repo: dict[str, dict] = {}
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        for entry in info.repos:
            cached_by_repo[entry.repo_id] = {
                "size_on_disk": entry.size_on_disk,
                "last_accessed": entry.last_accessed,
                "nb_files": entry.nb_files,
            }
    except Exception as e:
        if _cache_dir_missing(e):
            cached_by_repo = {}
        else:
            # WinError-448 fallback (#117/#118): use a direct disk scan so installed
            # models still show as installed instead of offering a re-download.
            logger.warning("scan_cache_dir failed (%s); using disk fallback", e)
            cached_by_repo = _scan_cache_on_disk()

    out = []
    host_tags = set(platform_tags)
    for m in KNOWN_MODELS:
        cached = cached_by_repo.get(m["repo_id"])
        if cached is not None and shares_repo(m):
            # The repository also holds other entries' packages: only this
            # entry's own files decide whether it is installed and its size.
            cached = package_cache_usage(m)
            on_disk = cached is not None
            incomplete = False
        else:
            on_disk = cached is not None and cached["size_on_disk"] > 0
            # A size-positive cache can still be a truncated download (config
            # landed, weight shard didn't). Treat that as not-installed +
            # incomplete so the wizard re-offers the download (#622).
            incomplete = on_disk and not cache_is_complete(m)
        key = model_key(m)
        out.append({
            **m,
            # Rows are addressed by catalogue identity; hf_repo is the Hub repo.
            "repo_id": key,
            "hf_repo": m["repo_id"],
            "installed": on_disk and not incomplete,
            "incomplete": incomplete,
            "size_on_disk_bytes": cached["size_on_disk"] if cached else 0,
            "nb_files": cached["nb_files"] if cached else 0,
            "supported": _model_supported(m),
            # Curated "best for your system" pick (curated_on in models.yaml) —
            # drives the recommended badge in the wizard and Settings model store.
            "curated": _model_curated(m, host_tags),
            "license_info": _model_license_info(key),
            "license_acceptance": _model_licence_acceptance(key),
            "license_dependencies": _model_licence_dependencies(m, key),
        })
    response = {
        "target": "local",
        "models": out,
        "total_installed_bytes": sum(m["size_on_disk_bytes"] for m in out),
        "hf_cache_dir": hf_cache_dir(),
        # Free space on the cache volume, so the Model Store header can warn
        # BEFORE an "Install all" overruns the disk (pairs with the per-install
        # disk_space_error guard in setup/download.py).
        "disk_free_gb": round(disk_free_bytes() / _GIB, 1),
        # The headroom disk_space_error keeps free on top of every download, so
        # the UI warns with the same rule (and names it) instead of a raw
        # "needs 4.8 GB, 10.2 GB free" that contradicts its own refusal (#2597).
        "disk_headroom_gb": INSTALL_HEADROOM_GB,
        "platform_tags": platform_tags,
    }
    _set_cache(cache_key, response)
    return response


@router.get("/setup/recommendations")
def recommendations():
    """Return a curated model preset for the caller's device + architecture.

    Data-driven from the ``curated_on`` field in models.yaml — adding or
    retargeting a curated pick is a catalog edit, not a code change. Only the
    TTS model is required; the ASR picks here are the optional "best for your
    system" set the wizard and Settings surface for on-demand install.
    """
    tags = set(_current_platform_tags())
    target_os = "darwin" if "darwin" in tags else "win32" if "win32" in tags else "linux"
    target_arch = next((tag.split("-", 1)[1] for tag in tags if tag.startswith(target_os + "-")), _platform.machine())
    is_mac_arm = target_os == "darwin" and target_arch == "arm64"
    is_mac_intel = target_os == "darwin" and target_arch == "x86_64"
    is_linux = target_os == "linux"
    is_windows = target_os == "win32"
    has_cuda = "cuda" in tags and "rocm" not in tags
    has_rocm = "rocm" in tags

    # Device label — used as the card title.
    if is_mac_arm:
        device_label = f"Apple Silicon ({target_arch})"
    elif is_mac_intel:
        device_label = "macOS Intel (x86_64)"
    elif is_windows:
        device_label = "Windows x64" + (" + CUDA" if has_cuda else " + ROCm" if has_rocm else "")
    elif is_linux:
        device_label = "Linux x64" + (" + CUDA" if has_cuda else " + ROCm" if has_rocm else "")
    else:
        device_label = f"{target_os} / {target_arch}"

    # Curated preset for this host, in catalog order (required entries lead).
    curated = [
        m for m in KNOWN_MODELS
        if _model_curated(m, tags) and _model_supported(m)
    ]

    if not (is_mac_arm or has_cuda or has_rocm):
        # CPU-only preset: lead with the light Whisper (small) so a user working
        # down the list meets the cheap download before large-v3 / Turbo. Only
        # the ASR slots are reordered (smallest first); every other entry keeps
        # its catalog position.
        asr_slots = [i for i, m in enumerate(curated) if m.get("role") == "ASR"]
        ordered = sorted((curated[i] for i in asr_slots), key=lambda m: m.get("size_gb") or 0)
        for slot, model in zip(asr_slots, ordered):
            curated[slot] = model

    if is_mac_arm:
        rationale = (
            "Apple Silicon preset: VoiceStudio (required) covers multilingual TTS + "
            "cloning on its own. The optional picks are Metal-native: MLX Whisper "
            "large-v3 for dubbing/transcription, Whisper Turbo (MLX) + Parakeet TDT "
            "v3 for live dictation, Kokoro + KittenTTS for instant English TTS."
        )
    elif has_cuda:
        rationale = (
            "NVIDIA preset: VoiceStudio (required) runs standalone. Optional ASR picks "
            "are CUDA-accelerated via CTranslate2 — Whisper large-v3 for dubbing "
            "(best word timestamps) and Turbo for 5× faster transcription. Whisper "
            "Tiny provides broad-language local dictation. KittenTTS adds CPU-realtime English."
        )
    elif has_rocm:
        rationale = (
            "AMD/ROCm preset: VoiceStudio (required) runs standalone. CTranslate2 has "
            "no ROCm backend, so the PyTorch Whisper large-v3 build is the "
            "GPU-accelerated ASR route; faster-whisper works on CPU, and Whisper Tiny "
            "provides broad-language local dictation."
        )
    else:
        rationale = (
            "CPU preset: VoiceStudio (required) runs standalone. Optional picks favour "
            "speed on CPU — Whisper small (int8) as the light default, large-v3 for "
            "accuracy and Turbo when you can spare the time and RAM, Whisper Tiny "
            "(ONNX) for live dictation, KittenTTS for instant English TTS."
        )

    cached_ids: set[str] = set()
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        cached_ids = {
            entry.repo_id for entry in info.repos if entry.size_on_disk > 0
        }
    except Exception as e:
        if _cache_dir_missing(e):
            cached_ids = set()
        else:
            # WinError-448 fallback (#117/#118): recommend based on the disk scan.
            logger.debug("scan_cache_dir failed (%s); using disk fallback", e)
            cached_ids = set(_scan_cache_on_disk().keys())

    entries = []
    for meta in curated:
        rid = model_key(meta)
        # Mirror /models: a truncated cache (weights missing) is not installed, so
        # the wizard counts it toward the remaining download instead of "all set".
        if shares_repo(meta):
            installed = package_cache_usage(meta) is not None
        else:
            installed = meta["repo_id"] in cached_ids and cache_is_complete(meta)
        entries.append({
            "repo_id": rid,
            "label": meta.get("label", rid),
            "role": meta.get("role", ""),
            "size_gb": meta.get("size_gb", 0),
            "required": bool(meta.get("required", False)),
            "note": meta.get("note"),
            "installed": installed,
        })

    to_download_gb = sum(e["size_gb"] for e in entries if not e["installed"])
    all_installed = all(e["installed"] for e in entries)

    return {
        "target": "local",
        "device": {
            "os": target_os,
            "arch": target_arch,
            "is_mac_arm": is_mac_arm,
            "is_mac_intel": is_mac_intel,
            "is_linux": is_linux,
            "is_windows": is_windows,
            "has_cuda": has_cuda,
            "label": device_label,
        },
        "rationale": rationale,
        "models": entries,
        "download_gb_remaining": round(to_download_gb, 2),
        "total_gb": round(sum(e["size_gb"] for e in entries), 2),
        "all_installed": all_installed,
    }

