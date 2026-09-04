import os
import sys
import platform
import time
import uuid
import psutil
import asyncio
import logging
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query, Request
from core.prefs import set_ as prefs_set, delete as prefs_delete
from services import network_share
from services import tailscale as _tailscale
from api.schemas import SysinfoResponse, SystemInfoResponse, ModelStatusResponse
from api.dependencies import is_loopback, require_admin, require_admin_action
from fastapi.responses import FileResponse, StreamingResponse
import torch
import shutil

from core.config import OUTPUTS_DIR, DATA_DIR, CRASH_LOG_PATH, LOG_PATH, IDLE_TIMEOUT_SECONDS
from core.version import APP_VERSION
from core.logging_utils import log_safe
from core.public_errors import public_failure
from services.model_manager import get_model_status, get_best_device, resolve_omnivoice_checkpoint
from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg

# Router-level admin gate. Every route mounted on `router` (GET + POST,
# present and future) is gated by `require_admin`: desktop requests must be
# loopback; server-mode mutations require the long API key. This closes the trust
# boundary that PR #81 only patched on `/system/set-env` and that the
# 260518-ivy deferred-items file enumerated for follow-up: /model/unload/*,
# /system/logs/clear, /system/logs/tauri/clear, /system/flush-memory,
# /clean-audio (POSTs) plus the read-side info-disclosure routes
# /system/info, /system/logs, /system/logs/tauri, /system/logs/stream.
# Native Tauri/dev callers remain loopback and need no credential.
router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger("omnivoice.api")

# Cache device checks at module load — they don't change at runtime
_is_mac = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
_is_cuda = torch.cuda.is_available()
# Prime psutil's internal CPU counter so the first non-blocking call returns useful data
psutil.cpu_percent(interval=None)


def _detect_cpu_model() -> str:
    """Human-readable CPU model. platform.processor() is empty on most
    Linux distros, so read /proc/cpuinfo there; sysctl on macOS."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            import subprocess
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, timeout=5
            ).strip()
        return platform.processor() or ""
    except Exception:
        return platform.processor() or ""


def _detect_gpu() -> tuple[str, float]:
    """(gpu_name, vram_total_gb) — static for the process lifetime.

    MPS has unified memory, so there's no separate VRAM figure to report;
    the name alone tells a bug-report reader what hardware this is.
    """
    try:
        if _is_cuda:
            props = torch.cuda.get_device_properties(0)
            return torch.cuda.get_device_name(0), round(props.total_memory / (1024 ** 3), 1)
        if _is_mac:
            return "Apple Silicon (MPS)", 0.0
    except Exception:
        pass
    return "", 0.0


# Static hardware facts, captured once — /system/info is hit on every
# Settings page load and must stay cheap.
_CPU_MODEL = _detect_cpu_model()
_GPU_NAME, _VRAM_TOTAL_GB = _detect_gpu()
_RAM_TOTAL_GB = round(psutil.virtual_memory().total / (1024 ** 3), 1)
_OS_VERSION = platform.platform()


def _disk_free_gb() -> float:
    try:
        return round(shutil.disk_usage(DATA_DIR).free / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _ui_port() -> int:
    """The Vite UI dev-server port, single-sourced from OMNIVOICE_UI_PORT.

    Mirrors the resolver in main.py (kept local to avoid importing the app
    module). Falls back to 3901 on a missing or malformed value.
    """
    raw = os.environ.get("OMNIVOICE_UI_PORT")
    if raw is None:
        return 3901
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 3901


def _fast_download_status() -> dict:
    """Report the download-acceleration state for the Settings UI (FDL-03).

    Reports the *runtime* truth, not just whether hf_xet is importable. The app
    currently sets ``HF_HUB_DISABLE_XET=1`` by default (main.py) — Xet's chunked
    transfer is fast but its progress bypasses our tqdm patch, so the legacy-LFS
    path is forced to keep accurate byte progress. So:

      * ``xet_installed`` — hf_xet present
      * ``xet_active``    — installed AND not disabled via HF_HUB_DISABLE_XET
      * ``xet_enabled``   — alias of xet_active (what the UI badge keys off)

    Must never throw: /system/info is called on every Settings load.
    """
    installed = False
    version = None
    try:
        import hf_xet  # noqa: F401
        installed = True
        try:
            from importlib.metadata import version as _ver
            version = _ver("hf-xet")
        except Exception:
            version = None
    except Exception:
        installed = False
    disabled = str(os.environ.get("HF_HUB_DISABLE_XET", "")).strip().lower() in {"1", "true", "yes", "on"}
    active = installed and not disabled
    try:
        from core import prefs
        high_perf = prefs.resolve(
            "xet_high_performance", env="HF_XET_HIGH_PERFORMANCE", default=False
        )
        high_perf = high_perf if isinstance(high_perf, bool) else \
            str(high_perf).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        high_perf = False
    return {
        "xet_installed": installed,
        "xet_active": active,
        "xet_enabled": active,  # UI badge: only true when Xet actually runs
        "xet_version": version,
        "high_performance": bool(high_perf),
    }


def _has_hf_token() -> bool:
    # Phase 1 AUTH-01..06 cascade. Delegates to the 3-source resolver
    # (App → Env → HF-CLI) instead of reading env/HF-CLI directly. This
    # closes #35: a user who only ran `huggingface-cli login` (no env
    # var, no app-store) is now reported as having a token, and so is a
    # user who saved one in Settings.
    try:
        from services import token_resolver
        return token_resolver.resolve() is not None
    except Exception:
        # Resolver must never break /system/info — fall back to False.
        return False

@router.get("/model/status", response_model=ModelStatusResponse)
def model_status():
    """Report model loading state for frontend warm-up indicators."""
    return get_model_status()


@router.get("/model/loaded")
def loaded_models():
    """List all currently loaded models for the flush dropdown (MM2-04).
    Thin delegation to the model_lifecycle facade — shape unchanged:
    ``{models, count}``."""
    from services import model_lifecycle
    return model_lifecycle.list_loaded()


@router.post("/model/unload/{model_id}")
async def unload_model(model_id: str):
    """Unload a specific model by id (MM2-04). Delegates to model_lifecycle;
    an unknown id maps to HTTP 400. ``tts`` | ``diarization`` |
    ``sidecar:<id>`` | ``sidecars``."""
    from services import model_lifecycle
    try:
        return await model_lifecycle.unload(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/system/info", response_model=SystemInfoResponse)
def system_info():
    """Settings page system info — model, tokens, data dir, timeout.

    This endpoint MUST never throw — it's called on every Settings page load
    and a 500 here blocks the entire UI from rendering system details.
    """
    try:
        _ffmpeg = find_ffmpeg()
        from services import model_manager as _mm
        from core import prefs as _prefs_mod
        return {
            "app_version": APP_VERSION,
            "generate_timeout_s": _mm.GPU_JOB_TIMEOUT_S,
            "cpu_generate_timeout_s": _mm.CPU_JOB_TIMEOUT_S,
            # #1787 review fix: a saved prefs.json value for either key can be
            # silently shadowed by an external env var (os.environ.setdefault
            # in core.prefs.restore_env is a no-op when one is already
            # present) — the Settings panel must say so rather than promise a
            # restart will apply a value that never will.
            "generate_timeout_shadowed": _prefs_mod.is_env_shadowed(
                "OMNIVOICE_GENERATE_TIMEOUT_S"),
            "cpu_generate_timeout_shadowed": _prefs_mod.is_env_shadowed(
                "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"),
            "code_fingerprint": os.environ.get("OMNIVOICE_BUILD_FINGERPRINT", ""),
            "data_dir": DATA_DIR,
            "outputs_dir": OUTPUTS_DIR,
            "crash_log_path": CRASH_LOG_PATH,
            "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
            "model_checkpoint": resolve_omnivoice_checkpoint(),  # #693: show the effective checkpoint, not a leaked raw value
            "asr_model": os.environ.get("ASR_MODEL", "Systran/faster-whisper-large-v3"),
            "translate_provider": os.environ.get("TRANSLATE_PROVIDER", "google"),
            "has_hf_token": _has_hf_token(),
            "fast_download": _fast_download_status(),
            "device": get_best_device(),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "arch": platform.machine(),
            "os_version": _OS_VERSION,
            "cpu_model": _CPU_MODEL,
            "cpu_count": psutil.cpu_count(logical=True) or 0,
            "ram_total_gb": _RAM_TOTAL_GB,
            "gpu_name": _GPU_NAME,
            "vram_total_gb": _VRAM_TOTAL_GB,
            "disk_free_gb": _disk_free_gb(),
            "ffmpeg_ok": bool(_ffmpeg),
            "ffmpeg_path": _ffmpeg or "",
            "proxy_url": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "",
            "share_enabled": network_share.get_state().enabled,
            "share_port": network_share.get_state().share_port,
            "lan_addresses": network_share.get_state().lan_addresses,
            "pin_required": bool(network_share.get_state().pin),
            "backend_port": network_share.backend_port(),
            "share_port_base": network_share.share_port_base(),
            "ui_port": _ui_port(),
        }
    except Exception as e:
        logger.exception("system_info failed — returning safe defaults")
        return {
            "app_version": APP_VERSION,
            "generate_timeout_s": 300.0,
            "cpu_generate_timeout_s": 600.0,
            "generate_timeout_shadowed": False,
            "cpu_generate_timeout_shadowed": False,
            "code_fingerprint": os.environ.get("OMNIVOICE_BUILD_FINGERPRINT", ""),
            "data_dir": DATA_DIR,
            "outputs_dir": OUTPUTS_DIR,
            "crash_log_path": str(CRASH_LOG_PATH),
            "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
            "model_checkpoint": "unknown",
            "asr_model": "unknown",
            "translate_provider": "unknown",
            "has_hf_token": False,
            "device": "cpu",
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "arch": platform.machine(),
            "os_version": _OS_VERSION,
            "cpu_model": _CPU_MODEL,
            "cpu_count": psutil.cpu_count(logical=True) or 0,
            "ram_total_gb": _RAM_TOTAL_GB,
            "gpu_name": _GPU_NAME,
            "vram_total_gb": _VRAM_TOTAL_GB,
            "disk_free_gb": _disk_free_gb(),
            "proxy_url": "",
            "share_enabled": network_share.get_state().enabled,
            "share_port": network_share.get_state().share_port,
            "lan_addresses": network_share.get_state().lan_addresses,
            "pin_required": bool(network_share.get_state().pin),
            "backend_port": network_share.backend_port(),
            "share_port_base": network_share.share_port_base(),
            "ui_port": _ui_port(),
            "error": "System information is temporarily unavailable; check the backend log for details.",
        }


def _tail_file(path: str, tail: int):
    """Read the last `tail` lines from `path`. Returns (lines, total)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return all_lines[-tail:], len(all_lines)


def _tauri_log_candidates():
    """Likely paths for Tauri-side logs, most useful first.

    Two distinct producers, both per-platform:

    - `tauri-plugin-log` writes `tauri.log` to the app log dir
      (`~/Library/Logs/<bundle_id>` on macOS, `$XDG_DATA_HOME/<bundle_id>/logs`
      on Linux, `%LOCALAPPDATA%\\<bundle_id>\\logs` on Windows). Bundle id is
      `com.debpalash.omnivoice-studio` (frontend/src-tauri/tauri.conf.json).
    - backend.rs::backend_log_path() redirects the spawned backend's
      stdout/stderr to `backend.log` / `backend_err.log` under
      `~/Library/Logs/OmniVoice` (macOS), `$XDG_STATE_HOME/VoiceStudio` falling
      back to `~/.local/state/OmniVoice` (Linux), and
      `%LOCALAPPDATA%\\OmniVoice\\Logs` (Windows). This is where uvicorn
      startup banners and hard-crash tracebacks land — keep all three OS
      shapes listed or sidecar crashes become invisible off-macOS.
    """
    home = os.path.expanduser("~")
    bid = "com.debpalash.omnivoice-studio"
    if sys.platform == "darwin":
        return [
            os.path.join(home, "Library/Logs", bid, "tauri.log"),
            os.path.join(home, "Library/Logs", bid, "VoiceStudio.log"),
            os.path.join(home, "Library/Logs/OmniVoice/backend.log"),
            os.path.join(home, "Library/Logs/OmniVoice/backend_err.log"),
        ]
    if sys.platform.startswith("linux"):
        data_dir = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local/share")
        state_dir = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local/state")
        return [
            os.path.join(data_dir, bid, "logs", "tauri.log"),
            os.path.join(home, ".config", bid, "logs", "tauri.log"),
            os.path.join(state_dir, "OmniVoice", "backend.log"),
            os.path.join(state_dir, "OmniVoice", "backend_err.log"),
        ]
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", home)
        localappdata = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [
            os.path.join(localappdata, bid, "logs", "tauri.log"),
            os.path.join(appdata, bid, "logs", "tauri.log"),
            os.path.join(localappdata, "OmniVoice", "Logs", "backend.log"),
            os.path.join(localappdata, "OmniVoice", "Logs", "backend_err.log"),
        ]
    return []


@router.get("/system/logs")
async def system_logs(tail: int = 200):
    """Tail the rolling runtime log — everything Python logged since last rotation.

    Back-stop: if the rolling log doesn't exist yet (fresh install, disk error),
    fall back to the crash log so the UI always has something to show.
    """
    try:
        tail = max(10, min(2000, int(tail)))
    except Exception:
        tail = 200

    path = LOG_PATH if os.path.exists(LOG_PATH) else CRASH_LOG_PATH
    if not os.path.exists(path):
        return {"lines": [], "path": LOG_PATH, "exists": False}
    try:
        lines, total = await asyncio.to_thread(_tail_file, path, tail)
        return {"lines": lines, "path": path, "exists": True, "total_lines": total}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read log at {path}: {e}. Check file permissions or delete it manually.",
        )


@router.get("/system/logs/tauri")
async def system_logs_tauri(tail: int = 200):
    """Tail the Tauri plugin log (or backend stdout redirect, whichever exists)."""
    try:
        tail = max(10, min(2000, int(tail)))
    except Exception:
        tail = 200
    candidates = _tauri_log_candidates()
    for p in candidates:
        if os.path.exists(p):
            try:
                lines, total = await asyncio.to_thread(_tail_file, p, tail)
                return {"lines": lines, "path": p, "exists": True, "total_lines": total}
            except Exception as e:
                error = public_failure(
                    logger,
                    "Could not read Tauri log",
                    e,
                    response="Could not read the Tauri log; check the backend log for details.",
                    traceback=True,
                )
                return {"lines": [], "path": p, "exists": True, "error": error}
    return {"lines": [], "path": None, "exists": False, "candidates": candidates}


@router.get("/system/logs/stream")
async def stream_logs(
    source: str = Query("backend", description="'backend' or 'tauri'"),
    interval: float = Query(1.0, ge=0.3, le=10.0, description="Poll interval in seconds"),
):
    """Server-Sent Events stream of new log lines.

    The client opens an EventSource connection and receives new lines as they
    are appended to the log file.  This replaces the polling pattern used by
    the LogsFooter component.

    Usage (frontend)::

        const es = new EventSource('/system/logs/stream?source=backend');
        es.onmessage = (e) => { const lines = JSON.parse(e.data); ... };
    """
    if source == "tauri":
        candidates = _tauri_log_candidates()
        path = next((p for p in candidates if os.path.exists(p)), None)
    else:
        path = LOG_PATH if os.path.exists(LOG_PATH) else CRASH_LOG_PATH

    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Log file not found for source={source}")

    try:
        initial_position = os.path.getsize(path)
    except OSError as exc:
        logger.warning("Log stream could not determine its starting position")
        raise HTTPException(
            status_code=503,
            detail="The log stream could not be started. Retry after checking file permissions.",
        ) from exc

    async def _generate():
        """Yield SSE events whenever new lines appear in the log file."""
        last_pos = initial_position
        while True:
            await asyncio.sleep(interval)
            try:
                size = os.path.getsize(path)
                if size < last_pos:
                    # File was truncated (log rotation or clear) — reset
                    last_pos = 0
                if size == last_pos:
                    continue
                new_lines = await asyncio.to_thread(_read_from_pos, path, last_pos)
                last_pos = size
                if new_lines:
                    import json
                    yield f"data: {json.dumps(new_lines)}\n\n"
            except Exception:
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _read_from_pos(path: str, pos: int) -> list[str]:
    """Read all lines from `pos` to EOF (runs in threadpool)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        return f.readlines()


@router.post("/system/logs/clear")
async def clear_system_logs():
    """Truncate the rolling runtime log and the crash log (what the Backend tab reads)."""
    cleared_any = False
    for p in (LOG_PATH, CRASH_LOG_PATH):
        if os.path.exists(p):
            try:
                await asyncio.to_thread(_truncate_file, p)
                cleared_any = True
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not clear log at {p}: {e}. The file may be open in another process or read-only — close tailing tools and retry.",
                )
    if cleared_any:
        # The crash log just shrank to zero — drop any stored ack so a stale
        # byte count can't suppress the next 'crash-last-session' notice.
        for key in ("crash_log_acked", "crash_log_acked_size"):
            try:
                prefs_delete(key)
            except Exception as exc:
                logger.warning("Cleared logs but could not reset crash acknowledgement state")
                raise HTTPException(
                    status_code=500,
                    detail="Logs were cleared, but notification state could not be reset. Retry the clear operation.",
                ) from exc
    return {"cleared": cleared_any}


def _truncate_file(path: str):
    """Truncate a file to zero length (runs in threadpool)."""
    with open(path, "w") as f:
        f.truncate(0)


@router.post("/system/logs/tauri/clear")
async def clear_tauri_logs():
    """Truncate whichever Tauri-side log files we know about. OS-level rotation may recreate them."""
    cleared = []
    failed = 0
    for p in _tauri_log_candidates():
        if os.path.exists(p):
            try:
                await asyncio.to_thread(_truncate_file, p)
                cleared.append(p)
            except OSError:
                failed += 1
    if failed:
        raise HTTPException(
            status_code=500,
            detail="One or more desktop log files could not be cleared. Close any app using them and retry.",
        )
    return {"cleared": cleared, "failed": 0}

@router.get("/sysinfo", response_model=SysinfoResponse)
def get_sys_info():
    vram = 0.0
    gpu_active = False

    try:
        if _is_mac:
            alloc = getattr(torch.mps, "current_allocated_memory", None)
            driver = getattr(torch.mps, "driver_allocated_memory", None)
            if driver:
                vram = driver() / (1024**3)
            elif alloc:
                vram = alloc() / (1024**3)
        elif _is_cuda:
            vram = torch.cuda.memory_allocated() / (1024**3)
    except Exception:
        pass
        
    if vram > 0.01:
        gpu_active = True

    vm = psutil.virtual_memory()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.used / (1024**3),
        "total_ram": vm.total / (1024**3),
        "vram": round(vram, 2),
        "gpu_active": gpu_active
    }

@router.post("/system/flush-memory")
async def flush_memory(unload_model: bool = False):
    """Aggressively release RAM/VRAM by clearing caches and running GC.

    When unload_model=true, the TTS model is fully unloaded and will be
    re-loaded lazily on the next generation request.
    """
    import gc
    from services.model_manager import free_vram

    freed_model = False
    if unload_model:
        import services.model_manager as mm
        async with mm._model_lock:
            # Also drops the clone-prompt side cache, which this path used to
            # leave resident — an "unload" that kept the encoded reference
            # tensors belonging to the model it just released (#1495).
            freed_model = mm.unload_shared_model()

    # Multi-pass GC to break reference cycles
    gc.collect(generation=2)
    gc.collect(generation=1)
    gc.collect(generation=0)

    free_vram()

    # Snapshot after flush. Two numbers, because one of them is a lie by
    # omission: `memory_allocated` counts live tensors only, so it reads ~0
    # after an unload while nvidia-smi still shows gigabytes — which is exactly
    # the report we keep getting ("flush says it worked, the GPU says it
    # didn't"). `memory_reserved` is what the caching allocator holds from the
    # driver, and the gap between reserved and the driver's own figure is the
    # CUDA context plus kernel workspaces, which no in-process call can return.
    vram_after = 0.0
    vram_reserved = 0.0
    try:
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            driver = getattr(torch.mps, "driver_allocated_memory", None)
            if driver:
                vram_after = driver() / (1024**3)
            current = getattr(torch.mps, "current_allocated_memory", None)
            vram_reserved = (current() / (1024**3)) if current else vram_after
        elif torch.cuda.is_available():
            vram_after = torch.cuda.memory_allocated() / (1024**3)
            vram_reserved = torch.cuda.memory_reserved() / (1024**3)
    except Exception:
        pass

    ram_after = psutil.virtual_memory().used / (1024**3)

    return {
        "flushed": True,
        "unloaded_model": freed_model,
        "ram_after": round(ram_after, 2),
        "vram_after": round(vram_after, 2),
        "vram_reserved": round(vram_reserved, 2),
    }


# ── Actionable notifications ──────────────────────────────────────────────

_GPU_ARCH_WARNING: "list[str | None]" = []  # [-1] = computed result


def _gpu_arch_warning_cached() -> "str | None":
    """check_device_compatibility() once per process (it lazy-imports torch —
    too heavy for the 30s notifications poll)."""
    if not _GPU_ARCH_WARNING:
        try:
            from services.model_manager import check_device_compatibility
            compatible, warning = check_device_compatibility()
            _GPU_ARCH_WARNING.append(None if compatible else warning)
        except Exception:
            _GPU_ARCH_WARNING.append(None)
    return _GPU_ARCH_WARNING[-1]


@router.get("/system/notifications")
def system_notifications():
    """Return actionable notifications for the UI notification panel.

    Each notification has:
      - id: unique key (for dismiss tracking)
      - level: "info" | "warn" | "error"
      - title: short heading
      - message: longer description
      - action: optional {"label": str, "type": "navigate|link|api", "target": str}
    """
    notes = []

    # 1. Missing HF_TOKEN (env var OR canonical ~/.cache/huggingface/token)
    if not _has_hf_token():
        notes.append({
            "id": "hf-token-missing",
            "level": "warn",
            "title": "HuggingFace token not set",
            "message": (
                "Downloads may be rate-limited and speaker diarization "
                "won't work without a HuggingFace token."
            ),
            "action": {
                "label": "Set token",
                "type": "navigate",
                "target": "settings",
            },
        })

    # 1b. GPU compute capability unsupported by this torch build (#284) —
    # the model "runs" but emits pure noise, the worst silent failure mode
    # (RTX 50-series Blackwell sm_120 on pre-cu128 wheels). The loader logs
    # this, but a log line never reached the affected users — surface it in
    # the panel. Checked once per process: it lazy-imports torch.
    gpu_warn = _gpu_arch_warning_cached()
    if gpu_warn:
        notes.append({
            "id": "gpu-arch-unsupported",
            "level": "error",
            "title": "GPU not supported by this PyTorch build",
            "message": gpu_warn + " Until then, output will be noise/garbage.",
        })

    # 2. Missing ffmpeg
    ffmpeg_ok = False
    try:
        ffmpeg_path = find_ffmpeg()
        # find_ffmpeg may return an absolute path or a bare command name.
        # Both are valid — only flag missing if find_ffmpeg raises.
        ffmpeg_ok = bool(ffmpeg_path)
    except Exception:
        pass
    if not ffmpeg_ok:
        notes.append({
            "id": "ffmpeg-missing",
            "level": "error",
            "title": "Media engine unavailable",
            "message": (
                "Video processing, audio conversion, and dubbing need the "
                "media engine (ffmpeg), which the app normally provisions "
                "itself. Open Settings > Audio tools and press Restore "
                "bundled to re-download it, or point it at a system copy."
            ),
            "action": {
                "label": "Open Audio tools",
                "type": "settings-tab",
                "target": "audio-tools",
            },
        })

    # 3. Low disk space
    try:
        usage = shutil.disk_usage(DATA_DIR)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 5:
            notes.append({
                "id": "disk-low",
                "level": "warn",
                "title": f"Low disk space ({free_gb:.1f} GB free)",
                "message": "VoiceStudio needs disk space for models, audio, and temp files.",
                "action": None,
            })
    except Exception:
        pass

    # 4. GPU not available
    device = get_best_device()
    if device == "cpu":
        notes.append({
            "id": "gpu-unavailable",
            "level": "info",
            "title": "Running on CPU",
            "message": (
                "No GPU detected. TTS generation will be slower. "
                "If you have a GPU, check CUDA/MPS drivers."
            ),
            "action": None,
        })

    # 5a. The previous backend RUN died without a clean shutdown (#1164) —
    #     the run-sentinel record is the browser/dev/Docker equivalent of the
    #     desktop shell's crash marker. The id embeds detected_at so a NEW
    #     unclean death re-notifies even after an older one was dismissed.
    #     Coexists with the crash-last-session note below (that one covers
    #     caught unhandled exceptions; this one covers process death).
    try:
        from core import run_sentinel

        rec = run_sentinel.newest_record()
        if rec is not None and not rec[1]:
            record = rec[0]
            last = record.get("last_activity") or {}
            doing = f" Last activity: {last.get('kind')}." if last.get("kind") else ""
            notes.append({
                # ms resolution: two deaths in the same second must still get
                # distinct ids, or the second one stays invisible post-ack.
                "id": f"last-run-crash-{int((record.get('detected_at') or 0) * 1000)}",
                "level": "error",
                "title": "The backend did not shut down cleanly last run",
                "message": (
                    "The previous backend process ended without a clean "
                    "shutdown — it likely crashed or was killed (for example "
                    "by the OS running out of memory)." + doing +
                    " A log tail was captured for bug reports."
                ),
                "action": {
                    "label": "View logs",
                    "type": "navigate",
                    "target": "settings",
                },
            })
    except Exception:
        logger.warning("Previous-run crash record could not be checked")

    # 5. A previous session logged a crash the user never saw.
    #    crash_log grew past the last acknowledged size AND predates this
    #    process — i.e. it happened last run, not just now (errors from the
    #    current session already surfaced as toasts).
    try:
        if _crashed_last_session():
            notes.append({
                "id": "crash-last-session",
                "level": "error",
                "title": "Last session ended with an error",
                "message": (
                    "A crash was logged before this session started. "
                    "Review the backend log and consider filing a report."
                ),
                "action": {
                    "label": "View logs",
                    "type": "navigate",
                    "target": "settings",
                },
            })
    except Exception:
        logger.warning("Previous-session crash log could not be checked")

    return {"notifications": notes, "count": len(notes)}


# Process start time — anchors "did the crash happen before this run?".
_PROCESS_START_TS = time.time()


def _crashed_last_session() -> bool:
    from core.prefs import get as prefs_get

    if not os.path.exists(CRASH_LOG_PATH):
        return False
    size = os.path.getsize(CRASH_LOG_PATH)
    if size == 0:
        return False
    mtime = os.path.getmtime(CRASH_LOG_PATH)
    # Composite ack (size + mtime): a bare byte count goes stale after the log
    # is truncated — the next crash log can stay smaller than the old acked
    # size forever, silently suppressing 'crash-last-session'. The ack only
    # holds while it still covers the file's current state.
    ack = prefs_get("crash_log_acked")
    if isinstance(ack, dict):
        if float(ack.get("mtime", 0) or 0) >= mtime and int(ack.get("size", 0) or 0) >= size:
            return False
    else:
        # Legacy size-only ack from older builds.
        if size <= int(prefs_get("crash_log_acked_size", 0) or 0):
            return False
    return mtime < _PROCESS_START_TS


@router.get("/system/last-run-crash")
async def get_last_run_crash():
    """Newest unclean-shutdown record from the previous backend run (#1164)
    — the deployment-agnostic twin of the desktop shell's crash marker
    (`get_last_backend_crash`), for browser/dev/Docker frontends that have no
    shell to ask. Version-gated like the shell's markers: records from a
    different release than the running build are ignored (kept on disk)."""
    from core import run_sentinel

    rec = run_sentinel.newest_record()
    if rec is None:
        return {"record": None, "acknowledged": True}
    record, acked = rec
    return {"record": record, "acknowledged": acked}


@router.post("/system/last-run-crash/ack")
async def ack_last_run_crash():
    """Mark the newest unclean-shutdown record as seen. Watermark semantics
    (like the shell's ack): the record itself is retained so bug reports can
    still attach the evidence; a NEWER death re-arms the notice."""
    from core import run_sentinel

    run_sentinel.acknowledge()
    return {"ok": True}


@router.post("/system/crash/ack")
async def ack_crash():
    """Mark the current crash log as seen — dismisses the
    'crash-last-session' notification until the log changes again."""
    size = mtime = 0
    if os.path.exists(CRASH_LOG_PATH):
        size = os.path.getsize(CRASH_LOG_PATH)
        mtime = os.path.getmtime(CRASH_LOG_PATH)
    prefs_set("crash_log_acked", {"size": size, "mtime": mtime})
    return {"acked_size": size}


# ── Environment variable setter ───────────────────────────────────────────


PERSISTENT_KEYS = {
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    "TRANSLATE_BASE_URL", "TRANSLATE_API_KEY", "TRANSLATE_MODEL",
    "DEEPL_API_KEY", "DEEPL_BASE_URL",
    "MICROSOFT_API_KEY", "MICROSOFT_BASE_URL",
    # User-configurable network ports. Persisted so they survive restarts;
    # the Rust sidecar reads OMNIVOICE_PORT at startup and the backend derives
    # the LAN-share/UI ports from the others.
    "OMNIVOICE_PORT", "OMNIVOICE_SHARE_PORT", "OMNIVOICE_UI_PORT",
    # Per-job compute-time budgets (#1787). Both are captured at import time
    # by services/model_manager.py (GPU_JOB_TIMEOUT_S / CPU_JOB_TIMEOUT_S), so
    # a value saved here takes effect on the NEXT backend restart — same
    # contract as OMNIVOICE_PORT above. Restored into os.environ during the
    # "env_prefs" startup step (main.py), which runs before model_manager is
    # first imported ("ml_imports"), so the restored value is what the module
    # captures. The Settings UI must say so (RestartBadge).
    "OMNIVOICE_GENERATE_TIMEOUT_S", "OMNIVOICE_CPU_GENERATE_TIMEOUT_S",
}

# Sidecar-engine install dirs (OMNIVOICE_INDEXTTS_DIR, …). The one-click
# installer persists these via prefs.json `env.*` (restored at startup in
# main.py); merging them here lets users inspect/clear them from the same
# Settings env panel as every other persisted var. Single-sourced from the
# installer's SPECS so a future sidecar engine can't forget to register.
try:
    from services.sidecar_install import persistent_env_vars as _sidecar_env_vars
    PERSISTENT_KEYS |= _sidecar_env_vars()
except Exception:  # pragma: no cover — defensive: env panel > installer wiring
    pass

# Keys whose value must be a valid TCP port (1024–65535). Validated before
# being set so a bad value never reaches uvicorn / the share listener.
_PORT_KEYS = {"OMNIVOICE_PORT", "OMNIVOICE_SHARE_PORT", "OMNIVOICE_UI_PORT"}

# Keys whose value is a wall-clock compute-time budget in seconds (#1787).
# Validated the same way as _PORT_KEYS: reject anything that isn't a
# positive number before it reaches services/model_manager.py. Upper bound is
# generous — long enough that a legitimate multi-hour, audiobook-length CPU
# render is never blocked — but still bounded, so a fat-fingered extra digit
# (300 -> 3000000) can't turn a wedged job into one that silently occupies a
# worker for days before the guard ever fires.
_TIMEOUT_KEYS = {"OMNIVOICE_GENERATE_TIMEOUT_S", "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"}
_MAX_GENERATE_TIMEOUT_S = 21600.0  # 6 hours


@router.post("/system/set-env")
async def set_env_var(body: dict):
    """Set an environment variable at runtime, persisted across restarts.

    Persistent keys (proxy, FFMPEG_PATH, translation provider keys, …) are
    saved to ``prefs.json`` so they survive backend restarts (restored at
    startup in ``main.py``). HF_TOKEN is persisted via
    ``huggingface_hub.login()`` (and cleared via ``logout()``). Other keys
    are set on ``os.environ`` for the running process.

    The loopback-origin gate that previously lived inline here is now applied
    at the router level via `dependencies=[Depends(require_admin)]` on
    `router` — see the top of this file. Every route on this router is
    gated, including this one. The 403 body and behavior are unchanged.
    """
    ALLOWED_KEYS = PERSISTENT_KEYS | {"HF_TOKEN", "TRANSLATE_API_KEY"}
    key = body.get("key", "")
    value = body.get("value", "")

    if key not in ALLOWED_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Key '{key}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_KEYS))}",
        )

    if value:
        # Port keys must be a numeric string in the unprivileged range so a
        # typo can't drop the backend onto a privileged port (<1024) or an
        # out-of-range value uvicorn would reject at bind time.
        if key in _PORT_KEYS:
            try:
                port_n = int(value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid port for {key}: '{value}' is not a number.",
                )
            if not (1024 <= port_n <= 65535):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid port for {key}: must be between 1024 and 65535.",
                )
        if key in _TIMEOUT_KEYS:
            try:
                timeout_n = float(value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid timeout for {key}: '{value}' is not a number.",
                )
            if not (0 < timeout_n <= _MAX_GENERATE_TIMEOUT_S):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid timeout for {key}: must be greater than 0 "
                        f"and at most {_MAX_GENERATE_TIMEOUT_S:.0f} seconds."
                    ),
                )
        os.environ[key] = value
        logger.info("Environment variable set (length=%d)", len(value))

        # Capability 1 / issue #35: HF_TOKEN persists across restarts via
        # huggingface_hub.login() — writes the token to $HF_HOME/token so
        # the next process pickup doesn't need an env var. add_to_git_credential
        # stays False; we don't want to spew tokens into the user's git config.
        if key == "HF_TOKEN":
            try:
                from huggingface_hub import login as _hf_login
                _hf_login(token=value, add_to_git_credential=False)
                logger.info("HF token persisted to $HF_HOME/token via login()")
            except Exception as e:
                # Non-fatal — the runtime env var is still set, so the
                # current process will still see the token. We just lose
                # persistence across restarts.
                logger.warning("Could not persist HF token to disk: %s", log_safe(e))
    else:
        os.environ.pop(key, None)
        logger.info("Environment variable cleared")

        # Mirror the persistence on clear — wipe the saved token file too.
        if key == "HF_TOKEN":
            try:
                from huggingface_hub import logout as _hf_logout
                _hf_logout()
                logger.info("HF token cleared from $HF_HOME/token via logout()")
            except Exception as e:
                logger.warning("Could not clear HF token file: %s", e)

    # HF_TOKEN persistence is handled above via huggingface_hub.login()/
    # logout() — it never touches prefs.json. Everything else in
    # PERSISTENT_KEYS (proxy, FFMPEG_PATH, translation provider keys, …) is
    # saved to prefs.json so it survives backend restarts (restored at
    # startup in main.py). Non-persistent keys stay process-local.
    if key != "HF_TOKEN" and key in PERSISTENT_KEYS:
        prefs_key = f"env.{key}"
        if value:
            prefs_set(prefs_key, value)
        else:
            prefs_delete(prefs_key)

    # #1787 review fix: tell the caller up front when the value just saved is
    # being shadowed by an external env var — set at THIS process's startup,
    # before our own prefs restore ran, so it predicts the next restart too.
    # A response that just said {"set": True} let the Settings panel promise
    # a restart would apply a value that never will.
    from core.prefs import is_env_shadowed
    return {"key": key, "set": bool(value), "shadowed": is_env_shadowed(key)}


@router.post("/clean-audio")
async def clean_audio(audio: UploadFile = File(...)):
    """Accept a raw mic recording, run demucs vocal isolation, return clean WAV."""
    clean_id = str(uuid.uuid4())[:8]
    tmp_dir = os.path.join(OUTPUTS_DIR, f"_clean_{clean_id}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        return await _do_clean_audio(audio, tmp_dir, clean_id)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _do_clean_audio(audio, tmp_dir, clean_id):
    raw_path = os.path.join(tmp_dir, "raw.wav")
    with open(raw_path, "wb") as f:
        f.write(await audio.read())

    converted_path = os.path.join(tmp_dir, "converted.wav")
    ffmpeg = find_ffmpeg()
    try:
        rc, _, _ = await run_ffmpeg(
            [ffmpeg, "-y", "-i", raw_path, "-ar", "24000", "-ac", "1", converted_path],
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        rc = -1
    if rc != 0:
        converted_path = raw_path

    clean_path = converted_path
    try:
        rc, _, _ = await run_ffmpeg(
            [sys.executable, "-m", "demucs.separate", "--two-stems", "vocals", "-n", "htdemucs",
             "-d", get_best_device(), converted_path, "-o", tmp_dir],
            timeout=900.0,
        )
        if rc == 0:
            demucs_out = os.path.join(tmp_dir, "htdemucs", "converted")
            vocals_file = os.path.join(demucs_out, "vocals.wav")
            if os.path.exists(vocals_file):
                clean_path = vocals_file
    except asyncio.TimeoutError:
        logger.warning("Demucs timed out for mic audio, using raw")
    except Exception as e:
        logger.warning(f"Demucs failed for mic audio, using raw: {e}")

    clean_filename = f"mic_{clean_id}.wav"
    final_path = os.path.join(OUTPUTS_DIR, clean_filename)

    conversion_fallback = False
    try:
        rc, _, _ = await run_ffmpeg(
            [ffmpeg, "-y", "-i", clean_path, "-ar", "24000", "-ac", "1", final_path],
            timeout=120.0,
        )
        conversion_fallback = rc != 0
    except asyncio.TimeoutError:
        conversion_fallback = True
        logger.warning("Final clean-audio conversion timed out; returning the cleaned source format")
    if conversion_fallback:
        shutil.copy2(clean_path, final_path)
    elif not os.path.exists(final_path):
        shutil.copy2(clean_path, final_path)

    headers = {"X-Clean-Filename": clean_filename}
    if conversion_fallback:
        headers["X-Clean-Conversion"] = "fallback"
    return FileResponse(final_path, media_type="audio/wav", filename=clean_filename,
                        headers=headers)


@router.get("/system/asr-backends")
def asr_backends():
    """List all registered ASR backends and their availability."""
    from services.asr_backend import list_backends, active_backend_id
    return {
        "active": active_backend_id(),
        "backends": list_backends(),
    }


# ── Phase 1 AUTH-01 / AUTH-03 — HF token resolver state ──────────────────


@router.get("/system/hf-token/state")
def hf_token_state():
    """Return the 3-source HF token cascade state for the Settings UI
    (Wave 2 React panel consumes this). Never returns the raw token —
    only a masked preview, whoami username, and per-source validity.
    """
    from dataclasses import asdict
    from services import token_resolver

    s = token_resolver.state()
    return {
        "active": s["active"],
        "sources": [asdict(row) for row in s["sources"]],
    }


# ── Error journal ─────────────────────────────────────────────────────────


@router.get("/system/errors/recent")
def recent_errors(limit: int = Query(20, ge=1, le=50)):
    """Recent unhandled backend errors, newest first — structured, deduped
    (count per fingerprint), classified (error_class), pre-scrubbed. The
    bug-report pipeline reads this to auto-attach the most recent backend
    failure; Settings → Logs can render it as a triage view.
    """
    from core import error_journal

    errors = error_journal.recent(limit)
    return {"errors": errors, "count": len(errors)}


# ── Diagnostic bundle ─────────────────────────────────────────────────────


@router.post("/system/diagnostic-bundle")
async def diagnostic_bundle(network: bool = Query(False, description="Include the hub reachability probe")):
    """Build the drag-onto-a-GitHub-issue zip (core.diagnostic_bundle):
    self-check report, recent error journal, scrubbed log tails. Returns the
    local path so the UI can reveal it in the file manager. The path itself
    is NOT scrubbed — this response never leaves the machine; the zip's
    *contents* are scrubbed because the zip does.
    """
    from core.diagnostic_bundle import build_bundle

    path = await asyncio.to_thread(build_bundle, network)
    return {"path": path, "filename": os.path.basename(path)}


# ── Self-check diagnostics ────────────────────────────────────────────────


@router.get(
    "/system/diagnose",
    dependencies=[Depends(require_admin_action)],
)
async def system_diagnose(
    network: bool = Query(True, description="Include the HuggingFace hub reachability probe"),
    deep: bool = Query(False, description="Also load the active engine and synthesize a short utterance (may cold-load the model — minutes on first run)"),
):
    """Run the self-check suite (core.diagnose) and return the structured report.

    The hub probe can block up to ~5s (and ``deep=true`` far longer), so the
    whole run goes through a threadpool; pass ``network=false`` for an
    instant offline report. Output is pre-scrubbed (core.scrub) — safe to
    paste into a GitHub issue.
    """
    from core.diagnose import run_diagnostics

    return await asyncio.to_thread(run_diagnostics, network, deep)


# ── Phase 1 Wave 3 — macOS Gatekeeper quarantine probe (#54) ────────────


@router.get("/system/quarantine-status")
def quarantine_status():
    """Report whether the running .app bundle has the macOS quarantine xattr.

    On non-macOS platforms or dev runs (not inside a .app bundle), always
    returns ``{"quarantined": false, "error_class": null}``. The React
    ErrorBoundary polls this endpoint on first load and renders the docs
    deeplink when ``error_class`` is set (Plan 01-02 wired the deeplink).
    """
    from core import gatekeeper_detect

    return gatekeeper_detect.quarantine_status()


# ── Network sharing (loopback-only control surface) ──────────────────────────

@router.get("/system/network/state")
async def network_state(request: Request):
    st = network_share.get_state()
    # PIN-only server mode permits unauthenticated read-only discovery, but the
    # PIN is itself a consumption credential. Reveal it only to the native
    # loopback UI or to a remote caller that already passed the configured
    # long API-key gate. The boolean lets headless dashboards remain useful.
    host = request.client.host if request.client else None
    may_reveal_pin = is_loopback(host) or bool(
        os.environ.get("OMNIVOICE_API_KEY", "").strip()
    )
    return {
        "enabled": st.enabled,
        "share_port": st.share_port,
        "pin": st.pin if may_reveal_pin else None,
        "pin_required": bool(st.pin),
        "lan_addresses": st.lan_addresses,
    }


@router.post("/system/network/enable")
async def network_enable(request: Request):
    st = await network_share.enable(request.app)
    return {
        "enabled": st.enabled,
        "share_port": st.share_port,
        "pin": st.pin,
        "lan_addresses": st.lan_addresses,
    }


@router.post("/system/network/disable")
async def network_disable(request: Request):
    st = await network_share.disable(request.app)
    return {"enabled": st.enabled}


# ── Tailscale (loopback-only control surface) ────────────────────────────────

@router.get("/system/tailscale/status")
async def tailscale_status():
    return _tailscale.status()


@router.post("/system/tailscale/enable")
async def tailscale_enable():
    result = _tailscale.serve_enable()
    if result.get("ok"):
        return result
    error = public_failure(
        logger,
        "Tailscale serve failed",
        result.get("error", "unknown error"),
        response="Tailscale sharing could not be enabled; check the backend log for details.",
    )
    return {"ok": False, "error": error}


@router.post("/system/tailscale/disable")
async def tailscale_disable():
    return _tailscale.serve_disable()


# ── Local-only usage insights (the user's own numbers, never transmitted) ───
# This answers "how am I using this?" for the USER by aggregating the history
# the app has ALREADY written to their own database. It collects nothing new,
# stores nothing new, and transmits nothing anywhere: the only consumer is the
# user's own UI over loopback. Read-only, content-free (counts and totals,
# never the text of a take). Product analytics for the PROJECT is a separate,
# consent-gated path (core/analytics.py: opt-in PostHog behind the first-run
# prompt, allowlisted content-free metadata only) — this endpoint stays local
# regardless of that consent.
@router.get("/stats/usage")
def stats_usage():
    from services.local_stats import usage_summary

    return usage_summary()
