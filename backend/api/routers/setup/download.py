"""Model download and deletion endpoints.

Extracted from the monolithic ``setup.py``.

- ``GET  /setup/download-stream``  — SSE for HF tqdm progress
- ``POST /models/install``         — start background model download
- ``DELETE /models/{repo_id}``     — remove cached model from disk
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import prefs
from core.failure import is_disk_full_error, is_hf_connectivity_error
from core.path_security import UnsafePath, contained_child, safe_relative_path
from services.hf_auth import token_for_endpoint
from services.hf_revisions import revision_for
from utils import hf_progress
from utils import download_aggregator
# Weight-floor scan (MM2-07 / #352) lives in ``models.py`` — the lowest module in
# the setup import graph — so install-time validation here, the first-run
# install-state detector (#622), and load-time repair share one set of floors and
# can't drift apart. ``_MIN_WEIGHT_BYTES``/``_WEIGHT_FLOORS`` re-exported for tests.
from .models import (  # noqa: F401
    KNOWN_MODELS,
    invalidate_cache,
    snapshot_is_complete,
    disk_full_message,
    disk_space_error,
    _MIN_WEIGHT_BYTES,
    _WEIGHT_FLOORS,
)

logger = logging.getLogger("omnivoice.setup.download")
router = APIRouter()

# Cooldown: prevent rapid re-install after a failure. Maps repo_id → last_fail_time.
_install_cooldowns: dict[str, float] = {}
# Last classified failure per repo. The SSE stream carries the same detail live;
# retaining it here keeps recovery useful after navigation or renderer reconnect.
_install_failures: dict[str, dict] = {}
_COOLDOWN_SECS = 60.0
# Evict cooldown entries older than this so the dict can't grow unbounded across
# a long-lived process (MM2-06). Anything past the cooldown window is dead state.
_COOLDOWN_TTL_SECS = 3600.0


def _sweep_cooldowns(now: float) -> None:
    """Drop cooldown entries older than the TTL (MM2-06). Keeps the dict bounded
    — without this it accumulated one entry per ever-failed repo forever."""
    stale = [k for k, t in _install_cooldowns.items() if (now - t) > _COOLDOWN_TTL_SECS]
    for k in stale:
        _install_cooldowns.pop(k, None)
        _install_failures.pop(k, None)
    stale_failures = [
        repo_id
        for repo_id, failure in _install_failures.items()
        if (now - float(failure.get("failed_at") or 0)) > _COOLDOWN_TTL_SECS
    ]
    for repo_id in stale_failures:
        _install_failures.pop(repo_id, None)


def clear_install_cooldowns() -> None:
    """Reset every install cooldown. Called when the HF endpoint changes
    (PUT /hf-mirror): the cooldown exists to stop hammering a network that
    just failed, but switching endpoints changes that situation — the user's
    very next action is "retry the failed download on the new mirror", and a
    429 there would dead-end the wizard's switch-and-retry flow."""
    _install_cooldowns.clear()
    _install_failures.clear()

# Repo_ids the user asked to cancel (FDL-11). Checked between retry attempts.
# Note: a single in-flight snapshot_download/Xet fetch is not interruptible
# mid-file in hf_hub 1.7.2 — cancel stops further retries, marks the row
# cancelled, and clears the cooldown so a cancel isn't rate-limited.
_cancelled: set[str] = set()

# One worker per repo. Repeated clicks and feature-level recovery can converge
# on the same install; starting a second snapshot_download against the same HF
# cache is wasteful and can corrupt the user-visible progress stream.
_active_installs: set[str] = set()
_active_installs_lock = threading.Lock()
_install_tasks: set[asyncio.Task] = set()
_install_tasks_by_repo: dict[str, asyncio.Task] = {}


def _download_max_workers() -> int:
    """Parallel-FILES worker count for snapshot_download (FDL-02). Default 8 —
    don't crank it: Xet already parallelises *within* each file via concurrent
    byte-range gets, so a high count just multiplies buffer pressure. Override
    via prefs / OMNIVOICE_DOWNLOAD_MAX_WORKERS for power users."""
    raw = prefs.resolve("download_max_workers", env="OMNIVOICE_DOWNLOAD_MAX_WORKERS", default=8)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 8


def _download_endpoint(gated: bool = False) -> "str | None":
    """Optional HF endpoint override, per-call ``endpoint=`` rather than a
    process-wide HF_ENDPOINT mutation. Explicit configuration (FDL-10 mirror
    path: HF_ENDPOINT env / ``hf_endpoint`` pref / Settings) always wins; when
    nothing was chosen, the automatic endpoint selection's cached pick applies
    (services.endpoint_race — probe-based, cached, never probes here). A
    mirror routes through the classic LFS path (no Xet) — documented in
    docs/downloading-models.md. A gated repository needs the token, so it
    ignores an automatically picked mirror (only an explicit one applies)."""
    from services import endpoint_race
    return endpoint_race.download_endpoint(gated=gated)


def apply_xet_env() -> None:
    """Apply opt-in Xet tuning knobs to the environment before a download
    (FDL-04). Both default OFF; env wins over the prefs store. high-performance
    can *hurt* low-RAM machines (needs lots of RAM/bandwidth); HDD-sequential
    avoids parallel-write thrash on spinning disks. Idempotent."""
    import os as _os
    high_perf = prefs.resolve("xet_high_performance", env="HF_XET_HIGH_PERFORMANCE", default=False)
    if _truthy(high_perf):
        _os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    hdd_seq = prefs.resolve("xet_hdd_sequential_write", env="HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY", default=False)
    if _truthy(hdd_seq):
        _os.environ["HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY"] = "1"


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


class _InstallCancelled(Exception):
    """Raised inside the install worker when the user cancels (FDL-11)."""


def compute_plan(plan_files) -> dict:
    """Summarise a snapshot_download(dry_run=True) result into the install_plan
    payload (FDL-05): total bytes, bytes already cached (skipped), bytes that
    will actually download, and file counts. ``will_download`` defaults to
    ``not is_cached`` for forward-compat with older DryRunFileInfo shapes."""
    total = sum(int(getattr(f, "file_size", 0) or 0) for f in plan_files)
    cached = sum(
        int(getattr(f, "file_size", 0) or 0)
        for f in plan_files if getattr(f, "is_cached", False)
    )
    will = [
        f for f in plan_files
        if getattr(f, "will_download", not getattr(f, "is_cached", False))
    ]
    to_dl = sum(int(getattr(f, "file_size", 0) or 0) for f in will)
    n_files = len(plan_files)
    n_cached = sum(1 for f in plan_files if getattr(f, "is_cached", False))
    return {
        "total_bytes": total,
        "cached_bytes": cached,
        "to_download_bytes": to_dl,
        "n_files": n_files,
        "n_cached": n_cached,
    }


def _segmented_enabled() -> bool:
    """IDM-style multi-connection accelerator (FDL-09), default **ON**. The app
    forces the legacy-LFS path (HF_HUB_DISABLE_XET=1) for clear progress, but that
    path is single-stream and slow — this restores parallel byte-range speed AND
    real live progress, and falls back to snapshot_download on any error so it
    can never compromise a correct install. Default-on so first-run downloads are
    fast out of the box (pairs with an HF token for higher rate limits); set
    OMNIVOICE_SEGMENTED_DOWNLOAD=0 to force the single-stream path."""
    return _truthy(prefs.resolve(
        "segmented_downloader", env="OMNIVOICE_SEGMENTED_DOWNLOAD", default=True,
    ))


def _xet_active() -> bool:
    """True only when hf_xet is installed AND not disabled. The app sets
    HF_HUB_DISABLE_XET=1 by default, so this is normally False — which is when
    the segmented accelerator pays off."""
    import importlib.util
    if importlib.util.find_spec("hf_xet") is None:
        return False
    return os.environ.get("HF_HUB_DISABLE_XET", "").strip().lower() not in {"1", "true", "yes", "on"}


def _repo_cancelled(repo_id: str) -> bool:
    return repo_id in _cancelled


def _create_cache_pointer(blob_path: str, pointer: str) -> None:
    """Keep the canonical blob while exposing it from the snapshot tree.

    huggingface_hub's ``new_blob=True`` fallback moves the blob into the
    snapshot when Windows symlinks are unavailable. The next model load then
    sees a missing blob and downloads the same multi-gigabyte weight again.
    NTFS hardlinks preserve both cache paths without doubling disk usage; other
    filesystems fall back to Hugging Face's copy/symlink path.
    """
    from huggingface_hub.file_download import _create_symlink

    if os.name == "nt":
        try:
            os.link(blob_path, pointer)
            return
        except FileExistsError:
            return
        except OSError:
            pass
    _create_symlink(blob_path, pointer, new_blob=False)


# Hub etags name blob files: a git blob SHA-1 or an LFS SHA-256, nothing else.
_ETAG_RE = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _segmented_snapshot(repo_id: str, *, endpoint: "str | None", revision: str) -> str:
    """Fetch every file of a repo via the segmented downloader into the HF
    cache, mirroring hf_hub_download's blob+snapshot+refs layout so the result
    is indistinguishable from snapshot_download (FDL-09) — keeping /models
    install-state, is_cached, and delete working. Feeds real bytes to the
    aggregator. Raises on any error; the caller falls back to snapshot_download.
    """
    import asyncio as _asyncio
    from huggingface_hub import HfApi, constants as _C
    from huggingface_hub.file_download import (
        hf_hub_url, get_hf_file_metadata, repo_folder_name,
    )
    from services.segmented_download import segmented_download
    from services.token_resolver import resolve as _resolve_token

    # `resolve()` returns a ResolvedToken record, not the bearer string, and
    # every consumer below is typed `token: str | None`. Handing over the
    # record fails silently rather than loudly (#2163): huggingface_hub's
    # build_hf_headers ignores a non-str token and falls back to its own
    # ambient discovery, so a token held only in VoiceStudio's settings sends
    # NO Authorization header at all and every gated file 401s; our own
    # segmented_download interpolates it into `f"Bearer {token}"` and sends a
    # malformed header carrying the raw secret. Unwrap once, here.
    _resolved = _resolve_token()
    # Mirrors never receive the token; False also stops huggingface_hub from
    # sending one it finds on its own.
    hub_token = token_for_endpoint(endpoint, _resolved.token if _resolved else None)
    token = hub_token or None
    api = HfApi(endpoint=endpoint, token=hub_token)
    info = api.repo_info(repo_id, repo_type="model", revision=revision)
    commit = info.sha
    files = [s.rfilename for s in (info.siblings or [])]
    if commit != revision or not files or not _COMMIT_RE.fullmatch(str(commit)):
        raise RuntimeError("repo_info returned no commit/siblings")
    # File names and etags come from the endpoint. Validate every one before
    # touching the disk; any refusal raises so the caller falls back to
    # snapshot_download, which applies huggingface_hub's own checks.
    for rel in files:
        try:
            safe_relative_path(rel)
        except UnsafePath as exc:
            raise RuntimeError("repo_info listed a file outside the repository") from exc

    repo_dir = os.path.join(_C.HF_HUB_CACHE, repo_folder_name(repo_id=repo_id, repo_type="model"))
    blobs_dir = os.path.join(repo_dir, "blobs")
    snap_dir = os.path.join(repo_dir, "snapshots", commit)
    refs_dir = os.path.join(repo_dir, "refs")
    for d in (blobs_dir, snap_dir, refs_dir):
        os.makedirs(d, exist_ok=True)

    for rel in files:
        if _repo_cancelled(repo_id):
            raise _InstallCancelled()
        url = hf_hub_url(repo_id, rel, endpoint=endpoint, revision=commit)
        meta = get_hf_file_metadata(url, token=hub_token)
        etag = (meta.etag or "").strip('"')
        if not _ETAG_RE.fullmatch(etag):
            raise RuntimeError("file metadata returned an invalid etag")
        try:
            blob_path = str(contained_child(blobs_dir, etag))
            pointer = str(contained_child(snap_dir, rel))
        except UnsafePath as exc:
            raise RuntimeError("download target is outside the model cache") from exc
        os.makedirs(os.path.dirname(pointer), exist_ok=True)
        if not os.path.exists(blob_path):
            _asyncio.run(segmented_download(
                meta.location or url, blob_path,
                token=token, expected_size=meta.size, expected_etag=etag,
                on_bytes=lambda d, k=rel: download_aggregator.add_bytes(repo_id, k, d),
                cancel_check=lambda: _repo_cancelled(repo_id),
            ))
        if not os.path.lexists(pointer):
            _create_cache_pointer(blob_path, pointer)

    # refs/main → commit so scan_cache_dir maps the revision correctly.
    ref_path = os.path.join(refs_dir, "main")
    ref_tmp = ref_path + ".tmp"
    try:
        with open(ref_tmp, "w") as f:
            f.write(commit)
        os.replace(ref_tmp, ref_path)
    except OSError as exc:
        logger.warning("Downloaded model revision could not be finalized")
        try:
            os.remove(ref_tmp)
        except FileNotFoundError:
            pass  # Idempotent cleanup: the failed write may not create it.
        except OSError:
            logger.warning("Downloaded model revision temporary-file cleanup did not complete")
        raise RuntimeError(
            "Downloaded model revision could not be finalized. Retry the install."
        ) from exc
    return snap_dir


# ── SSE Download Stream ───────────────────────────────────────────────────

def _safe_put(queue: asyncio.Queue, event) -> None:
    """Non-blocking enqueue — drop oldest on overflow rather than block."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(event)
        except Exception:
            pass


# Minimum size for "this snapshot actually contains model weights". An
# interrupted snapshot_download can leave config/tokenizer files but no
# weights; the install then looks complete and synthesis later fails with
# "does not appear to have a file named pytorch_model.bin or
# model.safetensors" (#352). 5 MB clears every weight format we ship
# (safetensors/bin shards, onnx, pt, gguf) without false-positiving on
# config-only aux repos.
def _validate_snapshot_has_weights(repo_id: str, snapshot_path: str) -> None:
    """Raise OSError when a finished snapshot has no plausible weight file —
    surfaces the truncated-download class (#352) at install time, where the
    retry loop and the UI's re-download path can deal with it, instead of at
    first synthesis with an opaque transformers error.

    Delegates to ``models.snapshot_is_complete`` so configuration-only pipeline
    repositories use their declared required files instead of a weight floor."""
    model = next((m for m in KNOWN_MODELS if m["repo_id"] == repo_id), {"repo_id": repo_id})
    if snapshot_is_complete(model, snapshot_path):
        return
    if model.get("config_only"):
        required = ", ".join(model.get("config_required_files") or ())
        raise OSError(f"{repo_id}: download is incomplete; required configuration files: {required}")
    if model.get("required_files"):
        required = ", ".join(model["required_files"])
        raise OSError(f"{repo_id}: required model files are missing or incomplete: {required}")
    biggest = 0
    try:
        for root, _dirs, files in os.walk(snapshot_path, followlinks=True):
            for f in files:
                try:
                    biggest = max(biggest, os.path.getsize(os.path.join(root, f)))
                except OSError:
                    continue
    except OSError:
        pass
    raise OSError(
        f"{repo_id}: download finished but no model weights were found in the "
        "snapshot (largest file "
        f"{biggest} bytes). The download was likely interrupted — delete the "
        "model in Model Catalogue and install it again."
    )


@router.get("/setup/download-stream")
async def setup_download_stream(target: str | None = None):
    """SSE: forward every HuggingFace download tqdm update as a JSON event."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)
    loop = asyncio.get_running_loop()

    def listener(event):
        if target and event.get("target", "local") != target:
            return
        try:
            loop.call_soon_threadsafe(_safe_put, queue, event)
        except RuntimeError:
            pass

    listener_id = hf_progress.register_listener(listener)

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            hf_progress.unregister_listener(listener_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ── Install ────────────────────────────────────────────────────────────────

class InstallModelRequest(BaseModel):
    repo_id: str
    target: str | None = None


@router.get("/models/install/status")
def model_install_status():
    """Read local and remote jobs after navigation without starting downloads."""
    from services import gpu_gateway  # noqa: PLC0415

    now = time.time()
    _sweep_cooldowns(now)
    with _active_installs_lock:
        active = tuple(_active_installs)
    jobs = []
    for repo_id in active:
        aggregate = download_aggregator._get(repo_id)
        jobs.append(
            {
                "repo_id": repo_id,
                "target": "local",
                "state": "downloading",
                **(aggregate.snapshot() if aggregate else {}),
            }
        )
    detailed = set()
    for repo_id, failure in tuple(_install_failures.items()):
        failed_at = float(failure.get("failed_at") or 0)
        if repo_id in active or now - failed_at >= _COOLDOWN_SECS:
            continue
        detailed.add(repo_id)
        cooldown_at = _install_cooldowns.get(repo_id)
        retry_after = (
            max(0, int(_COOLDOWN_SECS - (now - cooldown_at) + 0.999))
            if cooldown_at is not None
            else 0
        )
        jobs.append(
            {
                "repo_id": repo_id,
                "target": "local",
                "state": "failed",
                "retry_after_seconds": retry_after,
                **failure,
            }
        )
    # Preserve status for callers/tests that seed the legacy cooldown map alone.
    jobs.extend(
        {
            "repo_id": repo_id,
            "target": "local",
            "state": "failed",
            "retry_after_seconds": max(
                0, int(_COOLDOWN_SECS - (now - failed_at) + 0.999)
            ),
        }
        for repo_id, failed_at in tuple(_install_cooldowns.items())
        if repo_id not in active
        and repo_id not in detailed
        and now - failed_at < _COOLDOWN_SECS
    )
    jobs.extend(gpu_gateway.remote_download_jobs())
    return {"jobs": jobs}


def _is_retryable_download_error(exc: BaseException) -> bool:
    """Whether a failed download attempt is worth retrying.

    Decides by CLASSIFICATION, not by exception type. The type-based tuple this
    replaced — ``(HfHubHTTPError, LocalEntryNotFoundError, OSError)`` — silently
    excluded ``httpx.RemoteProtocolError``, which inherits ``Exception``: a
    4.6 GB model truncated at 4.0 GB escaped all five attempts and aborted the
    install (#1224). Any future transport error with a novel base class would
    have reopened the same hole.

    A user cancel is never retryable, and neither is anything
    ``is_hf_connectivity_error`` does not recognise.
    """
    # Imported here, not at module scope, for the same reason the worker does:
    # huggingface_hub is heavy and this module is on the setup import path.
    from huggingface_hub.utils import HfHubHTTPError, LocalEntryNotFoundError

    if isinstance(exc, _InstallCancelled):
        return False
    if is_disk_full_error(exc):
        # A full volume does not heal between backoff sleeps; five retries only
        # delay the actionable message (and re-preallocate the whole file).
        return False
    if isinstance(exc, HfHubHTTPError):
        # An auth / not-found / gone answer from the Hub is a settled verdict:
        # the token is wrong, the repo is gated, or it isn't there. Retrying
        # five times with backoff just delays the same message and postpones
        # the install cooldown. (Pre-existing behaviour — the type-based tuple
        # this replaced retried every HfHubHTTPError; surfaced in #1224 review.)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403, 404, 410):
            return False
        return True
    if isinstance(exc, (LocalEntryNotFoundError, OSError)):
        return True
    return is_hf_connectivity_error(str(exc))


def _segmented_retry_plan(
    exc: BaseException, attempt: int, max_attempts: int
) -> tuple[bool, bool]:
    """What to do after the segmented accelerator failed on ``attempt``.

    Returns ``(disable_accelerator, reraise)``.

    A dropped connection is not the accelerator's fault, so the error is
    re-raised for the outer retry: the next attempt re-enters
    :func:`_segmented_snapshot`, which resumes from the ``.part`` manifest.
    Falling straight through to ``snapshot_download`` instead would finish the
    install from a separate ``.incomplete`` file and strand that manifest — the
    restart-from-zero this exists to prevent.

    The final attempt is always reserved for the plain path, so the accelerator
    can never be the reason an install fails outright. The two flags are
    decoupled for that handover: the attempt that exhausts the accelerator still
    re-raises, so the plain path starts on the LAST attempt rather than the
    second-to-last. Disabling and falling through in the same attempt would
    abandon the resumable manifest one attempt early and restart through a
    separate file — which is the failure this whole helper exists to avoid.
    """
    if not _is_retryable_download_error(exc):
        return True, False  # the accelerator cannot work here at all
    if attempt >= max_attempts:
        # Nothing left to hand over to: take the plain path now rather than
        # re-raising out of the loop with no fallback ever tried.
        return True, False
    return attempt >= max_attempts - 1, True


def _segmented_retry_note(disable: bool, reraise: bool) -> str:
    """How to describe the outcome of :func:`_segmented_retry_plan` in the log.

    Three distinct states, and reading only ``disable`` conflates two of them:
    the attempt that exhausts the accelerator is disabled AND re-raises, so the
    fallback starts on the NEXT attempt, not this one.
    """
    if not disable:
        return "kept for the next attempt (resumes from its manifest)"
    if reraise:
        return "exhausted — retrying once more, then snapshot_download takes over"
    return "disabled for this install — falling back to snapshot_download now"


@router.post("/models/install")
async def install_model(req: InstallModelRequest):
    """Download one HF repo snapshot; progress goes through the shared
    ``/setup/download-stream`` SSE feed."""
    model_spec = next(
        (model for model in KNOWN_MODELS if model["repo_id"] == req.repo_id),
        None,
    )
    if model_spec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown model: {req.repo_id!r}. Known: "
                + ", ".join(m["repo_id"] for m in KNOWN_MODELS)
            ),
        )
    allow_patterns = list(model_spec.get("allow_patterns") or []) or None
    target = (req.target or "").strip()
    if target != "local":
        from services import gpu_gateway  # noqa: PLC0415
        from worker import routing  # noqa: PLC0415

        decision = routing.decide()
        if target and target != "local" and (
            not decision.remote or decision.worker_id != target
        ):
            raise HTTPException(status_code=409, detail="The selected GPU target changed; try again.")
        if decision.remote:
            try:
                return await gpu_gateway.download(req.repo_id, decision=decision)
            except gpu_gateway.GatewayError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Cooldown guard — don't retry if the same model just failed.
    import time as _time_check
    _sweep_cooldowns(_time_check.time())  # bound the dict (MM2-06)
    last_fail = _install_cooldowns.get(req.repo_id)
    if last_fail and (_time_check.time() - last_fail) < _COOLDOWN_SECS:
        remaining = int(_COOLDOWN_SECS - (_time_check.time() - last_fail))
        raise HTTPException(
            status_code=429,
            detail=(
                f"Model {req.repo_id!r} install failed recently. "
                f"Retry in {remaining}s or check your network."
            ),
        )
    loop = asyncio.get_running_loop()

    def _do():
        # Failure handling must work even when imports, token resolution or
        # revision lookup fail before the heartbeat thread is started.
        _resolving = threading.Event()
        _gated = bool(model_spec.get("gated"))
        _endpoint = None
        token = hf_progress.current_repo_id.set(req.repo_id)
        target_token = hf_progress.current_target.set("local")
        hf_progress.emit({
            "repo_id": req.repo_id,
            "filename": req.repo_id,
            "downloaded": 0, "total": 0, "pct": 0.0,
            "phase": "install_start",
        })
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import (
                HfHubHTTPError,
                LocalEntryNotFoundError,
            )
            logger.info("model install starting: %s", req.repo_id)
            # Apply opt-in Xet tuning knobs (high-perf / HDD) before downloading.
            apply_xet_env()
            # Drive snapshot_download explicitly (FDL-02): pass our progress-
            # emitting tqdm subclass so progress is deterministic + Xet-aware
            # (Xet feeds bytes into whatever tqdm_class is supplied), bound the
            # parallel-files worker count, and honour an optional mirror endpoint.
            dl_kwargs: dict = {
                "repo_id": req.repo_id,
                "revision": revision_for(req.repo_id),
                "max_workers": _download_max_workers(),
            }
            from services.token_resolver import resolve as resolve_token
            resolved_token = resolve_token()
            _bearer = resolved_token.token if resolved_token else None
            if allow_patterns:
                dl_kwargs["allow_patterns"] = allow_patterns
            _tqdm_cls = hf_progress.tracked_tqdm_class()
            if _tqdm_cls is not None:
                dl_kwargs["tqdm_class"] = _tqdm_cls
            _endpoint = _download_endpoint(gated=_gated)
            if _endpoint:
                dl_kwargs["endpoint"] = _endpoint
            # The token goes to Hugging Face only, never to a mirror.
            _hub_token = token_for_endpoint(_endpoint, _bearer)
            if _hub_token is not None:
                dl_kwargs["token"] = _hub_token
            if sys.platform == "win32":
                dl_kwargs["local_dir_use_symlinks"] = False

            # Emit a 'resolving' heartbeat every 2s while snapshot_download
            # resolves repo metadata (before any tqdm bars appear).
            import time as _t

            def _heartbeat():
                _step = 0
                while not _resolving.is_set():
                    _resolving.wait(2.0)
                    if _resolving.is_set():
                        break
                    _step += 1
                    hf_progress.emit({
                        "repo_id": req.repo_id,
                        "filename": req.repo_id,
                        "downloaded": 0, "total": 0, "pct": 0.0,
                        "phase": "resolving",
                        "step": _step,
                    })

            hb = threading.Thread(target=_heartbeat, daemon=True)
            hb.start()

            # Pre-flight (FDL-05): a dry-run resolve gives the UI an accurate
            # denominator — total bytes, bytes already cached (skipped), and the
            # bytes that will actually download — BEFORE any byte flows. Seeds
            # the overall aggregator so its bar/ETA are correct from the first
            # event. Degrades gracefully (totals=None) on older/gated repos.
            _preflight_kwargs = {
                "repo_id": req.repo_id,
                "revision": dl_kwargs["revision"],
                "dry_run": True,
            }
            if allow_patterns:
                _preflight_kwargs["allow_patterns"] = allow_patterns
            if _endpoint:
                _preflight_kwargs["endpoint"] = _endpoint
            if _hub_token is not None:
                _preflight_kwargs["token"] = _hub_token
            try:
                _plan = list(snapshot_download(**_preflight_kwargs))  # nosec B615 -- immutable revision_for pin
                for dependency in model_spec.get("dependencies") or ():
                    if req.repo_id in _cancelled:
                        raise _InstallCancelled()
                    dependency_plan_kwargs = {
                        **_preflight_kwargs,
                        "repo_id": dependency["repo_id"],
                        "revision": revision_for(dependency["repo_id"]),
                    }
                    dependency_plan_kwargs.pop("allow_patterns", None)
                    if dependency.get("allow_patterns"):
                        dependency_plan_kwargs["allow_patterns"] = dependency["allow_patterns"]
                    _plan.extend(snapshot_download(**dependency_plan_kwargs))  # nosec B615 -- immutable revision_for pin
                _summary = compute_plan(_plan)
                # Disk-space guard (before a single byte flows): the preflight
                # gives an exact "to download" size, so reject an install that
                # would overrun the cache volume — with the numbers named —
                # instead of failing mid-download with a cryptic OSError. No-op
                # when it fits or the size is unknown. Same on every platform.
                _disk_err = disk_space_error(_summary["to_download_bytes"])
                if _disk_err:
                    logger.info("model install %s: rejected — %s", req.repo_id, _disk_err)
                    _resolving.set()  # stop the heartbeat thread before we bail
                    hf_progress.emit({
                        "repo_id": req.repo_id,
                        "filename": req.repo_id,
                        "downloaded": 0, "total": 0, "pct": 0.0,
                        "phase": "install_error",
                        "error": _disk_err,
                    })
                    _install_failures[req.repo_id] = {
                        "failed_at": time.time(),
                        "error": _disk_err,
                        "docs_topic": "DISK_SPACE_LOW",
                    }
                    # A disk-full is not a transient network failure — don't set
                    # a cooldown (freeing space, not waiting, is the fix). The
                    # outer finally still cleans up the aggregator + context.
                    return
                download_aggregator.start(
                    req.repo_id,
                    target=target or "local",
                    total_bytes=_summary["to_download_bytes"],
                    files_total=max(0, _summary["n_files"] - _summary["n_cached"]),
                )
                hf_progress.emit({
                    "repo_id": req.repo_id,
                    "filename": req.repo_id,
                    "phase": "install_plan",
                    **_summary,
                })
            except _InstallCancelled:
                raise
            except Exception as _pf_err:
                # No preflight (older/gated repo, mirror without dry-run, etc.):
                # fall back to today's fill-in-as-files-appear behaviour.
                logger.info("model install %s: preflight unavailable (%s)", req.repo_id, _pf_err)
                download_aggregator.start(req.repo_id, target=target or "local")
                hf_progress.emit({
                    "repo_id": req.repo_id,
                    "filename": req.repo_id,
                    "phase": "install_plan",
                    "total_bytes": None,
                    "cached_bytes": None,
                    "to_download_bytes": None,
                    "n_files": None,
                    "n_cached": None,
                })

            _max_attempts = 5
            _attempt = 0
            # The accelerator is retried across attempts so its manifest-based
            # resume actually gets used; it is disabled for the rest of the
            # install only when it fails for a reason that is NOT transient
            # network trouble (i.e. the accelerator itself is unusable here).
            _segmented_off = False
            while True:
                if req.repo_id in _cancelled:
                    raise _InstallCancelled()
                _attempt += 1
                try:
                    # Segmented accelerator (FDL-09, default ON): parallel
                    # byte-range fetch with real live progress, for the
                    # legacy-LFS path. A failure that is not transient network
                    # trouble falls through to snapshot_download, and so does the
                    # install's last attempt — the accelerator can never
                    # compromise a correct install (see _segmented_retry_plan).
                    _snapshot_path = None
                    if (
                        not _segmented_off
                        and not allow_patterns
                        and _segmented_enabled()
                        and not _xet_active()
                    ):
                        try:
                            _snapshot_path = _segmented_snapshot(
                                req.repo_id,
                                endpoint=_endpoint,
                                revision=dl_kwargs["revision"],
                            )
                        except _InstallCancelled:
                            raise
                        except Exception as _seg_err:
                            if is_disk_full_error(_seg_err):
                                raise  # the plain path would hit the same wall
                            _segmented_off, _seg_reraise = _segmented_retry_plan(
                                _seg_err, _attempt, _max_attempts
                            )
                            logger.info(
                                "segmented download for %s failed (%s); accelerator %s",
                                req.repo_id, _seg_err,
                                _segmented_retry_note(_segmented_off, _seg_reraise),
                            )
                            if _seg_reraise:
                                raise
                            _snapshot_path = None
                    if _snapshot_path is None:
                        _snapshot_path = snapshot_download(**dl_kwargs)  # nosec B615 -- immutable revision_for pin
                    _validate_snapshot_has_weights(req.repo_id, _snapshot_path)
                    from huggingface_hub.constants import HF_HUB_CACHE
                    from services.hf_revisions import remember_revision
                    remember_revision(req.repo_id, dl_kwargs["revision"], HF_HUB_CACHE)
                    # A pipeline config is not a runnable installation by itself.
                    # Download its reviewed dependencies only inside this explicit
                    # install action, retaining the parent cancellation/retry flow.
                    for dependency in model_spec.get("dependencies") or ():
                        if req.repo_id in _cancelled:
                            raise _InstallCancelled()
                        dependency_id = dependency["repo_id"]
                        dependency_kwargs = {
                            **dl_kwargs,
                            "repo_id": dependency_id,
                            "revision": revision_for(dependency_id),
                        }
                        dependency_kwargs.pop("allow_patterns", None)
                        if dependency.get("allow_patterns"):
                            dependency_kwargs["allow_patterns"] = dependency["allow_patterns"]
                        dependency_path = snapshot_download(**dependency_kwargs)  # nosec B615 -- immutable revision_for pin
                        if not snapshot_is_complete(dependency, dependency_path):
                            raise OSError(f"{dependency_id}: required model files are missing or incomplete")
                        remember_revision(dependency_id, dependency_kwargs["revision"], HF_HUB_CACHE)
                    if req.repo_id in _cancelled:
                        raise _InstallCancelled()
                    break
                except Exception as net_err:
                    # #1224: a truncated body ("peer closed connection without
                    # sending complete message body") arrives as
                    # httpx.RemoteProtocolError, which inherits from Exception
                    # — NOT OSError — so it escaped the old
                    # (HfHubHTTPError, LocalEntryNotFoundError, OSError) tuple
                    # and aborted a 4.6 GB install at 4.0 GB with no retry.
                    # Widen to Exception and decide by CLASSIFICATION:
                    # is_hf_connectivity_error is already the single source of
                    # truth for "transient download failure" and now knows the
                    # truncation signatures. Anything unrecognised (a cancel, a
                    # validation failure, a bug) propagates untouched, exactly
                    # as before.
                    if _attempt >= _max_attempts or not _is_retryable_download_error(
                        net_err
                    ):
                        raise
                    _backoff = min(30, 2 ** _attempt)
                    logger.info(
                        "model install %s: attempt %d/%d failed (%s); retry in %ds",
                        req.repo_id, _attempt, _max_attempts, net_err, _backoff,
                    )
                    # Endpoint failover (auto mode only, once per repo per
                    # process): a network-classified failure re-races the
                    # endpoints and, when the winner changed, the next attempt
                    # retries on it — so a mid-download endpoint outage heals
                    # instead of burning every retry on a dead host. Explicit
                    # user endpoints are never switched.
                    from services import endpoint_race
                    if endpoint_race.reselect_after_failure(req.repo_id, str(net_err)):
                        _endpoint = _download_endpoint(gated=_gated)
                        if _endpoint:
                            dl_kwargs["endpoint"] = _endpoint
                        else:
                            dl_kwargs.pop("endpoint", None)
                        _hub_token = token_for_endpoint(_endpoint, _bearer)
                        if _hub_token is None:
                            dl_kwargs.pop("token", None)
                        else:
                            dl_kwargs["token"] = _hub_token
                        logger.info(
                            "model install %s: endpoint failover — retrying on %s",
                            req.repo_id, _endpoint or "https://huggingface.co",
                        )
                    hf_progress.emit({
                        "repo_id": req.repo_id,
                        "filename": req.repo_id,
                        "downloaded": 0, "total": 0, "pct": 0.0,
                        "phase": "install_retry",
                        "attempt": _attempt,
                        "error": str(net_err),
                    })
                    _t.sleep(_backoff)
            # Stop heartbeat once download completes
            _resolving.set()
            # Flush the overall bar to 100% with the true byte total (FDL-06):
            # under Xet the per-file byte bars don't surface completion, so the
            # aggregator can sit below 100% even though every file landed.
            download_aggregator.complete(req.repo_id, target=target or "local")
            logger.info("model install done: %s", req.repo_id)
            hf_progress.emit({
                "repo_id": req.repo_id,
                "filename": req.repo_id,
                "downloaded": 0, "total": 0, "pct": 1.0,
                "phase": "install_done",
            })
            _install_cooldowns.pop(req.repo_id, None)  # success clears any cooldown (MM2-06)
            _install_failures.pop(req.repo_id, None)
            invalidate_cache()
            # A saved performance pack owns the desired engine/model policy.
            # Reconcile after every successful local install so the new model
            # becomes usable without a restart or a second manual selection.
            try:
                from services.performance_profiles import reconcile_active_profile

                activated = reconcile_active_profile()
                if activated:
                    logger.info("model install activated performance profile: %s", activated)
            except Exception:
                # The model is fully installed even if optional preference
                # reconciliation fails; readiness refresh and manual selection
                # remain available instead of misreporting the download.
                logger.exception("performance profile reconciliation failed after model install")
        except _InstallCancelled:
            _resolving.set()
            logger.info("model install cancelled: %s", req.repo_id)
            # A cancel is user intent, not a failure — don't set a cooldown.
            _install_cooldowns.pop(req.repo_id, None)
            _install_failures.pop(req.repo_id, None)
            hf_progress.emit({
                "repo_id": req.repo_id,
                "filename": req.repo_id,
                "downloaded": 0, "total": 0, "pct": 0.0,
                "phase": "install_cancelled",
            })
        except Exception as e:
            _resolving.set()
            logger.info("model install failed for %s: %s", req.repo_id, e)
            import time as _time_fail
            _failed_at = _time_fail.time()
            _install_cooldowns[req.repo_id] = _failed_at
            # #874: when the install failed because the configured HF mirror is
            # unreachable, name the mirror + the setting instead of leaking the
            # raw connectivity error. #959: likewise for the SOCKS-proxy class
            # (missing socksio fails the download's session construction).
            # No-op for every other failure. docs_topic carries the failure
            # class so the wizard can react structurally (HF_MIRROR_UNREACHABLE
            # raises the inline mirror picker) without string-matching.
            from core.failure import append_hint, classify
            _error = append_hint(str(e))
            _docs_topic = classify(str(e))
            if is_disk_full_error(e):
                _error = disk_full_message()
                _docs_topic = "DISK_SPACE_LOW"
            # Gated catalogue entries own their recovery topic. Hugging Face
            # uses several exception wordings for the same access verdict, so
            # the UI must not depend on parsing an English 401/403 message.
            _catalogue_topic = str(model_spec.get("failure_topic") or "")
            if _catalogue_topic and _docs_topic in {
                "",
                "HF_AUTH_FAILED",
                "PYANNOTE_LICENSE_REQUIRED",
            }:
                _docs_topic = _catalogue_topic
            # A mirror the user chose never receives the token, so no token or
            # accepted terms can make a gated install succeed there. Say so
            # instead of sending the user to the token settings.
            from services.endpoint_race import explicit_mirror
            if (
                _gated
                and _endpoint
                and _endpoint == explicit_mirror()
                and _docs_topic in {
                    "",
                    "HF_AUTH_FAILED",
                    "PYANNOTE_LICENSE_REQUIRED",
                    "POCKETTTS_GATED_WEIGHTS",
                }
            ):
                from core.failure import public_hint_for_topic
                _docs_topic = "HF_MIRROR_GATED"
                _error = f"{e} — {public_hint_for_topic(_docs_topic)}"
            # Waiting cannot fix an access/token verdict. Let the user accept
            # the terms or update the token and retry immediately.
            if _docs_topic in {
                "HF_AUTH_FAILED",
                "HF_MIRROR_GATED",
                "PYANNOTE_LICENSE_REQUIRED",
                "POCKETTTS_GATED_WEIGHTS",
                "DISK_SPACE_LOW",  # freeing space, not waiting, is the fix
            }:
                _install_cooldowns.pop(req.repo_id, None)
            _install_failures[req.repo_id] = {
                "failed_at": _failed_at,
                "error": _error,
                "docs_topic": _docs_topic,
            }
            hf_progress.emit({
                "repo_id": req.repo_id,
                "filename": req.repo_id,
                "downloaded": 0, "total": 0, "pct": 0.0,
                "phase": "install_error",
                "error": _error,
                "docs_topic": _docs_topic,
            })
        finally:
            _resolving.set()
            _cancelled.discard(req.repo_id)
            download_aggregator.finish(req.repo_id, target=target or "local")
            hf_progress.current_repo_id.reset(token)
            hf_progress.current_target.reset(target_token)
            with _active_installs_lock:
                _active_installs.discard(req.repo_id)

    with _active_installs_lock:
        if req.repo_id in _active_installs:
            return {"status": "already_running", "repo_id": req.repo_id}
        _active_installs.add(req.repo_id)
        # Admission and task publication are one atomic generation boundary:
        # cancellation can never observe an admitted install without its task.
        _cancelled.discard(req.repo_id)
        _install_failures.pop(req.repo_id, None)
        try:
            task = loop.create_task(asyncio.to_thread(_do))
            _install_tasks.add(task)
            _install_tasks_by_repo[req.repo_id] = task
        except Exception:
            _active_installs.discard(req.repo_id)
            raise

    def install_finished(completed: asyncio.Task) -> None:
        with _active_installs_lock:
            _install_tasks.discard(completed)
            if _install_tasks_by_repo.get(req.repo_id) is completed:
                _install_tasks_by_repo.pop(req.repo_id, None)

    task.add_done_callback(install_finished)
    return {"status": "install_started", "repo_id": req.repo_id}


async def cancel_install_and_wait(repo_id: str) -> None:
    """Request cancellation and retain authority until its thread exits."""
    from worker.async_utils import drain_task  # noqa: PLC0415

    with _active_installs_lock:
        _cancelled.add(repo_id)
        _install_cooldowns.pop(repo_id, None)
        task = _install_tasks_by_repo.get(repo_id)
    if task is None:
        return
    try:
        # asyncio.to_thread cannot stop snapshot_download mid-file. Cancelling
        # its wrapper would only detach the thread, so wait until the blocking
        # call observes the flag or naturally returns.
        await drain_task(task)
    finally:
        with _active_installs_lock:
            current = _install_tasks_by_repo.get(repo_id)
            if current is None or current is task:
                _cancelled.discard(repo_id)


@router.post("/models/install/cancel")
async def cancel_install(req: InstallModelRequest):
    """Request cancellation of an in-flight install (FDL-11).

    Best-effort: stops further retry attempts and marks the row cancelled. A
    single in-flight snapshot_download/Xet fetch isn't interruptible mid-file
    in hf_hub 1.7.2, so an already-streaming file finishes; the cancel takes
    effect at the next retry boundary. Clears the cooldown so the user can
    immediately restart."""
    target = (req.target or "local").strip() or "local"
    if target != "local":
        from services import gpu_gateway  # noqa: PLC0415

        try:
            return await gpu_gateway.cancel_download(req.repo_id, target=target)
        except gpu_gateway.GatewayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    _cancelled.add(req.repo_id)
    _install_cooldowns.pop(req.repo_id, None)
    _install_failures.pop(req.repo_id, None)
    return {"cancelling": req.repo_id}


# ── Delete ─────────────────────────────────────────────────────────────────

@router.delete("/models/{repo_id:path}")
def delete_model(repo_id: str):
    """Remove every cached revision of a repo from the HF cache."""
    hf_progress.emit({
        "repo_id": repo_id,
        "filename": repo_id,
        "downloaded": 0, "total": 0, "pct": 0.0,
        "phase": "delete_start",
    })
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        commits = [
            rev.commit_hash
            for entry in info.repos if entry.repo_id == repo_id
            for rev in entry.revisions
        ]
        if not commits:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Model {repo_id!r} isn't installed. Nothing to delete — "
                    "run POST /models/install first if you want a fresh download."
                ),
            )
        strategy = info.delete_revisions(*commits)
        strategy.execute()
        hf_progress.emit({
            "repo_id": repo_id,
            "filename": repo_id,
            "downloaded": 0, "total": 0, "pct": 1.0,
            "phase": "delete_done",
            "freed_bytes": strategy.expected_freed_size,
        })
        invalidate_cache()
        return {
            "deleted": True,
            "repo_id": repo_id,
            "freed_bytes": strategy.expected_freed_size,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not delete {repo_id}: {e}. "
                "Close any process using the model (e.g. the app's main dub job) and retry."
            ),
        )
