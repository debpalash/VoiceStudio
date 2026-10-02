"""Storage usage report for Settings → Storage.

Computes, for everything the app owns on disk:

  * per-volume totals (total / used / free, grouped by ``st_dev`` so two
    roots on the same disk are reported once),
  * per-category directory sizes — the HF model cache (with the largest
    model dirs), the app data dir (broken into voices / outputs / dub_jobs /
    batch / preview / database / logs / other subtotals), the per-engine
    venvs under ``backend/engines/*/.venv`` (+ the app venv), and any
    ``omnivoice*`` entries in the OS temp dir,
  * server-side ``warnings`` (low disk, volume pressure, unreadable paths)
    so every client renders the same guidance.

Directory walks are **bounded**: each top-level category gets a deadline
(default 10 s) and returns a partial total (``complete: false`` + an
``unreadable`` warning with ``reason: "timeout"``) when it expires. Results
are cached in-process for 5 minutes; ``refresh`` bypasses the cache. The API
layer runs the whole build in a worker thread so the event loop never blocks.
"""
from __future__ import annotations

import glob
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

CACHE_TTL_SECONDS = 300.0
CATEGORY_TIMEOUT_SECONDS = 10.0
TOP_MODEL_COUNT = 10
VOLUME_PRESSURE_PERCENT = 90.0
DEFAULT_MIN_FREE_GB = 10  # callers pass setup.wizard.MIN_FREE_GB — this is the standalone fallback

# DATA_DIR children we know by name (core.config constants + routers that
# write there). Anything else lands in the "other" subtotal so the numbers
# always add up to the real on-disk footprint.
_DATA_CHILD_DIRS = ("voices", "outputs", "dub_jobs", "batch", "preview")
_DB_PREFIX = "omnivoice.db"          # omnivoice.db + -wal / -shm / -journal
_LOG_FILES = ("crash_log.txt", "error_journal.jsonl")
_LOG_PREFIX = "omnivoice.log"        # rolling log + rotations

_GB = 1024 ** 3


def default_engines_dir() -> str:
    """``DATA_DIR/engines`` — where sidecar engine installs (IndexTTS-2 & friends)
    keep their per-engine venv (`<id>/.venv`) and weights.

    Not ``backend/engines`` (the built-in engine *modules*, which share the app
    venv and have no `.venv` of their own): that dir is import-time code, and a
    sidecar install never lands there. Pointing the report at it meant the
    engine-venv category always measured an empty tree while a real multi-GB
    IndexTTS-2 install silently rolled up into the data dir's "other" subtotal.
    Mirrors ``backend/services/sidecar_install.py`` (`DATA_DIR/engines/<id>`).
    """
    from core.config import DATA_DIR

    return str(Path(DATA_DIR) / "engines")


def _has_venv(entry_path: str) -> bool:
    """True when ``<entry>/.venv`` is a directory. Only a *missing* path means
    "no venv"; a permission or I/O error propagates so the caller can mark the
    scan incomplete instead of silently reclassifying an installed engine."""
    try:
        return stat.S_ISDIR(os.stat(os.path.join(entry_path, ".venv")).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _engines_child_name(engines_dir: str, data_dir: str) -> str | None:
    """Basename of ``engines_dir`` when it is a direct child of ``data_dir`` —
    so the data category can skip it and not double-count what the engine-venv
    category already measures. ``None`` when engines live elsewhere."""
    parent = os.path.dirname(os.path.normpath(engines_dir))
    if os.path.normpath(parent) == os.path.normpath(data_dir):
        return os.path.basename(os.path.normpath(engines_dir))
    return None


def default_app_venv() -> str | None:
    """The venv this backend runs from, when it is one (None for system python)."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return sys.prefix
    return None


def _existing_ancestor(path: str) -> str:
    """Deepest existing ancestor of ``path`` (for disk_usage on missing dirs)."""
    p = os.path.abspath(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def _mount_point(path: str) -> str:
    """Mount point of the volume holding ``path`` (best-effort, cheap)."""
    p = _existing_ancestor(path)
    try:
        while p and not os.path.ismount(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    except OSError:
        pass
    return p or os.path.abspath(os.sep)


def _dir_size(path: str, deadline: float) -> tuple[int, bool, str | None]:
    """du-style size of ``path``: ``(bytes, complete, first_unreadable_path)``.

    Never follows symlinks (lstat + walk default), never raises. Stops early
    and reports ``complete=False`` once ``deadline`` (time.monotonic) passes.
    """
    err_path: str | None = None

    def _onerror(e: OSError) -> None:
        nonlocal err_path
        if err_path is None:
            err_path = getattr(e, "filename", None) or path

    try:
        if not os.path.exists(path):
            return 0, True, None
        if time.monotonic() >= deadline:
            return 0, False, None
        if not os.path.isdir(path):
            return os.lstat(path).st_size, True, None
    except OSError:
        return 0, True, path

    total = 0
    complete = True
    for root, _dirs, files in os.walk(path, onerror=_onerror):
        if time.monotonic() >= deadline:
            complete = False
            break
        for name in files:
            # A large flat directory yields only one walk root. Checking just
            # between roots can spend minutes stat'ing that root's files.
            if time.monotonic() >= deadline:
                return total, False, err_path
            fp = os.path.join(root, name)
            try:
                total += os.lstat(fp).st_size
            except OSError:
                if err_path is None:
                    err_path = fp
    return total, complete, err_path


def _sum_files(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += os.lstat(p).st_size
        except OSError:
            pass
    return total


def _hf_model_dirs(cache_dir: str) -> list[str]:
    """`models--org--name` dirs in the cache root and its `hub/` child.

    HF_HUB_CACHE points straight at the hub dir; HF_HOME needs `/hub`
    appended — scanning both covers either env resolution.
    """
    out: list[str] = []
    for base in (cache_dir, os.path.join(cache_dir, "hub")):
        try:
            with os.scandir(base) as it:
                out.extend(
                    e.path for e in it
                    if e.name.startswith("models--") and e.is_dir(follow_symlinks=False)
                )
        except OSError:
            continue
    return out


def _model_display_name(dir_name: str) -> str:
    return dir_name.removeprefix("models--").replace("--", "/")


def build_report(
    *,
    data_dir: str,
    hf_cache_dir: str,
    engines_dir: str | None = None,
    app_venv: str | None = None,
    temp_root: str | None = None,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    category_timeout: float = CATEGORY_TIMEOUT_SECONDS,
) -> dict:
    """Build the full storage report (synchronous; call from a worker thread)."""
    engines_dir = engines_dir if engines_dir is not None else default_engines_dir()
    temp_root = temp_root if temp_root is not None else tempfile.gettempdir()
    warnings: list[dict] = []
    categories: list[dict] = []

    def _warn_unreadable(category_id: str, path: str, reason: str) -> None:
        warnings.append({
            "kind": "unreadable",
            "severity": "warning",
            "category_id": category_id,
            "path": path,
            "reason": reason,
        })

    def _finish(category_id: str, cat: dict, complete: bool, err_path: str | None) -> None:
        cat["complete"] = complete and err_path is None
        if not complete:
            _warn_unreadable(category_id, cat["path"], "timeout")
        if err_path is not None:
            _warn_unreadable(category_id, err_path, "permission")

    # ── 1. HF model cache (+ top model dirs) ───────────────────────────────
    deadline = time.monotonic() + category_timeout
    hf_total = 0
    hf_complete = True
    hf_err: str | None = None
    models: list[dict] = []
    model_dirs = set(_hf_model_dirs(hf_cache_dir))
    seen: set[str] = set()
    for mdir in sorted(model_dirs):
        size, ok, err = _dir_size(mdir, deadline)
        hf_total += size
        hf_complete = hf_complete and ok
        hf_err = hf_err or err
        models.append({"name": _model_display_name(os.path.basename(mdir)), "bytes": size})
        seen.add(os.path.realpath(mdir))
    # Non-model remainder of the cache (datasets, xet chunks, token file, …):
    # walk the top-level entries that aren't model dirs so the category total
    # reflects the whole cache, not just models.
    try:
        with os.scandir(hf_cache_dir) as it:
            entries = list(it)
    except OSError:
        entries = []
        if os.path.exists(hf_cache_dir):
            hf_err = hf_err or hf_cache_dir
    for e in entries:
        if os.path.realpath(e.path) in seen:
            continue
        if e.name == "hub":
            # hub/ holds the model dirs (already counted) + misc; count the rest.
            try:
                with os.scandir(e.path) as hub_it:
                    for h in hub_it:
                        if os.path.realpath(h.path) in seen:
                            continue
                        size, ok, err = _dir_size(h.path, deadline)
                        hf_total += size
                        hf_complete = hf_complete and ok
                        hf_err = hf_err or err
            except OSError:
                hf_err = hf_err or e.path
            continue
        size, ok, err = _dir_size(e.path, deadline)
        hf_total += size
        hf_complete = hf_complete and ok
        hf_err = hf_err or err
    models.sort(key=lambda m: m["bytes"], reverse=True)
    hf_cat = {
        "id": "hf_cache",
        "path": hf_cache_dir,
        "exists": os.path.isdir(hf_cache_dir),
        "bytes": hf_total,
        "items": models[:TOP_MODEL_COUNT],
    }
    _finish("hf_cache", hf_cat, hf_complete, hf_err)
    categories.append(hf_cat)

    # Use one ownership snapshot for both categories: only installed sidecars
    # belong to engine_venvs. Interrupted installs inside DATA_DIR remain data.
    engine_err: str | None = None
    try:
        with os.scandir(engines_dir) as it:
            engine_entries = list(it)
    except FileNotFoundError:
        engine_entries = []
    except OSError:
        engine_entries = []
        engine_err = engines_dir
    engine_dirs = []
    unclassified_engines = set()
    for e in engine_entries:
        # Resolve a transient inspection failure before either category claims
        # the entry. Persistently unknown ownership remains an incomplete scan.
        for _ in range(2):
            try:
                if e.is_dir(follow_symlinks=False) and _has_venv(e.path):
                    engine_dirs.append(e.path)
                break
            except OSError:
                engine_err = engine_err or e.path
        else:
            unclassified_engines.add(e.path)
    engine_dirs.sort()

    # ── 2. App data dir, broken into subtotals ─────────────────────────────
    deadline = time.monotonic() + category_timeout
    data_complete = True
    data_err: str | None = None
    children: list[dict] = []
    claimed: set[str] = set()

    # When sidecar engines live under DATA_DIR/engines, the engine-venv category
    # below owns installed sidecars; count the unclaimed remainder separately.
    engines_child = _engines_child_name(engines_dir, data_dir)
    if engines_child:
        claimed.add(engines_child)
        data_err = engine_err

    for name in _DATA_CHILD_DIRS:
        p = os.path.join(data_dir, name)
        size, ok, err = _dir_size(p, deadline)
        data_complete = data_complete and ok
        data_err = data_err or err
        claimed.add(name)
        children.append({"id": name, "path": p, "bytes": size, "complete": ok})

    db_files = sorted(glob.glob(os.path.join(glob.escape(data_dir), _DB_PREFIX + "*")))
    claimed.update(os.path.basename(p) for p in db_files)
    children.append({
        "id": "database",
        "path": os.path.join(data_dir, _DB_PREFIX),
        "bytes": _sum_files(db_files),
        "complete": True,
    })

    log_files = sorted(glob.glob(os.path.join(glob.escape(data_dir), _LOG_PREFIX + "*")))
    log_files += [os.path.join(data_dir, n) for n in _LOG_FILES]
    claimed.update(os.path.basename(p) for p in log_files)
    children.append({
        "id": "logs",
        "path": data_dir,
        "bytes": _sum_files(log_files),
        "complete": True,
    })

    other_bytes = 0
    # Unlisted or unclassifiable managed engines may hide bytes owned by Other.
    other_complete = not (engines_child and engine_err is not None)
    try:
        with os.scandir(data_dir) as it:
            for e in it:
                if e.name in claimed:
                    continue
                if time.monotonic() >= deadline:
                    data_complete = other_complete = False
                    break
                if e.is_dir(follow_symlinks=False):
                    size, ok, err = _dir_size(e.path, deadline)
                    other_bytes += size
                    data_complete = data_complete and ok
                    other_complete = other_complete and ok and err is None
                    data_err = data_err or err
                else:
                    try:
                        other_bytes += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        data_err = data_err or e.path
                        other_complete = False
    except OSError:
        if os.path.exists(data_dir):
            data_err = data_err or data_dir
            other_complete = False
    if engines_child:
        for e in engine_entries:
            if e.path in engine_dirs or e.path in unclassified_engines:
                continue
            if time.monotonic() >= deadline:
                data_complete = other_complete = False
                break
            try:
                if e.is_dir(follow_symlinks=False):
                    size, ok, err = _dir_size(e.path, deadline)
                    other_bytes += size
                    data_complete = data_complete and ok
                    other_complete = other_complete and ok and err is None
                    data_err = data_err or err
                else:
                    other_bytes += e.stat(follow_symlinks=False).st_size
            except OSError:
                data_err = data_err or e.path
                other_complete = False
    children.append({"id": "other", "path": data_dir, "bytes": other_bytes, "complete": other_complete})

    data_cat = {
        "id": "data",
        "path": data_dir,
        "exists": os.path.isdir(data_dir),
        "bytes": sum(c["bytes"] for c in children),
        "children": children,
    }
    _finish("data", data_cat, data_complete, data_err)
    categories.append(data_cat)

    # ── 3. Engine venvs (+ the app venv) ───────────────────────────────────
    deadline = time.monotonic() + category_timeout
    venv_total = 0
    venv_complete = True
    venv_err: str | None = engine_err
    venv_items: list[dict] = []
    for edir in engine_dirs:
        # Measure the whole installed sidecar: environment, checkout and weights.
        size, ok, err = _dir_size(edir, deadline)
        venv_total += size
        venv_complete = venv_complete and ok
        venv_err = venv_err or err
        venv_items.append({"name": os.path.basename(edir), "bytes": size})
    if app_venv:
        size, ok, err = _dir_size(app_venv, deadline)
        venv_total += size
        venv_complete = venv_complete and ok
        venv_err = venv_err or err
        venv_items.append({"name": "app", "bytes": size})
    venv_items.sort(key=lambda m: m["bytes"], reverse=True)
    venv_cat = {
        "id": "engine_venvs",
        "path": engines_dir,
        "exists": os.path.isdir(engines_dir),
        "bytes": venv_total,
        "items": venv_items,
    }
    _finish("engine_venvs", venv_cat, venv_complete, venv_err)
    categories.append(venv_cat)

    # ── 4. Temp/working files the app owns (omnivoice* in the OS temp dir) ─
    deadline = time.monotonic() + category_timeout
    tmp_total = 0
    tmp_complete = True
    tmp_err: str | None = None
    for p in sorted(glob.glob(os.path.join(glob.escape(temp_root), "omnivoice*"))):
        size, ok, err = _dir_size(p, deadline)
        tmp_total += size
        tmp_complete = tmp_complete and ok
        tmp_err = tmp_err or err
    tmp_cat = {
        "id": "temp",
        "path": temp_root,
        "exists": os.path.isdir(temp_root),
        "bytes": tmp_total,
        "items": [],
    }
    _finish("temp", tmp_cat, tmp_complete, tmp_err)
    categories.append(tmp_cat)

    # ── Volumes: group category roots by device, disk_usage once each ──────
    roots = {"hf_cache": hf_cache_dir, "data": data_dir, "engine_venvs": engines_dir, "temp": temp_root}
    by_dev: dict[object, dict] = {}
    for cid, root in roots.items():
        anchor = _existing_ancestor(root)
        try:
            dev: object = os.stat(anchor).st_dev
        except OSError:
            dev = anchor
        if dev not in by_dev:
            try:
                usage = shutil.disk_usage(anchor)
            except OSError:
                continue
            by_dev[dev] = {
                "path": _mount_point(anchor),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "used_percent": round(usage.used / usage.total * 100.0, 1) if usage.total else 0.0,
                "roots": [],
            }
        by_dev[dev]["roots"].append(cid)
    volumes = list(by_dev.values())

    # ── Server-side warnings ────────────────────────────────────────────────
    for v in volumes:
        free_gb = v["free_bytes"] / _GB
        base = {
            "path": v["path"],
            "free_gb": round(free_gb, 1),
            "min_free_gb": min_free_gb,
            "roots": v["roots"],
        }
        if free_gb < min_free_gb:
            warnings.append({"kind": "low_disk", "severity": "critical", **base})
        elif free_gb < 2 * min_free_gb:
            warnings.append({"kind": "low_disk", "severity": "low", **base})
        if v["used_percent"] > VOLUME_PRESSURE_PERCENT and ({"hf_cache", "data"} & set(v["roots"])):
            warnings.append({
                "kind": "volume_pressure",
                "severity": "warning",
                "path": v["path"],
                "used_percent": v["used_percent"],
                "roots": v["roots"],
            })

    # Order: critical first, then the rest in computed order (stable sort).
    warnings.sort(key=lambda w: 0 if w["severity"] == "critical" else 1)

    return {
        "generated_at": time.time(),
        "min_free_gb": min_free_gb,
        "volumes": volumes,
        "categories": categories,
        "warnings": warnings,
    }


# ── In-process cache (5-minute TTL, refresh bypasses) ──────────────────────
_cache_lock = threading.Lock()
_cache: dict = {"key": None, "ts": 0.0, "report": None}


def get_report(
    *,
    data_dir: str,
    hf_cache_dir: str,
    engines_dir: str | None = None,
    app_venv: str | None = None,
    temp_root: str | None = None,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    category_timeout: float = CATEGORY_TIMEOUT_SECONDS,
    refresh: bool = False,
    ttl: float = CACHE_TTL_SECONDS,
) -> dict:
    """Cached ``build_report``. ``refresh=True`` forces a rescan."""
    key = (data_dir, hf_cache_dir, engines_dir, app_venv, temp_root, min_free_gb)
    if not refresh:
        with _cache_lock:
            fresh = (
                _cache["report"] is not None
                and _cache["key"] == key
                and (time.monotonic() - _cache["ts"]) < ttl
            )
            if fresh:
                return {**_cache["report"], "cached": True}
    report = build_report(
        data_dir=data_dir,
        hf_cache_dir=hf_cache_dir,
        engines_dir=engines_dir,
        app_venv=app_venv,
        temp_root=temp_root,
        min_free_gb=min_free_gb,
        category_timeout=category_timeout,
    )
    with _cache_lock:
        _cache.update(key=key, ts=time.monotonic(), report=report)
    return {**report, "cached": False}


def clear_cache() -> None:
    """Testing hook — drop the in-process cache."""
    with _cache_lock:
        _cache.update(key=None, ts=0.0, report=None)


def clear_temp(temp_root: str | None = None) -> dict:
    """Delete the app-owned ``omnivoice*`` entries in the OS temp dir.

    Removes exactly the population ``build_report`` counts as the "temp"
    category — direct children of ``temp_root`` whose basename starts with
    ``omnivoice`` — so nothing outside VoiceStudio's own working files can ever
    be swept up. Symlinked entries are unlinked, never followed, so a stray
    ``omnivoice*`` link cannot make this delete its target's contents.

    Returns ``{"removed": [basenames], "freed_bytes": int, "errors":
    [{"path", "error"}]}`` — partial failures (e.g. a file held open by a
    running job on Windows) are reported per entry instead of aborting.
    """
    temp_root = temp_root if temp_root is not None else tempfile.gettempdir()
    removed: list[str] = []
    errors: list[dict] = []
    freed = 0
    deadline = time.monotonic() + CATEGORY_TIMEOUT_SECONDS
    for p in sorted(glob.glob(os.path.join(glob.escape(temp_root), "omnivoice*"))):
        try:
            if os.path.islink(p):
                size = 0
                os.unlink(p)
            elif os.path.isfile(p):
                size = os.path.getsize(p)
                os.unlink(p)
            else:
                size, _complete, _err = _dir_size(p, deadline)
                shutil.rmtree(p)
            removed.append(os.path.basename(p))
            freed += size
        except OSError as e:
            errors.append({"path": p, "error": str(e)})
    return {"removed": removed, "freed_bytes": freed, "errors": errors}
