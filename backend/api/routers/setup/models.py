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

# ── Model Catalog (loaded from YAML) ──────────────────────────────────────

_YAML_PATH = Path(__file__).resolve().parents[3] / "config" / "models.yaml"


def _load_models_from_yaml() -> list[dict]:
    """Load model catalog from config/models.yaml.

    Falls back to an empty list if the file is missing or unreadable.
    The YAML file is read once at import time — restart to pick up edits.
    """
    try:
        import yaml  # PyYAML is already a transitive dep of huggingface_hub
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        models = data.get("models", [])
        logger.info("Loaded %d models from %s", len(models), _YAML_PATH)
        return models
    except FileNotFoundError:
        logger.warning("models.yaml not found at %s — using empty catalog", _YAML_PATH)
        return []
    except Exception:
        logger.exception("Failed to load models.yaml — using empty catalog")
        return []


KNOWN_MODELS = _load_models_from_yaml()

# Back-compat tuple view for code that expects (repo_id, label) pairs.
REQUIRED_MODELS = [(m["repo_id"], m["label"]) for m in KNOWN_MODELS if m.get("required")]


# ── Dependency Injection ───────────────────────────────────────────────────
# Use `catalog: ModelCatalog = Depends(get_model_catalog)` in endpoint params
# for testable, mockable access to the model registry.

class ModelCatalog:
    """Injectable service wrapping the model catalog + cache scanner."""

    def __init__(self, models: list[dict] | None = None):
        self.models = models if models is not None else KNOWN_MODELS
        self._by_id = {m["repo_id"]: m for m in self.models}
        self._required = [(m["repo_id"], m["label"]) for m in self.models if m.get("required")]

    def get(self, repo_id: str) -> dict | None:
        return self._by_id.get(repo_id)

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

def _target_worker():
    """Selected live remote worker, or None when the catalog targets local."""
    try:
        from worker import routing, service  # noqa: PLC0415

        decision = routing.decide()
        plane = service.control_plane
        return plane.pool.get(decision.worker_id) if decision.remote and plane.pool else None
    except Exception:
        return None


def _target_host() -> dict | None:
    """Selected remote worker host, or None when the catalog targets local."""
    live = _target_worker()
    return dict(live.record.host or {}) if live is not None else None


def _target_repo_inventory() -> tuple[str, set[str]] | None:
    """Selected worker id and the catalog repositories it reports on disk."""
    live = _target_worker()
    if live is None:
        return None
    downloaded: set[str] = set()
    for capability in live.record.capabilities or []:
        if capability.get("downloaded"):
            downloaded.update(str(repo) for repo in capability.get("repo_ids") or [])
    return live.worker_id, downloaded


def _current_platform_tags() -> list[str]:
    """Return platform tags that the current host supports.

    Beyond the OS/arch tags, emits the acceleration family so both the
    ``platforms`` gate and the ``curated_on`` recommendation field can key on
    it: ``cuda`` (NVIDIA — also present on ROCm hosts, where torch reports
    CUDA available, so existing ``platforms: [cuda]`` entries keep working),
    ``rocm`` (AMD HIP builds), and ``cpu`` (no GPU acceleration at all —
    Apple Silicon is NOT tagged cpu; it curates via ``darwin-arm64``).
    """
    target = _target_host()
    if target is not None:
        target_os = {"windows": "win32", "darwin": "darwin"}.get(
            str(target.get("os") or "").lower(), "linux"
        )
        arch = str(target.get("arch") or "").lower()
        arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
        tags = [target_os, f"{target_os}-{arch}"]
        backend = ""
        if target.get("gpus"):
            backend = str(target["gpus"][0].get("backend") or "").lower()
        if backend:
            tags.append(backend)
            if backend == "rocm":
                tags.append("cuda")
        if not backend and not (target_os == "darwin" and arch == "arm64"):
            tags.append("cpu")
        return tags

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

def hf_cache_dir() -> str:
    return (
        os.environ.get("HF_HUB_CACHE")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("HF_HOME")
        or os.path.expanduser("~/.cache/huggingface")
    )


# ── Disk-space guard (shared, single-sourced) ──────────────────────────────
# MIN_FREE_GB is the headroom we insist on keeping free on the model-cache
# volume — the wizard's absolute pre-install floor AND the extra buffer the
# per-install check demands on top of the download itself, so an "Install all"
# can't fill the disk to the brim (setup/download.py). Lives here — the lowest
# module in the setup import graph — so the wizard, the /models header, and the
# install endpoint can't drift apart (mirrors the weight-floor single-sourcing).
_GIB = 1024 ** 3
MIN_FREE_GB = 10


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
    """Actionable message when *to_download_bytes* (+ MIN_FREE_GB headroom) won't
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
    required = int(to_download_bytes) + MIN_FREE_GB * _GIB
    if free >= required:
        return None

    def _gb(n: int) -> str:
        return f"{n / _GIB:.1f} GB"

    return (
        f"Not enough disk space to install: this download needs {_gb(int(to_download_bytes))} "
        f"plus {MIN_FREE_GB} GB free headroom ({_gb(required)} total), but only {_gb(free)} "
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


def _repo_dir_name(repo_id: str) -> str:
    """HF cache dir name for a repo: 'k2-fsa/OmniVoice' → 'models--k2-fsa--OmniVoice'."""
    return "models--" + repo_id.replace("/", "--")


def _hub_cache_roots() -> list[str]:
    """Candidate roots that directly contain ``models--*`` dirs.

    HF stores repos under ``$HF_HUB_CACHE`` (== ``$HF_HOME/hub`` by default). When
    only ``HF_HOME`` (or the ``~/.cache/huggingface`` default) is known, the repos
    live under the ``hub`` subdir — so we probe both ``<dir>`` (the
    ``HF_HUB_CACHE``-is-set case, e.g. VoiceStudio's Windows short cache) and
    ``<dir>/hub`` (the ``HF_HOME``-only case). Without this the WinError-448
    fallback would look one level too high and miss the cache (CodeRabbit #137).
    """
    base = hf_cache_dir()
    roots = [base]
    hub = os.path.join(base, "hub")
    if hub not in roots:
        roots.append(hub)
    return roots


# ── Weight-presence (truncated-cache) detection ─────────────────────────────
# A cache that downloaded config/tokenizer files but not the weight shard still
# occupies bytes on disk, so a size-only "installed" check (#352/#581/#606) reads
# it as installed and the first-run wizard hides the re-download button, stranding
# the user (#622). These helpers tell a *complete* snapshot from a truncated one by
# checking for a plausible weight file — the same class `download.py` guards at
# install time and `model_manager.py` repairs at load time. Shared here (the lowest
# module in the setup import graph; `download.py` imports from this module) so the
# floors live in exactly one place and can't drift between the three call sites.

_MIN_WEIGHT_BYTES = 5 * 1024 * 1024  # tensor formats: a real shard is ≥ a few MB

# Per-extension floors. ONNX graphs are legitimately small (a complete model can be
# well under 5 MB), so they get a lower floor that still rejects a bytes-only partial.
_WEIGHT_FLOORS = {
    ".safetensors": _MIN_WEIGHT_BYTES,
    ".bin": _MIN_WEIGHT_BYTES,
    ".ckpt": _MIN_WEIGHT_BYTES,
    ".pt": _MIN_WEIGHT_BYTES,
    ".pth": _MIN_WEIGHT_BYTES,
    ".gguf": _MIN_WEIGHT_BYTES,
    ".onnx": 64 * 1024,
}


def snapshot_has_weights(snapshot_path: str) -> bool:
    """True when a finished snapshot dir holds a plausible weight file.

    A snapshot is complete if it contains a recognized weight file meeting its
    per-extension floor OR any file ≥ the global 5 MB floor (the lenient catch for
    non-standard weight names). Returns True when the path can't be inspected — an
    un-walkable dir must never be reported as truncated, only a confirmed weight-less
    one. `getsize` follows symlinks, so HF's snapshot→blob links resolve correctly;
    a broken link (missing blob) raises OSError and is skipped, i.e. counts as absent.
    """
    try:
        for root, _dirs, files in os.walk(snapshot_path, followlinks=True):
            for f in files:
                try:
                    size = os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
                ext = os.path.splitext(f)[1].lower()
                floor = _WEIGHT_FLOORS.get(ext)
                if floor is not None and size >= floor:
                    return True
                if size >= _MIN_WEIGHT_BYTES:
                    return True
    except OSError:
        return True  # can't inspect — don't mislabel as truncated
    return False


def _snapshot_dirs(repo_id: str) -> list[str]:
    """Existing snapshot revision dirs for a repo across the candidate cache roots."""
    name = _repo_dir_name(repo_id)
    dirs: list[str] = []
    for root in _hub_cache_roots():
        snaps = os.path.join(root, name, "snapshots")
        try:
            for rev in os.listdir(snaps):
                rev_dir = os.path.join(snaps, rev)
                if os.path.isdir(rev_dir):
                    dirs.append(rev_dir)
        except OSError:
            continue
    return dirs


def snapshot_is_complete(model: dict, snapshot_path: str) -> bool:
    """Apply the same catalogue requirements during installation and listing."""
    config_only = bool(model.get("config_only"))
    required = tuple(str(name) for name in (
        model.get("config_required_files") if config_only else model.get("required_files")
    ) or ())
    if config_only and not required:
        return False
    try:
        present = all(
            os.path.isfile(os.path.join(snapshot_path, name))
            and os.path.getsize(os.path.join(snapshot_path, name)) >= (
                1 if config_only else _WEIGHT_FLOORS.get(os.path.splitext(name)[1].lower(), 1)
            )
            for name in required
        )
        return present and (config_only or snapshot_has_weights(snapshot_path))
    except OSError:
        return False


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
        snapshots = [path for path in _snapshot_dirs(repo_id) if snapshot_is_complete(meta, path)]
        if not snapshots:
            return None
        try:
            from services.hf_cache_repair import hf_cache_home
            from services.hf_revisions import installed_revision
            pinned = installed_revision(repo_id, hf_cache_home())
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
    repositories = [repo_id]
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


@router.get("/models")
def list_models():
    """Catalogue every known model + its on-disk install state.

    Uses a 10 s response cache to avoid repeated ``scan_cache_dir()`` disk
    walks when the frontend polls.
    """
    platform_tags = _current_platform_tags()
    remote_inventory = _target_repo_inventory()
    target_key = remote_inventory[0] if remote_inventory else "local"
    cache_key = "models:" + target_key + ":" + ",".join(sorted(platform_tags))
    cached_response = _cached(cache_key)
    if cached_response is not None:
        return cached_response

    cached_by_repo: dict[str, dict] = {}
    if remote_inventory is not None:
        for model in KNOWN_MODELS:
            if model["repo_id"] in remote_inventory[1]:
                cached_by_repo[model["repo_id"]] = {
                    "size_on_disk": int(float(model.get("size_gb") or 0) * _GIB),
                    "last_accessed": None,
                    "nb_files": 0,
                }
    else:
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
        on_disk = (
            m["repo_id"] in remote_inventory[1]
            if remote_inventory is not None
            else cached is not None and cached["size_on_disk"] > 0
        )
        # A size-positive cache can still be a truncated download (config landed,
        # weight shard didn't). Treat that as not-installed + incomplete so the
        # wizard re-offers the download instead of stranding the user (#622).
        incomplete = on_disk and remote_inventory is None and not cache_is_complete(m)
        out.append({
            **m,
            "installed": on_disk and not incomplete,
            "incomplete": incomplete,
            "size_on_disk_bytes": cached["size_on_disk"] if cached else 0,
            "nb_files": cached["nb_files"] if cached else 0,
            "supported": _model_supported(m),
            # Curated "best for your system" pick (curated_on in models.yaml) —
            # drives the recommended badge in the wizard and Settings model store.
            "curated": _model_curated(m, host_tags),
        })
    response = {
        "target": target_key,
        "models": out,
        "total_installed_bytes": sum(m["size_on_disk_bytes"] for m in out),
        "hf_cache_dir": "" if remote_inventory is not None else hf_cache_dir(),
        # Free space on the cache volume, so the Model Store header can warn
        # BEFORE an "Install all" overruns the disk (pairs with the per-install
        # disk_space_error guard in setup/download.py).
        "disk_free_gb": None if remote_inventory is not None else round(disk_free_bytes() / _GIB, 1),
        # The headroom disk_space_error keeps free on top of every download, so
        # the UI warns with the same rule (and names it) instead of a raw
        # "needs 4.8 GB, 10.2 GB free" that contradicts its own refusal (#2597).
        "disk_headroom_gb": MIN_FREE_GB,
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

    remote_inventory = _target_repo_inventory()
    cached_ids: set[str] = set()
    if remote_inventory is not None:
        cached_ids = remote_inventory[1]
    else:
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
        rid = meta["repo_id"]
        # Mirror /models: a truncated cache (weights missing) is not installed, so
        # the wizard counts it toward the remaining download instead of "all set".
        installed = rid in cached_ids and (
            remote_inventory is not None or cache_is_complete(meta)
        )
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
        "target": remote_inventory[0] if remote_inventory is not None else "local",
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
