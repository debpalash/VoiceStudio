import os
import sys

# Ensure `backend/` is on sys.path so bare imports like `from core.config`
# work regardless of how uvicorn is invoked:
#   - `uvicorn main:app`           (cwd = backend/)
#   - `uvicorn backend.main:app`   (cwd = /app, Docker)
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# PyInstaller re-executes this entry module when the frozen backend binary is
# launched. Nested operation supervisors therefore dispatch here, before math,
# logging, FastAPI, torch, or any application initialization. Source launches
# use this same entry contract so frozen/source behavior cannot drift.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--supervise":
    from core.contained_subprocess import supervisor_main

    raise SystemExit(supervisor_main(sys.argv[1:]))

# Rust clears CLOEXEC only for the backend exec. Re-arm PEP 446 immediately:
# nested supervisors receive this descriptor solely through explicit pass_fds,
# so a third-party close_fds=False child cannot hold the desktop drain barrier.
from core.contained_subprocess import secure_backend_drain_fd  # noqa: E402

secure_backend_drain_fd()

import math  # noqa: E402

# Windows: run every child process (ffmpeg, engine sidecars, yt-dlp, demucs, …)
# WITHOUT popping a console window. The backend itself is spawned console-less by
# the Tauri shell, so on Windows each console subprocess it launches would
# otherwise get a brand-new cmd window flashed on screen. Patch subprocess.Popen
# once, before anything spawns, so our 70+ call sites AND third-party libraries
# (imageio-ffmpeg, yt-dlp) are all covered. No-op off Windows. (#1178)
from core.win_subprocess import install as _install_no_window  # noqa: E402

_install_no_window()

# #564: also make the project's OWN `omnivoice` package importable from source
# when the venv's editable install is missing/broken (interrupted/offline
# `uv sync`, antivirus-quarantined `_editable_impl_omnivoice.pth`, …). Without
# this the backend boots fine and only fails at the first model call with
# `No module named 'omnivoice'`. The bootstrap now gates on omnivoice being
# importable too (re-syncing to re-lay the editable install); this is the
# runtime safety net. See core/omnivoice_path.py for the full rationale.
from core.omnivoice_path import ensure_omnivoice_importable
ensure_omnivoice_importable(_backend_dir)

# Triton is unavailable on Windows — disable torch.compile / dynamo / inductor
# to prevent TritonMissing errors at inference time. Must be set before torch
# is imported (it is lazily imported in services/model_manager.py). Uses
# setdefault so an explicit user-set value is never overridden, and is guarded
# to win32 so cross-platform default behavior is unchanged.
if sys.platform == "win32":
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    os.environ.setdefault("TORCHINDUCTOR_DISABLE", "1")

# The Intel Fortran runtime bundled with MKL (under numpy/scipy) installs a
# console CTRL handler that aborts the whole process with `forrtl: error
# (200): program aborting due to window-CLOSE event` when a Windows console
# CLOSE/LOGOFF/SHUTDOWN event reaches it — seen in the wild as backend crashes
# with exit code 2 / 0xC000013A mid-session (#1153 class). The RTL reads this
# at DLL init, so it must be set before torch/numpy import MKL; setdefault so
# an explicit user value wins. A no-op everywhere the Fortran RTL isn't
# handling console events (macOS/Linux), hence unconditional (and testable).
os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")

# The backend's stdout/stderr are pipes owned by the desktop shell that
# spawned it. If that shell exits while the backend survives (crash,
# relaunch, orphan), the pipes close — and the next write raises
# BrokenPipeError. transformers' tqdm weight-loading bar writes constantly,
# so an orphaned backend couldn't load the model at all (caught in the wild
# by the in-app diagnostic report). Wrap stdio so EPIPE is swallowed
# process-wide: logs are best-effort for a server, model loading is not.
# (utils.hf_progress.SafeFileWrapper — same wrapper the patched hub tqdm
# already uses for its own fp.)
from utils.hf_progress import SafeFileWrapper as _SafeStdio  # noqa: E402
from core.parent_liveness import arm_desktop_parent_watchdog  # noqa: E402

# Force UTF-8 stdio before wrapping (#1155): on Windows the spawned backend's
# stdout defaults to cp1252, and any library that prints user text (kittentts
# prints the full synth text on every generate) raised UnicodeEncodeError on
# Vietnamese/CJK/…, killing the request with a bogus 400. backslashreplace
# keeps even a non-UTF-8-able sink from ever raising.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:  # noqa: BLE001 — pythonw/frozen builds may lack reconfigure
        pass

# The desktop keeps the backend's stdin pipe open for its own lifetime. EOF is
# therefore a stable ownership signal that survives PID reuse and lets a child
# terminate even when the shell crashes before its normal process-tree teardown.
arm_desktop_parent_watchdog()

if not getattr(sys.stdout, "_is_safe_wrapper", False):
    sys.stdout = _SafeStdio(sys.stdout)
if not getattr(sys.stderr, "_is_safe_wrapper", False):
    sys.stderr = _SafeStdio(sys.stderr)

try:
    import dotenv

    dotenv.load_dotenv()
    # Also load .env from the project root (parent of backend/)
    _project_env = os.path.join(os.path.dirname(_backend_dir), ".env")
    if os.path.isfile(_project_env):
        dotenv.load_dotenv(_project_env, override=False)
    # Load the durable per-user config (the in-app Settings source of truth) so
    # env vars set once survive Tauri/Finder launches that don't inherit a shell
    # environment. This OVERRIDES launcher-injected defaults: the desktop app
    # injects a stale OMNIVOICE_CACHE_DIR from its own config before startup, so
    # without override a models dir changed in Settings was ignored forever (#480).
    from core.user_env import load_into_environ as _load_user_env
    _load_user_env()
except ImportError:
    pass

# ── cuDNN 8 library preload ─────────────────────────────────────────────
# Moved into _phase_a_build (`native_preload` step, early-bind refactor): the
# native dlopen/LoadLibrary belongs to the deferred heavy phase, and its one
# hard invariant — run before any ctranslate2/torch import — is preserved
# there (native_preload strictly precedes ml_imports).

# Route HF/Torch caches to a single external directory when requested.
_cache_dir = os.environ.get("OMNIVOICE_CACHE_DIR")
if _cache_dir:
    os.makedirs(_cache_dir, exist_ok=True)
    os.environ["HF_HOME"] = _cache_dir
    os.environ["HF_HUB_CACHE"] = _cache_dir
    os.environ["TORCH_HOME"] = _cache_dir

# ── Windows symlink fix ─────────────────────────────────────────────────────
# HuggingFace Hub creates NTFS symlinks in its cache to deduplicate blobs
# across model revisions.  On Windows, symlink creation requires either
# Developer Mode enabled or an elevated (Administrator) shell.  Without
# either, `snapshot_download` / `hf_hub_download` raises:
#   OSError: [WinError 1314] A required privilege is not held by the client
# Setting HF_HUB_DISABLE_SYMLINKS_WARNING silences the console spam, and the
# newer HF_HUB_DISABLE_SYMLINKS (huggingface_hub ≥ 0.21) forces file copies
# instead — slightly more disk but always works on first install.
if sys.platform == "win32":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# ── HF Xet → legacy LFS fallback ────────────────────────────────────────────
# huggingface_hub ≥ 1.5 routes large file downloads through the Xet content-
# addressed protocol (hf_xet runtime), which has its own internal progress
# reporting that bypasses our `tqdm` monkey-patch in `utils.hf_progress`.
# As a result the SetupWizard install rows show no byte progress while the
# download is actually running. Force the legacy LFS path until we add a
# proper hf_xet progress hook — this still streams via the standard tqdm
# wrapper that our patch intercepts. Override-able by the user.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ── HF network timeouts ─────────────────────────────────────────────────────
# Bound HF network ops so a stalled metadata HEAD or a dead download socket
# RAISES (and surfaces as an error) instead of hanging the model-load worker
# forever — the root cause of the "demo voice spins forever, no error" report
# (most often hit on Windows behind a proxy / firewall / antivirus that wedges
# the multi-GB legacy-LFS transfer). HF_HUB_DOWNLOAD_TIMEOUT is a *per-read*
# timeout: it resets on every received chunk, so a slow-but-progressing
# download is never punished — only a genuinely dead socket trips it. Both are
# user-overridable for unusually slow links. Set before huggingface_hub is
# imported so its constants pick them up.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

# ── OS trust store for TLS (#976) ───────────────────────────────────────────
# Users behind a corporate/antivirus proxy that TLS-inspects HTTPS traffic get
# a raw "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure"
# on every model install — the TCP connection succeeds (a different failure
# mode from #984's TCP-level blocked-host case), but the proxy re-signs the
# certificate with its own root CA, which the OS trusts (Windows CryptoAPI/
# SChannel) and Python's bundled `certifi` CA list does not. `inject_into_ssl`
# patches `ssl.SSLContext` process-wide to verify against the OS trust store
# instead, which is the actual fix (not just a nicer error message). Must run
# here — at MODULE level, before huggingface_hub/requests/httpx do any network
# I/O — not inside lifespan(), which runs too late. Not platform-gated: it's a
# correctness improvement everywhere. Best-effort: never block startup.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

# Prevent torchaudio from lazy-importing torchcodec (broken on some installs).
# Proper fix = exclude torchcodec in pyproject.toml; this is a belt-and-braces guard.
os.environ.setdefault("TORCHAUDIO_USE_TORCHCODEC", "0")
sys.modules.setdefault("torchcodec", None)

import warnings
import logging
from logging.handlers import RotatingFileHandler

# The heavy tail that used to run here at module scope — prefs.json env
# restore, the legacy-translate migration (#963), the yt-dlp overlay, the
# cuDNN 8 preload, `import torchaudio` (10-20s cold, drags torch), the
# 30-router import fan-out — now lives in `_phase_a_build` /
# `_phase_a_finalize` below, so uvicorn can bind the socket and answer
# `/health` + `/startup/progress` within ~1s of spawn instead of after all
# of it. Under pytest (or OMNIVOICE_EAGER_INIT=1) it still runs at import,
# at the bottom of this module — same order, same side effects, so the
# ~100 lifespan-less TestClient call sites see today's fully-built app.


class _WindowsSafeRotatingFileHandler(RotatingFileHandler):
    def doRollover(self):
        _log = logging.getLogger("omnivoice.api")
        try:
            super().doRollover()
        except PermissionError:
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename("%s.%d" % (self.baseFilename, i))
                dfn = self.rotation_filename("%s.%d" % (self.baseFilename, i + 1))
                if os.path.exists(sfn):
                    try:
                        from utils.fsops import safe_replace
                        safe_replace(sfn, dfn)
                    except OSError as e:
                        _log.warning("log rotation rename failed: %s", e)
            dfn = self.rotation_filename(self.baseFilename + ".1")
            if os.path.exists(dfn):
                try:
                    os.remove(dfn)
                except OSError as e:
                    _log.warning("log rotation remove failed: %s", e)
            try:
                self.rotate(self.baseFilename, dfn)
            except PermissionError:
                _log.warning("log rotation rotate failed (PermissionError)")
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = self._open()

_LOG_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class _JsonFormatter(logging.Formatter):
    """Single-line JSON-per-record formatter. Opt in with `OMNIVOICE_JSON_LOGS=1`.

    Keeps every field unquoted-string-safe so downstream log shippers
    (Vector, Fluent Bit, grep) can stream without extra parsing.
    """

    def format(self, record: logging.LogRecord) -> str:
        import json as _json

        payload = {
            "t": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


_json_logs = os.environ.get("OMNIVOICE_JSON_LOGS") == "1"
logging.basicConfig(
    level=os.environ.get("OMNIVOICE_LOG_LEVEL", "INFO"),
    format=_LOG_FMT,
)

# Phase 1 AUTH-05 / threat T-01-02: install the HF-token redactor on the
# root logger BEFORE any handler-attaching code runs. Every handler then
# inherits the filter, so even handler-formatted output (file, stream,
# JSON) strips real HF tokens. Cheap (regex on each record) and
# idempotent — extra calls are no-ops.
from core.logging_filter import install_redaction_filter  # noqa: E402
install_redaction_filter()

class AsyncioExceptionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.WARNING and "socket.send() raised exception" in record.getMessage():
            return False
        return True

logging.getLogger("asyncio").addFilter(AsyncioExceptionFilter())

# Silence HF Hub unauthenticated warnings unless specifically requested.
logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
# Silence httpx INFO — every HF Hub API call logs a line; the SSE stream
# already surfaces download progress to the UI.
logging.getLogger("httpx").setLevel(logging.WARNING)
if _json_logs:
    # Replace every existing handler's formatter with the JSON one.
    for _h in logging.getLogger().handlers:
        _h.setFormatter(_JsonFormatter())

# Rolling file handler so the Settings UI > Logs > Backend tab has something to read.
# Attached to root so uvicorn, fastapi, and every `omnivoice.*` namespace land here.
# Not attached under _disable_file_log to keep CI/headless tests quiet.
if not os.environ.get("OMNIVOICE_DISABLE_FILE_LOG"):
    from core.config import (
        LOG_PATH as _LOG_PATH,
    )  # local import — avoids circular import at module top

    try:
        _file_handler = _WindowsSafeRotatingFileHandler(
            _LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        _file_handler.setLevel(logging.INFO)
        _file_handler.setFormatter(
            _JsonFormatter() if _json_logs else logging.Formatter(_LOG_FMT)
        )
        logging.getLogger().addHandler(_file_handler)
        # Re-install the redactor so the new file handler picks up the
        # filter too (install_redaction_filter is idempotent).
        install_redaction_filter()
    except Exception as _e:  # disk full, permission denied, etc. — don't block startup
        logging.getLogger("omnivoice.api").warning("Runtime log file disabled: %s", _e)

logger = logging.getLogger("omnivoice.api")

import asyncio
import time
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
# Docs-only dependency: a venv created before scalar-fastapi entered the
# dependency set must still boot the backend (#307) — /docs degrades instead.
try:
    from scalar_fastapi import get_scalar_api_reference
except ImportError:
    get_scalar_api_reference = None
import traceback

_crash_log_lock = threading.Lock()

from core.config import OUTPUTS_DIR, VOICES_DIR, CRASH_LOG_PATH
from core.tasks import task_manager
from core import job_store
from core import startup_progress as _startup_progress
from services import network_share

# Rebound by _phase_a_build's `ml_imports` step (services.model_manager drags
# numpy and lazily torch, so it belongs to the deferred phase). Every use is
# either inside the startup/shutdown paths — which run strictly after Phase A
# — or None-guarded (the global exception handler's isinstance, which already
# has the name-based fallback for the dual-module-identity case).
ModelLoadInterruptedByShutdown = None
model_loads_begin_shutdown = None
idle_worker = None
preload_model = None
model_loads_reset_shutdown = None

from core.auth import (
    CredentialTransport,
    PrincipalKind,
    credential_matches,
    is_local_host,
    principal_for,
    remote_api_key,
)
from core.csrf import SAFE_HTTP_METHODS, cookie_csrf_allowed, origin_allowed

# The 30-router fan-out (`from api.routers import (...)`) is the widest —
# and, because dub_core/dub_generate/system import torch at module level,
# one of the slowest — import in the codebase. It now happens in
# _phase_a_build (`api_routes` step) after ml_imports; _phase_a_finalize
# registers the routers on the app.


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Eager mode runs the heavy phases inline at module import — today's exact
# behavior — and exists for the ~100 lifespan-less `TestClient(app)` call
# sites that expect `from main import app` to yield the fully-built app.
# Server runs (desktop, Docker, `uvicorn main:app`, `python main.py`) get the
# deferred path: socket up in ~1s, heavy work behind /startup/progress.
_EAGER = _env_flag("OMNIVOICE_EAGER_INIT", default=("pytest" in sys.modules))


def _env_float(name: str, default: float) -> float:
    """Parse a float env override, rejecting negative and non-finite values.

    Shared by the preload-delay / timeout knobs: NaN would silently never
    fire, a negative would fire during startup I/O, so both fall back to the
    default instead (the bug class CodeRabbit flagged on the watermark knob
    in PR #1577 — latent in the older copies too, closed here for all)."""
    raw = os.environ.get(name, "")
    try:
        value = float(raw) if raw.strip() else default
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _capture_preload_delay_s() -> float:
    """Seconds after boot before the dictation (capture ASR) model warms.

    Late enough that it never competes with startup I/O or the TTS preload;
    overridable via OMNIVOICE_CAPTURE_PRELOAD_DELAY (mostly for tests)."""
    return _env_float("OMNIVOICE_CAPTURE_PRELOAD_DELAY", 30.0)

def _watermark_preload_delay_s() -> float:
    """Seconds after boot before the AudioSeal generator warm-up fires.

    Own knob, NOT ``_capture_preload_delay_s`` + offset: a capture-specific
    env override must not retime the watermark warm too, and the two cold
    imports shouldn't fire on the same tick (CodeRabbit, PR #1577). Default
    35s sits ~5s past the capture-ASR warm for the same reason."""
    return _env_float("OMNIVOICE_PRELOAD_WATERMARK_DELAY", 35.0)


def _capture_preload_ram_ok(min_free_bytes: int = 4 * 1024**3) -> bool:
    """RAM guard for the dictation warm-up: skip below 4 GB free so the
    background load never pushes a small machine into swap. If free memory
    can't be measured, warm anyway (the load path has its own error handling)."""
    try:
        import psutil
        return psutil.virtual_memory().available >= min_free_bytes
    except Exception:
        return True


def _mcp_start_timeout_s() -> float:
    """Seconds to wait for the MCP session manager to start before giving up
    and serving without it (#632). Overridable via OMNIVOICE_MCP_START_TIMEOUT_S."""
    return max(_env_float("OMNIVOICE_MCP_START_TIMEOUT_S", 30.0), 0.001)


async def _serve_mcp(session_manager, ready: "asyncio.Event", stop: "asyncio.Event") -> None:
    """Own the MCP session manager's full enter→exit lifecycle in ONE task.

    FastMCP's ``run()`` opens an anyio task group, and anyio requires the cancel
    scope to be exited in the *same task* that entered it. So we must NOT enter
    it via ``wait_for`` (which runs the enter in a throwaway sub-task) or on the
    lifespan task and exit it elsewhere — either raises "Attempted to exit cancel
    scope in a different task". This coroutine enters and exits the context
    itself: it signals ``ready`` once mounted, then idles until ``stop``.
    """
    try:
        async with session_manager.run():
            ready.set()
            await stop.wait()
    except Exception as e:
        logger.warning("MCP session manager stopped: %s", e)
    finally:
        ready.set()  # never leave startup blocked on the readiness wait


async def _start_mcp_session_manager(session_manager, *, timeout: float):
    """Start MCP off the startup critical path; wait up to ``timeout`` for it to
    signal ready. Returns ``(task, stop_event, mounted)``.

    The MCP layer is best-effort and must never wedge backend startup. On some
    platforms (observed: Apple-Silicon M1, #632) ``run()`` can *hang* on its
    anyio task group; the old code awaited the enter before serving, so the hang
    meant "Application startup complete" never fired and the whole backend was
    unreachable with no error. Now the enter lives in its own task and we only
    *optionally* wait on a ready signal — a hang becomes a logged warning + a
    backend that serves normally without MCP.
    """
    stop = asyncio.Event()
    if session_manager is None:
        return None, stop, False
    ready = asyncio.Event()
    task = asyncio.create_task(_serve_mcp(session_manager, ready, stop))
    try:
        await asyncio.wait_for(ready.wait(), timeout=timeout)
        mounted = not task.done()  # ready is also set on failure → not mounted
    except asyncio.TimeoutError:
        logger.warning(
            "MCP session manager did not signal ready within %.0fs (#632); "
            "serving without waiting. Set OMNIVOICE_MCP_START_TIMEOUT_S to adjust.",
            timeout,
        )
        mounted = False
    return task, stop, mounted


async def _cancel_and_await_tasks(*tasks, timeout: float = 3.0) -> None:
    """Cancel each background task and give it a bounded chance to actually
    finish before shutdown proceeds — ``None`` entries are skipped (a task
    that's conditionally created, e.g. ``capture_preload_task``, may not
    exist).

    ``task.cancel()`` alone is not enough for a task awaiting
    ``run_in_executor()``: once the underlying OS thread is inside blocking
    native/import work, cancellation can't stop it, so cancel-and-move-on lets
    shutdown finish while that thread is still running — invisible to
    asyncio, but very much alive when the interpreter starts tearing down
    module state under it (#1000 class). Awaiting with a bound (instead of
    just cancelling) gives an early-stage task a real chance to exit cleanly
    first; a task that's genuinely still deep in blocking work times out here
    same as before, and the caller's own GPU-pool reset handles that case.
    """
    for t in tasks:
        if t is None:
            continue
        t.cancel()
    for t in tasks:
        if t is None:
            continue
        try:
            await asyncio.wait_for(t, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            # A background task that dies with a real error during teardown
            # must not abort the lifespan shutdown (#1174 class): uvicorn
            # would mark the whole application shutdown failed, skip the rest
            # of this cleanup (sentinel clear included), and the process exits
            # crash-shaped for what was a deliberate SIGTERM. The task's own
            # code already logged its failure.
            logger.warning(
                "Background task %r raised during shutdown (ignored)",
                t.get_name(), exc_info=True,
            )


# ── Deferred heavy startup (early-bind refactor) ──────────────────────────
# Phase A build: heavy imports + env restoration — thread-safe, no app
# mutation, runs in an executor thread (deferred) or inline (eager).
# Phase A finalize: router/mount registration — mutates the app, so it runs
# ON the event loop (deferred) with no awaits inside, making it atomic with
# respect to in-flight requests; the StartupGate keeps everything but
# /health + /startup/progress out until ready regardless.
# Phase B: the old lifespan startup body (DB, background services).

_phase_a_built = False
_phase_a_finalized = False
_router_modules: "list" = []
# Executor-thread lifecycle markers: cancelling the deferred-startup TASK
# cannot stop the THREAD already inside blocking import work, so shutdown
# waits (bounded) on _phase_a_finished when a build actually started —
# otherwise interpreter teardown races the imports (#1000 class).
_phase_a_started = threading.Event()
_phase_a_finished = threading.Event()


def _phase_a_build() -> None:
    """Everything heavy that used to run at module scope, same relative
    order per step. Idempotent. Imports are literal statements so
    PyInstaller's tracer still sees them (backend.spec unchanged).

    `_phase_a_finished` is set on EVERY exit — including the already-built
    early return — so a shutdown that observed `_phase_a_started` can never
    wait on an event nothing will set."""
    _phase_a_started.set()
    try:
        if not _phase_a_built:
            _phase_a_build_inner()
    finally:
        _phase_a_finished.set()


def _phase_a_build_inner() -> None:
    global _phase_a_built, ModelLoadInterruptedByShutdown
    global model_loads_begin_shutdown, idle_worker, preload_model
    global model_loads_reset_shutdown

    _startup_progress.begin_step("env_prefs")
    # Legacy (≤v0.3.7) Translation-LLM rows (env.TRANSLATE_*) must migrate
    # into the LLM provider store BEFORE the prefs→environ restore below —
    # once TRANSLATE_BASE_URL lands in os.environ it hijacks the LLM provider
    # selection for the whole session (#963). Real env vars are untouched.
    try:
        from services.llm_providers import migrate_legacy_translate_prefs
        migrate_legacy_translate_prefs()
    except Exception:
        pass  # never block startup on the migration; it retries next launch
    # Restore persisted env vars from prefs.json (Settings UI writes them
    # there so they survive backend restarts) — before any user code reads
    # os.environ, and never overriding an explicitly-set env var. Also
    # snapshots which keys an external source (shell, `.env`, Docker, …)
    # already provided, so a Settings control can tell the user their saved
    # value is being shadowed instead of silently promising it will apply
    # (core.prefs.is_env_shadowed — #1787 review fix).
    try:
        from core.prefs import _load as _load_all_prefs, restore_env
        restore_env(_load_all_prefs())
    except Exception:
        pass  # prefs.json missing or broken — fine on first run
    # yt-dlp user-update overlay: must run before anything imports yt_dlp so
    # a user-updated version wins over the locked wheel. Best-effort.
    try:
        from services.media_tools import activate_ytdlp_overlay
        activate_ytdlp_overlay()
    except Exception:
        pass
    # #1256: publish resolved ffmpeg/ffprobe dirs on PATH for dependencies
    # that shell out by bare name — after prefs restored any FFMPEG_PATH
    # override, before any engine loads.
    try:
        from services.ffmpeg_utils import ensure_media_tools_on_path
        ensure_media_tools_on_path()
    except Exception:
        pass  # best-effort: find_ffprobe() still resolves it for our callers

    _startup_progress.begin_step("native_preload")
    # cuDNN 8 preload for CTranslate2 (faster-whisper/WhisperX): native
    # dlopen/LoadLibrary, and the one hard ordering invariant of this phase —
    # it must run before any ctranslate2/torch import (#1371).
    try:
        from core.cudnn8 import preload as _preload_cudnn8
        _preload_cudnn8()
    except Exception:  # noqa: BLE001 — never block startup on a preload
        pass

    _startup_progress.begin_step("ml_imports")
    import torchaudio
    warnings.filterwarnings("ignore", category=UserWarning)
    torchaudio.set_audio_backend("soundfile")
    from utils import hf_progress
    # HF tqdm patch before any library import that can trigger
    # hf_hub_download (transformers, mlx_whisper, …).
    hf_progress.install()
    # Overall download aggregator's byte sink onto the patched tqdm (FDL-06).
    try:
        from utils import download_aggregator
        download_aggregator.install()
    except Exception:
        pass
    from services.model_manager import (  # noqa: E402
        ModelLoadInterruptedByShutdown as _MLIS,
        begin_shutdown as _mm_begin_shutdown,
        idle_worker as _mm_idle_worker,
        preload_model as _mm_preload_model,
        reset_shutdown_flag as _mm_reset_shutdown,
    )
    ModelLoadInterruptedByShutdown = _MLIS
    model_loads_begin_shutdown = _mm_begin_shutdown
    idle_worker = _mm_idle_worker
    preload_model = _mm_preload_model
    model_loads_reset_shutdown = _mm_reset_shutdown

    _startup_progress.begin_step("api_routes")
    from api.routers import (
        system,
        profiles,
        exports,
        generation,
        dub_core,
        dub_generate,
        dub_export,
        dub_translate,
        projects,
        glossary,
        engines,
        tools,
        stories,
        setup,
        gallery,
        archetypes,
        describe_voice,
        community,
        batch,
        watermark,
        events,
        capture,
        capture_ws,
        speech_platform,
        dictation,
        openai_compat,
        tts_stream,
        marketplace,
        personas,
        sonitranslate,
        audiobook,
        longform_jobs,
        pronunciation,  # Expressive-TTS Spec 01: user pronunciation dictionary
        settings as settings_router,  # Phase 1 AUTH-03: HF token save/clear/state
        media_tools as media_tools_router,  # Audio tools: ffmpeg/ffprobe/yt-dlp
        auth as auth_router,
        voice_convert,  # Studio Convert: speech-to-speech via ASR → TTS
    )
    from api.routers import mcp_bindings as _mcp_bindings_router  # noqa: E402
    from api.routers import workers as workers_router  # noqa: E402
    _router_modules.extend([
        system, profiles, exports, generation, voice_convert, dub_core, dub_generate,
        dub_export, dub_translate, projects, glossary, engines, tools,
        stories, setup, gallery, archetypes, describe_voice, community,
        batch, watermark, events, capture, capture_ws, speech_platform, dictation,
        openai_compat, tts_stream, marketplace, personas, sonitranslate,
        audiobook, longform_jobs, pronunciation, settings_router,
        media_tools_router, auth_router, _mcp_bindings_router, workers_router,
    ])
    # Download-acceleration state, once, for triage-from-logs (FDL-03).
    try:
        from api.routers.system import _fast_download_status as _fd_status
        _fd = _fd_status()
        _xet_ver = f" {_fd['xet_version']}" if _fd.get("xet_version") else ""
        logging.getLogger("omnivoice.model").info(
            "downloads: Xet %s (hf_xet%s installed=%s), high_perf=%s",
            "ACTIVE" if _fd["xet_active"] else "disabled → legacy LFS",
            _xet_ver, _fd["xet_installed"], _fd["high_performance"],
        )
    except Exception:
        pass
    _phase_a_built = True


def _phase_a_finalize() -> None:
    """Register everything Phase A imported. Mutates the app — must run on
    the event loop in deferred mode (no awaits inside → atomic wrt requests).
    Idempotent."""
    global _phase_a_finalized
    if _phase_a_finalized:
        return
    for _mod in _router_modules:
        app.include_router(_mod.router)

    # MCP server sub-mounted at /mcp; its session manager is stashed on
    # app.state for Phase B to run. Opt-out via OMNIVOICE_MCP_DISABLE=1;
    # best-effort (SystemExit included, #1156) so a missing mcp package
    # never breaks startup.
    if os.environ.get("OMNIVOICE_MCP_DISABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
        try:
            from mcp_server import mount_mcp
            mount_mcp(app)
        except (Exception, SystemExit) as _mcp_err:  # noqa: BLE001
            logging.getLogger("omnivoice.api").info(
                "MCP server not mounted (%s); /mcp disabled.", _mcp_err
            )

    app.mount("/audio", StaticFiles(directory=OUTPUTS_DIR), name="audio")
    app.mount("/voice_audio", StaticFiles(directory=VOICES_DIR), name="voice_audio")
    # Bundled demo assets — read-only, ships with the app, no network.
    _demo_dir = os.path.join(os.path.dirname(__file__), "assets", "samples")
    if os.path.isdir(_demo_dir):
        app.mount("/demo_audio", StaticFiles(directory=_demo_dir), name="demo_audio")

    # SPA shell LAST so the "/" StaticFiles mount can't shadow any router.
    _frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.exists(_frontend_path):
        # Runtime API-base override (Docker / reverse-proxy): inject
        # OMNIVOICE_PUBLIC_API_BASE into index.html; unset → untouched.
        from core.spa_inject import is_valid_public_api_base, inject_api_base

        _public_api_base = os.environ.get("OMNIVOICE_PUBLIC_API_BASE", "").strip().rstrip("/")
        _index_path = os.path.join(_frontend_path, "index.html")
        if _public_api_base and not is_valid_public_api_base(_public_api_base):
            logging.getLogger("omnivoice.api").warning(
                "OMNIVOICE_PUBLIC_API_BASE=%r is not a valid http(s) URL; ignoring.",
                _public_api_base,
            )
            _public_api_base = ""

        if _public_api_base and os.path.isfile(_index_path):
            from fastapi.responses import HTMLResponse

            def _index_with_api_base() -> "HTMLResponse":
                with open(_index_path, "r", encoding="utf-8") as _fh:
                    return HTMLResponse(inject_api_base(_fh.read(), _public_api_base))

            @app.get("/", include_in_schema=False)
            def _index_root():
                return _index_with_api_base()

            @app.get("/index.html", include_in_schema=False)
            def _index_html():
                return _index_with_api_base()

        app.mount("/", StaticFiles(directory=_frontend_path, html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        def _dev_fallback():
            return RedirectResponse(url="http://localhost:3901")

    # An early /docs or /openapi.json hit may have cached a schema without
    # the routers — bust it so the next request rebuilds the full one.
    app.openapi_schema = None
    _phase_a_finalized = True


def _disarm_startup_watchdog() -> None:
    try:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass  # diagnostics-only: a failed disarm must never affect startup


async def _deferred_startup(app: FastAPI) -> None:
    """Run the heavy phases behind the already-bound socket. A failure here
    has import-crash semantics: full traceback to stderr (→ backend_err.log,
    feeding the shell's crash forensics and venv-heal signature match #314),
    one beat for a last /startup/progress poll to capture the failed step,
    then a hard exit — the run sentinel stays uncleared so the next run
    attributes it (#1164)."""
    try:
        loop = asyncio.get_running_loop()
        # Mark the build as started BEFORE submission: an executor callable
        # can be queued-but-not-yet-running when shutdown samples the flag,
        # and a missed flag skips the thread join below — the exact teardown
        # race the join exists to close. shield() pairs with it: a cancel
        # landing while the callable is still queued must not cancel the
        # inner future (the callable would then never run and never set
        # `_phase_a_finished`, stalling shutdown's bounded join for its full
        # 20s) — shielded, the build always runs to completion and always
        # sets the event; the cancel still propagates to THIS task.
        _phase_a_started.set()
        await asyncio.shield(loop.run_in_executor(None, _phase_a_build))
        _phase_a_finalize()
        await _phase_b(app)
        _disarm_startup_watchdog()
        _startup_progress.mark_ready()
        logger.info("Deferred startup complete — all routes live.")
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — must convert to process death
        _step = _startup_progress.snapshot().get("step")
        _startup_progress.fail(f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        print(
            f"FATAL: backend startup failed during '{_step or 'startup'}': {exc}",
            file=sys.stderr, flush=True,
        )
        # Async, not time.sleep: the whole point of this beat is letting the
        # event loop serve one last /startup/progress poll with the failed
        # step — a blocking sleep would freeze the loop instead.
        await asyncio.sleep(1.0)
        os._exit(1)


async def _phase_b(app: FastAPI) -> None:
    """The old lifespan startup body: DB init + background services. Handles
    land on app.state so the shutdown block (which may run after a startup
    that never finished) reads them with getattr(..., None) guards."""
    from core.db import init_db

    _startup_progress.begin_step("db_migrate")
    init_db()
    # Network sharing is loopback-only by default; seed the (disabled) state
    # so the middleware and /system/network/state always have something to read.
    app.state.network_share = network_share.get_state()
    from api.routers.gallery import _init_gallery_db

    _init_gallery_db()
    # Seed a demo voice profile on first run (empty DB only).
    from core.onboarding import seed_sample_project
    seed_sample_project()
    # Any job still in pending/running at startup is orphaned — flip to failed
    # so the UI doesn't show a fake spinner.
    try:
        swept = job_store.sweep_orphans_on_startup()
        if swept:
            logger.info("Startup: marked %d orphaned job(s) as failed.", swept)
    except Exception:
        logger.exception("Startup job-sweep failed (non-fatal).")

    _startup_progress.begin_step("services_start")
    # Phase 1 Wave 3 — macOS Gatekeeper quarantine probe (#54). Informational
    # only; we never auto-run `xattr -cr`.
    try:
        from core import event_bus, gatekeeper_detect
        status = gatekeeper_detect.quarantine_status()
        if status.get("quarantined"):
            logger.warning(
                "Gatekeeper quarantine detected on app bundle %s — "
                "users must run `xattr -cr <bundle>` once. error_class=%s",
                status.get("bundle_path"),
                status.get("error_class"),
            )
            event_bus.emit(
                "system_error",
                {
                    "error_class": status.get("error_class"),
                    "bundle_path": status.get("bundle_path"),
                },
            )
    except Exception:
        logger.exception("Gatekeeper probe failed (non-fatal).")
    # #1174: arm model loads for THIS run — an in-process relaunch may carry a
    # stale shutting-down flag from a previous lifespan.
    model_loads_reset_shutdown()
    from services.model_manager import begin_watermark_pool_lifecycle
    begin_watermark_pool_lifecycle()
    app.state.idle_task = asyncio.create_task(idle_worker())
    app.state.worker_task = asyncio.create_task(task_manager.worker())
    # Warm the TTS model in the background so first /generate is instant.
    app.state.preload_task = asyncio.create_task(preload_model())
    # Dictation v2: capture ASR warms in the background BY DEFAULT (~30s
    # post-boot, skipped under 4 GB free RAM at warm time).
    app.state.capture_preload_task = None  # only assigned when it runs (#1000 class)
    if _env_flag("OMNIVOICE_PRELOAD_CAPTURE_ASR", default=True):
        async def _preload_capture_asr():
            await asyncio.sleep(_capture_preload_delay_s())
            if not _capture_preload_ram_ok():
                logger.info(
                    "Capture ASR preload skipped: <4GB free RAM; "
                    "dictation ASR will load on first use.")
                return
            loading_detail = None
            prev_loading_detail = None
            try:
                from services.model_manager import _gpu_pool, _loading_detail
                loading_detail = _loading_detail
                prev_loading_detail = dict(loading_detail)
                loop = asyncio.get_running_loop()
                def _warm():
                    from services.asr_backend import (
                        asr_model_missing_error,
                        get_capture_asr_backend,
                    )
                    # TTS-only install: no dictation ASR model on disk —
                    # skip; the first dictation prompts for the download.
                    if asr_model_missing_error(purpose="dictation") is not None:
                        logger.info(
                            "Capture ASR preload skipped: no ASR model installed; "
                            "dictation will offer a download on first use.")
                        return
                    loading_detail["sub_stage"] = "loading_asr"
                    loading_detail["detail"] = "Warming up ASR engine…"
                    backend = get_capture_asr_backend()
                    logger.info("Capture ASR backend selected: %s", backend.id)
                    if hasattr(backend, 'warmup'):
                        loading_detail["detail"] = f"Loading {backend.display_name}…"
                        backend.warmup()
                    loading_detail["sub_stage"] = "ready"
                    loading_detail["detail"] = "ASR engine ready"
                await loop.run_in_executor(_gpu_pool, _warm)
            except Exception as e:
                if loading_detail is not None and loading_detail.get("sub_stage") == "loading_asr":
                    loading_detail.clear()
                    loading_detail.update(prev_loading_detail or {})
                logger.warning("Capture ASR preload skipped: %s", e)
        app.state.capture_preload_task = asyncio.create_task(_preload_capture_asr())
    else:
        logger.info("Capture ASR preload disabled; dictation ASR will load on first use.")

    # Watermark: warm the AudioSeal generator in the background so the first
    # mark_synthetic doesn't serialize the audioseal import + model load
    # inside the first synthesis (measured ~42 s inline on a cold filesystem,
    # 2026-08-17 macOS report — 3 s short of the client's 90 s timeout).
    # Small model on CPU; deferred a few seconds past the capture-ASR warm so
    # the two cold imports don't contend for the same disk, and no RAM guard
    # is needed. Runs on the watermark pool — where the model is used — not
    # the shared default executor.
    if _env_flag("OMNIVOICE_PRELOAD_WATERMARK", default=True):
        async def _preload_watermark():
            await asyncio.sleep(_watermark_preload_delay_s())
            loop = asyncio.get_running_loop()
            from services import watermark as _watermark

            # Gate BEFORE touching get_watermark_pool(): the pool is lazy so
            # hosts with watermarking disabled never spawn its thread, and
            # creating it unconditionally would break that invariant. The
            # race with a first embed is benign — pool creation is itself
            # lock-guarded.
            if not _watermark.will_mark():
                logger.debug("Watermark preload skipped (disabled or audioseal absent)")
                return
            from services.model_manager import get_watermark_pool

            # Default startup may warm an existing local checkpoint but may
            # not fetch one. Only an explicit user opt-in permits a download.
            raw_preload = os.environ.get("OMNIVOICE_PRELOAD_WATERMARK", "")
            allow_download = raw_preload.strip().lower() in {"1", "true", "yes", "on"}

            try:
                await loop.run_in_executor(
                    get_watermark_pool(),
                    lambda: _watermark.prefetch_generator(
                        allow_download=allow_download
                    ),
                )
            except Exception:
                # prefetch_generator swallows its own errors; this guards the
                # setup half (imports, pool construction) so a broken warm-up
                # is visible now, not as an unretrieved exception at shutdown.
                logger.warning("Watermark preload task failed", exc_info=True)

        app.state.watermark_preload_task = asyncio.create_task(_preload_watermark())

    # ── MCP session manager (Wave 2.2) ────────────────────────────────────
    # Run it in its OWN task owning the full enter→exit lifecycle (anyio
    # task-affinity, see _serve_mcp); only wait, with a timeout, for ready —
    # a hang (observed on M1, #632) can never wedge startup.
    _sm = getattr(app.state, "mcp_session_manager", None)
    mcp_task, mcp_stop, mcp_mounted = await _start_mcp_session_manager(
        _sm, timeout=_mcp_start_timeout_s()
    )
    app.state.mcp_task = mcp_task
    app.state.mcp_stop = mcp_stop
    if mcp_mounted:
        logger.info("MCP server mounted at /mcp")
    # Remote GPU workers (opt-in). Starts nothing unless enabled.
    try:
        from worker import service as worker_service
        await worker_service.start_if_enabled()
    except Exception:
        logger.exception("Remote worker startup failed (continuing without it)")

    # The other side of the same feature: worker mode connects out.
    try:
        from worker import agent as worker_agent
        await worker_agent.start_if_worker_mode()
    except Exception:
        logger.exception("Worker agent startup failed (continuing without it)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.dependencies import validate_server_admin_key

    validate_server_admin_key()
    # Startup watchdog (#632): a silent hang during startup (e.g. a model-load /
    # MCP deadlock on some platforms) means "Application startup complete" never
    # logs and the app sits forever with no error. If startup hasn't finished
    # within the window, dump every thread's stack to stderr (→ backend_err.log)
    # so the hang point is captured instead of invisible. Cancelled the instant
    # startup completes, so a normal (even slow-download) boot never trips it.
    # Tune with OMNIVOICE_STARTUP_WATCHDOG_S (seconds; 0 disables). Best-effort —
    # never let the diagnostic itself break startup.
    _watchdog_armed = False
    try:
        import faulthandler
        _wd = float(os.environ.get("OMNIVOICE_STARTUP_WATCHDOG_S", "300"))
        if _wd > 0 and hasattr(faulthandler, "dump_traceback_later"):
            faulthandler.dump_traceback_later(_wd, repeat=False, exit=False)
            _watchdog_armed = True
            logger.info("Startup watchdog armed: thread dump if startup exceeds %.0fs (#632).", _wd)
    except Exception:
        pass

    # Run-sentinel forensics (#1164): detect an uncleanly-ended previous run
    # (OOM kill, hard crash — anything that skipped the shutdown block) and
    # write the crash record BEFORE any heavy init, so even a crash later in
    # THIS startup is attributed by the next run. Best-effort by contract.
    # (Now strictly earlier than before the early-bind refactor: it used to
    # run after the heavy imports, so a crash DURING them went unattributed.)
    from core import run_sentinel
    _crash_record = None
    try:
        _crash_record = run_sentinel.detect_unclean_shutdown()
        run_sentinel.write_sentinel()
    except Exception:
        logger.exception("Run-sentinel startup failed (non-fatal).")
    # Opt-in lifecycle analytics (core/analytics.py): install/update/crash
    # events. A no-op without the user's explicit consent AND a build token.
    # The run-sentinel record above is the ONE authoritative crash source —
    # the desktop shell's markers cover the same deaths, so the frontend
    # never emits a crash event (no double-count).
    try:
        from core import analytics
        analytics.record_startup_lifecycle(_crash_record)
    except Exception:
        logger.exception("Analytics startup lifecycle failed (non-fatal).")

    if _EAGER:
        # Pytest / opt-in embedders: today's exact behavior — everything done
        # before serving. Phase A already ran at module import (no-ops here).
        _phase_a_build()
        _phase_a_finalize()
        await _phase_b(app)
        if _watchdog_armed:
            _disarm_startup_watchdog()
        _startup_progress.mark_ready()
    else:
        # Server runs: the socket binds the moment this yields — /health and
        # /startup/progress answer while the heavy phases run behind the
        # StartupGate. The watchdog stays armed until the deferred task
        # disarms it, so a hang inside Phase A/B still gets its thread dump.
        app.state.startup_task = asyncio.create_task(_deferred_startup(app))
    yield
    # ── Graceful shutdown (SIGTERM from Tauri, Ctrl+C, etc.) ────────────
    # May run after a startup that never finished (SIGTERM mid-Phase-A/B), so
    # every handle is read from app.state with a None default and every
    # deferred-phase name is guarded.
    #
    # FIRST: stop the deferred startup task, bounded like the preload waits
    # below (#1000 class): cancel() can't stop an executor thread already
    # inside blocking import work, so give Phase A a real chance to complete
    # before the interpreter starts tearing down module state under it.
    _startup_task = getattr(app.state, "startup_task", None)
    if _startup_task is not None and not _startup_task.done():
        _startup_task.cancel()
        try:
            await asyncio.wait_for(_startup_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass  # bounded wait — a wedged startup must not hang shutdown
        except Exception:
            pass  # the task's own failure path already logged and exited
    # Cancelling the task can't stop the executor THREAD already inside
    # Phase A's blocking imports — wait (bounded) for the thread itself so
    # interpreter teardown doesn't race a mid-`import torch` (#1000 class).
    # Only when a build actually started and hasn't finished, so a shutdown
    # before Phase A began pays nothing.
    if _phase_a_started.is_set() and not _phase_a_finished.is_set():
        await asyncio.to_thread(_phase_a_finished.wait, 20.0)
    # Stop accepting remote work early in shutdown: a worker that reconnects
    # to a half-torn-down control plane is worse than one that simply finds it
    # gone and backs off.
    try:
        from worker import agent as worker_agent
        await worker_agent.stop()
    except Exception:
        logger.exception("Worker agent shutdown failed")
    try:
        from worker import service as worker_service
        await worker_service.stop()
    except Exception:
        logger.exception("Remote worker shutdown failed")
    logger.info("Shutdown: cleaning up…")
    # Flip model_manager into shutdown mode, so a model load in flight (or
    # still queued) on a GPU-pool thread classifies executor rejections as a
    # benign cancelled-load instead of a crash-shaped failure (#1174). None
    # when Phase A never completed — nothing armed, nothing to flip.
    if model_loads_begin_shutdown is not None:
        model_loads_begin_shutdown()
    # Stop MCP first — signal its task to exit its own anyio context (correct
    # task-affinity), then bound the wait so a wedged manager can't hang exit.
    mcp_stop = getattr(app.state, "mcp_stop", None)
    mcp_task = getattr(app.state, "mcp_task", None)
    if mcp_stop is not None:
        mcp_stop.set()
    if mcp_task is not None:
        try:
            await asyncio.wait_for(mcp_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass
    # preload_task/capture_preload_task matter most here (#1000 class): a quit
    # mid-preload used to fall straight through to "Shutdown: done." while the
    # model load was still running on a GPU-pool thread — cancel() can't stop
    # a thread already inside blocking import/load work, so the process
    # reported a clean exit while that background thread was still mid-
    # `import transformers`, and got torn down by interpreter finalization
    # instead. That surfaced as a misleading "Could not import module
    # 'AutoFeatureExtractor'" — transformers' own generic lazy-import wrapper,
    # not a real dependency problem. Awaiting here lets an early-stage load
    # (still importing, not yet mid weight-download) finish cleanly before we
    # report done; a load that's genuinely deep into a multi-GB download still
    # times out — _reset_gpu_pool() below abandons it either way.
    #
    # 20s, not the original 3s (code-review finding post-merge): a cold
    # transformers import alone can take longer than 3s on a slow disk or a
    # first-ever launch, so the original bound left a real residual window —
    # cancellation detaches the asyncio task, but the underlying OS thread
    # keeps running past it, and shutdown could still report "done" while
    # that thread was alive. Python cannot forcibly kill a running thread, so
    # no finite bound eliminates this outright — 20s just shrinks the window
    # from "any preload" to "an unusually slow cold-import," which is the
    # practical ceiling before a longer shutdown itself becomes the
    # complaint. A thread that's still running past 20s was never going to
    # finish in a shutdown-appropriate timeframe regardless.
    await _cancel_and_await_tasks(
        getattr(app.state, "idle_task", None),
        getattr(app.state, "worker_task", None),
        getattr(app.state, "preload_task", None),
        getattr(app.state, "capture_preload_task", None),
        getattr(app.state, "watermark_preload_task", None),
        timeout=20.0,
    )
    # The watermark warm-up runs on its dedicated 1-worker pool. Cancellation
    # detaches the asyncio future but cannot kill a thread inside AudioSeal,
    # so drain it fully before lifespan teardown reports completion.
    try:
        from services.model_manager import shutdown_watermark_pool as _wm_drain

        _wm_drain()
    except Exception:
        # Best-effort drain: a failure here must not abort the remaining
        # shutdown steps (model unload, MCP teardown) below.
        logger.warning("Watermark pool drain failed at shutdown", exc_info=True)
    # Unload the model and free GPU memory
    try:
        import services.model_manager as mm
        if mm.unload_shared_model():
            logger.info("Shutdown: model unloaded.")
        # Still unconditional: there are allocator caches to hand back even when
        # no model was resident.
        mm.free_vram()
        # Abandon a still-running preload's GPU-pool thread (Python can't kill
        # a thread mid blocking call) so it can't outlive this shutdown block
        # holding a reference into module state that's about to be torn down.
        mm._reset_gpu_pool()
    except Exception:
        pass
    # Run GC to release any remaining references
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    # Close shared httpx connection pool
    try:
        from api.http_client import close_http_client
        await close_http_client()
    except Exception:
        pass
    # Last thing on a clean shutdown: retire the run sentinel so the next
    # startup doesn't misread this exit as a crash (#1164). If clearing fails,
    # retain the sentinel and report a degraded shutdown truthfully.
    try:
        sentinel_cleared = run_sentinel.clear_sentinel()
    except Exception:
        sentinel_cleared = False
    if sentinel_cleared:
        logger.info("Shutdown: done.")
    else:
        logger.warning("Shutdown completed, but the run sentinel could not be cleared")


from core.version import APP_VERSION  # single source of truth (pyproject metadata)

app = FastAPI(
    title="VoiceStudio API",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,       # Disabled — replaced by Scalar at /docs
    redoc_url=None,      # Disabled — Scalar covers this
)


@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    """Interactive API documentation powered by Scalar."""
    if get_scalar_api_reference is None:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "API docs unavailable: scalar-fastapi is not installed "
                          "in the backend environment (#307)."
            },
        )
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )


def _cors_headers_for(request: Request) -> "dict[str, str]":
    """Allowed-origin headers for a hand-built error response.

    CORSMiddleware doesn't always get a shot at `exception_handler`-created
    responses, which leaves the browser reporting the error as a bare CORS
    failure instead of surfacing the real `detail`. Every error response this
    module builds must go through here — a 503 whose actionable message the
    browser discards is no better than the 500 it replaced.
    """
    origin = request.headers.get("origin", "")
    if origin and (origin in _allowed or "*" in _allowed):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


# Largest `input` echo we put in a 422 body. Enough to see which field is
# wrong, far too small to mirror an upload back at the client or the log.
_VALIDATION_INPUT_MAX = 200


def _safe_validation_input(value):
    """Render a pydantic error's ``input`` as something JSON-encodable.

    FastAPI's default handler runs ``jsonable_encoder(exc.errors())``, and for
    a body-level validation failure ``errors()[i]["input"]`` is the RAW REQUEST
    BODY. Two bugs fall out of that, both live before this handler existed:

    1. ``jsonable_encoder`` decodes ``bytes`` as UTF-8, so ANY binary body
       (a multipart audio upload posted to a JSON-body route — easy to do by
       hand, and what several MCP/OpenAI-compat clients do on a bad path)
       raised ``UnicodeDecodeError`` *inside the error handler*. The client got
       a 500 where the request was merely malformed, and the escaping
       exception dumped the entire body into omnivoice.log — a 145 KB WAV
       wrote ~500 KB of log. For a local-first voice app that is user audio
       landing on disk in a file we invite people to paste into bug reports.
    2. Even when decodable, the whole body was mirrored into the response.

    So: bytes are never decoded (only their length is reported), and every
    echoed value is truncated. Pure — unit-testable without a request.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes of binary data>"
    if isinstance(value, str) and len(value) > _VALIDATION_INPUT_MAX:
        return value[:_VALIDATION_INPUT_MAX] + f"… (+{len(value) - _VALIDATION_INPUT_MAX} chars)"
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 for a malformed request — never a 500, never an audio-sized body.

    Mirrors FastAPI's default shape (``{"detail": [...]}``) so existing clients
    and the frontend's error parsing are unaffected; only ``input`` is
    sanitized (see :func:`_safe_validation_input`). Goes through
    ``_cors_headers_for`` like every other hand-built error response here, so
    the browser sees the real detail instead of a bare CORS failure.
    """
    safe = []
    for err in exc.errors():
        err = dict(err)
        if "input" in err:
            err["input"] = _safe_validation_input(err["input"])
        # `ctx` can carry the triggering exception object, which is not
        # JSON-encodable either.
        if "ctx" in err:
            err["ctx"] = {k: str(v) for k, v in dict(err["ctx"]).items()}
        safe.append(err)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": safe}),
        headers=_cors_headers_for(request),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Client disconnected mid-stream (browser canceled a <video>/range fetch).
    # The response is already partially sent — trying to wrap it in a 500 just
    # produces a second protocol error. Log a one-liner and bail.
    exc_name = type(exc).__name__
    if exc_name in (
        "LocalProtocolError",
        "ClientDisconnect",
    ) or "Content-Length" in str(exc):
        logger.info("Client disconnect during %s (%s)", request.url, exc_name)
        return Response(status_code=499)
    # The backend is on its way out and a request asked for a model load
    # (#1276). #1174 already made this benign for the *background preload*,
    # but a user-initiated request fell through to the generic 500 path below
    # — crash log, ERROR traceback, journal entry — so quitting the app while
    # a generate was queued surfaced "500 Internal Server Error: model load
    # skipped: backend shutting down" and offered to file a bug for it.
    #
    # Nothing failed: the process is exiting. 503 + Retry-After is what a
    # shutting-down server owes a client, and it keeps this out of the
    # crash/bug-report pipeline entirely.
    #
    # Matched by isinstance OR class name: `services.model_manager` can be
    # imported under two module names (`main`/`backend.main` on different
    # sys.path roots, and the frozen build's own layout), which makes two
    # distinct class objects and breaks a bare isinstance. The name check is
    # the durable half — don't "simplify" it away.
    if (
        ModelLoadInterruptedByShutdown is not None
        and isinstance(exc, ModelLoadInterruptedByShutdown)
    ) or exc_name == "ModelLoadInterruptedByShutdown":
        # `.path`, not the full URL — a query string can carry tokens and
        # newlines, and neither belongs in a log line.
        logger.info(
            "Model load skipped during shutdown for %s — benign.", request.url.path
        )
        return JSONResponse(
            status_code=503,
            content={
                # The [shutting_down] marker is what the UI keys off to skip the
                # "Report" action (the same convention as [clone_ref_unusable]).
                # NOT the bare 503 status: 503 is also how a real engine-load
                # timeout and an unavailable engine are reported, and those are
                # genuinely reportable bugs — suppressing the report button for
                # every 503 would silence exactly the class users need to file.
                "detail": (
                    "[shutting_down] VoiceStudio is shutting down, so it didn't "
                    "start loading the model. Reopen the app and try again."
                )
            },
            headers={"Retry-After": "5", **_cors_headers_for(request)},
        )
    try:
        # Serialize writes so concurrent unhandled exceptions don't interleave frames.
        with _crash_log_lock, open(CRASH_LOG_PATH, "a", encoding="utf-8", errors="backslashreplace") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---\n")
            f.write(f"Request: {request.url}\n")
            f.write(traceback.format_exc())
    except Exception:
        logger.exception("Failed to write crash log")
    logger.exception("Unhandled exception for %s", request.url)
    # Structured journal entry (dedup + error_class) — feeds /system/errors/
    # recent, the diagnostic bundle, and the bug-report pipeline. record()
    # never raises; a journal failure must not shadow the real error.
    from core import error_journal
    _entry = error_journal.record(
        exc, route=str(request.url.path), trace=traceback.format_exc()
    )
    headers: dict[str, str] = _cors_headers_for(request)
    # #874: a model download that failed because the CONFIGURED Hugging Face
    # mirror (HF_ENDPOINT) is unreachable used to leak the raw transformers
    # message ("We couldn't connect to 'https://hf-mirror.com' …") as the 500
    # detail with no next step. #959: same story for the SOCKS-proxy class
    # ("Using SOCKS proxy, but the 'socksio' package is not installed").
    # Appending the shared hints HERE covers every route that can leak a
    # model-load/download error (generate, dub, archetypes, …), not just TTS
    # generate. append_hint is a no-op for every other error and never raises.
    from core.public_errors import public_exception_response

    content = public_exception_response(
        exc,
        fallback="VoiceStudio hit an internal error; check the backend log for details.",
    )
    content["error_class"] = _entry.get("error_class")
    return JSONResponse(
        content,
        status_code=500,
        headers=headers,
    )


_SHELL_PATHS = {"/", "/index.html", "/favicon.ico", "/health"}

# Paths that answer while the deferred startup is still running.
_STARTUP_EXEMPT = {"/health", "/startup/progress"}


class StartupGateMiddleware:
    """503 everything except /health + /startup/progress until the deferred
    startup completes. Two jobs: honest not-ready signaling (the [starting]
    marker keeps the UI from offering "Report" for it, same convention as
    [shutting_down]), and route-mutation safety — no request can reach the
    router while _phase_a_finalize is still adding routes, because the ready
    flag flips only after finalize + Phase B complete.

    Registered FIRST → innermost, so its 503s pass out through Bearer → CORS
    → BackendMarkerMiddleware and arrive with CORS headers + the
    x-omnivoice-backend marker. Pure ASGI; permanently inert once ready.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _startup_progress.is_ready() or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and scope.get("path") in _STARTUP_EXEMPT:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()  # consume websocket.connect
            return await send({"type": "websocket.close", "code": 1008})
        _step, _label = _startup_progress.current_step()
        resp = JSONResponse(
            status_code=503,
            content={
                "detail": (
                    f"[starting] VoiceStudio is still starting "
                    f"({_label or 'initializing'}). Retry shortly."
                ),
                "step": _step,
            },
            headers={"Retry-After": "2"},
        )
        return await resp(scope, receive, send)


class NetworkAccessMiddleware:
    """When a share PIN is set, require it for non-loopback clients on API
    routes. Inert when no PIN (default + docker deploys). Loopback (incl.
    Tailscale-proxied) always bypasses; the SPA shell is always served so the
    PIN gate UI can load.

    Pure ASGI (not BaseHTTPMiddleware) so it never buffers the response body.
    BaseHTTPMiddleware collects StreamingResponse/SSE bodies before forwarding,
    which makes PIN'd LAN clients on streaming endpoints (dictation SSE, tts
    streaming, /system/logs/stream) laggy. As a plain ASGI app we forward
    `send` untouched on the pass-through paths and only wrap it to inject the
    Set-Cookie header — the body still streams chunk-by-chunk."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        ns = getattr(request.app.state, "network_share", None)
        pin = getattr(ns, "pin", None) if ns else None
        if not pin:
            return await self.app(scope, receive, send)
        client = scope["client"][0] if scope.get("client") else None
        if is_local_host(client):
            return await self.app(scope, receive, send)
        path = scope["path"]
        if (
            path in _SHELL_PATHS
            or path.startswith("/assets/")
            or path.startswith("/favicon")
            or path == "/api/auth/session"
        ):
            return await self.app(scope, receive, send)
        supplied = (
            request.headers.get("x-omnivoice-pin")
            or request.query_params.get("pin")
            or request.cookies.get("ov_pin")
            or ""
        )
        if not credential_matches(supplied, pin):
            resp = JSONResponse({"detail": "PIN required"}, status_code=401)
            return await resp(scope, receive, send)
        # Valid PIN. Set the cookie by wrapping send to inject Set-Cookie on the
        # http.response.start message — without ever materialising the body.
        if request.cookies.get("ov_pin") != pin:
            async def send_with_cookie(message):
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers.append("set-cookie", f"ov_pin={pin}; Path=/; SameSite=Lax")
                await send(message)

            return await self.app(scope, receive, send_with_cookie)
        return await self.app(scope, receive, send)


#: Header stamped on EVERY response so a client can tell this backend apart
#: from whatever else might answer at the same URL (#1385). A rehosted UI
#: whose API requests land on a static host or a proxy with no API route gets
#: that host's 404 page; the frontend needs an authoritative "this really is
#: a VoiceStudio backend" signal rather than guessing from the body shape,
#: since a proxy can return JSON too. Value is the version, which is also
#: useful when a desktop app talks to an older remote backend.
BACKEND_MARKER_HEADER = "x-omnivoice-backend"


class BackendMarkerMiddleware:
    """Stamp ``x-omnivoice-backend: <version>`` on every response.

    Pure ASGI, same reasoning as the gates below it: wrapping only the
    ``http.response.start`` message keeps streaming bodies streaming.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_marker(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault(BACKEND_MARKER_HEADER, _backend_marker_value())
                if str(scope.get("path", "")).startswith("/api/auth/"):
                    headers["cache-control"] = "no-store"
            await send(message)

        return await self.app(scope, receive, send_with_marker)


def _backend_marker_value() -> str:
    # ImportError only: the marker's JOB is to be present, so a frozen build
    # that cannot import the version module still answers "yes, a backend".
    # Anything else is a real defect and should surface, not be masked.
    try:
        from core.version import APP_VERSION

        return str(APP_VERSION)
    except ImportError:
        return "unknown"


class BearerKeyMiddleware:
    """When OMNIVOICE_API_KEY is set, non-loopback clients must present it on
    every HTTP + WebSocket request. Durable API-key transports remain compatible;
    the first-party UI may instead present a short-lived admin session. The
    middleware never reflects a presented master key into browser state.

    Loopback always bypasses — the desktop default is unchanged — and the SPA
    shell paths stay reachable so a remote UI can load and show what's wrong.

    Inert when the env var is unset (the default). Pure ASGI for the same
    no-buffering reason as NetworkAccessMiddleware above. Plain-HTTP caveat
    is documented in docs/remote-gpu.md: the key is sniffable outside a
    WireGuard (Tailscale) or TLS (tailscale serve) transport.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        key = remote_api_key() or ""
        if not key:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope["type"] == "http" and (
            path in _SHELL_PATHS
            or path.startswith("/assets/")
            or path.startswith("/favicon")
            or path == "/api/auth/session"
        ):
            return await self.app(scope, receive, send)

        from starlette.requests import HTTPConnection

        conn = HTTPConnection(scope)
        principal = principal_for(conn)
        if principal.kind not in {
            PrincipalKind.LOOPBACK,
            PrincipalKind.TRUSTED_NETWORK,
            PrincipalKind.API_KEY,
            PrincipalKind.ADMIN_SESSION,
        }:
            if scope["type"] == "websocket":
                # Reject the handshake; 1008 = policy violation.
                await receive()  # consume websocket.connect
                await send({"type": "websocket.close", "code": 1008})
                return
            resp = JSONResponse({"detail": "API key required"}, status_code=401)
            return await resp(scope, receive, send)
        if (
            scope["type"] == "http"
            and str(scope.get("method", "GET")).upper() not in SAFE_HTTP_METHODS
            and principal.transport
            in {CredentialTransport.COOKIE, CredentialTransport.LEGACY_COOKIE}
            and not cookie_csrf_allowed(conn)
        ):
            resp = JSONResponse(
                {"detail": "browser origin rejected"},
                status_code=403,
            )
            return await resp(scope, receive, send)
        if (
            scope["type"] == "websocket"
            and principal.transport
            in {
                CredentialTransport.COOKIE,
                CredentialTransport.LEGACY_COOKIE,
                CredentialTransport.WS_TICKET,
            }
            and not origin_allowed(conn)
        ):
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        return await self.app(scope, receive, send)


# UI dev-server port — single-sourced from OMNIVOICE_UI_PORT so a user who
# moves the Vite dev server off 3901 still gets a matching CORS allow-list.
def _ui_port() -> int:
    raw = os.environ.get("OMNIVOICE_UI_PORT")
    if raw is None:
        return 3901
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 3901


_ui = _ui_port()
_allowed = os.environ.get(
    "OMNIVOICE_ALLOWED_ORIGINS",
    f"http://localhost:{_ui},http://127.0.0.1:{_ui},tauri://localhost,http://tauri.localhost",
).split(",")

# Registered FIRST → innermost: the startup gate holds every request except
# the two probe paths until the deferred startup completes (and is a no-op
# forever after).
app.add_middleware(StartupGateMiddleware)

# Inert unless a PIN is set. CORS is registered after both auth gates below so
# Starlette places it outside them: browser preflights carry no credentials and
# must reach CORS before either gate can reject the request.
app.add_middleware(NetworkAccessMiddleware)

# Remote-backend bearer gate (parity program Wave 2.3 / §R2). Inert unless
# OMNIVOICE_API_KEY is set. Distinct from the PIN gate above: the PIN guards
# casual LAN-share guests for one session; the API key is the durable
# credential for running this backend remotely (Tailscale / Docker GPU box).
# Covers WebSockets too — the PIN gate never did, because every WS endpoint
# carried its own loopback guard; remote mode is exactly the case where a
# keyed non-loopback client must reach them.
app.add_middleware(BearerKeyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The marker must be readable cross-origin too — a browser UI served from
    # another origin is exactly the deployment that needs to tell "the backend
    # answered 404" from "something else answered 404" (#1385).
    expose_headers=["Content-Disposition", BACKEND_MARKER_HEADER],
)

# Registered LAST, which in Starlette means OUTERMOST — so the marker lands on
# every response. CORS is immediately inside it and outside both auth gates, so
# preflights and gate-generated 401s retain the browser contract. The marker's
# absence lets a client conclude that the responder is not VoiceStudio (#1385).
app.add_middleware(BackendMarkerMiddleware)

# Register canonical audio MIME types before any StaticFiles mount.
# Python's `mimetypes.guess_type()` returns `audio/x-wav` for `.wav` and
# `audio/x-flac` for `.flac` on most platforms — these are vendor-experimental
# (x- prefix, never IANA-registered). macOS Chrome/Safari MIME-sniff leniently
# via CoreAudio so playback works there, but Linux Chrome/Firefox (FFmpeg) and
# Android Chrome (ExoPlayer) strictly honor the declared type and treat the
# x- variants as download-only — manifesting as the play button silently
# doing nothing in the browser app while working in the Tauri desktop shell.
# `audio/wav` / `audio/flac` are the IANA-canonical types.
# Ref: https://www.iana.org/assignments/media-types/media-types.xhtml#audio
import mimetypes as _mimetypes
_mimetypes.add_type("audio/wav",  ".wav")
_mimetypes.add_type("audio/flac", ".flac")

# ── Health check ────────────────────────────────────────────────────────
# Used by Docker health checks, load balancers, and the Tauri desktop shell.
# Answers from the moment the socket binds: 503 with the current startup step
# while the deferred phases run (curl -f / the shell's probes treat that as
# not-ready, exactly like the connection-refused it replaces), the full body
# once ready. No torch import pre-ready — it would block 10-20s on the very
# import whose progress this endpoint exists to report.
@app.get("/health")
def health():
    if not _startup_progress.is_ready():
        _step, _label = _startup_progress.current_step()
        return JSONResponse(
            status_code=503,
            content={
                "status": "starting",
                "step": _step,
                "label": _label,
                "version": APP_VERSION,
            },
            headers={"Retry-After": "2"},
        )
    import torch

    device = "cpu"
    if torch.cuda.is_available():
        device = f"cuda ({torch.cuda.get_device_name(0)})"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"

    return {"status": "ok", "device": device, "version": APP_VERSION}


# ── Startup progress ────────────────────────────────────────────────────
# Always 200, even post-ready — the one endpoint whose job is to say what the
# backend is doing before it can serve, so "starting at step X" / "ready" /
# "failed at step X" are distinguishable from "dead" (#1393 class). The shell
# requires the x-omnivoice-backend marker header (stamped by the outermost
# middleware) before trusting this body.
@app.get("/startup/progress", include_in_schema=False)
def startup_progress_endpoint():
    return {**_startup_progress.snapshot(), "app_version": APP_VERSION}


# Router registration, the MCP mount, the /audio|/voice_audio|/demo_audio
# mounts, and the SPA shell all happen in _phase_a_finalize — immediately
# below for eager (pytest) runs, on the event loop after the deferred build
# for server runs.

if _EAGER:
    _phase_a_build()
    _phase_a_finalize()
    # Lifespan-less TestClient(app) call sites (~100 of them) expect the
    # fully-built app with every route live — mark ready at import so the
    # StartupGate stays out of their way, exactly like today. The lifespan's
    # eager branch re-runs these as no-ops.
    _startup_progress.mark_ready()


if __name__ == "__main__":
    import argparse
    import sys
    import threading
    import time
    import urllib.request
    import uvicorn

    parser = argparse.ArgumentParser(prog="omnivoice-backend")
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Boot the server, poll /health, exit 0 on success / 1 on timeout. "
             "Used by the release-time installer smoke step in .github/workflows/release.yml.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run the self-check suite (device, ffmpeg, HF token, disk, engines, "
             "network) without starting the server. Exit 0 if healthy, 1 if any "
             "check fails. Output is scrubbed — safe to paste into a GitHub issue.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="With --diagnose: also load the active TTS engine and synthesize a "
             "short utterance. Catches 'installed but broken'. May cold-load the "
             "model (minutes + a large download on a fresh install).",
    )
    args, _unknown = parser.parse_known_args()

    if args.diagnose:
        # The checks read the user's restored env (HF token, FFMPEG_PATH) and
        # the ML stack — both now live in Phase A, which a server run defers
        # but a one-shot CLI must run up front.
        _phase_a_build()
        from core.diagnose import run_diagnostics, format_text

        _report = run_diagnostics(deep=args.deep)
        print(format_text(_report), flush=True)
        sys.exit(0 if _report["summary"]["ok"] else 1)

    # Single-sourced from OMNIVOICE_PORT so the bare `python main.py` path and
    # `--health-check` agree with the Rust sidecar / uvicorn-CLI `--port`.
    _port = network_share.backend_port()

    if args.health_check:
        HEALTH_URL = f"http://127.0.0.1:{_port}/health"
        # 180, not 60: /health now answers 503 from ~1s after spawn and only
        # flips 200 once the DEFERRED heavy init (torch import, routers, DB)
        # completes — the old 60s window measured serving-time only, because
        # module import used to happen before the poll loop even started.
        TIMEOUT_S = 180
        INTERVAL_S = 5

        def _serve():
            # log_level="warning" silences the per-request access log spam
            # so the smoke output stays readable in GH Actions.
            uvicorn.run(app, host="127.0.0.1", port=_port, log_level="warning")

        t = threading.Thread(target=_serve, daemon=True)
        t.start()

        elapsed = 0
        while elapsed < TIMEOUT_S:
            try:
                with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
                    if resp.status == 200:
                        print(f"OK — /health responded 200 after {elapsed}s", flush=True)
                        sys.exit(0)
            except Exception:
                pass
            time.sleep(INTERVAL_S)
            elapsed += INTERVAL_S

        print(
            f"FAIL — /health did not respond 200 within {TIMEOUT_S}s",
            file=sys.stderr, flush=True,
        )
        sys.exit(1)

    # Distinct exit code for "the port was already taken" (#1223), so the
    # desktop shell can tell that apart from a crash without parsing an
    # OS-translated error string. Kept out of the 0-2 range the interpreter
    # itself uses, and mirrored in frontend/src-tauri/src/backend.rs.
    _EXIT_PORT_IN_USE = 78  # EX_CONFIG, sysexits.h

    # Port 3900 picked to dodge common 8000 conflicts (Django/Rails/Jupyter).
    # Rust sidecar launcher in lib.rs::BACKEND_PORT must stay in sync.
    #
    # SECURITY: default to loopback (127.0.0.1) so the API isn't reachable
    # from the LAN out of the box. VoiceStudio ships no authentication; binding
    # to 0.0.0.0 by default would expose every router on this process to any
    # host on the user's network. Docker images that need to publish the port
    # set OMNIVOICE_BIND_HOST=0.0.0.0 explicitly (see deploy/docker-compose.yml)
    # — the host-side port mapping is what enforces 127.0.0.1-only there.
    _bind_host = os.environ.get("OMNIVOICE_BIND_HOST", "127.0.0.1")

    def _port_taken(host: str, port: int) -> "OSError | None":
        """The EADDRINUSE error a bind would raise, or None if the port is free.

        Mirrors uvicorn's own socket options — notably SO_REUSEADDR off
        Windows — so this can't report "taken" for a TIME_WAIT socket uvicorn
        would happily bind. Any non-EADDRINUSE failure returns None: this is a
        diagnostic, and uvicorn must remain the authority on whether the real
        bind succeeds.
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if sys.platform != "win32":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError as exc:
                in_use = exc.errno in (48, 98, 10048) or getattr(
                    exc, "winerror", None
                ) == 10048
                return exc if in_use else None
        return None

    def _fail_port_in_use(exc: "OSError | None") -> None:
        print(
            f"FATAL: port {_port} is already in use — another VoiceStudio "
            f"backend (or another app) is listening on it. Quit the other "
            f"instance and relaunch; if nothing is visibly running, an "
            f"orphaned backend from a previous session is still holding the "
            f"port." + (f" Underlying error: {exc}" if exc else ""),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(_EXIT_PORT_IN_USE)

    class _BindErrorWatcher(logging.Filter):
        """Remembers the EADDRINUSE uvicorn logged on its way out (#1364).

        uvicorn's startup does ``logger.error(exc); sys.exit(1)`` with the
        OSError itself as the record's message, so the errno is available as an
        object — no locale-dependent string matching. Passing every record
        through untouched; this only observes.
        """

        def __init__(self) -> None:
            super().__init__()
            self.bind_error: "OSError | None" = None

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.msg
            if isinstance(msg, OSError) and (
                msg.errno in (48, 98, 10048)
                or getattr(msg, "winerror", None) == 10048
            ):
                self.bind_error = msg
            return True

    # #1223: uvicorn does NOT let a bind failure reach the caller — it logs the
    # raw errno and raises SystemExit(1) from inside its startup, so an
    # `except OSError` around uvicorn.run() never fires (verified, not assumed).
    # And the message it logs is useless to match on: the Windows wording
    # ("only one usage of each socket address is normally permitted") is
    # OS-translated into the user's locale. So probe the port ourselves first —
    # errno is locale-independent (EADDRINUSE = 48 macOS/BSD, 98 Linux, 10048
    # Windows) — and exit with a code the shell can recognise.
    if (_bind_err := _port_taken(_bind_host, _port)) is not None:
        _fail_port_in_use(_bind_err)

    _watcher = _BindErrorWatcher()
    # Attached BEFORE uvicorn.run because uvicorn configures logging during
    # startup, well after we lose control. Two properties this depends on, both
    # measured against the installed uvicorn rather than assumed, and both
    # pinned by tests in tests/test_port_in_use_exit.py:
    #
    #  1. uvicorn's `configure_logging()` runs `dictConfig`, which replaces the
    #     logger's HANDLERS but leaves its FILTERS in place — so this survives.
    #  2. it does reset the logger's LEVEL to the configured log_level, which
    #     would overwrite anything we set here. A filter only runs on records
    #     the logger actually emits, so a `log_level` above ERROR would blind
    #     this watcher. We therefore pass no log_level to uvicorn.run() at all
    #     (its default is INFO); the test asserts we never start.
    logging.getLogger("uvicorn.error").addFilter(_watcher)
    try:
        uvicorn.run(app, host=_bind_host, port=_port)
    except SystemExit:
        # Lost the race between the probe above and uvicorn's own bind (a
        # competing process grabbed the port in between).
        #
        # Re-probing alone is not enough (#1364): if the process that took the
        # port was itself exiting — an orphaned backend from the previous
        # session, which is the common case — the port is free again by the
        # time we look, so the probe says "fine" and the user gets a bare
        # `exit code 1` with no explanation for a crash we fully understood.
        # uvicorn already told us the errno on its way out; believe that first
        # and fall back to the probe.
        if _watcher.bind_error is not None:
            _fail_port_in_use(_watcher.bind_error)
        if _port_taken(_bind_host, _port) is not None:
            _fail_port_in_use(None)
        raise
