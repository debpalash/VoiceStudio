"""Model Catalogue entries and their Hugging Face cache state.

Shared by the setup routers and by services (engine disk usage), so services
never import a router module. ``api.routers.setup.models`` re-exports every
name here.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("omnivoice.setup.models")

_YAML_PATH = Path(__file__).resolve().parents[1] / "config" / "models.yaml"


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


def model_key(model: dict) -> str:
    """Catalogue identity of one entry.

    Most entries are a whole Hugging Face repository and their identity is the
    repo id. An entry that selects one package from a repository shared with
    other entries declares its own ``id`` (``<repo_id>:<package>``, never a
    valid Hub id). API rows, install/cancel/delete requests, progress events
    and licence records use this identity; Hub calls and the cache always use
    ``repo_id``.
    """
    return str(model.get("id") or model["repo_id"])


def shares_repo(model: dict, models: "list[dict] | None" = None) -> bool:
    """True when another catalogue entry downloads from the same Hub repo."""
    repo = model["repo_id"]
    return any(
        other is not model and other.get("repo_id") == repo
        for other in (KNOWN_MODELS if models is None else models)
    )


KNOWN_MODELS = _load_models_from_yaml()


def catalogue_entry(key: str, models: "list[dict] | None" = None) -> "dict | None":
    """Catalogue entry by identity (:func:`model_key`), else None."""
    return next(
        (m for m in (KNOWN_MODELS if models is None else models) if model_key(m) == key),
        None,
    )


def hf_cache_dir() -> str:
    return (
        os.environ.get("HF_HUB_CACHE")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("HF_HOME")
        or os.path.expanduser("~/.cache/huggingface")
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


def _package_files(model: dict, snapshot_path: str) -> list[str]:
    """Files of one snapshot that this entry's ``allow_patterns`` select."""
    import fnmatch

    patterns = [str(p) for p in model.get("allow_patterns") or ()]
    found = []
    for root, _dirs, files in os.walk(snapshot_path):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, snapshot_path).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(rel, pattern) for pattern in patterns):
                found.append(path)
    return found


def package_cache_usage(model: dict) -> "dict | None":
    """``{size_on_disk, nb_files}`` of an installed package entry, else None.

    For an entry that shares its Hub repository with other entries: complete
    only when a snapshot holds every required file, and sized by the files its
    ``allow_patterns`` select, so a sibling package never makes it look
    installed or inflates its size.
    """
    for snapshot in _snapshot_dirs(model["repo_id"]):
        if not snapshot_is_complete(model, snapshot):
            continue
        files = _package_files(model, snapshot)
        size = 0
        for path in files:
            try:
                size += os.path.getsize(path)
            except OSError:
                # A broken snapshot link (blob removed) adds nothing; the
                # required-files check above already vouched for completeness.
                continue
        return {"size_on_disk": size, "last_accessed": None, "nb_files": len(files)}
    return None
