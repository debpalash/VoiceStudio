import os
import re
import sys
import time
import asyncio
import logging
import queue
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor

from utils.containment import contain_system_exit

# ── Lazy imports ─────────────────────────────────────────────────────
# torch and VoiceStudio are heavy (~2-3s import on Apple Silicon).
# Deferring them until first use cuts cold start from ~4s to ~1.5s,
# so health/status endpoints respond immediately on boot.

_torch = None
_OmniVoice = None


def _lazy_torch():
    global _torch
    if _torch is None:
        import torch as _t
        _torch = _t
    return _torch


def _missing_module_is_omnivoice(exc: ModuleNotFoundError) -> bool:
    """True when *exc* says the ``omnivoice`` package itself is not importable.

    ``ModuleNotFoundError`` is raised for two very different situations along
    this import, and only one of them is fixable by putting the source tree on
    ``sys.path`` (#1415):

    * ``omnivoice`` (or a submodule of it) is genuinely absent — a missing or
      broken editable install, which the #564 fallback repairs; ``exc.name``
      names the omnivoice package.
    * something ``omnivoice`` imports is absent or broken — a torch /
      torchaudio / torchvision mismatch, or transformers' lazy module refusing
      an attribute whose backing import failed
      ("Could not import module 'AutoFeatureExtractor'", which carries no
      ``name`` at all). Nothing about ``sys.path`` is wrong here.

    Treating the second as the first re-imported from the same broken
    environment, failed identically, and logged that the editable install was
    missing — a confident diagnosis of the wrong component.

    ``exc.name`` is the authority, and its absence is decisive rather than
    unknown: the stdlib always sets it, so a ModuleNotFoundError without one
    was raised by hand — which is exactly what transformers' lazy module does.
    """
    name = getattr(exc, "name", None)
    if not name:
        return False
    return name == "omnivoice" or name.startswith("omnivoice.")


def _lazy_omnivoice():
    global _OmniVoice
    if _OmniVoice is None:
        try:
            # The class is OmniVoice — a library identifier, not product
            # branding. The VoiceStudio rename must not touch it (checkpoint
            # configs reference the class name via transformers architectures).
            from omnivoice.models.omnivoice import OmniVoice as _OV
        except ModuleNotFoundError as exc:
            if not _missing_module_is_omnivoice(exc):
                # Something in omnivoice's OWN import chain is missing — not
                # omnivoice itself (#1415). transformers' lazy module raises
                # ModuleNotFoundError for any attribute whose backing import
                # failed ("Could not import module 'AutoFeatureExtractor'"),
                # and a missing torchaudio/torchvision raises it by name. The
                # source-tree fallback below cannot fix any of those: it
                # re-imports from the same broken environment and fails
                # identically, having logged that the *editable install* is
                # broken — which sent the reporter, and us, after the wrong
                # thing. Let it through with its own cause intact; classify()
                # already names it TRANSFORMERS_IMPORT and hints at the real
                # remedy.
                raise
            # The venv's editable install is missing/broken (#564). main.py wires
            # the source fallback at startup, but resolve it here too so the
            # model-load path self-heals and logs the paths it searched.
            from core.omnivoice_path import ensure_omnivoice_importable
            _backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ensure_omnivoice_importable(_backend_dir, logger)
            from omnivoice.models.omnivoice import OmniVoice as _OV
        _OmniVoice = _OV
    return _OmniVoice


from core.config import IDLE_TIMEOUT_SECONDS, CPU_POOL_WORKERS

logger = logging.getLogger("omnivoice.model")

# Per-TTS-job VRAM headroom estimate. VoiceStudio's forward + autoregressive
# decode peaks around 1.6 GB, but the interactive clone path co-loads WhisperX
# large-v3 ASR (~3 GB) to transcribe the reference, so a *concurrent* clone job
# is realistically ~5 GB. The old 2.5 GB budget over-committed: an 8 GB card
# (~7 GB free) got 2 workers, and two concurrent clone jobs blew past VRAM into
# a sticky CUDA "illegal memory access" that aborts the whole backend process —
# the wave of "Can't reach the local backend" crash reports on 8 GB GPUs
# (#567/#570/#571/#580/#582/#583/#584). Budgeting 5 GB serializes to 1 worker on
# ≤10 GB cards (no contention → no crash) while 16/24 GB cards still parallelize.
# Power users override with OMNIVOICE_GPU_WORKERS.
_GPU_VRAM_PER_JOB_GB = 5.0
_GPU_WORKER_CAP = 4

class WorkerStopIteration(RuntimeError):
    """A pool worker raised a bare ``StopIteration``.

    asyncio refuses to put ``StopIteration`` into a Future — ``_copy_future_
    state`` raises ``TypeError: StopIteration interacts badly with generators
    and cannot be raised into a Future`` *inside the event loop's callback*, so
    the ``run_in_executor`` future is never completed and the awaiting caller
    waits **forever**. Not a theoretical edge: verified on the bundled CPython
    3.11, and the failure has no error, no event and no timeout — a render just
    stops, which is indistinguishable to the user from a wedged app.

    Generator-driven engines reach it on ordinary bad input: VoxCPM's
    ``next_and_close`` is a bare ``next(gen)``, so a generator that ends without
    yielding (text the model normalises away to nothing, for instance) raises
    exactly this out of ``backend.generate`` (#1321 class).

    Translating it to a RuntimeError at the pool boundary — the one place every
    dispatch funnels through — turns a silent hang into a normal failure that
    the existing per-chapter / per-job error handling reports. Subclasses
    RuntimeError so every `except Exception` site upstream keeps working.
    """


def _guard_stopiteration(fn):
    """Wrap `fn` so a bare StopIteration can never escape into a Future."""
    def _guarded(*a, **kw):
        try:
            return fn(*a, **kw)
        except StopIteration as e:
            raise WorkerStopIteration(
                "the engine stopped without producing a result (StopIteration) — "
                "its generator ended before yielding anything, which usually means "
                "it could not handle this input"
            ) from e
    return _guarded


class _GuardedCpuPool(ThreadPoolExecutor):
    """CPU pool with the same StopIteration guard as the GPU pool."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(_guard_stopiteration(fn), *args, **kwargs)


_gpu_pool_singleton: "_ResilientGpuPool | None" = None
_cpu_pool = _GuardedCpuPool(max_workers=CPU_POOL_WORKERS)


def _workers_for_free_vram(free_gb: float) -> int:
    """GPU worker count for a given free-VRAM figure: free // per-job budget,
    floored at 1 and capped at _GPU_WORKER_CAP. Pure so the sizing policy is
    unit-tested without a GPU (the #567 crash hinged on this returning >1 on
    8 GB cards)."""
    return max(1, min(_GPU_WORKER_CAP, int(free_gb // _GPU_VRAM_PER_JOB_GB)))


def _pick_gpu_workers() -> int:
    """Pick a sensible GPU worker count from the runtime environment.

    Resolution order:
      1. OMNIVOICE_GPU_WORKERS env var (explicit user override, clamped 1..16).
      2. CUDA / ROCm: free VRAM // per-job budget, capped at 4.
      3. MPS / CPU / unknown: 1.

    Designed to fail safe — any exception → 1 worker, never propagated.
    """
    override = os.environ.get("OMNIVOICE_GPU_WORKERS")
    if override:
        try:
            n = int(override)
            return max(1, min(16, n))
        except ValueError:
            logger.warning("OMNIVOICE_GPU_WORKERS=%r is not an integer; ignoring", override)
    try:
        torch = _lazy_torch()
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            free_bytes, _total = torch.cuda.mem_get_info()
            free_gb = free_bytes / (1024 ** 3)
            workers = _workers_for_free_vram(free_gb)
            logger.info(
                "GPU pool sized to %d worker(s) — %.1f GB free / %.1f GB per job (cap %d)",
                workers, free_gb, _GPU_VRAM_PER_JOB_GB, _GPU_WORKER_CAP,
            )
            return workers
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            logger.info("GPU pool: MPS detected, using 1 worker (shared system memory)")
            return 1
    except Exception as e:
        logger.warning("GPU worker probe failed (%s); defaulting to 1", e)
    return 1


# thread_name_prefix for the GPU pool, centralised so the "am I on a gpu-pool
# worker?" predicates (running_on_gpu_pool below; SubprocessBackend.generate's
# on-pool skip) cannot drift from the pool's actual prefix. A drift would
# silently re-introduce the 1-worker self-deadlock this couples against.
_GPU_POOL_THREAD_PREFIX = "gpu-pool"


def _build_gpu_pool() -> ThreadPoolExecutor:
    workers = _pick_gpu_workers()
    return ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix=_GPU_POOL_THREAD_PREFIX)


def running_on_gpu_pool() -> bool:
    """True iff the calling thread is a gpu-pool worker (already holds a slot).

    Routes that dispatch backend work via run_on_gpu_pool_guarded are already on
    a pool worker; re-acquiring a slot there would self-deadlock on a 1-worker
    pool (MPS). Used by SubprocessBackend.generate()'s on-pool skip and by
    _heal_tts_placement.
    """
    return threading.current_thread().name.startswith(_GPU_POOL_THREAD_PREFIX)


class _ResilientGpuPool(Executor):
    """A stable, self-healing wrapper around the GPU `ThreadPoolExecutor`.

    The crash this fixes (#589 #599): `_reset_gpu_pool()` shuts the pool down on
    a model-load timeout, but consumers that captured the executor *object* at
    import time (`from services.model_manager import _gpu_pool` at module level —
    generation, dub_generate, dub_core, dub_translate, openai_compat) kept
    submitting to the dead pool and got `RuntimeError: cannot schedule new
    futures after shutdown` on the next generate/dub/translate.

    Making `_gpu_pool` a single long-lived wrapper whose *inner* pool is swapped
    means those references never go stale: every `submit()` resolves the live
    pool, and a submit that races a shutdown rebuilds once and retries. Building
    the inner pool stays lazy so we still size workers after torch's device
    probe (the reason for the original `__getattr__` indirection).
    """

    def __init__(self):
        self._pool: "ThreadPoolExecutor | None" = None
        self._lock = threading.Lock()
        # ── Queue accounting (#1190/#1202) ───────────────────────────────
        # `queued` = submitted but not yet picked up by a worker; `running` =
        # executing right now. Admission control (check_gpu_admission) and the
        # Retry-After estimate both read these, so a scripted client learns the
        # pool is saturated at SUBMIT instead of after a 300s silent wait.
        self._stats_lock = threading.Lock()
        self._queued = 0
        self._running = 0
        self._avg_job_s = 0.0  # EMA of completed job wall time

    def _live_pool(self) -> ThreadPoolExecutor:
        pool = self._pool
        if pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = _build_gpu_pool()
                pool = self._pool
        return pool

    def _submit_live(self, fn, /, *args, **kwargs):
        try:
            return self._live_pool().submit(fn, *args, **kwargs)
        except RuntimeError as e:
            # "cannot schedule new futures after shutdown": the inner pool was
            # reset (or torn down) under us. Rebuild once and retry so a stale
            # caller self-heals instead of 500-ing. (Interpreter-shutdown races
            # re-raise on the retry — we don't loop.)
            if "shutdown" not in str(e).lower():
                raise
            with self._lock:
                self._pool = _build_gpu_pool()
                pool = self._pool
            return pool.submit(fn, *args, **kwargs)

    def submit(self, fn, /, *args, **kwargs):
        # Every dispatch (guarded or raw run_in_executor) funnels through here,
        # so wrapping the callable is the one place that sees queue→run→done
        # for the whole pool.
        token = {"counted": False}

        def _tracked(*a, **kw):
            with self._stats_lock:
                token["counted"] = True
                self._queued -= 1
                self._running += 1
            t0 = time.monotonic()
            try:
                # A bare StopIteration here would never reach the caller — it
                # hangs the awaiting future instead (see WorkerStopIteration).
                return _guard_stopiteration(fn)(*a, **kw)
            finally:
                elapsed = time.monotonic() - t0
                with self._stats_lock:
                    self._running -= 1
                    self._avg_job_s = (
                        elapsed if self._avg_job_s <= 0
                        else 0.7 * self._avg_job_s + 0.3 * elapsed
                    )

        with self._stats_lock:
            self._queued += 1
        try:
            fut = self._submit_live(_tracked, *args, **kwargs)
        except BaseException:
            with self._stats_lock:
                if not token["counted"]:
                    token["counted"] = True
                    self._queued -= 1
            raise

        def _drain(_f, token=token):
            # A job cancelled before a worker picked it up never runs _tracked;
            # release its queue slot here so the depth can't drift upward.
            with self._stats_lock:
                if not token["counted"]:
                    token["counted"] = True
                    self._queued -= 1

        fut.add_done_callback(_drain)
        return fut

    def stats(self) -> dict:
        """Live queue depth / worker occupancy — the input to admission control."""
        with self._stats_lock:
            queued, running, avg = self._queued, self._running, self._avg_job_s
        pool = self._pool
        workers = getattr(pool, "_max_workers", None) or 1
        return {"queued": queued, "running": running,
                "workers": workers, "avg_job_s": avg}

    def reset(self) -> None:
        """Abandon the current worker pool; the next submit builds a fresh one.

        Deliberately **not** ``cancel_futures=True`` (#1190/#1202): that killed
        innocent peers — a queued job belonging to a *different* request was
        cancelled because *this* request timed out, and surfaced to that caller
        as a bare ``CancelledError``. ``shutdown(wait=False)`` only refuses NEW
        submissions; work already in the old pool's queue still drains on the
        old pool's workers, so peers complete normally while new work goes to
        the fresh pool.

        Honesty about what this reclaims: **nothing**. Python cannot kill the
        thread wedged in the timed-out job — it keeps running (and keeps its
        VRAM) until it finishes on its own. Dropping the pool only stops NEW
        work from queueing behind it; it does not restore the device. That is
        why the timeout guidance no longer claims capacity was restored.
        """
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=wait, cancel_futures=cancel_futures)


def _get_gpu_pool() -> "_ResilientGpuPool":
    """Internal accessor for the GPU pool singleton. Same object as the
    module-level `_gpu_pool` attribute, but resolvable from inside this module
    (Python's module `__getattr__` only fires for lookups from *outside*).
    """
    global _gpu_pool_singleton
    if _gpu_pool_singleton is None:
        _gpu_pool_singleton = _ResilientGpuPool()
    return _gpu_pool_singleton


def __getattr__(name: str):
    """Lazy module attribute — initialises `_gpu_pool` on first access so we
    can probe the device after torch finishes its lazy import. Without this
    we'd be forced to commit to max_workers=1 at module import time, before
    knowing whether CUDA is even available.
    """
    if name == "_gpu_pool":
        return _get_gpu_pool()
    raise AttributeError(f"module 'services.model_manager' has no attribute {name!r}")


# ── GPU-job timeout guard (#730 class; residual #850/#802/#755 …) ─────
# A blocking GPU job that wedges on a Windows+CUDA hang keeps occupying its
# worker forever — run_in_executor can't cancel the thread. With a 1–2 worker
# pool that starves *every* other request, so the next user action surfaces as
# the misleading "Can't reach the local backend" even though the process is
# alive. ASR/dub/model-load already bound+reset on hang (run_transcribe_guarded,
# _reset_pool_on_wedge, _load_model_with_timeout); the TTS **generate** paths
# (generation.py, tts_stream.py) were the last unguarded dispatch — and the
# residual on-main reports all fail on generate:start (audio). This is the same
# guard generalised so every GPU dispatch shares one recovery path.
_GENERATE_TIMEOUT_EXPLICIT = "OMNIVOICE_GENERATE_TIMEOUT_S" in os.environ
GPU_JOB_TIMEOUT_S = float(os.environ.get("OMNIVOICE_GENERATE_TIMEOUT_S", "300.0"))
_CONFIGURED_GPU_JOB_TIMEOUT_S = GPU_JOB_TIMEOUT_S
# CPU synthesis is healthy but substantially slower than accelerated inference.
# Keep a separate, bounded floor so a short render on CPU is not abandoned at
# the GPU-oriented five-minute deadline (#1588).
#
# #1787 review fix: an explicit OMNIVOICE_CPU_GENERATE_TIMEOUT_S must ALWAYS
# govern CPU dispatches, even when OMNIVOICE_GENERATE_TIMEOUT_S is ALSO
# explicit. Before this flag existed, `universal_override` below treated any
# explicit GENERATE_TIMEOUT_S as authoritative for CPU too, so the Settings
# panel's "CPU budget" row could be saved and silently never apply whenever
# the "Accelerated" row was also set — the exact defect (a control that looks
# like it works and doesn't) issue #1787 exists to remove. Setting ONLY
# OMNIVOICE_GENERATE_TIMEOUT_S keeps its historical "universal" behavior
# unchanged (test_explicit_universal_generate_timeout_wins_on_cpu) — nobody
# who already relies on that single-var override loses it. The only case that
# changes is the previously-undocumented, previously-broken combination of
# setting BOTH: the more specific (CPU) value now wins for CPU jobs, matching
# what a user who filled in both Settings rows was told would happen.
_CPU_GENERATE_TIMEOUT_EXPLICIT = "OMNIVOICE_CPU_GENERATE_TIMEOUT_S" in os.environ
CPU_JOB_TIMEOUT_S = float(os.environ.get("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "600.0"))
_CONFIGURED_CPU_JOB_TIMEOUT_S = CPU_JOB_TIMEOUT_S

# Queue-wait budget — a SEPARATE, deliberately generous clock (#1190/#1202).
# The execution bound above must never be spent waiting in line: a job queued
# behind a busy 1-worker pool used to burn its whole 300s budget without
# executing a single instruction and then be told it was "too heavy for the
# available compute". Waiting long is normal on a 1-worker host (that is what
# serialization means); waiting *forever* is not, so the queue still has a
# bound — crossing it means saturation, which is a retryable 503, not a
# too-heavy job.
GPU_QUEUE_TIMEOUT_S = float(os.environ.get("OMNIVOICE_GPU_QUEUE_TIMEOUT_S", "1800.0"))

# ── model-load heartbeats (#1367) ────────────────────────────────────────────
# A first-use generate on a subprocess engine DOWNLOADS the model inside the
# job, and the sidecar proves the download is healthy by emitting a progress
# frame every ~5s. The execution clock above ignored that: a slow connection
# blew the 300s budget mid-download and the user was told their hardware was
# too slow, while the sidecar's own watchdog was happily fed. These three make
# the two clocks agree — a job is only "wedged" when it is SILENT.
#
# How long a heartbeat stays fresh. Sidecars emit every ~5s (_HEARTBEAT_S in
# each engine's main.py); 30s tolerates a stall between frames without keeping
# a genuinely dead load alive for long.
MODEL_LOAD_HEARTBEAT_GRACE_S = float(
    os.environ.get("OMNIVOICE_MODEL_LOAD_HEARTBEAT_GRACE_S", "30.0"))
# Cap on the EXTRA time heartbeats can buy beyond the normal execution budget.
# Without a cap, a load that heartbeats but never finishes would hold its
# worker forever. 1800s of extension ≈ a 5 GB model at ~2.5 MB/s on top of the
# 300s base — beyond that, telling the user is better than silently waiting.
MODEL_LOAD_EXTRA_TIMEOUT_S = float(
    os.environ.get("OMNIVOICE_MODEL_LOAD_TIMEOUT_S", "1800.0"))

# How long a SYNTHESIS heartbeat stays fresh. Much longer than the load grace
# on purpose: the finest progress signal a generate has is "a chunk finished",
# and one chunk of a long text on a modest GPU can legitimately take minutes
# (#1391: an RTX 2060 SUPER with 5.7 GB free). Judging that by the 30s
# sidecar-frame grace would call every slow-but-healthy render wedged, which is
# the bug. At this grace the distinction is the honest one: a job that has not
# finished a single chunk in a whole base budget really has stopped.
GENERATE_PROGRESS_GRACE_S = float(
    os.environ.get("OMNIVOICE_GENERATE_PROGRESS_GRACE_S", "300.0"))

#: thread ident -> (monotonic time of its last heartbeat, how long it stays
#: fresh). Written by report_model_load_activity() / report_generate_progress()
#: from pool-worker threads, read by the guarded waiter, cleared when the job
#: ends. Plain dict: CPython dict ops are atomic enough for a small tuple, and
#: a torn read only costs one 5s wait slice.
_MODEL_LOAD_ACTIVITY: dict = {}


def report_model_load_activity() -> None:
    """Record that the CURRENT THREAD's job is making model-load progress.

    Called by engine code that can prove liveness — e.g. SubprocessBackend
    each time a sidecar progress frame arrives during a cold load. The
    guarded waiter uses it to extend the execution deadline (bounded by
    MODEL_LOAD_EXTRA_TIMEOUT_S) instead of abandoning a healthy download.
    """
    _MODEL_LOAD_ACTIVITY[threading.get_ident()] = (
        time.monotonic(), MODEL_LOAD_HEARTBEAT_GRACE_S,
    )


def report_generate_progress() -> None:
    """Record that the CURRENT THREAD's job finished a unit of synthesis.

    Same contract as the load heartbeat, different evidence: a multi-chunk
    render that just completed chunk 7 of 20 is demonstrably working, however
    slow it is. Without this, a long text on a modest GPU hit the 300s
    execution budget mid-render and was abandoned as "too heavy for the
    available compute" — with most of its chunks already rendered, and no way
    for the user to tell that from a genuine wedge (#1338/#1348/#1391).

    Carries a longer freshness window than the load heartbeat because chunks
    are coarse: see GENERATE_PROGRESS_GRACE_S.
    """
    _MODEL_LOAD_ACTIVITY[threading.get_ident()] = (
        time.monotonic(), GENERATE_PROGRESS_GRACE_S,
    )


class GpuJobTimeoutError(TimeoutError):
    """A GPU-pool job **that actually started executing** overran its bound.

    Only raised once a worker picked the job up, so the message's "too heavy
    for the available compute" reading is truthful. Queue wait is bounded
    separately and surfaces as :class:`GpuPoolBusyError`.
    """


class GpuPoolBusyError(TimeoutError):
    """The GPU pool is saturated — the job never started, so nothing was lost.

    Retryable verbatim: no compute was spent, no partial state exists. Carries
    ``retry_after`` (seconds) so HTTP callers can emit a real ``Retry-After``
    and scripted clients can back off instead of hammering a busy backend.
    """

    def __init__(self, message: str, *, retry_after: float = 30.0):
        super().__init__(message)
        self.retry_after = max(1, int(round(retry_after)))


def generate_timeout_s(
    text: "str | None", *, engine: object = None, execution_device: "str | None" = None,
) -> float:
    """THE wall-clock execution budget for one synthesis job, scaled to input.

    Single source of truth for every TTS dispatch (#1190/#1202). The
    length-scaled budget landed in v0.3.22 but was wired into only two call
    sites in generation.py's classic path — the streaming path the UI tries
    FIRST, plus /v1/audio/speech, batch, dub and archetype previews, all still
    used the flat 300s, which is why 0.3.22 users kept seeing "exceeded 300s"
    on long inputs. Lives here (not in a router) so every router shares it
    without importing generation.py.

    Policy: floor at the configured OMNIVOICE_GENERATE_TIMEOUT_S (accelerated
    hosts) or OMNIVOICE_CPU_GENERATE_TIMEOUT_S (CPU hosts — the latter wins
    for CPU whenever it is itself explicit, even if the former also is; see
    the #1787 comment on the module-level constants), plus 1s per 40
    characters past a 1200-character free allowance — generous enough for
    CPU-class hardware, still bounded (a wedged job is caught in minutes, not
    hours).
    """
    base = GPU_JOB_TIMEOUT_S
    try:
        from core.device_caps import detect_host_caps
        family = execution_device or detect_host_caps().family
        if execution_device is None and engine is not None:
            from services.engine_routing import resolve_routing
            compat = getattr(engine, "gpu_compat", None)
            if compat is None:
                compat = getattr(type(engine), "gpu_compat", (family, "cpu"))
            if tuple(compat) == ("cpu",):
                family = "cpu"
            else:
                family = resolve_routing(
                    compat, detect_host_caps(),
                    float(getattr(engine, "min_vram_gb", 0.0) or 0.0),
                )["effective_device"]
        universal_override = (
            _GENERATE_TIMEOUT_EXPLICIT
            or GPU_JOB_TIMEOUT_S != _CONFIGURED_GPU_JOB_TIMEOUT_S
        )
        # An explicit (env-set, or runtime-changed the same way tests do)
        # CPU budget is more specific than the universal override and always
        # wins for CPU dispatches — see the #1787 comment above.
        cpu_explicit = (
            _CPU_GENERATE_TIMEOUT_EXPLICIT
            or CPU_JOB_TIMEOUT_S != _CONFIGURED_CPU_JOB_TIMEOUT_S
        )
        if family == "cpu" and (cpu_explicit or not universal_override):
            base = CPU_JOB_TIMEOUT_S
    except Exception:
        # Device probing is advisory here; the configured universal bound is
        # still safe when a platform probe is unavailable during startup.
        pass
    return base + (max(0, len(text or "") - 1200) / 40.0)


def _retry_after_estimate(stats: dict) -> float:
    """Seconds a caller should wait before retrying, from live pool state.

    Queue depth ahead of you, divided by workers, times a recent job's wall
    time. Bounded to 5..300s so the hint is always usable (and never zero on a
    cold pool with no timing history yet)."""
    base = stats.get("avg_job_s") or 0.0
    if base <= 0:
        base = 30.0
    workers = max(1, int(stats.get("workers") or 1))
    waves = (int(stats.get("queued") or 0) + 1) / workers
    return max(5.0, min(300.0, base * waves))


def gpu_pool_stats(executor=None) -> dict:
    """Live pool occupancy, or a permissive default for executors that don't
    track it (plain ThreadPoolExecutor in tests / injected executors)."""
    ex = executor if executor is not None else _get_gpu_pool()
    fn = getattr(ex, "stats", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — telemetry must never break a request
            pass
    return {"queued": 0, "running": 0, "workers": 1, "avg_job_s": 0.0}


def check_gpu_admission(*, what: str = "GPU job", executor=None) -> None:
    """Admission control at SUBMIT (#1190/#1202) — raise before queueing when
    the pool is already backed up.

    Policy: refuse when ``queued >= workers`` — every worker is busy AND a full
    wave of jobs is *already waiting* ahead of this one. Deliberately NOT the
    stricter "no worker is free": on the 1-worker hosts this bug hurts most,
    that would reject the ordinary second concurrent request the desktop UI
    issues routinely and which completes fine today. The looser rule still
    catches the case that matters — a scripted client fanning out N requests at
    a pool that can only serialize them — and turns a silent multi-minute wait
    into an immediate, honest "retry in N seconds".
    """
    stats = gpu_pool_stats(executor)
    if stats.get("queued", 0) < max(1, int(stats.get("workers") or 1)):
        return
    retry_after = _retry_after_estimate(stats)
    raise GpuPoolBusyError(
        f"{what} was not accepted: the local GPU worker pool is saturated "
        f"({stats.get('running', 0)} running, {stats.get('queued', 0)} already "
        f"queued on {stats.get('workers', 1)} worker(s)). Nothing was started, "
        f"so this request is safe to retry as-is in about "
        f"{int(retry_after)}s. To raise throughput, run fewer "
        f"concurrent requests, or set OMNIVOICE_GPU_WORKERS if the machine has "
        f"spare VRAM.",
        retry_after=retry_after,
    )


def _log_safe(what: str) -> str:
    """Backward-compatible alias for the shared logging seam."""
    from core.logging_utils import log_safe

    return log_safe(what, limit=120)


def _swallow_abandoned(fut) -> None:
    """Consume the result of a future we stopped awaiting, so an abandoned
    wedged job can't emit "Future exception was never retrieved" noise."""
    try:
        if not fut.cancelled():
            fut.exception()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — cleanup only
        pass


async def run_on_gpu_pool_guarded(fn, *, what: str = "GPU job",
                                  timeout: "float | None" = None,
                                  executor=None,
                                  queue_timeout: "float | None" = None,
                                  min_vram_gb: float = 0.0,
                                  on_abandon=None):
    """Run blocking ``fn`` on the GPU pool, bounding **execution** — not the
    wait for a free worker.

    Two clocks (#1190/#1202):

    * ``queue_timeout`` (generous, ``GPU_QUEUE_TIMEOUT_S``) covers the time the
      job sits in the pool queue. Exceeding it raises :class:`GpuPoolBusyError`
      — the job is cancelled out of the queue before it ever runs, so no
      compute is wasted and the caller can retry verbatim.
    * ``timeout`` (``GPU_JOB_TIMEOUT_S`` by default) starts only when a worker
      actually picks the job up. Exceeding *that* is a genuinely wedged/too-slow
      job → :class:`GpuJobTimeoutError` + pool ``reset()``.

    Previously both were one clock started at submit: ``run_in_executor``
    returns immediately, so a job queued behind a busy 1-worker pool burned its
    entire budget waiting and then reported "too heavy for the available
    compute" without having executed one instruction.

    ``fn`` must be a zero-arg callable — wrap args with ``functools.partial``.
    Executors without ``reset`` (a plain ThreadPoolExecutor in tests) still get
    both bounds; only the reset step is skipped.

    ``min_vram_gb`` is the declared VRAM floor of the engine this job belongs
    to (``TTSBackend.min_vram_gb``); it only shapes the timeout MESSAGE. Left
    at 0 — the default, and correct for every non-TTS job on this pool
    (reference transcribe, watermarking, dub steps) — the under-provisioned-GPU
    wording is never used, because nothing measured says it applies (#1226).

    ``on_abandon`` is called once, after a job whose caller stopped waiting can
    no longer access its inputs. A queued job that is cancelled before it
    starts calls it immediately; a running thread calls it from ``_job``'s
    finalizer. Normal completion never calls it. This lets request-owned temp
    files outlive abandoned workers without delaying ordinary requests (#1668).
    """
    loop = asyncio.get_running_loop()
    ex = executor if executor is not None else _get_gpu_pool()
    # Resolved at CALL time, not def time, so monkeypatching/reloading the
    # module constant reaches every call site (the old default bound at def).
    timeout = GPU_JOB_TIMEOUT_S if timeout is None else float(timeout)
    queue_timeout = GPU_QUEUE_TIMEOUT_S if queue_timeout is None else float(queue_timeout)

    started = asyncio.Event()
    _inner = contain_system_exit(fn, what)
    # The worker thread's ident, published by _job so the waiter can read this
    # job's model-load heartbeats (#1367). A dict, not a nonlocal: the closure
    # runs on a pool thread while the waiter reads from the event loop.
    _ident_box: dict = {}
    _abandon_lock = threading.Lock()
    _abandon_state = {
        "requested": False,
        "finished": False,
        "callback_called": False,
    }

    def _fire_abandon_callback() -> None:
        if on_abandon is None:
            return
        with _abandon_lock:
            if _abandon_state["callback_called"]:
                return
            _abandon_state["callback_called"] = True
        try:
            on_abandon()
        except Exception:  # noqa: BLE001 — cleanup cannot hide the pool result
            logger.exception("%s abandon cleanup failed", _log_safe(what))

    def _job():
        # First thing the worker does: tell the awaiting coroutine the
        # execution clock may start. call_soon_threadsafe is the only
        # loop-safe way to touch an asyncio primitive from a pool thread.
        _ident_box["ident"] = threading.get_ident()
        try:
            loop.call_soon_threadsafe(started.set)
        except RuntimeError:
            pass  # loop already closed (caller vanished) — still run the job
        try:
            return _inner()
        finally:
            # Idents are reused by the OS; a stale heartbeat under this ident
            # must not vouch for some future job on the same thread.
            _MODEL_LOAD_ACTIVITY.pop(threading.get_ident(), None)
            with _abandon_lock:
                _abandon_state["finished"] = True
                abandoned = _abandon_state["requested"]
            if abandoned:
                _fire_abandon_callback()

    concurrent_fut = ex.submit(_job)
    fut = asyncio.wrap_future(concurrent_fut, loop=loop)

    def _abandon() -> None:
        # Keep the concurrent future so we can distinguish a job cancelled out
        # of the queue from a thread that Python cannot stop once it has begun.
        cancelled_before_start = concurrent_fut.cancel()
        with _abandon_lock:
            _abandon_state["requested"] = True
            finished = _abandon_state["finished"]
        fut.cancel()
        if cancelled_before_start or finished:
            _fire_abandon_callback()

    waiter = asyncio.ensure_future(started.wait())
    try:
        # Phase 1 — queue wait. Watch the future too, so a job that fails or is
        # cancelled while still queued resolves here instead of hanging.
        done, _pending = await asyncio.wait(
            {waiter, fut}, timeout=queue_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        # Caller went away (client disconnect). We stop awaiting the job, so
        # make sure its eventual result/exception is consumed rather than
        # logged as "Future exception was never retrieved".
        _abandon()
        fut.add_done_callback(_swallow_abandoned)
        raise
    finally:
        waiter.cancel()

    if not done:
        # Never picked up: cancel it out of the queue (a not-yet-started
        # concurrent future cancels cleanly) and report saturation, NOT a
        # too-heavy job.
        _abandon()
        fut.add_done_callback(_swallow_abandoned)
        stats = gpu_pool_stats(ex)
        logger.warning(
            "%s waited %.0fs for a free GPU worker and was never started "
            "(%d queued / %d running) — reporting pool saturation (#1190).",
            _log_safe(what), queue_timeout,
            stats.get("queued", 0), stats.get("running", 0),
        )
        raise GpuPoolBusyError(
            f"{what} waited {queue_timeout:.0f}s for a free GPU worker and "
            f"never started, so nothing was computed and the request is safe "
            f"to retry as-is. The backend is alive but every worker is busy "
            f"with earlier jobs. Run fewer concurrent requests, or raise "
            f"OMNIVOICE_GPU_WORKERS if the machine has spare VRAM.",
            retry_after=_retry_after_estimate(stats),
        )

    # Phase 2 — execution. The clock starts here: this job owns a worker.
    #
    # Not a single wait_for (#1367): a first-use generate on a subprocess
    # engine downloads its model inside the job, and the sidecar proves the
    # download is healthy with progress frames the backend forwards via
    # report_model_load_activity(). Sliced waiting lets the deadline extend
    # while those heartbeats stay fresh — bounded by MODEL_LOAD_EXTRA_TIMEOUT_S
    # — so a slow connection is no longer reported as too-slow hardware. A job
    # that goes SILENT still dies at the original deadline (± one slice).
    _t0 = time.monotonic()
    _soft_deadline = _t0 + timeout
    _hard_deadline = _soft_deadline + MODEL_LOAD_EXTRA_TIMEOUT_S
    _extended = False
    try:
        while True:
            _now = time.monotonic()
            if _now < _soft_deadline:
                _slice = min(_soft_deadline - _now, 5.0)
            else:
                # Soft budget exhausted. Keep waiting ONLY on the strength of a
                # fresh heartbeat from this job's worker thread — a model-load
                # progress frame, or a completed synthesis chunk. Each carries
                # its own freshness window (loads report every ~5s; chunks are
                # minutes apart on slow hardware).
                _beat = _MODEL_LOAD_ACTIVITY.get(_ident_box.get("ident"))
                _last, _grace = _beat if _beat else (None, 0.0)
                if (_last is None
                        or _now - _last > _grace
                        or _now >= _hard_deadline):
                    raise asyncio.TimeoutError()
                if not _extended:
                    _extended = True
                    logger.info(
                        "%s reached its %.0fs execution budget while still "
                        "making progress — extending while heartbeats continue "
                        "(grace %.0fs, cap +%.0fs) (#1367/#1391).",
                        _log_safe(what), timeout, _grace,
                        MODEL_LOAD_EXTRA_TIMEOUT_S,
                    )
                # Wake at the next decision point (heartbeat expiry or the
                # cap), not a fixed 5s — a fixed slice overshoots both.
                _slice = max(0.05, min(
                    (_last + _grace) - _now,
                    _hard_deadline - _now,
                    5.0,
                ))
            _done, _ = await asyncio.wait({fut}, timeout=_slice)
            if _done:
                return fut.result()
    except asyncio.CancelledError:
        # Caller went away mid-execution. The old wait_for cancelled the
        # wrapper itself; asyncio.wait does not, so do both halves here or the
        # eventual result is logged as "Future exception was never retrieved".
        _abandon()
        fut.add_done_callback(_swallow_abandoned)
        raise
    except asyncio.TimeoutError as timeout_exc:
        # Parity with the old wait_for semantics: cancel the asyncio wrapper;
        # the worker thread keeps going regardless. Consume whatever it
        # eventually produces.
        _abandon()
        fut.add_done_callback(_swallow_abandoned)
        # Capture the stacks BEFORE reset(): reset() replaces the executor, and
        # once the wedged thread is no longer a pool worker we can no longer
        # tell it apart from any other thread in the process.
        stacks = log_gpu_pool_worker_stacks(what, timeout, executor=ex)
        _reset = getattr(ex, "reset", None)
        if callable(_reset):
            try:
                _reset()
                logger.warning(
                    "%s exceeded %.0fs of EXECUTION time — abandoned the "
                    "GPU-pool worker; it keeps running (and holding the "
                    "device) until it finishes on its own (#730/#1190).",
                    _log_safe(what), timeout,
                )
            except Exception:
                logger.exception("GPU pool reset after %s timeout failed",
                                 _log_safe(what))
        raise GpuJobTimeoutError(
            _timeout_guidance(
                what, timeout, min_vram_gb, wedged=_stack_shows_a_wedge(stacks),
            )
        ) from timeout_exc


#: Frames to keep per wedged worker. Deep enough to cross the engine adapter
#: into the model's own call stack, shallow enough that a 1-worker and an
#: 8-worker host both produce a log a human will actually read.
_WEDGE_STACK_DEPTH = 25


def _live_pool_thread_idents(executor) -> "set | None":
    """Thread idents belonging to ``executor``'s CURRENT inner pool, or None
    when they can't be established.

    Needed because a wedged worker survives ``reset()`` — it cannot be
    cancelled, so it keeps running under the same ``gpu-pool`` name the
    replacement pool also uses. Without this, the second timeout in a session
    logs the stale thread alongside the live one with nothing to tell them
    apart, and the stale stack is the more misleading of the two: it names an
    operation that is no longer the one that just failed (greptile).

    ``ThreadPoolExecutor._threads`` is private but has been the storage for its
    worker set since 3.2 and is stable across every version we support; None
    here is a soft degrade to "label nothing", never an error.
    """
    pool = getattr(executor, "_pool", executor)  # unwrap _ResilientGpuPool
    threads = getattr(pool, "_threads", None)
    if not threads:
        return None
    try:
        return {t.ident for t in threads if t.ident is not None}
    except Exception:  # noqa: BLE001
        return None


def log_gpu_pool_worker_stacks(what: str, timeout: float, executor=None) -> str:
    """Log where every GPU-pool worker is currently executing. Never raises.

    The gap this closes (#1338/#1329/#1348): when a job overran its execution
    budget we logged *that* it had, reset the pool, and returned a message
    about the machine being too slow — with no record of what the abandoned
    thread was actually doing. So every report of this class arrived
    undiagnosable, and the only way forward was to ask the user to reproduce it
    under a debugger. On an RTX 3060 rendering one sentence, "too heavy for the
    available compute" is almost certainly the wrong story, and nothing in the
    log could contradict it.

    ``sys._current_frames()`` reads the frame of every live thread, including
    one wedged inside a C call — which is exactly the case here, since the
    worker cannot be cancelled and keeps running after we abandon it. Filtered
    to gpu-pool workers so the log names the stuck job, not the web server.

    Returns the formatted text (also for tests); empty when nothing matched.
    """
    try:
        import sys as _sys
        import threading as _threading
        import traceback as _traceback

        names = {
            t.ident: t.name for t in _threading.enumerate()
            if t.ident is not None and t.name.startswith(_GPU_POOL_THREAD_PREFIX)
        }
        if not names:
            return ""
        live = _live_pool_thread_idents(executor) if executor is not None else None
        frames = _sys._current_frames()
        blocks = []
        for ident, name in sorted(names.items(), key=lambda kv: kv[1]):
            frame = frames.get(ident)
            if frame is None:
                continue
            if live is None:
                label = name
            elif ident in live:
                label = f"{name} (current pool)"
            else:
                label = (
                    f"{name} (STALE — a worker abandoned by an earlier timeout, "
                    f"still running; not the job that just failed)"
                )
            stack = "".join(_traceback.format_stack(frame, limit=_WEDGE_STACK_DEPTH))
            blocks.append(f"--- {label} ---\n{stack.rstrip()}")
        if not blocks:
            return ""
        # Stack frames carry absolute source paths, and on a user's machine
        # those start with their home directory — i.e. their account name. This
        # log lands in backend.log, which goes into diagnostic bundles and
        # prefilled bug reports, so it must be sanitized like every other
        # surfaced text (CWE-532; CodeRabbit). core.failure.sanitize also
        # redacts HF tokens and *TOKEN*/*KEY*/*SECRET* env values, which a
        # frame's local-variable-free repr should never contain — but "should
        # never" is not a reason to log it unredacted.
        try:
            from core.failure import sanitize as _sanitize
            text = _sanitize("\n".join(blocks))
        except Exception:  # noqa: BLE001 — never lose the diagnostic to this
            logger.exception("Could not sanitize GPU-pool worker stacks; "
                             "omitting them rather than logging raw paths")
            return ""
        logger.warning(
            "%s exceeded %.0fs — stack of every GPU-pool worker at the moment "
            "it was abandoned. The deepest frame is where it is stuck; if that "
            "is inside the model rather than a data copy, this is a hang and "
            "not an under-provisioned machine (#1338):\n%s",
            _log_safe(what), timeout, text,
        )
        return text
    except Exception:  # noqa: BLE001 — diagnostics must never mask the timeout
        logger.exception("Could not capture GPU-pool worker stacks")
        return ""


#: Standard-library modules whose blocking primitives a wedged worker parks in.
#: Matched on the *file* of the deepest frame, so a user function that happens
#: to be named ``wait`` or ``result`` cannot be mistaken for one of these.
_WEDGE_STDLIB_FILES = (
    "/threading.py", "\\threading.py",
    "/asyncio/locks.py", "\\asyncio\\locks.py",
    "/concurrent/futures/_base.py", "\\concurrent\\futures\\_base.py",
    "/queue.py", "\\queue.py",
)

#: Blocking entry points within those modules. A thread sitting in one of these
#: is waiting on another thread, by definition — there is no slow-but-working
#: interpretation of it.
_WEDGE_FUNCTIONS = frozenset({
    "acquire", "wait", "result", "get", "join", "_wait_for_tstate_lock",
})

_FRAME_HEAD = re.compile(r'^\s*File "(?P<file>.+)", line \d+, in (?P<func>\S+)\s*$')


def _stack_shows_a_wedge(stacks: "str | None") -> bool:
    """True when the abandoned worker's DEEPEST frame is a blocking wait.

    The message this feeds is the one users actually read, and for years it
    said the same thing whatever happened: "too heavy for the available
    compute". That is a specific, testable claim, and when the worker is
    parked on a lock it is simply false — nothing was computed, so nothing was
    too heavy. #1416 and #1419 both arrived as "my machine is too slow"
    reports from people whose jobs never ran at all (a cold load waiting on a
    lock owned by another event loop, #1417), and #1329 is the same wedge seen
    from the dub loop. Every one of them was sent to look at their hardware.

    Only the last frame counts, and it must be a blocking primitive in a
    standard-library module. Both halves matter (CodeRabbit): a compute job's
    *callers* routinely include a lock it has already left, so scanning the
    whole stack would flag nearly everything; and an application function
    named ``wait`` or ``result`` is not evidence of anything, so the function
    name alone is not enough either.

    Reads the text :func:`log_gpu_pool_worker_stacks` already captured — no
    second stack walk, and no cost at all on the healthy path.

    Conservative: unknown or unparseable stacks return False and keep the old
    wording. Claiming a hang we cannot see would be the same mistake pointing
    the other way.
    """
    if not stacks:
        return False
    deepest = None
    for line in str(stacks).splitlines():
        m = _FRAME_HEAD.match(line)
        if m:
            deepest = m
    if deepest is None:
        return False
    func = deepest.group("func")
    if func not in _WEDGE_FUNCTIONS:
        return False
    path = deepest.group("file").replace("\\", "/")
    return any(
        path.endswith(tail.replace("\\", "/")) for tail in _WEDGE_STDLIB_FILES
    )


def _timeout_guidance(
    what: str, timeout: float, min_vram_gb: float = 0.0, *, wedged: bool = False,
) -> str:
    """Device-aware timeout message (#896): a CPU-only host must never be told
    to "set the engine to CPU" or blamed on VRAM — on CPU the job is simply
    compute-bound. GPU hosts keep the VRAM-contention guidance.

    Honesty fix (#1190/#1202): this used to promise "Capacity was restored
    automatically". It was not. Python cannot kill the abandoned worker
    thread — it runs to completion still holding its VRAM, so an immediate
    retry contends with the zombie and is *more* likely to fail, which is
    exactly how one slow chunk cascaded into a whole failed batch. The message
    now says what actually happens and gives both interactive and scripted
    callers something to do about it.
    """
    family = "cuda"  # conservative default: GPU wording if the probe fails
    device_name, vram_gb = "", 0.0
    try:
        from core.device_caps import detect_host_caps
        _caps = detect_host_caps()
        family = _caps.family
        device_name, vram_gb = _caps.device_name, _caps.vram_gb
    except Exception:  # noqa: BLE001 — guidance must never mask the timeout
        pass
    if wedged:
        # The worker spent the whole budget parked on a lock. None of the
        # hardware advice below applies — shorter text and a lighter engine
        # cannot speed up a job that never started (#1416/#1419/#1329).
        return (
            f"{what} was abandoned after {timeout:.0f}s without doing any "
            "work — it spent the whole time waiting on an internal lock, not "
            "computing. This is a bug in VoiceStudio, not a limit of your "
            "machine, so shorter text or a lighter engine won't help. "
            "Restart the backend to clear it (Settings → Logs → Backend has "
            "the stack trace that was captured), and please report it with "
            "that log at https://github.com/debpalash/VoiceStudio/issues — "
            "the trace names exactly where it stopped."
        )
    common = (
        f"{what} ran for more than {timeout:.0f}s of actual compute time and "
        "was abandoned — the backend is running, but this job was too heavy "
        "for the available compute. The abandoned job cannot be killed: it "
        "keeps running and keeps holding the device until it finishes on its "
        "own, so an immediate retry competes with it. Wait for the current "
        "job to drain (or restart the backend) before retrying; "
    )
    if family == "cpu":
        return common + (
            "this machine renders on CPU, where long generations are "
            "compute-bound. For a durable fix try shorter text or a lighter "
            "engine (OmniVoice GGUF and Supertonic-3 are CPU-tuned). If you "
            "expect very long single generations, raise "
            "OMNIVOICE_GENERATE_TIMEOUT_S."
        )
    # #1226/#1222: two users on 4 GB cards were told, generically, that the GPU
    # "is VRAM-starved" — true, but it read as a transient contention problem
    # they could flush their way out of, when their card was simply too small
    # for the engine they had selected. Say so instead — but ONLY when the
    # caller passed the engine's measured floor and the host is a dedicated-
    # VRAM family below it. This function serves every GPU-pool job (reference
    # transcribe, watermarking, dub steps, CPU-only engines on a GPU host), so
    # a threshold applied without knowing whose job it is would confidently
    # misdiagnose most of them. And on MPS `vram_gb` is a unified-memory
    # heuristic (RAM/2), not a dedicated pool to compare against.
    if (
        min_vram_gb > 0
        and family in ("cuda", "rocm")
        and 0 < vram_gb < min_vram_gb
    ):
        return common + (
            f"{device_name or 'this GPU'} has {vram_gb:.1f} GB of VRAM and "
            f"this engine wants about {min_vram_gb:.0f} GB — generations here "
            f"are slow enough to hit the limit even with nothing else loaded. "
            f"The durable fix is a lighter engine (OmniVoice GGUF and "
            f"Supertonic-3 are tuned for small/no GPU) or shorter text; "
            f"Flush caches / Unload the resident model (top toolbar or "
            f"Model Catalogue → Models) frees what little headroom there is. (Raise "
            f"OMNIVOICE_GENERATE_TIMEOUT_S if you'd rather let long "
            f"generations run.)"
        )
    return common + (
        "most often the GPU is VRAM-starved (a resident model and this job "
        "contend for memory). For a durable fix, Flush caches / Unload the "
        "resident model (top toolbar or Model Catalogue → Models) before retrying, "
        "try shorter text, a lighter engine, or set the engine to CPU in "
        "Model Catalogue → Models. (Raise OMNIVOICE_GENERATE_TIMEOUT_S for very "
        "long single generations.)"
    )


# ── Watermark pool (#1169 load, split out in #1190) ──────────────────────
# AudioSeal's generator is loaded with `AudioSeal.load_generator(...)` and
# never moved to an accelerator: `embed_watermark` is CPU work on CPU tensors.
# Running it on the GPU pool therefore reserves a *GPU* worker for a job that
# uses no VRAM — and since #1169 routed every producer (including per-chunk
# stream previews) through mark_synthetic, on an 8 GB host (exactly 1 GPU
# worker) each watermark embed serialized directly ahead of the next generate,
# doubling the effective queue depth of a streamed multi-chunk render.
# Giving it its own tiny pool removes that head-of-line blocking with no VRAM
# risk, because the work was never on the device to begin with.
_WATERMARK_STOP = object()


class _WatermarkExecutor(Executor):
    """Single daemon worker with a bounded shutdown contract.

    ``ThreadPoolExecutor`` uses non-daemon workers that Python joins at exit,
    so ``wait=False`` still delays process exit while ``wait=True`` can hang
    lifespan teardown forever. AudioSeal loading is not cooperatively
    cancellable; a daemon worker plus a bounded join is the only thread-based
    contract that both preserves in-process model warm-up and guarantees exit.
    """

    def __init__(self) -> None:
        self._items: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._shutdown = False
        self._thread: threading.Thread | None = None

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="watermark_0",
                    daemon=True,
                )
                self._thread.start()
            self._items.put((future, fn, args, kwargs))
        return future

    def _run(self) -> None:
        while True:
            item = self._items.get()
            if item is _WATERMARK_STOP:
                return
            future, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except (Exception, SystemExit, KeyboardInterrupt) as exc:
                future.set_exception(exc)

    def is_stopped(self) -> bool:
        """Whether shutdown has completed and this executor can be replaced."""
        with self._lock:
            return self._shutdown and (
                self._thread is None or not self._thread.is_alive()
            )

    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
        timeout: float | None = None,
    ) -> bool:
        with self._lock:
            self._shutdown = True
            thread = self._thread
            if cancel_futures:
                while True:
                    try:
                        item = self._items.get_nowait()
                    except queue.Empty:
                        break
                    if item is not _WATERMARK_STOP:
                        item[0].cancel()
            self._items.put(_WATERMARK_STOP)
        if wait and thread is not None:
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()


_watermark_pool_singleton: "_WatermarkExecutor | None" = None
_watermark_pool_lock = threading.Lock()
_watermark_pool_accepting = True


def begin_watermark_pool_lifecycle() -> None:
    """Open watermark submissions for a newly-started app lifespan."""
    global _watermark_pool_accepting, _watermark_pool_singleton
    with _watermark_pool_lock:
        if (
            _watermark_pool_singleton is not None
            and _watermark_pool_singleton.is_stopped()
        ):
            _watermark_pool_singleton = None
        _watermark_pool_accepting = (
            _watermark_pool_singleton is None
            or not _watermark_pool_singleton.is_shutdown()
        )


def get_watermark_pool() -> _WatermarkExecutor:
    """Dedicated 1-worker pool for provenance marking. Built lazily so hosts
    with watermarking disabled never spawn the thread.

    The executor is captured and returned UNDER the lock: reading the global
    again after an unlocked null-check could race shutdown_watermark_pool's
    reset and hand out None (CodeRabbit, PR #1577)."""
    global _watermark_pool_accepting, _watermark_pool_singleton
    with _watermark_pool_lock:
        if not _watermark_pool_accepting:
            if (
                _watermark_pool_singleton is not None
                and _watermark_pool_singleton.is_stopped()
            ):
                _watermark_pool_singleton = None
                _watermark_pool_accepting = True
            else:
                raise RuntimeError("watermark executor is shutting down")
        if (
            _watermark_pool_singleton is not None
            and _watermark_pool_singleton.is_stopped()
        ):
            _watermark_pool_singleton = None
        if _watermark_pool_singleton is None:
            _watermark_pool_singleton = _WatermarkExecutor()
        return _watermark_pool_singleton


def shutdown_watermark_pool(*, timeout: float = 20.0) -> None:
    """Drain the watermark pool at app shutdown (PR #1577).

    Refuse queued work and wait for the active operation: Python cannot kill
    a thread inside AudioSeal loading, so returning early would let model
    initialization continue during interpreter teardown. The draining pool
    remains published until its worker stops, preventing concurrent producers
    from creating a replacement that escapes this shutdown. A process that
    keeps running after lifespan shutdown (the test suite does exactly this)
    gets a fresh pool once the old worker has actually stopped."""
    global _watermark_pool_accepting, _watermark_pool_singleton
    with _watermark_pool_lock:
        _watermark_pool_accepting = False
        pool = _watermark_pool_singleton
    if pool is not None:
        stopped = pool.shutdown(
            wait=True,
            cancel_futures=True,
            timeout=max(0.0, float(timeout)),
        )
        if stopped:
            with _watermark_pool_lock:
                if _watermark_pool_singleton is pool:
                    _watermark_pool_singleton = None
        else:
            logger.warning(
                "Watermark worker exceeded the %.1fs shutdown deadline; "
                "abandoning its daemon thread",
                timeout,
            )


model = None  # type: ignore
_model_lock = asyncio.Lock()

#: Process-wide exclusion for a cold load that runs INLINE on a GPU-pool
#: worker (#1417). `_model_lock` cannot serve there — it is an asyncio.Lock
#: bound to the server loop, and that path arrives on a bootstrap loop from
#: another thread. A threading.Lock is loop-agnostic, so the two together
#: guarantee only one cold load is ever in flight whichever route reached it.
_model_load_thread_lock = threading.Lock()
_last_used = time.time()
# Idle timeout is resolved per-tick in _resolve_idle_timeout() (MM2-05) from
# prefs/env/core.config — no module-level duplicate of IDLE_TIMEOUT_SECONDS.

# ── Loading sub-stage tracker ────────────────────────────────────────
# Updated by _load_model_sync() so get_model_status() can report
# granular progress to the frontend pill.
_loading_detail: dict = {
    "sub_stage": None,   # importing | loading_weights | loading_asr | compiling | ready | error
    "detail": "",        # human-readable description
    "error": None,       # error message string if failed
    "progress": None,    # 0-100 percentage (None = indeterminate)
}

def _configure_rocm_if_needed(torch):
    """Auto-set HSA_OVERRIDE_GFX_VERSION for AMD GPUs on ROCm.

    ROCm-enabled PyTorch reports `torch.cuda.is_available() == True` but
    some consumer AMD GPUs have GFX IDs the installed build wasn't compiled
    for. Setting HSA_OVERRIDE_GFX_VERSION lets them run with the closest
    supported architecture.

    The override is applied **only when the native gfx is genuinely absent
    from this build's arch list**. Newer ROCm wheels support parts that used
    to need remapping (gfx1151/Strix Halo is native from ROCm 7.x), and
    overriding a natively-supported GPU forces it onto foreign kernels for no
    reason — so the map is a fallback, not an unconditional rewrite.
    """
    from core.device_caps import (
        ROCM_GFX_OVERRIDES,
        build_arch_list,
        hsa_override_for,
    )

    if os.environ.get("HSA_OVERRIDE_GFX_VERSION"):
        return  # User already set it manually
    try:
        device_name = torch.cuda.get_device_name(0).lower()
        # Only AMD GPUs need this — skip NVIDIA
        if not any(kw in device_name for kw in ("amd", "radeon", "instinct")):
            return
        # Try to read the GFX version from the device properties
        props = torch.cuda.get_device_properties(0)
        gcn_arch = getattr(props, "gcnArchName", "") or ""
        gfx_id = gcn_arch.split(":")[0].strip().lower()
        target = ROCM_GFX_OVERRIDES.get(gfx_id)
        if not target:
            return
        arch_list = {a.split(":")[0].strip().lower() for a in build_arch_list(torch)}
        if not arch_list:
            # Metadata unavailable — an UNKNOWN build, not a confirmed
            # mismatch. Remapping on a guess could push a natively-supported
            # GPU onto foreign kernels, so fail open and change nothing.
            logger.debug(
                "ROCm: no arch list from this torch build; leaving "
                "HSA_OVERRIDE_GFX_VERSION unset for %s (%s)", device_name, gfx_id,
            )
            return
        if gfx_id in arch_list:
            logger.info("ROCm: %s (%s) is natively supported by this build; "
                        "no HSA_OVERRIDE_GFX_VERSION needed", device_name, gfx_id)
            return
        if target not in arch_list:
            # The remap target isn't in this build either — setting the
            # override would only change WHICH kernel is missing. Leave it
            # unset so check_device_compatibility() reports the real mismatch
            # and the CPU fallback engages.
            logger.warning(
                "ROCm: %s (%s) is unsupported by this build and its remap "
                "target %s is missing too — not setting "
                "HSA_OVERRIDE_GFX_VERSION.", device_name, gfx_id, target,
            )
            return
        override = hsa_override_for(target)
        os.environ["HSA_OVERRIDE_GFX_VERSION"] = override
        logger.info("ROCm: auto-set HSA_OVERRIDE_GFX_VERSION=%s (%s) for %s (%s)",
                    override, target, device_name, gfx_id)
    except Exception as e:
        logger.debug("ROCm GFX auto-config skipped: %s", e)


def check_device_compatibility():
    """Check if PyTorch supports the current GPU's architecture.

    Returns (compatible, warning_message). Compatible is True if OK or
    no discrete GPU is present. The arch comparison itself lives in
    ``core.device_caps.arch_unsupported()`` — shared with the probe, and
    CUDA/ROCm-aware (a ROCm build lists ``gfx…``, not ``sm_…`` — #1228).
    """
    from core.device_caps import arch_unsupported

    torch = _lazy_torch()
    if not torch.cuda.is_available():
        return True, None
    mismatch = arch_unsupported(torch)
    if mismatch is None:
        return True, None
    device_arch, arch_list = mismatch
    try:
        device_name = torch.cuda.get_device_name(0)
    except Exception:
        device_name = "GPU"
    if getattr(getattr(torch, "version", None), "hip", None) is not None:
        return False, (
            f"{device_name} ({device_arch}) is not supported by this ROCm "
            f"PyTorch build. Supported architectures: {', '.join(arch_list)}. "
            f"Set HSA_OVERRIDE_GFX_VERSION to the closest supported target "
            f"(e.g. 11.0.0 for a gfx11xx card) or install a ROCm build that "
            f"lists {device_arch}."
        )
    return False, (
        f"{device_name} ({device_arch}) is not supported by this PyTorch build. "
        f"Supported architectures: {', '.join(arch_list)}. "
        f"Install a build that covers it: pip install --force-reinstall torch "
        f"--index-url https://download.pytorch.org/whl/cu128"
    )


def get_best_device():
    """Detect the best available compute device.

    Priority: CUDA/ROCm > Intel XPU > DirectML > MPS > CPU

    The *family* decision delegates to ``core.device_caps.detect_host_caps()``
    (the single source of truth) so the probe and this loader can never
    disagree. This function keeps the side-effects the probe deliberately
    avoids: the ROCm ``HSA_OVERRIDE_GFX_VERSION`` env override and the
    DirectML device-string return (DirectML is not a torch device family, so
    the probe reports it as ``cpu`` — we still resolve the real device string
    here for Windows DirectML users). The string contract is unchanged:
    ``"cuda"`` / ``"xpu"`` / a DirectML device string / ``"mps"`` / ``"cpu"``.
    """
    from core.device_caps import detect_host_caps

    torch = _lazy_torch()
    family = detect_host_caps().family

    # ── NVIDIA CUDA or AMD ROCm (both present through torch.cuda) ─────
    if family in ("cuda", "rocm"):
        _configure_rocm_if_needed(torch)
        compatible, warning = check_device_compatibility()
        if not compatible:
            logger.warning(warning)
            # #756: the GPU's compute capability isn't in this torch build's arch
            # list, so CUDA kernels can't launch ("no kernel image is available
            # for execution") — every generate would 500. Too-old (Pascal sm_61)
            # and too-new (Blackwell sm_120 on pre-cu128 wheels) both land here.
            # Fall back to CPU so the app WORKS (slowly) instead of dead-ending;
            # OMNIVOICE_FORCE_CUDA=1 overrides for users who installed a matching
            # torch and know the arch_list probe is wrong for their setup.
            if not _env_flag("OMNIVOICE_FORCE_CUDA"):
                logger.warning(
                    "Falling back to CPU: this GPU is unsupported by the installed "
                    "PyTorch build (set OMNIVOICE_FORCE_CUDA=1 to force CUDA anyway)."
                )
                return "cpu"
        return "cuda"

    # ── Intel Arc / discrete GPU via IPEX ────────────────────────────
    if family == "xpu":
        try:
            logger.info("Using Intel XPU device: %s", torch.xpu.get_device_name(0))
        except Exception:
            logger.info("Using Intel XPU device")
        return "xpu"

    # ── Apple Silicon MPS ────────────────────────────────────────────
    # Checked BEFORE DirectML to mirror the probe's family-priority order
    # (cuda > rocm > xpu > mps; DirectML is not a torch family) so the loader
    # and detect_host_caps() never disagree on a host that somehow exposes both.
    if family == "mps":
        return "mps"

    # ── DirectML — universal Windows GPU (probe reports this as "cpu") ─
    # Reached only when no torch family was detected (family == "cpu"), which is
    # exactly the DirectML case — the probe classifies DirectML hosts as cpu.
    try:
        import torch_directml
        if torch_directml.device_count() > 0:
            logger.info("Using DirectML device (GPU %d)", 0)
            return str(torch_directml.device(0))
    except ImportError:
        pass

    return "cpu"

_COMPILE_ERR_MODULE_PREFIXES = ("torch._dynamo", "torch._inductor", "torch.fx", "triton")
_COMPILE_ERR_TB_MARKERS = ("/_dynamo/", "/_inductor/", "/triton/", "torch/fx/")
_COMPILE_ERR_MSG_MARKERS = (
    "dynamo", "inductor", "triton", "cudagraph",
    "symbolically trace", "torch.compile", "fx graph",
)


def _is_compile_runtime_failure(exc: BaseException) -> bool:
    """True when an exception originates in the torch.compile stack (Dynamo /
    Inductor / Triton / FX / CUDA-graph trees) rather than in the model itself.

    #278: on GPU architectures Triton doesn't support yet (e.g. Blackwell
    sm_120), the compiled model dies mid-generation with errors like
    "Detected that you are using FX to symbolically trace a dynamo-optimized
    function" or an AssertionError out of torch/_inductor/cudagraph_trees.py.
    Walks the exception chain and checks (a) the exception type's module,
    (b) the message, (c) the traceback file paths — the cudagraph case is a
    bare AssertionError, so the traceback check is load-bearing.
    """
    import traceback as _tb

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        mod = type(cur).__module__ or ""
        if mod.startswith(_COMPILE_ERR_MODULE_PREFIXES):
            return True
        msg = str(cur).lower()
        if any(marker in msg for marker in _COMPILE_ERR_MSG_MARKERS):
            return True
        try:
            for frame in _tb.extract_tb(cur.__traceback__):
                filename = (frame.filename or "").replace("\\", "/")
                if any(marker in filename for marker in _COMPILE_ERR_TB_MARKERS):
                    return True
        except Exception as traceback_scan_error:
            logging.debug(
                "Skipping traceback marker scan while classifying compile runtime failure: %s",
                traceback_scan_error,
            )
        # Follow the chain, honoring `raise ... from None` (the eager-retry
        # path suppresses the original compile error so a genuine eager
        # failure isn't misclassified as a compile failure).
        if cur.__cause__ is not None:
            cur = cur.__cause__
        elif not cur.__suppress_context__:
            cur = cur.__context__
        else:
            cur = None
    return False


def _install_compile_fallback(_model) -> None:
    """Wrap ``model.generate`` so a torch.compile failure at inference time
    falls back to the eager (uncompiled) model instead of failing the
    generation (#278).

    All TTS paths (generate, archetype previews, dub, stream, batch) funnel
    through ``model.generate``, so this is the single choke point. On a
    compile-stack failure we: log a clear warning, restore the eager module
    (``OptimizedModule._orig_mod``), disable compile for the rest of the
    session via ``engine_env.mark_compile_runtime_failure``, reset dynamo
    state, and retry the call once eagerly. Non-compile errors (real OOM,
    validation, …) propagate unchanged — fully backward compatible for users
    whose torch.compile works.
    """
    orig_generate = _model.generate

    def _generate_with_compile_fallback(*args, **kwargs):
        try:
            return orig_generate(*args, **kwargs)
        except Exception as exc:
            compiled = getattr(_model, "llm", None)
            eager = getattr(compiled, "_orig_mod", None)
            if eager is None or not _is_compile_runtime_failure(exc):
                raise
            logger.warning(
                "torch.compile runtime failure during generation (%s: %s) — "
                "falling back to the eager model and disabling torch.compile "
                "for this session. Generation is being retried without it.",
                type(exc).__name__, exc,
            )
            from services import engine_env
            engine_env.mark_compile_runtime_failure(f"{type(exc).__name__}: {exc}")
            _model.llm = eager
            try:
                torch = _lazy_torch()
                torch._dynamo.reset()
            except Exception as reset_exc:
                logger.debug(
                    "Non-fatal: failed to reset torch._dynamo state after compile failure (%s: %s). "
                    "Continuing with eager fallback.",
                    type(reset_exc).__name__,
                    reset_exc,
                )
            try:
                return orig_generate(*args, **kwargs)
            except Exception as eager_exc:
                # `from None` so a genuine eager failure (e.g. a real OOM)
                # isn't chained to — and misclassified as — the compile error.
                raise eager_exc from None

    _model.generate = _generate_with_compile_fallback


# ── FlashInfer runtime fallback (upstream k2-fsa port) ──────────────────────


def _is_flashinfer_runtime_failure(exc: BaseException) -> bool:
    """True when an exception originates in the FlashInfer fast path (the
    flashinfer package, our omnivoice_flashinfer patch module, or CUDA-graph
    capture/replay) rather than in the model or the request itself. Same
    chain/traceback walk as ``_is_compile_runtime_failure``."""
    import traceback as _tb

    tb_markers = ("/flashinfer/", "omnivoice_flashinfer")
    msg_markers = ("flashinfer", "cuda graph", "cudagraph")
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        mod = type(cur).__module__ or ""
        if mod.startswith("flashinfer"):
            return True
        msg = str(cur).lower()
        if any(marker in msg for marker in msg_markers):
            return True
        try:
            for frame in _tb.extract_tb(cur.__traceback__):
                filename = (frame.filename or "").replace("\\", "/")
                if any(marker in filename for marker in tb_markers):
                    return True
        except Exception:
            pass
        if cur.__cause__ is not None:
            cur = cur.__cause__
        elif not cur.__suppress_context__:
            cur = cur.__context__
        else:
            cur = None
    return False


def _unapply_flashinfer(_model) -> None:
    """Restore the standard execution path on a FlashInfer-patched model.

    ``apply_flashinfer`` works entirely through *instance-level* state —
    MethodType-bound ``forward``/``_generate_iterative`` overrides and
    ``_fi_*`` attributes — so deleting those attributes restores the class
    implementations exactly. The attention implementation is restored to the
    one captured before apply (``_fi_orig_attn_impl`` — could be
    flash_attention_2, not just sdpa), and use_cache is re-enabled."""
    llm = getattr(_model, "llm", None)
    orig_attn = getattr(_model, "_fi_orig_attn_impl", None) or "sdpa"
    if llm is not None:
        for module in llm.modules():
            if "forward" in vars(module):
                del module.forward
            for attr in ("_fi_w_qkv", "_fi_qkv_split", "_fi_rope_theta", "_fi_w_gate_up"):
                if attr in vars(module):
                    delattr(module, attr)
        try:
            llm.set_attn_implementation(orig_attn)
        except Exception:
            logger.exception(
                "failed to restore %s attention after FlashInfer", orig_attn
            )
        llm.config.use_cache = True
    for attr in (
        "_fi_orig_attn_impl",
        "_generate_iterative",
        "_fi_runner",
        "_fi_graph_cache",
        "_fi_enable_cuda_graph",
        "_fi_graph_buckets",
        "_fi_overhead_budget",
    ):
        if attr in vars(_model):
            delattr(_model, attr)


def _install_flashinfer_fallback(_model) -> None:
    """Wrap ``model.generate`` so a FlashInfer failure at inference time falls
    back to the standard path instead of failing the generation — the same
    contract as ``_install_compile_fallback`` (#278): an optimization must
    never turn a working generation into an error."""
    orig_generate = _model.generate

    def _generate_with_flashinfer_fallback(*args, **kwargs):
        try:
            return orig_generate(*args, **kwargs)
        except Exception as exc:
            if not _is_flashinfer_runtime_failure(exc):
                raise
            logger.warning(
                "FlashInfer runtime failure during generation (%s: %s) — "
                "restoring the standard path and disabling FlashInfer for "
                "this session. Generation is being retried without it.",
                type(exc).__name__, exc,
            )
            from services import engine_env
            engine_env.mark_flashinfer_runtime_failure(
                f"{type(exc).__name__}: {exc}"
            )
            # Unapply BEFORE exposing the eager path: while the teardown
            # mutates modules, _model.generate still routes through the
            # thread-affinity wrapper, so a concurrent render queues behind
            # this call instead of racing the half-restored model (Greptile,
            # #1565 round 2). Only a fully restored model is published.
            _unapply_flashinfer(_model)
            _model.generate = orig_generate
            try:
                return orig_generate(*args, **kwargs)
            except Exception as plain_exc:
                # `from None`: a genuine standard-path failure must not be
                # chained to — and misread as — the FlashInfer error.
                raise plain_exc from None

    _model.generate = _generate_with_flashinfer_fallback


# ── #315: thread affinity for cudagraph-compiled models ─────────────────────
# `torch.compile(mode="reduce-overhead")` captures CUDA graphs, and captured
# graph state is **thread-local** (torch/_inductor/cudagraph_trees keys its
# tree manager off the capturing thread). The `_gpu_pool` runs up to
# `_GPU_WORKER_CAP` threads, so render #1 captures the graph on worker A and a
# later render dispatched to worker B replays against mismatched cudagraph
# state — silently corrupting the audio (static / slowed playback, no
# exception, so the #278 eager fallback never fires). Fix: every call into a
# cudagraph-compiled model executes on ONE dedicated thread; uncompiled
# models (CPU / MPS / Windows-no-Triton / compile-disabled) keep the full pool.

_TORCH_COMPILE_MODE = "reduce-overhead"
# Compile modes that enable CUDA graphs under the hood — these need the
# single-thread affinity below. "default" / "max-autotune-no-cudagraphs"
# would not.
_CUDAGRAPH_COMPILE_MODES = frozenset({"reduce-overhead", "max-autotune"})

_compiled_inference_executor: "ThreadPoolExecutor | None" = None
_compiled_inference_thread_ident: "int | None" = None


def _get_compiled_inference_executor() -> ThreadPoolExecutor:
    """The single-thread executor that owns ALL inference on a compiled model.

    Created lazily the first time a model is compiled with a cudagraph mode;
    reused across model reloads (idle unload → reload keeps the same thread,
    which is fine — a fresh compile simply captures its graphs there too).
    The worker is spun up eagerly so its thread ident is known for the
    re-entrancy guard in `_install_compile_thread_affinity`.
    """
    global _compiled_inference_executor, _compiled_inference_thread_ident
    if _compiled_inference_executor is None:
        _compiled_inference_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="compiled-infer",
        )
        _compiled_inference_thread_ident = _compiled_inference_executor.submit(
            threading.get_ident
        ).result()
    return _compiled_inference_executor


def _install_compile_thread_affinity(_model) -> None:
    """Pin every ``model.generate`` call to the dedicated compile thread (#315).

    Wraps ``model.generate`` (the single choke point all TTS paths funnel
    through — generate, archetype previews, dub, stream, batch) so the call
    body always runs on `_get_compiled_inference_executor()`'s one thread.
    That makes the thread that *captures* the CUDA graph on the first render
    and the thread that *replays* it on every later render the same thread,
    deterministically, regardless of which `_gpu_pool` worker dispatched it.

    Installed AFTER `_install_compile_fallback`, so the call-time order is:
    caller thread → hop to the dedicated thread → eager-fallback wrapper →
    real generate (the #278 classification/retry also runs on the dedicated
    thread, with native tracebacks). The hop is a no-op when already on the
    dedicated thread — a 1-worker executor submitting to itself would
    deadlock, so the re-entrancy guard is load-bearing.
    """
    executor = _get_compiled_inference_executor()
    inner_generate = _model.generate

    def _generate_on_compile_thread(*args, **kwargs):
        if threading.get_ident() == _compiled_inference_thread_ident:
            return inner_generate(*args, **kwargs)
        return executor.submit(inner_generate, *args, **kwargs).result()

    _model.generate = _generate_on_compile_thread


def _set_loading(sub_stage: str, detail: str = "", error: str | None = None, progress: float | None = None):
    """Update the loading detail dict atomically."""
    _loading_detail["sub_stage"] = sub_stage
    _loading_detail["detail"] = detail
    _loading_detail["error"] = error
    _loading_detail["progress"] = progress


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def should_preload_tts_asr() -> bool:
    """Whether VoiceStudio.from_pretrained should attach PyTorch Whisper.

    The default is intentionally false. On Apple Silicon, eager TTS + ASR
    loading can overcommit unified memory and leave desktop startup stuck
    at the model-loading stage. ASR backends still load on demand.
    """
    return _env_flag("OMNIVOICE_PRELOAD_TTS_ASR")


def _is_incomplete_cache_error(exc: BaseException) -> bool:
    """True when `exc` is the truncated-HF-cache class (#352 / #581 / #1273).

    transformers raises an OSError when the on-disk snapshot has config and
    tokenizer files but no weight shard — the signature of an interrupted
    download. We match on the message (stable across transformers 4.x/5.x)
    rather than the error type, since the same OSError type covers unrelated
    I/O failures.

    There are TWO wordings, and this used to match only the first, so a
    half-written repo whose *subfolder* failed to load (#1273:
    "Error no file named model.safetensors, … found in directory
    …/snapshots/<rev>/audio_tokenizer") got neither the automatic repair nor
    an actionable message — just a raw 500. `core.failure` owns the phrase
    list so the heal and the error text can't drift apart."""
    from core.failure import is_incomplete_cache_message

    return is_incomplete_cache_message(str(exc))


def _is_corrupt_model_file_error(exc: BaseException) -> bool:
    """True when a model weight or config file cannot be parsed.

    The other half of the interrupted-download class (#1406). transformers
    only raises the "does not appear to have a file named …" signature when
    the shard is *absent*; a shard that stops mid-file, gets truncated by
    antivirus, or is actually a saved HTML error page opens fine and then
    fails inside safetensors:

        Error while deserializing header: header too large

    That is a ``SafetensorError`` from a Rust extension — not an ``OSError``,
    so it never reached the recovery ladder and surfaced as a raw 500 on every
    generation (the reporter hit it from voice design *and* from a gallery
    preview, which is what a shared broken shard looks like).

    The whole exception chain is checked, not just the outermost message:
    transformers wraps the tensor library's error in its own before it gets
    here, and matching only the surface would miss every wrapped case."""
    from core.failure import is_corrupt_model_file_message

    return any(is_corrupt_model_file_message(str(e)) for e in _exception_chain(exc))


def _is_corrupt_weights_error(exc: BaseException) -> bool:
    """Backward-compatible wrapper for the original #1406 helper name."""
    return _is_corrupt_model_file_error(exc)


def _hf_offline() -> bool:
    """Respect HF's offline switches so repair never makes a network call the
    user opted out of. `snapshot_download` would itself raise offline, but
    checking up front lets us skip straight to the actionable message."""
    return _env_flag("HF_HUB_OFFLINE") or _env_flag("TRANSFORMERS_OFFLINE")


# ── Broken-snapshot-link self-heal ───────────────────────────────────
# A sibling of the incomplete-cache class above: the blobs are FULLY
# downloaded, but the snapshots/<rev>/ entries pointing at them are dangling
# symlinks (0 KB) or zero-byte stand-ins — blob-naming mismatches between
# download modes, interrupted renames, or antivirus interference all produce
# this state (reported on Windows, where the NTFS links show as 0 KB, but the
# heal is generic). os.path.isfile() on a dangling link is False, so
# transformers raises the same "does not appear to have a file named …"
# signature even though the bytes are on disk. The resume repair below can't
# fix it (snapshot_download may trust/short-circuit on the existing broken
# entry), so rung 0 of the recovery ladder deletes exactly the broken entries
# and restores them — see services.hf_cache_repair.

# Repos this process already attempted the link self-heal for — the retry
# after a repair may only happen ONCE per repo per process, so a cache that
# stays broken can't loop repair↔retry.
_LINK_REPAIR_ATTEMPTED: set[str] = set()

#: Repos whose weights we have already force-re-downloaded this process
#: (#1406). Without it, a shard that stays unparseable after a full re-fetch
#: would pull the whole model again on EVERY generate request — one bad file
#: turning into unbounded traffic. Same once-per-repo-per-process contract as
#: the snapshot-link repair above (CodeRabbit).
_FORCED_REDOWNLOAD_ATTEMPTED: set[str] = set()


def _selfheal_broken_snapshot_links(checkpoint: str) -> bool:
    """Rung 0 of cache recovery: delete-and-restore broken snapshot entries.

    Returns True only when broken entries were found, removed AND restored —
    i.e. retrying the load is worth it. At most one attempt per repo per
    process. Never raises; when it returns False the legacy resume/force
    ladder still runs."""
    if checkpoint in _LINK_REPAIR_ATTEMPTED:
        return False
    _LINK_REPAIR_ATTEMPTED.add(checkpoint)
    if os.path.isdir(checkpoint):
        return False  # a local-directory checkpoint doesn't use the hub cache
    try:
        from services.hf_cache_repair import repair_repo_cache
        summary = repair_repo_cache(checkpoint)
    except Exception as repair_err:  # repair must never break the ladder
        logger.warning("Snapshot-link self-heal for %s errored: %s",
                       checkpoint, repair_err)
        return False
    if summary.get("removed") and summary.get("ok"):
        logger.warning(
            "Model cache for %s had %d broken file link(s) — repaired "
            "automatically (%s), retrying the load.",
            checkpoint, summary["removed"],
            summary.get("outcome") or "healed",
        )
        return True
    if summary.get("found"):
        logger.warning(
            "Model cache for %s has %d broken file link(s) that could not be "
            "auto-repaired (%s).",
            checkpoint, summary["found"], summary.get("error") or "unknown",
        )
    return False


def _manual_cache_delete_hint(checkpoint: str) -> str:
    """Names the exact on-disk folder to delete when every auto-repair rung
    failed — "delete the model" is only actionable if the user can find it.
    Empty for local-directory checkpoints (they don't live in the hub cache)."""
    try:
        if os.path.isdir(checkpoint):
            return ""
        from services.hf_cache_repair import repo_cache_dir
        return (
            f" If the problem persists, quit VoiceStudio, delete "
            f"{repo_cache_dir(checkpoint)} and restart — the model "
            "re-downloads automatically."
        )
    except Exception:
        return ""


# Why the LAST _repair_model_cache run failed ("" when it succeeded / hasn't
# run). #886: the "could not be auto-repaired" message used to drop the cause
# entirely, so a mirror outage, offline mode, or a full disk all read the same.
_last_repair_error: str = ""


def _repair_failure_detail() -> str:
    """One sanitized clause naming why auto-repair failed, or "" (#886).

    Feeds user-facing messages (the generate 500 detail / model status), so it
    goes through core.failure.sanitize — and because the cause text is now part
    of the surfaced error, the shared HF-mirror hint (#874) fires on it when
    the repair failed against an unreachable configured mirror."""
    if not _last_repair_error:
        return ""
    try:
        from core.failure import sanitize
        cause = sanitize(_last_repair_error)
    except Exception:
        cause = _last_repair_error
    return f" Auto-repair failed with: {cause}."


def _repair_model_cache(checkpoint: str, *, force: bool = False) -> bool:
    """Re-fetch a checkpoint's missing files in place and report success.

    An interrupted download leaves the cache missing only some files;
    `snapshot_download` resumes/fills exactly those (already-present, correctly
    sized blobs are skipped by hash, so a near-complete cache repairs in
    seconds and a complete one would no-op). Returns False — leaving the caller
    to surface the actionable delete-and-reinstall message — when repair is
    impossible (offline) or the re-fetch itself fails (no network, gated repo,
    full disk). Never raises; repair is best-effort.

    ``force=True`` passes ``force_download`` so the re-fetch replaces files that
    are *present but corrupt* — a truncated/garbled blob that still has the right
    size won't be re-fetched by the default resume (#739). It re-downloads the
    whole snapshot, so it's the last resort the load path only reaches after a
    plain resume-repair didn't fix the cache."""
    global _last_repair_error
    _last_repair_error = ""
    if _hf_offline():
        logger.warning(
            "Model cache for %s is incomplete but HF offline mode is set — "
            "cannot auto-repair.", checkpoint,
        )
        _last_repair_error = (
            "Hugging Face offline mode is enabled (HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE)"
        )
        return False
    try:
        from huggingface_hub import snapshot_download
    except Exception as imp_err:  # pragma: no cover - huggingface_hub is a hard dep
        logger.warning("Cannot import snapshot_download to repair cache: %s", imp_err)
        _last_repair_error = f"{type(imp_err).__name__}: {imp_err}"
        return False
    try:
        from services.hf_cache_repair import hf_cache_home
        from services.hf_revisions import installed_revision
        cache_root = hf_cache_home()
        revision = installed_revision(checkpoint, cache_root)
    except (OSError, ValueError) as revision_err:
        _last_repair_error = str(revision_err)
        logger.warning("Refusing unpinned model repair for %s: %s", checkpoint, revision_err)
        return False
    dl_kwargs: dict = {
        "repo_id": checkpoint,
        "revision": revision,
        "cache_dir": cache_root,
    }
    # Explicit endpoint (HF_ENDPOINT / pref) wins; otherwise the automatic
    # endpoint selection's cached pick applies (services.endpoint_race).
    try:
        from services import endpoint_race
        endpoint = endpoint_race.effective_endpoint()
    except Exception:  # endpoint resolution must never break the repair
        endpoint = os.environ.get("HF_ENDPOINT")
    if endpoint:
        dl_kwargs["endpoint"] = endpoint
    if force:
        # Replace present-but-corrupt blobs that resume would trust by size.
        dl_kwargs["force_download"] = True
    if os.name == "nt":
        # Match the install path (download.py): avoid symlinks on Windows.
        dl_kwargs["local_dir_use_symlinks"] = False

    def _attempt() -> None:
        """One snapshot_download, tolerating an hf_hub that rejects the optional
        symlink knob. Lets real failures (network, gated repo, disk) propagate."""
        try:
            snapshot_download(**dl_kwargs)  # nosec B615 -- installed immutable revision
        except TypeError:
            # Older/newer huggingface_hub may not accept local_dir_use_symlinks
            # on a cache-only call — retry without the optional knob.
            dl_kwargs.pop("local_dir_use_symlinks", None)
            snapshot_download(**dl_kwargs)  # nosec B615 -- installed immutable revision

    # Bounded retries (#739): an incomplete cache *is* an interrupted download, so
    # a single transient blip mid-repair shouldn't drop the user back to a manual
    # delete-and-reinstall. snapshot_download resumes between attempts (present,
    # correctly-sized blobs are skipped by hash), so each retry continues where
    # the last left off — cheap and idempotent. Counts/backoff are env-tunable
    # for restricted networks and kept fast (backoff=0) in tests.
    try:
        retries = max(1, int(os.environ.get("OMNIVOICE_MODEL_REPAIR_RETRIES", "3")))
    except ValueError:
        retries = 3
    try:
        backoff = max(0.0, float(os.environ.get("OMNIVOICE_MODEL_REPAIR_BACKOFF_S", "2")))
    except ValueError:
        backoff = 2.0

    logger.info(
        "Auto-repairing incomplete model cache for %s (up to %d attempt(s)) …",
        checkpoint, retries,
    )
    for attempt in range(1, retries + 1):
        try:
            _attempt()
            logger.info("Auto-repair of %s completed; retrying model load.", checkpoint)
            return True
        except Exception as e:
            logger.warning(
                "Auto-repair of %s attempt %d/%d failed: %s",
                checkpoint, attempt, retries, e,
            )
            _last_repair_error = f"{type(e).__name__}: {e}"
            if attempt < retries:
                # Endpoint failover (auto mode only, once per repo per
                # process — same guard pattern as the snapshot-link rung): a
                # network-classified repair failure re-races the endpoints so
                # the next attempt retries on the winner instead of burning
                # every retry on a dead host. Explicit user endpoints are
                # never switched.
                try:
                    from services import endpoint_race
                    if endpoint_race.reselect_after_failure(checkpoint, str(e)):
                        new_ep = endpoint_race.effective_endpoint()
                        if new_ep:
                            dl_kwargs["endpoint"] = new_ep
                        else:
                            dl_kwargs.pop("endpoint", None)
                        logger.info(
                            "Auto-repair of %s: endpoint failover — retrying on %s",
                            checkpoint, new_ep or "https://huggingface.co",
                        )
                except Exception:  # failover must never break the ladder
                    pass
                if backoff:
                    time.sleep(backoff * attempt)
    return False


_DEFAULT_OMNIVOICE_CHECKPOINT = "k2-fsa/OmniVoice"


def resolve_omnivoice_checkpoint() -> str:
    """Resolve the VoiceStudio TTS checkpoint from ``OMNIVOICE_MODEL``, self-healing
    a misconfigured value.

    A valid checkpoint is either a HuggingFace repo id (``org/repo`` — contains a
    ``/``) or an existing local directory. A bare token like ``"omnivoice"`` — a
    TTS *engine id* that leaked into ``OMNIVOICE_MODEL`` (e.g. a stale pref/env) —
    is neither, and would crash model load with *"omnivoice is not a local folder
    and is not a valid model identifier listed on huggingface.co/models"* (#693).
    Fall back to the default rather than 500 on every launch.
    """
    checkpoint = os.environ.get("OMNIVOICE_MODEL", _DEFAULT_OMNIVOICE_CHECKPOINT).strip()
    if not checkpoint:
        return _DEFAULT_OMNIVOICE_CHECKPOINT
    if checkpoint == "test":
        # Test-suite sentinel (tests/conftest.py sets OMNIVOICE_MODEL=test):
        # return it verbatim. Self-healing it to the real default — "test"
        # is a bare token like the #693 engine-id leak — would hand every
        # app-booting test the real 2.3 GB k2-fsa/OmniVoice checkpoint,
        # which is exactly the download the sentinel exists to prevent. A
        # real load against "test" fails fast with a clear HF error instead.
        return checkpoint
    # Honor a HF repo id (org/repo) or an EXPLICIT local path (absolute, or with
    # a path separator). A bare token like "omnivoice" must NOT be treated as a
    # local dir even if a cwd-relative folder happens to share its name — that
    # is exactly the engine-id leak (#693), so self-heal to the default.
    if "/" in checkpoint or "\\" in checkpoint or os.path.isabs(checkpoint):
        return checkpoint
    logger.warning(
        "OMNIVOICE_MODEL=%r is not a HuggingFace repo id (org/repo) or a local "
        "path — falling back to %s (#693).",
        checkpoint, _DEFAULT_OMNIVOICE_CHECKPOINT,
    )
    return _DEFAULT_OMNIVOICE_CHECKPOINT


#: CPython's exact executor-rejection message once interpreter shutdown began
#: (concurrent/futures/thread.py). The "interpreter" word is what separates a
#: process teardown from an ordinary single-pool reset ("…after shutdown").
_INTERPRETER_SHUTDOWN_MSG = "cannot schedule new futures after interpreter shutdown"
#: Prefix shared by BOTH executor-rejection variants (interpreter + plain pool).
_SCHEDULE_AFTER_SHUTDOWN_MSG = "cannot schedule new futures after"


class ModelLoadInterruptedByShutdown(RuntimeError):
    """A model load cut short because the backend is shutting down (#1174).

    Benign by definition — the load didn't *fail*, the process is exiting.
    ``_load_model_sync`` raises this instead of the raw executor error so no
    caller (preload task, request handler, log formatter) can dress an
    expected teardown up as a crash: no ERROR log, no ``/model/status``
    phantom error, no exit-code-poisoning traceback.
    """


# Flipped by main.py's lifespan: set the moment graceful shutdown starts,
# cleared on startup (in-process relaunches: TestClient boots, the
# --health-check thread). While set, executor-rejection errors during a load
# — including the plain single-pool "…after shutdown" variant our own
# _reset_gpu_pool() causes — are classified as a benign cancelled-load
# instead of the #589-class real fault.
_shutting_down = threading.Event()


def begin_shutdown() -> None:
    """Graceful shutdown started: in-flight/queued model loads are now benign
    cancellations, and new loads must not start (#1174)."""
    _shutting_down.set()


def reset_shutdown_flag() -> None:
    """New run starting — arm model loads again (lifespan startup)."""
    _shutting_down.clear()


def is_shutting_down() -> bool:
    return _shutting_down.is_set()


def _exception_chain(exc: "BaseException | None"):
    """Yield ``exc`` and every ``__cause__``/``__context__`` ancestor once
    (cycle-safe). transformers' lazy-import + materialization machinery wraps
    the original error several layers deep."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _is_interpreter_shutdown_error(exc: "BaseException | None") -> bool:
    """True when `exc` (or anything in its cause/context chain) is — or
    carries the text of — the ``RuntimeError`` a ``ThreadPoolExecutor`` raises
    once Python has begun interpreter shutdown, i.e. the operation was
    interrupted by the process exiting, not by a real fault.

    Two match modes, both required:

    - the live exception object: ``RuntimeError`` whose message mentions
      ``interpreter shutdown`` anywhere in the chain;
    - the *stringified* form: transformers ≥5 aggregates materializer-worker
      errors into NEW exceptions whose message embeds the original traceback
      as text (``log_conversion_errors`` formats it into
      ``loading_info.conversion_errors`` → ``SkipParameters`` → summary
      raise), which changes the type AND severs the cause chain — the exact
      miss behind the "Model loading failed: cannot schedule new futures
      after interpreter shutdown" ERROR logged during pytest teardown
      (#1174). Matching the full CPython phrase inside any message keeps
      that conclusive without loosening the plain-pool case.
    """
    for e in _exception_chain(exc):
        if isinstance(e, RuntimeError) and "interpreter shutdown" in str(e):
            return True
        if _INTERPRETER_SHUTDOWN_MSG in str(e):
            return True
    return False


def _is_schedule_after_shutdown_error(exc: "BaseException | None") -> bool:
    """Any executor 'cannot schedule new futures after …' rejection, either
    variant, live or stringified. Only consulted while ``_shutting_down`` is
    set: during app shutdown even the plain single-pool variant is benign
    (our own ``_reset_gpu_pool()``/executor teardown caused it). Outside
    shutdown the plain variant stays the #589-class real fault and must NOT
    be silenced."""
    return any(_SCHEDULE_AFTER_SHUTDOWN_MSG in str(e) for e in _exception_chain(exc))


def _load_model_sync():
    global model
    if _shutting_down.is_set():
        # The graceful shutdown began before this queued load got a worker
        # (e.g. 1-worker MPS pool with a capture-ASR warmup ahead of it).
        # Don't start a multi-GB import/load the process is about to abandon
        # — bail before torch is even imported (#1174).
        logger.info("Model load skipped: backend is shutting down.")
        raise ModelLoadInterruptedByShutdown("model load skipped: backend shutting down")
    from utils.hf_progress import register_listener, unregister_listener

    # Register a listener that updates _loading_detail with real-time
    # download/weight-loading percentages from hf_hub_download tqdm bars.
    def _on_hf_progress(ev):
        pct = ev.get("pct", 0.0)
        filename = ev.get("filename", "")
        phase = ev.get("phase", "")
        if pct > 0:
            pct_int = min(round(pct * 100), 99)  # cap at 99 until fully done
            detail = _loading_detail.get("detail", "")
            # Append percentage to the existing detail label
            base = detail.split(" —")[0].split(" (")[0]  # strip old suffix
            _loading_detail["progress"] = pct_int
            _loading_detail["detail"] = f"{base} — {pct_int}%"

    lid = register_listener(_on_hf_progress)
    try:
        _set_loading("importing", "Importing PyTorch & VoiceStudio runtime…")
        logger.info("Importing PyTorch & VoiceStudio runtime…")
        torch = _lazy_torch()
        VoiceStudio = _lazy_omnivoice()
        device = get_best_device()

        checkpoint = resolve_omnivoice_checkpoint()
        _set_loading("loading_weights", f"Loading TTS weights on {device}…")
        logger.info("Loading VoiceStudio model on device: %s", device)
        preload_asr = should_preload_tts_asr()
        if preload_asr:
            logger.info("Preloading PyTorch Whisper after TTS model load.")
        else:
            logger.info("Skipping PyTorch Whisper preload; ASR will load on demand.")
        def _load():
            return VoiceStudio.from_pretrained(
                checkpoint, device_map=device, dtype=torch.float16, load_asr=False,
            )

        def _recover_corrupt_weights(exc: BaseException):
            """Re-fetch weights that are on disk but unparseable (#1406).

            Deliberately a FORCED re-download rather than the resume ladder
            below: a resume trusts a blob that is already the expected size
            and would never re-fetch the one that is actually wrong.
            """
            repair_checkpoint = checkpoint
            for nested_exc in _exception_chain(exc):
                repository_id = getattr(nested_exc, "repository_id", None)
                if repository_id == "eustlb/higgs-audio-v2-tokenizer":
                    repair_checkpoint = repository_id
                    break
            asset_label = (
                "audio tokenizer"
                if repair_checkpoint != checkpoint
                else "TTS model"
            )
            if repair_checkpoint in _FORCED_REDOWNLOAD_ATTEMPTED:
                # Already re-fetched this repo once this process and it is
                # still unparseable. Re-downloading again would be the same
                # gigabytes for the same result, once per generate request.
                raise RuntimeError(
                    f"The {asset_label} files for {repair_checkpoint} are damaged and a "
                    "re-download did not fix them. Open Model Catalogue → Models, "
                    "delete the VoiceStudio TTS model, and install it again."
                    f"{_manual_cache_delete_hint(repair_checkpoint)}"
                ) from exc
            _FORCED_REDOWNLOAD_ATTEMPTED.add(repair_checkpoint)
            logger.warning(
                "%s files for %s are present but unparseable (%s) — a "
                "download that stopped mid-file, or a file altered on disk "
                "after it arrived. Re-fetching them.",
                asset_label,
                repair_checkpoint,
                exc,
            )
            _set_loading("loading_weights", "Model files are damaged — re-downloading…")
            if not _repair_model_cache(repair_checkpoint, force=True):
                raise RuntimeError(
                    f"The {asset_label} files for {repair_checkpoint} are damaged — a "
                    "download that stopped part-way, or a file changed on "
                    "disk after it arrived — and could not be re-downloaded "
                    f"automatically.{_repair_failure_detail()} Open Settings "
                    "→ Models, delete the VoiceStudio TTS model, and install "
                    f"it again.{_manual_cache_delete_hint(repair_checkpoint)}"
                ) from exc
            _set_loading("loading_weights", f"Loading TTS weights on {device}…")
            try:
                return _load()
            except Exception as exc2:
                if not _is_corrupt_weights_error(exc2):
                    raise
                raise RuntimeError(
                    f"The {asset_label} files for {repair_checkpoint} are still damaged "
                    "after being re-downloaded. Open Model Catalogue → Models, "
                    "delete the VoiceStudio TTS model, and install it again."
                    f"{_manual_cache_delete_hint(repair_checkpoint)}"
                ) from exc2

        try:
            _model = _load()
        except OSError as e:
            # #352 / #581: a truncated HF cache surfaces here as "does not
            # appear to have a file named pytorch_model.bin or
            # model.safetensors". Instead of dead-ending the user with a
            # manual delete-and-reinstall instruction, try to self-repair: an
            # interrupted download leaves the cache missing only some files,
            # and snapshot_download() resumes/fills exactly those (a complete
            # cache never reaches this branch, so the fast path is untouched).
            if _is_corrupt_weights_error(e):
                # Present-but-unparseable wearing an OSError (#1406) —
                # transformers wraps a tensor-library failure in one. The
                # resume ladder below is the wrong repair (it would trust the
                # bad blob), so divert before the missing-shard check drops
                # this as unrecognised and 500s.
                _model = _recover_corrupt_weights(e)
            elif not _is_incomplete_cache_error(e):
                raise
            else:
                # Rung 0: broken snapshot links — the blobs are on disk but the
                # snapshot entries don't resolve (dangling symlinks / zero-byte
                # stand-ins). Delete exactly the broken entries, restore, and
                # retry the load ONCE (guarded per repo per process). A cache
                # without broken links falls straight through to the resume
                # ladder below.
                _model = None
                if _selfheal_broken_snapshot_links(checkpoint):
                    _set_loading(
                        "loading_weights",
                        "Model cache had broken file links — repaired "
                        "automatically, retrying…",
                    )
                    try:
                        _model = _load()
                    except OSError as e_link:
                        if not _is_incomplete_cache_error(e_link):
                            raise
                        logger.warning(
                            "Load still failing after snapshot-link repair of %s — "
                            "falling back to resume repair.", checkpoint,
                        )
                        e = e_link
                        _model = None
                if _model is None:
                    _set_loading("loading_weights", "Repairing incomplete model cache…")
                    if not _repair_model_cache(checkpoint):
                        raise RuntimeError(
                            f"The TTS model cache for {checkpoint} is incomplete "
                            "(weights missing — usually an interrupted download)."
                            f"{_repair_failure_detail()} "
                            "Open Model Catalogue → Models, delete the VoiceStudio TTS model, "
                            f"and install it again.{_manual_cache_delete_hint(checkpoint)}"
                        ) from e
                    _set_loading("loading_weights", f"Loading TTS weights on {device}…")
                    try:
                        _model = _load()
                    except OSError as e2:
                        # Resume-repair ran but the cache is still unusable. The usual
                        # cause beyond "repo genuinely lacks weights" is a blob that's
                        # present with the right size but corrupt — snapshot_download's
                        # resume trusts it and never re-fetches it (#739). Force a full
                        # re-download (replaces corrupt blobs) and retry once more before
                        # falling back to the manual delete-and-reinstall message.
                        if _is_corrupt_weights_error(e2):
                            # The resume filled the missing files, then exposed a
                            # present-but-damaged blob. A second resume would trust
                            # that blob, so switch to the forced corruption repair.
                            _model = _recover_corrupt_weights(e2)
                        elif _is_incomplete_cache_error(e2):
                            _set_loading("loading_weights", "Re-downloading model files…")
                            if _repair_model_cache(checkpoint, force=True):
                                try:
                                    _model = _load()
                                except OSError as e3:
                                    raise RuntimeError(
                                        f"The TTS model cache for {checkpoint} is incomplete "
                                        "and could not be auto-repaired. Open Model "
                                        "Catalogue → Models, delete the VoiceStudio TTS model, and install "
                                        f"it again.{_manual_cache_delete_hint(checkpoint)}"
                                    ) from e3
                            else:
                                raise RuntimeError(
                                    f"The TTS model cache for {checkpoint} is incomplete and "
                                    f"could not be auto-repaired.{_repair_failure_detail()} "
                                    "Open Model Catalogue → Models, delete the VoiceStudio TTS model, "
                                    f"and install it again.{_manual_cache_delete_hint(checkpoint)}"
                                ) from e2
                        else:
                            raise RuntimeError(
                                f"The TTS model cache for {checkpoint} is incomplete and "
                                "could not be auto-repaired. Open Model Catalogue → Models, delete "
                                "the VoiceStudio TTS model, and install it again."
                                f"{_manual_cache_delete_hint(checkpoint)}"
                            ) from e2
        except Exception as e_corrupt:
            # safetensors raises SafetensorError from a Rust extension and
            # torch raises UnpicklingError — neither is an OSError, so the
            # ladder above never saw them and the load 500'd with a raw
            # "Error while deserializing header: header too large" (#1406).
            # Anything that is not this class re-raises untouched, so no
            # unrelated failure is swallowed by the broad clause.
            if not _is_corrupt_weights_error(e_corrupt):
                raise
            _model = _recover_corrupt_weights(e_corrupt)

        if preload_asr:
            # Keep ASR outside `from_pretrained`: if its separate HF cache is
            # corrupt, it must never be mistaken for the TTS checkpoint and
            # trigger a second multi-GB TTS load/re-download (CodeRabbit).
            try:
                _model.load_asr_model()
            except Exception as asr_exc:
                if not _is_corrupt_model_file_error(asr_exc):
                    raise
                raise RuntimeError(
                    "The transcription model's files are damaged. Open "
                    "Model Catalogue → Models, delete the transcription (ASR) model, "
                    "and install it again; or set OMNIVOICE_PRELOAD_TTS_ASR=0 "
                    "to stop preloading it alongside TTS."
                ) from asr_exc

        # FlashInfer opt-in (upstream k2-fsa port): packed CFG attention +
        # fused kernels, ~2x on upstream's benchmarks. Applied INSTEAD of
        # torch.compile — both rewrite the llm's execution and they do not
        # compose. Best-effort: any apply failure latches the session off and
        # the standard path continues untouched.
        flashinfer_applied = False
        try:
            from services.engine_env import (
                mark_flashinfer_runtime_failure,
                should_flashinfer,
            )

            fi_mode = should_flashinfer(device)
            if fi_mode != "off":
                _set_loading("compiling", "Applying FlashInfer kernels…")
                try:
                    from omnivoice.models.omnivoice_flashinfer import apply_flashinfer

                    # Captured BEFORE apply so unapply (either the failure
                    # branch below or the generate-time fallback) restores
                    # the true prior implementation.
                    _model._fi_orig_attn_impl = getattr(
                        _model.llm.config, "_attn_implementation", "sdpa"
                    )
                    apply_flashinfer(_model, enable_cuda_graph=(fi_mode == "graph"))
                except Exception as fi_exc:  # noqa: BLE001 — perf opt, never fatal
                    mark_flashinfer_runtime_failure(
                        f"{type(fi_exc).__name__}: {fi_exc}"
                    )
                    # apply_flashinfer mutates the model as it goes — a
                    # failure partway leaves half-patched modules that would
                    # crash the next render (Greptile, #1565). Restore fully.
                    _unapply_flashinfer(_model)
                else:
                    flashinfer_applied = True
                    _install_flashinfer_fallback(_model)
                    # BOTH modes pin inference to one thread. Graph mode for
                    # the #315 reason (captured CUDA-graph state is
                    # thread-local); eager mode because the FlashInfer
                    # attention wrapper and packed position ids are planned
                    # per generation in module state — two _gpu_pool workers
                    # interleaving plan() and run() would corrupt each
                    # other's layout (CodeRabbit/Greptile, #1565).
                    _install_compile_thread_affinity(_model)
                    logger.info(
                        "FlashInfer applied (mode=%s) — torch.compile skipped "
                        "for this load.", fi_mode,
                    )
        except Exception:
            logger.exception("FlashInfer opt-in check failed; continuing without")

        try:
            # plan-02 (#65): gate on Triton availability (+ user setting), not
            # just device==cuda. Triton has no Windows wheel, so the old
            # cuda-only check OOM'd on Windows+CUDA; should_torch_compile()
            # falls back to eager there.
            from services.engine_env import should_torch_compile

            if not flashinfer_applied and should_torch_compile(device):
                _set_loading("compiling", "Compiling model (torch.compile)…")
                try:
                    _model.llm = torch.compile(_model.llm, mode=_TORCH_COMPILE_MODE)
                except Exception as compile_exc:
                    # #278: compile is an optimization, never a point of
                    # failure — keep the eager model and remember the failure
                    # so later loads this session skip compile up front.
                    from services.engine_env import mark_compile_runtime_failure
                    mark_compile_runtime_failure(f"{type(compile_exc).__name__}: {compile_exc}")
                    logger.warning(
                        "torch.compile failed (%s) — continuing with the eager model.",
                        compile_exc,
                    )
                else:
                    # Compilation is lazy: Dynamo/Inductor/Triton can still
                    # blow up on the first *forward* (e.g. unsupported new GPU
                    # archs, #278). Wrap generate so that falls back to eager
                    # instead of failing the generation.
                    _install_compile_fallback(_model)
                    if _TORCH_COMPILE_MODE in _CUDAGRAPH_COMPILE_MODES:
                        # #315: reduce-overhead uses CUDA graphs, whose
                        # captured state is thread-local. Pin all inference to
                        # one dedicated thread so a later render dispatched to
                        # a different _gpu_pool worker can't replay a graph it
                        # didn't capture (static / slowed audio from the 2nd
                        # render onward).
                        _install_compile_thread_affinity(_model)
                        logger.info(
                            "torch.compile mode %r uses CUDA graphs — compiled-model "
                            "inference pinned to a single dedicated thread (#315).",
                            _TORCH_COMPILE_MODE,
                        )
                    logger.info("torch.compile applied.")
        except Exception as e:
            logger.info("torch.compile skipped: %s", e)

        _set_loading("ready", "Model ready", progress=100)
        logger.info("VoiceStudio model loaded successfully.")
        return _model
    except ModelLoadInterruptedByShutdown:
        raise
    except Exception as exc:
        # A model load interrupted by *interpreter/process shutdown* is not a
        # real fault — the backend is on its way out (uvicorn stopping, a failed
        # port bind, or the user closing the app mid-load). transformers
        # materializes weights in its OWN thread pool, which raises "cannot
        # schedule new futures after interpreter shutdown" on the way down.
        # Likewise once main.py's lifespan flipped `begin_shutdown()`, even the
        # plain single-pool rejection is benign — our own _reset_gpu_pool()
        # caused it. Convert those to ModelLoadInterruptedByShutdown (logged
        # calmly at INFO) instead of dressing an expected teardown up as a
        # crash: otherwise the backend-crash report fills with a scary
        # traceback for what is a normal shutdown, /model/status flips to a
        # phantom error, and on some shutdown paths the escaping RuntimeError
        # poisons the process exit code (#1174: SIGTERM mid-load → exit 1 →
        # the desktop shell toasts "the backend crashed").
        if _is_interpreter_shutdown_error(exc) or (
            _shutting_down.is_set() and _is_schedule_after_shutdown_error(exc)
        ):
            logger.info(
                "Model load aborted: shutdown during load — benign, not a failure."
            )
            raise ModelLoadInterruptedByShutdown("shutdown during load") from exc
        # Surface an ACTIONABLE, sanitized error in /model/status (it's shown in
        # the first-run System Check). build_failure classifies the cause and
        # attaches a fix hint — e.g. a corrupted transformers install
        # ([Errno 2] … modeling_*.py) now says "reinstall transformers" instead
        # of an unhelpful raw path + "try restarting" — and strips the home dir.
        try:
            from core.failure import build_failure
            _f = build_failure(exc, stage="model-load", include_diagnostic=False)
            err_msg = _f["reason"] + (f" — {_f['hint']}" if _f.get("hint") else "")
        except Exception:  # never let failure-formatting mask the real error
            err_msg = str(exc)
        _set_loading("error", "Model loading failed", error=err_msg)
        # #1000 class: transformers' lazy-import machinery wraps ANY disruption
        # to an inner import (including one interrupted by process teardown)
        # in a generic "Could not import module X. Are this object's
        # requirements defined correctly?" — logging only str(exc) discarded
        # the real cause in __cause__/__context__ and made a shutdown race
        # look like a broken install. exc_info surfaces the full chain.
        logger.error("Model loading failed: %s", str(exc), exc_info=exc)
        raise
    finally:
        unregister_listener(lid)

def _model_load_timeout() -> float:
    """Overall ceiling (seconds) for a single model load/download attempt.

    Backstop for any hang the HF per-read socket timeouts don't catch
    (a wedged torch.compile, a deadlock, etc.). Generous by default so a
    legitimate cold multi-GB download on a slow link still completes;
    overridable via OMNIVOICE_MODEL_LOAD_TIMEOUT for very slow networks.
    """
    try:
        return max(30.0, float(os.environ.get("OMNIVOICE_MODEL_LOAD_TIMEOUT", "1200")))
    except (ValueError, TypeError):
        return 1200.0


def _reset_gpu_pool() -> None:
    """Recover from a wedged/timed-out load by abandoning the GPU worker pool.

    The resilient wrapper is kept (its identity is shared by every importer);
    only its inner `ThreadPoolExecutor` is dropped, so the next submit builds a
    fresh worker. This is what stops stale references from raising "cannot
    schedule new futures after shutdown" after a reset (#589 #599).
    """
    if _gpu_pool_singleton is not None:
        _gpu_pool_singleton.reset()


async def _load_model_with_timeout():
    """Run the blocking model load on the GPU pool, bounded by a deadline.

    Raises RuntimeError on timeout (and resets the poisoned pool) so callers
    surface an actionable error instead of hanging indefinitely.

    This is the shared load boundary for BOTH get_model() and the startup
    preload_model() — the memory reclaim must live here, or a memory-tight
    machine gets protected on demand loads but OS-killed during the startup
    preload (review finding on the original placement in get_model()).
    """
    _make_room_before_tts_load()
    loop = asyncio.get_running_loop()
    timeout = _model_load_timeout()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_get_gpu_pool(), _load_model_sync),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        _set_loading("error", "Model load timed out", error="timeout")
        _reset_gpu_pool()
        logger.error("Model load exceeded %ss; resetting GPU pool.", timeout)
        raise RuntimeError(
            f"Model loading timed out after {int(timeout)}s — usually a network "
            "stall downloading the model (proxy, firewall, or antivirus). Check "
            "your connection or set a Hugging Face mirror in Settings, then retry."
        ) from exc


async def get_model():
    global model, _last_used
    _last_used = time.time()
    if model is not None:
        # Placement self-heal (#1191). The ASR offload/restore pair below is a
        # *balanced-call* contract, and any unbalanced path (abort, terminal
        # error, client disconnect) used to leave the TTS model resident on CPU
        # — where it stayed for EVERY later generation until the idle unload
        # fired, at 10-50x the latency. Verifying placement here makes the
        # contract unnecessary: a future unbalanced offload can no longer
        # strand the model, because the next generation moves it back.
        await _heal_tts_placement()
        # Free idle GPU memory before this warm generate reuses the resident
        # model. The cold-load path already evicts (_make_room_before_tts_load);
        # this closes the WARM path for every native TTS generate (/generate, WS
        # TTS, dub, batch, audiobook), not just a couple of routes. No-op on a
        # roomy machine. Off the event loop because the eviction does gc.collect
        # + cache drop + ASR teardown that can block for hundreds of ms.
        await asyncio.get_running_loop().run_in_executor(None, make_room_before_generate)
        return model

    if running_on_gpu_pool():
        # Same reasoning as _heal_tts_placement below, applied to the COLD
        # path it never covered (#1417). We are on a pool worker, reached from
        # OmniVoiceBackend._ensure_loaded(), which bootstraps a *fresh* event
        # loop with asyncio.run(). `_model_lock` is bound to the server loop,
        # so awaiting it here either raises outright:
        #
        #   RuntimeError: <asyncio.locks.Lock …> is bound to a different event loop
        #
        # (the reported 500 on /v1/audio/speech) or deadlocks, depending on
        # which loop touched the lock first.
        #
        # The load must also run INLINE, in this very thread. Going through
        # `_load_model_with_timeout()` would hand `_load_model_sync` back to
        # `_get_gpu_pool()` — the pool we are currently occupying — and MPS
        # pins that pool to a single worker, so it would wait on itself. That
        # is the same deadlock wearing a different hat (CodeRabbit, #1418).
        #
        # Exclusion comes from `_model_load_thread_lock` rather than the GPU
        # slot: holding a slot is not exclusion when the pool has more than
        # one worker, which CUDA hosts do.
        if model is None:
            with _model_load_thread_lock:
                if model is None:  # another thread loaded it while we waited
                    from core.run_sentinel import touch_activity
                    touch_activity("model_load", "omnivoice-tts")
                    # Same reclaim `_load_model_with_timeout` performs; a
                    # memory-tight machine needs it on this path too.
                    _make_room_before_tts_load()
                    model = _load_model_sync()
        return model

    async with _model_lock:
        if model is None:
            # Crash forensics (#1164): a cold TTS model load is where memory
            # exhaustion (OS OOM kill) most often lands — record that one
            # started so an unclean death is attributable by the next run.
            from core.run_sentinel import touch_activity
            touch_activity("model_load", "omnivoice-tts")
            model = await _load_model_with_timeout()
    return model


def _make_room_before_tts_load() -> None:
    """Evict-then-load: free what we already own before a tight TTS load.

    The audit's top gap: on a 16 GB unified-memory box a plain TTS load could
    still be OS-killed — the dub path frees memory before *ASR* loads
    (offload_tts_for_asr, #1119), but nothing freed memory before a *TTS*
    load, and a warm dictation model (~2 GB) is routinely the difference.

    Deliberately NOT admission control: refusing a load on an estimate would
    brick machines that would actually cope (the #1111 decision — advisory
    only). This only releases things the app already reclaims on idle anyway
    (the capture-ASR model, engine instances, allocator caches), just *now*
    instead of after the idle timeout — and only when free memory is actually
    tight, so a roomy machine pays nothing.
    """
    try:
        from services.memory_budget import available_memory
        free_gb = (available_memory() or {}).get("ram_available_gb")
        if free_gb is None or free_gb >= _UNIFIED_OFFLOAD_HEADROOM_GB:
            return
        logger.info(
            "Memory tight before TTS load (%.1f GB free), releasing idle "
            "models first.", free_gb,
        )
        _release_idle_tts_memory("load")
    except Exception:  # noqa: BLE001 -- making room must never break loading
        logger.debug("pre-load memory reclaim skipped", exc_info=True)


def _release_idle_tts_memory(stage):
    """Drop capture-ASR, TTS side caches, and allocator caches. Best-effort;
    never raises (a cleanup failure must not break the load/generate that called
    it). Shared by the cold-load and warm-generate make-room paths so the
    eviction recipe cannot drift between them (#730/#1190)."""
    try:
        try:
            from services.asr_backend import release_idle_capture_backend
            release_idle_capture_backend(0.0)  # 0s idle = release if unleased
        except Exception:  # noqa: BLE001 -- best-effort, never blocks the caller
            logger.debug("capture-ASR pre-%s release failed", stage, exc_info=True)
        release_tts_side_caches()
        free_vram()
    except Exception:  # noqa: BLE001 -- a cleanup failure must never break the caller
        logger.debug("pre-%s memory reclaim skipped", stage, exc_info=True)


def _should_make_room_for_generate():
    """Decide whether to free idle GPU memory before a generate (#730/#1190).

    Modes (OMNIVOICE_FREE_VRAM_BEFORE_GENERATE):
      auto (default): free when free system RAM is below the unified headroom,
        mirroring _make_room_before_tts_load. A roomy machine pays nothing.
      always: free before every generate (small per-call cost from gc.collect +
        cache drop).
      never: opt out.
    """
    mode = os.environ.get("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", "auto").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    try:
        from services.memory_budget import available_memory
        free_gb = (available_memory() or {}).get("ram_available_gb")
        if free_gb is not None and free_gb < _UNIFIED_OFFLOAD_HEADROOM_GB:
            return True
    except Exception:  # noqa: BLE001 -- a probe failure must never block a generate
        logger.debug("make_room memory probe failed", exc_info=True)
    return False


def make_room_before_generate():
    """Free idle GPU memory before a warm, heavy generate (#730/#1190).

    The cold LOAD path already evicts (``_make_room_before_tts_load`` runs inside
    ``_load_model_with_timeout``), but the warm path (model already resident,
    ``get_model`` returns early at the cache check) skipped it. A long generate
    on a VRAM-tight MPS box then contended with capture-ASR and the clone-prompt
    side cache until it exceeded the execution budget and was abandoned, which is
    exactly how one slow synth cascaded into a stuck, device-holding backend.
    This runs the same fail-safe eviction the load path uses, just before a
    generate the policy says is likely to starve.

    Deliberately NOT admission control and NOT a device reclaim. It only drops
    things the app already releases on idle, just now instead of later, so a
    roomy machine or a short synth pays nothing. It cannot kill an already
    abandoned worker; only a crash-isolated subprocess engine can (see
    services.subprocess_backend).
    """
    if not _should_make_room_for_generate():
        return
    _release_idle_tts_memory("generate")


def _checkpoint_in_local_cache(checkpoint: str) -> bool:
    """True when ``checkpoint`` is loadable with NO network: an existing local
    directory, or a COMPLETE HF cache snapshot. ``snapshot_download(...,
    local_files_only=True)`` never constructs an HTTP session, so a broken
    proxy env (#959: ``ALL_PROXY``/``HTTPS_PROXY=socks5://`` without socksio)
    can't false-negative this probe. Never raises."""
    if os.path.isdir(checkpoint):
        return True
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(checkpoint, local_files_only=True)
        return True
    except Exception:
        return False


def _headless_worker() -> bool:
    """True when this process serves remote work and has no local UI."""
    try:
        from worker.agent import worker_mode_enabled  # noqa: PLC0415
    except Exception:
        return False
    return worker_mode_enabled()


async def preload_model():
    """Background model warm-up — call from lifespan startup.

    Loads the TTS model on the GPU pool thread so the first /generate
    call is near-instant instead of waiting 4-6s for weight loading.
    Non-blocking: if models aren't installed yet, silently exits.
    """
    global model, _last_used
    if model is not None:
        return  # already loaded

    # On MPS the configured ``omnivoice`` id resolves to a crash-isolated
    # sidecar. Warming the native singleton here would put the same fatal MPS
    # allocator risk back into the API process before the isolated engine is
    # ever asked to synthesize.
    try:
        from core.device_caps import detect_host_caps

        if detect_host_caps().family == "mps":
            logger.info(
                "Native TTS preload skipped: OmniVoice uses crash isolation on this host."
            )
            return
    except Exception:  # noqa: BLE001 -- preload selection must stay best-effort
        logger.debug("effective TTS preload selection failed", exc_info=True)

    # A machine lending its GPU has no local user to warm the model FOR. This
    # preload exists to make the first /generate feel instant for the person
    # sitting in front of the app; on a headless node there is nobody sitting
    # there, so it is several GB of VRAM held from boot against a request that
    # may never come — and the idle sweep cannot reclaim it, because the sweep
    # owns the worker executor's engines and this is the default local model.
    # Observed on hardware: a node that had run nothing still sat at 2.4 GB.
    #
    # A machine that is BOTH a desktop app and a worker keeps the warm-up:
    # there is a real user there, and the whole point stands.
    if _headless_worker():
        logger.info(
            "Preload skipped: this process is running as a remote worker, so the "
            "model loads on first request and is released when it goes idle."
        )
        return
    try:
        # Warm-up is gated on LOCAL availability only — never a Hub API
        # probe. The old `model_info(checkpoint)` probe proved the repo
        # exists on huggingface.co, NOT that this machine has it installed,
        # so on any networked machine with an uninstalled model (fresh
        # install, empty-cache CI/test run) every app boot silently pulled
        # the multi-GB checkpoint in a background thread the moment lifespan
        # started — violating this function's "if models aren't installed
        # yet, silently exits" contract. The cache-only check also never
        # constructs an HTTP session, so the #959 class (broken
        # ALL_PROXY/HTTPS_PROXY=socks5:// env raising at client
        # construction) can't false-negative it, and startup stays free of
        # network calls (local-first). Uses the same resolver as the load
        # path (#693) so a leaked engine id can't skew the probe.
        checkpoint = resolve_omnivoice_checkpoint()
        if not _checkpoint_in_local_cache(checkpoint):
            logger.info(
                "Preload skipped: %s is not installed locally — the model "
                "will load (and download if requested) on first use.",
                checkpoint,
            )
            return

        logger.info("Preloading TTS model in background…")
        _last_used = time.time()
        async with _model_lock:
            if model is None:
                model = await _load_model_with_timeout()
        logger.info("Preload complete — model ready.")
    except ModelLoadInterruptedByShutdown:
        # Expected teardown (#1174): the backend was shut down while the
        # preload was still loading weights. Info, no traceback — a WARNING
        # with a stack here is exactly the crash-shaped noise the
        # classification exists to prevent.
        logger.info("Model preload stopped: shutdown during load — benign.")
    except Exception as e:
        # See the matching exc_info note on the _load_model_sync handler above
        # (#1000 class) — the full chain, not just str(e), is what actually
        # distinguishes a real dependency problem from a shutdown-interrupted
        # import.
        logger.warning("Model preload failed (non-fatal): %s", e, exc_info=e)
        # Non-fatal must not mean invisible (#1415). A broken dependency in the
        # model's import chain fails here and nowhere else until the user tries
        # to generate — so the app starts clean, reports itself healthy, and
        # simply produces nothing, which is how the reporter's environment
        # looked. Record it on the status the UI already reads, with the
        # classified remedy attached; the next successful load clears it.
        try:
            from core.failure import build_failure

            from core.failure import describe_exception

            # The whole chain, not just the surface: transformers reports a
            # broken dependency as a lazy-attribute error and keeps the real
            # cause in __cause__, so classifying the outermost message alone
            # loses the only part that names a remedy.
            reason = " | ".join(
                describe_exception(exc) for exc in _exception_chain(e)
            ) or describe_exception(e)
            failure = build_failure(
                reason, stage="model-preload", include_diagnostic=False,
            )
            detail = failure.get("hint") or failure.get("reason") or str(e)
        except Exception:  # noqa: BLE001 — never lose the warning to this
            # NOT str(e): the whole point of build_failure is that it sanitizes,
            # and an exception message routinely carries absolute paths — i.e.
            # the user's account name — which this string is about to publish
            # through /model/status (CWE-532; CodeRabbit). A fixed message that
            # points at the log beats leaking one into the API.
            detail = (
                "The TTS model could not be loaded. Settings → Logs → Backend "
                "has the full error."
            )
        # `sub_stage` is a public API enum and the frontend keys failure state
        # off `error`. Keep the human-readable word "failed" in the detail,
        # not in the state machine (#1695).
        _set_loading("error", detail, error=detail)

def get_model_status():
    is_loaded = model is not None
    # asyncio.Lock exposes .locked() on all supported Python versions; wrap in try for safety.
    try:
        is_loading = (not is_loaded) and _model_lock.locked()
    except Exception:
        is_loading = False

    status = "loading" if is_loading else ("ready" if is_loaded else "idle")
    result = {
        "loaded": is_loaded,
        "loading": is_loading,
        "status": status,
    }
    # Attach sub-stage detail when loading or after an error
    sub = _loading_detail.get("sub_stage")
    if sub:
        result["sub_stage"] = sub
        result["detail"] = _loading_detail.get("detail", "")
        progress = _loading_detail.get("progress")
        if progress is not None:
            result["progress"] = progress
        err = _loading_detail.get("error")
        if err:
            result["error"] = err
    return result

def _resolve_idle_timeout() -> float:
    """In-process model idle timeout in seconds (MM2-05): prefs store → env →
    core.config default, env winning. Resolved per-tick so a settings change
    takes effect without a restart."""
    try:
        from core import prefs
        return float(prefs.resolve(
            "idle_timeout_seconds",
            env="OMNIVOICE_IDLE_TIMEOUT_S",
            default=IDLE_TIMEOUT_SECONDS,
        ))
    except (TypeError, ValueError, ImportError):
        return float(IDLE_TIMEOUT_SECONDS)


async def idle_worker():
    torch = _lazy_torch()
    while True:
        await asyncio.sleep(30)
        idle_timeout = _resolve_idle_timeout()
        async with _model_lock:
            if model is not None and time.time() - _last_used > idle_timeout:
                logger.info("Idle timeout reached. Unloading VoiceStudio model to free VRAM.")
                unload_shared_model()
        # The capture/dictation ASR was never idle-released — so once a user
        # dictated, its model stayed resident for the life of the process while
        # the TTS model dutifully freed its 3.8 GB. On a 16 GB Mac that left the
        # backend sitting at ~6.2 GB idle, which is what tipped it into the
        # memory pressure that gets it killed mid-generate (#1076/#1092/#1093/
        # #1101). Give it the same bargain the TTS model already makes. Held
        # off while a live dictation stream has a lease, so nothing is unloaded
        # mid-sentence.
        try:
            from services.asr_backend import release_idle_capture_backend

            if release_idle_capture_backend(idle_timeout):
                free_vram()
        except Exception:  # noqa: BLE001 — the reaper must never kill idle_worker
            logger.warning("idle capture-ASR release failed", exc_info=True)
        # Same bargain for the AudioSeal watermark models, which loaded on the
        # first embed and were never released. Deliberately only here and not
        # in the make-room paths: watermarking runs immediately *after* a
        # generate, so evicting it just before one would only buy a reload.
        try:
            from services.watermark import release_idle_models

            release_idle_models(idle_timeout)
        except Exception:  # noqa: BLE001 — the reaper must never kill idle_worker
            logger.warning("idle watermark-model release failed", exc_info=True)

def release_tts_side_caches():
    """Drop caches keyed to the TTS model, for when the model itself is released.

    The voice-clone prompt cache (services.tts_backend) holds encoded reference
    tensors belonging to *this* model instance. If the model is unloaded but the
    prompts survive, an "unload" no longer means unload (#1119) — they sit in the
    very memory the unload was reclaiming (``_offload_unified_memory`` drops the
    model precisely to hand that RAM to the ASR model).

    Previously only ``OmniVoiceBackend.unload()`` cleared them, which sufficed
    while the cache was adapter-only. The native ``/generate`` path now populates
    it too, and that path unloads through *here*, never through the adapter.

    Reached through ``sys.modules`` rather than an import, deliberately:
    ``tts_backend`` already imports this module, so importing it back would close
    a real cycle — and doing it at *import* time (e.g. a registration hook) drags
    ``core.config`` in earlier than it is today, which perturbs DATA_DIR binding.
    A plain lookup has neither problem, and is exactly right besides: if the module
    was never imported, it has no cache to clear.

    Best-effort by construction — cache hygiene must never be able to break an
    unload, because a failed unload is how the backend gets OOM-killed.
    """
    mod = sys.modules.get("services.tts_backend")
    if mod is None:
        return
    try:
        mod.clear_clone_prompt_cache()
    except Exception:  # noqa: BLE001
        logger.debug("clone-prompt cache clear failed during unload", exc_info=True)


def _clear_cublas_workspaces(torch) -> None:
    """Drop cuBLAS's per-handle workspaces before emptying the cache.

    Measured on a 4090: after unloading the model, ``empty_cache()`` left
    803 MB reserved with 8.5 MB allocated. A segment dump explained it —
    **one** 803 MB segment, 794.7 MB of it inactive-but-split, pinned by a
    single live 8,519,680-byte block. That number is cuBLAS's default
    workspace. It is taken from the caching allocator on first use, it lands
    inside whatever segment the model load had just grown, and it is held for
    the life of the cuBLAS handle — so one 8.5 MB block kept three quarters of
    a gigabyte from ever going back to the driver, no matter how many times
    the user pressed Flush Memory.

    Clearing the workspaces first lets the whole segment go. The next cuBLAS
    call re-allocates one, which is why this belongs here (on the unload
    paths) and not on any hot path.

    Private API, so it is optional by construction: a torch build without it
    keeps today's behaviour rather than failing an unload.
    """
    clear = getattr(getattr(torch, "_C", None), "_cuda_clearCublasWorkspaces", None)
    if clear is None:
        return
    try:
        clear()
    except Exception:  # noqa: BLE001 — freeing memory must never raise
        logger.debug("clearing cuBLAS workspaces failed", exc_info=True)


def free_vram():
    """Release cached GPU memory on any accelerator (CUDA, MPS, XPU)."""
    torch = _lazy_torch()
    import gc
    gc.collect()
    if torch.cuda.is_available():
        _clear_cublas_workspaces(torch)
        torch.cuda.empty_cache()
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif hasattr(torch, "xpu") and torch.xpu.is_available():
        torch.xpu.empty_cache()


def unload_shared_model() -> bool:
    """Drop the shared VoiceStudio model and actually give the memory back.

    The order is the entire point of this function. Clearing the reference has
    to come FIRST, then the allocator caches. ``free_vram()`` run while
    ``model`` is still bound releases nothing: the weights are still reachable,
    so ``gc.collect()`` keeps them and ``empty_cache()`` only returns blocks
    the allocator already considered free. The reference drops a moment later,
    the weights go back into torch's cache, and nobody ever hands that cache to
    the driver — so the unload is logged, the engine is dropped from the
    registry, and ``nvidia-smi`` does not move.

    Six call sites open-coded this pair and one of them had it inverted — the
    one the engine-registry sweep reaches, which is the sweep a headless worker
    node runs. A worker therefore sat on 3.6 GB indefinitely while reporting
    the engine released, and every other path looked fine (#1495). One helper,
    so there is one ordering and nowhere left to get it wrong.

    Takes no lock of its own: the sync engine-registry path
    (``OmniVoiceBackend.unload``) cannot await one, and callers that do hold
    ``_model_lock`` simply keep holding it across the call. Assignment is
    GIL-atomic, so the worst a race costs is a redundant reload. Idempotent —
    returns False when nothing was resident.
    """
    global model
    if model is None:
        return False
    model = None
    release_tts_side_caches()
    free_vram()
    return True


def _has_dedicated_vram():
    """Check if the current device has limited dedicated VRAM that needs offloading."""
    torch = _lazy_torch()
    if torch.cuda.is_available():
        return True
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return True
    return False



# Free RAM below which the TTS model is released before ASR loads on a
# unified-memory machine. WhisperX large-v3 needs ~3 GB plus VAD and overhead,
# so a box with less than this much headroom cannot hold both — and on a Mac the
# loser is the whole backend process (the OS kills it). Tunable for bigger boxes.
_UNIFIED_OFFLOAD_HEADROOM_GB = float(
    os.environ.get("OMNIVOICE_UNIFIED_OFFLOAD_HEADROOM_GB", "6.0")
)


def _offload_unified_memory() -> bool:
    """Release the TTS model on a unified-memory host when RAM is tight.

    Returns True when the model was actually released. Never raises — a failure
    to make room must not abort the transcription that asked for it."""
    global model
    try:
        from services.memory_budget import available_memory

        free_gb = available_memory().get("ram_available_gb")
        if free_gb is not None and free_gb > _UNIFIED_OFFLOAD_HEADROOM_GB:
            return False  # plenty of room — keep the model warm, pay no reload
        logger.info(
            "Unified memory tight (%s GB free) — releasing the TTS model so ASR has room "
            "(it reloads on the next generation).",
            "unknown" if free_gb is None else f"{free_gb:.1f}",
        )
        unload_shared_model()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("unified-memory TTS offload failed (continuing): %s", e)
        return False


def offload_tts_for_asr():
    """Move TTS model to CPU to free VRAM for ASR (WhisperX large-v3).

    On a 7-8 GB laptop GPU the TTS model (~2.4 GB) and WhisperX large-v3
    (~3 GB) plus the VAD model can't coexist. Offloading the TTS model to
    CPU before transcription prevents CUDA OOM, then restore_tts_after_asr()
    moves it back.

    Works on CUDA (NVIDIA + ROCm) and Intel XPU.
    """
    global model
    torch = _lazy_torch()
    if model is None:
        return
    if not _has_dedicated_vram():
        # UNIFIED MEMORY (Apple Silicon / CPU). Moving the model "to CPU" frees
        # nothing here — it is the same physical RAM — which is why this used to
        # bail out entirely. But the conclusion was wrong: the fix on unified
        # memory isn't to MOVE the model, it's to RELEASE it.
        #
        # Holding the ~3.8 GB TTS model resident while WhisperX large-v3 (~3 GB)
        # loads on top of it is what gets the backend OOM-killed mid-dub on a
        # 16 GB Mac (#1119) — the transcribe stream just dies. Unload it and the
        # room is real. get_model() lazily reloads on the next TTS use, so the
        # only cost is that reload, and only when memory was actually tight.
        _offload_unified_memory()
        return
    try:
        # Check if there's enough free VRAM to skip offloading
        if torch.cuda.is_available():
            free_mem = torch.cuda.mem_get_info()[0]
            if free_mem > 8 * 1024 ** 3:  # > 8 GB free → skip offload
                return
    except Exception:
        pass
    try:
        logger.info("Offloading TTS model to CPU to free VRAM for ASR...")
        model.to("cpu")
        free_vram()
        logger.info("TTS model offloaded. VRAM freed for ASR.")
    except Exception as e:
        logger.warning("TTS offload failed: %s", e)


def restore_tts_after_asr():
    """Move TTS model back to the GPU after ASR completes."""
    global model
    torch = _lazy_torch()
    if model is None:
        return
    if not _has_dedicated_vram():
        # Nothing to restore on unified memory: offload UNLOADED the model, and
        # get_model() reloads it lazily on the next TTS call. Reloading it here
        # would just re-occupy the RAM we freed, right when the dub still has
        # translation and synthesis ahead of it.
        return
    try:
        device = get_best_device()
        if device in ("cuda", "xpu"):
            logger.info("Restoring TTS model to %s...", device)
            model.to(device)
            free_vram()
    except Exception as e:
        logger.warning("TTS restore to %s failed: %s", get_best_device(), e)


def _first_param_device(obj):
    """Device the weights of ``obj`` actually live on, or None if undeterminable.

    The TTS runtime is a wrapper object, not necessarily an ``nn.Module``, so
    fall back to the first sub-module that owns parameters. Never raises.
    """
    try:
        params = getattr(obj, "parameters", None)
        if callable(params):
            for p in params():
                return p.device
    except Exception:  # noqa: BLE001 — a probe must never break generation
        pass
    try:
        torch = _lazy_torch()
        for v in vars(obj).values():
            if isinstance(v, torch.nn.Module):
                for p in v.parameters():
                    return p.device
    except Exception:  # noqa: BLE001
        pass
    return None


def _stranded_tts_target():
    """Target device string when the loaded TTS model is stranded off it, else None.

    Ordered cheapest-first so the hot path (model already on the accelerator)
    costs a single parameter probe: anything not sitting on CPU is by
    definition not stranded, because the only thing that moves the model is
    ``offload_tts_for_asr()`` and it only ever moves it to CPU.
    """
    m = model
    if m is None:
        return None
    dev = _first_param_device(m)
    if dev is None or getattr(dev, "type", None) != "cpu":
        return None
    if not _has_dedicated_vram():
        # Unified memory / CPU-only: the offload RELEASES the model rather than
        # moving it, and CPU is the legitimate home here. Nothing to heal.
        return None
    try:
        target = get_best_device()
    except Exception:  # noqa: BLE001
        return None
    return target if target in ("cuda", "xpu") else None


def ensure_tts_on_device() -> bool:
    """Move the TTS model back onto its target device if it was stranded on CPU.

    Returns True when a move actually happened. Never raises — a failed move
    just leaves the model on CPU, which is exactly the pre-fix behaviour
    (slow), never a failed generation.
    """
    target = _stranded_tts_target()
    m = model
    if target is None or m is None:
        return False
    try:
        logger.warning(
            "TTS model found stranded on CPU (an ASR offload was never restored) — "
            "moving it back to %s; generation would otherwise run 10-50x slower (#1191).",
            target,
        )
        m.to(target)
        free_vram()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("TTS placement self-heal to %s failed (staying on CPU): %s", target, e)
        return False


async def _heal_tts_placement() -> None:
    """Async wrapper for :func:`ensure_tts_on_device` used by ``get_model()``.

    The cheap mismatch probe runs inline; the rare actual move is dispatched to
    the **GPU pool** so it serializes against in-flight inference — moving a
    shared model's weights underneath a running ``generate()`` is the one way
    this could make things worse than the bug it fixes. The pool that can
    strand a model is always 1-worker (``offload_tts_for_asr`` only fires below
    8 GB free VRAM, and ``_workers_for_free_vram`` gives such a host a single
    worker), so occupying a slot is genuine mutual exclusion there.
    """
    if _stranded_tts_target() is None:
        return
    if running_on_gpu_pool():
        # Reached from a GPU-pool thread — OmniVoiceBackend._ensure_loaded()
        # bootstraps a fresh loop with asyncio.run(get_model()) from inside
        # generate(). We already hold the GPU slot, so we already have the
        # exclusion the move needs; awaiting our own pool (or the model lock
        # held by the loop that is waiting on us) would deadlock. Move inline.
        ensure_tts_on_device()
        return
    async with _model_lock:
        if _stranded_tts_target() is None:
            return  # another caller healed it while we waited
        try:
            await asyncio.get_running_loop().run_in_executor(
                _get_gpu_pool(), ensure_tts_on_device
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("TTS placement self-heal could not run: %s", e)

_diar_pipeline = None

# Sentinel error classes used by callers (dub_core) to decide whether to
# emit a structured SSE warning with a docs deeplink. Kept as module-level
# constants so tests can pin them — they cross the SSE wire and the
# frontend's errorDocsMap classifies on the same strings.
DIARIZATION_ERR_NO_TOKEN = "NO_TOKEN"
DIARIZATION_ERR_LICENSE  = "PYANNOTE_LICENSE_REQUIRED"
DIARIZATION_ERR_LOAD     = "LOAD_FAILED"


def _classify_diarization_error(exc: BaseException) -> str:
    """Map a pyannote/HF-hub exception to one of the diarization error
    sentinels above.

    The 401/403 path is the canonical "user hasn't accepted the model
    license on huggingface.co" symptom — both `Pipeline.from_pretrained`
    and `huggingface_hub` raise distinct exception classes for it
    depending on the installed versions, so we sniff on both the class
    name and the stringified message rather than importing the
    `HfHubHTTPError` symbol directly (which is not stable across
    huggingface_hub majors).
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if (
        "401" in msg
        or "403" in msg
        or "unauthorized" in msg
        or "gated" in msg
        or "accept" in msg and ("license" in msg or "terms" in msg or "user conditions" in msg)
        or "hfhubhttperror" in name
        or "gatedrepoerror" in name
        or "repositorynotfounderror" in name and "gated" in msg
    ):
        return DIARIZATION_ERR_LICENSE
    return DIARIZATION_ERR_LOAD


def _ensure_pyannote_hf_token_compat():
    """pyannote-audio 3.x calls huggingface_hub.hf_hub_download / snapshot_download
    with the ``use_auth_token`` kwarg, which huggingface_hub 1.x removed (only
    ``token`` remains) — raising ``hf_hub_download() got an unexpected keyword
    argument 'use_auth_token'`` and breaking diarization (#167).

    Wrap those functions to translate the deprecated kwarg. We patch
    huggingface_hub itself BEFORE pyannote is imported, so pyannote's
    ``from huggingface_hub import hf_hub_download`` binds the wrapped fn; we
    also patch any already-imported pyannote submodule that bound it directly.
    Idempotent (guarded by an attribute marker).
    """
    import functools
    import sys as _sys
    import huggingface_hub as _hf

    def _wrap(orig):
        if orig is None or getattr(orig, "_ov_uat_shim", False):
            return orig

        @functools.wraps(orig)
        def _wrapped(*args, **kwargs):
            if "use_auth_token" in kwargs:
                kwargs.setdefault("token", kwargs.pop("use_auth_token"))
            return orig(*args, **kwargs)

        _wrapped._ov_uat_shim = True
        return _wrapped

    for _name in ("hf_hub_download", "snapshot_download"):
        if hasattr(_hf, _name):
            setattr(_hf, _name, _wrap(getattr(_hf, _name)))
    for _modname, _mod in list(_sys.modules.items()):
        if _modname.startswith("pyannote.") and _mod is not None:
            for _name in ("hf_hub_download", "snapshot_download"):
                if hasattr(_mod, _name):
                    setattr(_mod, _name, _wrap(getattr(_mod, _name)))


def get_diarization_pipeline(return_error: bool = False):
    """Load (or return the cached) pyannote speaker-diarization-3.1 pipeline.

    Default return: the pipeline instance, or `None` if anything went
    wrong (no token, license not accepted, model load crashed). Existing
    callers (dub_core legacy `_transcribe`) rely on the `None` sentinel.

    When `return_error=True`, returns a 2-tuple
    `(pipeline | None, error_sentinel | None)` where `error_sentinel` is
    one of the `DIARIZATION_ERR_*` constants. This shape is what the
    streaming `_diarize` path uses to emit a structured SSE warning with
    a docs deeplink — issue #78.
    """
    global _diar_pipeline
    if _diar_pipeline is not None:
        return (_diar_pipeline, None) if return_error else _diar_pipeline

    # Phase 1 AUTH-01: 3-source resolver (App → Env → HF-CLI). Per
    # Pitfall #1 in 01-RESEARCH.md — exactly one place in the backend
    # reads HF tokens, and that place is `token_resolver.resolve()`.
    from services import token_resolver
    resolved = token_resolver.resolve()
    if not resolved:
        return (None, DIARIZATION_ERR_NO_TOKEN) if return_error else None
    hf_token = resolved.token
    try:
        torch = _lazy_torch()
        _ensure_pyannote_hf_token_compat()  # #167: use_auth_token -> token
        # PyTorch 2.6 flipped torch.load's default to weights_only=True, whose
        # secure unpickler rejects the pyannote checkpoint's metadata globals
        # (torch_version.TorchVersion, omegaconf nodes, …) — surfacing as
        # "Weights only load failed / Unsupported global" and breaking
        # diarization on torch>=2.6 even after the license is accepted (#270).
        # Reuse the exact allowlist the WhisperX VAD load registers so the
        # secure load path succeeds; it is idempotent and per-process.
        try:
            from services.asr_backend import WhisperXBackend
            WhisperXBackend._allow_vad_pickle_globals()
        except Exception as _glob_e:
            logger.debug("pyannote safe-globals allowlist skipped: %s", _glob_e)
        from pyannote.audio import Pipeline
        logger.info("Loading Pyannote Diarization Pipeline...")
        _diar_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
        device = get_best_device()
        # Pyannote supports CUDA and CPU; route XPU/DirectML to CPU
        if device in ("cuda",):
            _diar_pipeline.to(torch.device(device))
        logger.info("Pyannote Diarization Pipeline loaded on %s.", device)
        return (_diar_pipeline, None) if return_error else _diar_pipeline
    except Exception as e:
        err_class = _classify_diarization_error(e)
        logger.exception(
            "Failed to load Pyannote pipeline (class=%s)", err_class,
        )
        return (None, err_class) if return_error else None
