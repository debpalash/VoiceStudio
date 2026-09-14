"""SubprocessBackend — long-lived sidecar-process TTS primitive (Phase 2.1).

The architectural keystone for engine isolation. Engines that need their
own Python venv (because their dependency pins conflict with VoiceStudio's
— IndexTTS demands `transformers<5`, VoiceStudio demands `transformers>=5.3`)
run inside a `subprocess.Popen` child interpreter. The parent backend
talks to them through length-prefixed JSON over the child's stdin/stdout.

Subclasses (e.g. ``IndexTTSSubprocessBackend`` in Plan 02-03, the future
Supertonic-3 backend in Phase 3) override exactly two class methods:

    @classmethod
    def venv_python(cls) -> Path: ...   # path to the engine's python
    @classmethod
    def sidecar_script(cls) -> Path: ...  # path to backend/engines/<id>/main.py

Everything else — spawn, ready-handshake, request/response, GPU-slot
accounting, atexit teardown, stderr drainage, process-group cleanup — is
owned by this base class.

NOT IMPLEMENTED with the multiprocessing module (Locked Decision D4 — no
process-cloning variants of any kind). The whole point of this primitive
is to run a *different* Python interpreter than the parent's;
multiprocessing can only clone the current interpreter, which defeats
the dependency-isolation goal. Anyone tempted to "simplify" this should
re-read 02-RESEARCH.md Pitfall 1.

Threat-model summary (see Plan 02-01 frontmatter):
    T-02-01 — DoS via length-prefix: hard cap 64 MB per frame in ``_recv``.
    T-02-02 — GPU slot leak on sidecar death: try/finally in ``generate``.
    T-02-03 — token bytes in stderr: drain via the same logging filter that
              AUTH-05 installed (``HFTokenRedactor``) on the root logger.
    T-02-04 — compromised sidecar emitting unexpected ops: parent allowlist
              ``PARENT_INBOUND_OPS`` rejects everything else.
    T-02-05 — nested containment: a retained POSIX supervisor process group or
              Windows Job owns each engine operation, while still permitting
              independent timeout teardown and cleanup on backend death.
"""
from __future__ import annotations

import atexit
import base64
import contextlib
import collections
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Optional

import numpy as np
import torch

from core.contained_subprocess import spawn_owned
from services.tts_backend import TTSBackend

logger = logging.getLogger("omnivoice.subprocess_backend")


def _os_exec_refusal(exc: OSError) -> str:
    """User-facing cause for a spawn-time OSError, built from errno/strerror
    only — ``str(exc)`` commonly embeds ``exc.filename`` (the interpreter's
    absolute path, i.e. the user's home directory), and this string flows
    into a 503 detail and the UI log viewer / pasted bug reports."""
    cause = exc.strerror or "execution failed"
    if exc.errno is not None:
        cause = f"[Errno {exc.errno}] {cause}"
    return cause


# ── Wire protocol constants ────────────────────────────────────────────────

#: Hard cap per frame body. Defeats length-prefix DoS where a malicious or
#: corrupted sidecar sends `0xFFFFFFFF` and the parent would allocate 4 GB
#: before realising the body never arrives. See T-02-01.
MAX_FRAME_BYTES = 64 * 1024 * 1024

#: Parent-side op allowlist. Any sidecar frame whose ``op`` is not in this
#: set is logged and discarded — prevents a compromised sidecar from
#: invoking unintended parent code paths. See T-02-04.
PARENT_INBOUND_OPS = frozenset({
    "ready", "pong", "audio", "segments", "progress", "error",
    "gpu_acquire", "gpu_release",
})

#: Reference list of ops the sidecar accepts (informational — enforced on
#: the sidecar side, not in this module).
SIDECAR_INBOUND_OPS = frozenset({"ping", "synthesize", "transcribe", "shutdown"})

#: Timeout for the initial ready handshake. Some engines (IndexTTS, large
#: torch.compile graphs) take 20–25 s to import their dependencies before
#: emitting the first frame; 30 s is a comfortable upper bound that still
#: surfaces a hung sidecar within a single CI run.
SPAWN_READY_TIMEOUT_S = 30.0
# How much of a sidecar's stderr a failed ready handshake quotes (#2026).
_STDERR_TAIL_LINES = 12
_STDERR_TAIL_CHARS = 800

#: Per-frame _recv read timeout (best-effort — applies to header read; body
#: read is uninterruptible on a stdlib BufferedReader). Used in health_check
#: and generate to bound a hung sidecar.
RECV_TIMEOUT_S = 60.0


# ── Idle sidecar reaping (parity Action 13) ─────────────────────────────────
#
# A subprocess engine's sidecar holds a process and, for GPU engines, VRAM —
# for the whole life of the backend, even when the user has moved on to another
# engine. The default in-process VoiceStudio model already idle-unloads via
# model_manager.idle_worker; this gives the *subprocess* engine class the same
# treatment: a background reaper shuts down sidecars that have been idle past a
# timeout, and the next request transparently respawns one (the base already
# relaunches on a dead process). Reaping is provably safe against an in-flight
# op because the reaper only acts while holding the per-backend lock acquired
# NON-blockingly — if an op holds it, the reaper skips that backend this round.
#
# Default idle timeout. The live value is resolved per-tick via
# _resolve_sidecar_idle_timeout() (MM2-05) so the Settings store can tune it
# without a restart; the env var still wins. Kept as a module constant for the
# import-time default and for tests that monkeypatch it.
SIDECAR_IDLE_TIMEOUT_S = float(os.environ.get("OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S", "300"))
_REAPER_INTERVAL_S = 30.0


def _resolve_sidecar_idle_timeout() -> float:
    """Idle-reap timeout in seconds (MM2-05): prefs store → env → default, with
    env winning. ``<= 0`` disables reaping. Resolved lazily so a settings change
    takes effect without a restart."""
    from core import prefs
    try:
        return float(prefs.resolve(
            "sidecar_idle_timeout_seconds",
            env="OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S",
            default=SIDECAR_IDLE_TIMEOUT_S,
        ))
    except (TypeError, ValueError):
        return SIDECAR_IDLE_TIMEOUT_S

#: Weak registry of live SubprocessBackend instances the reaper scans. Weak so
#: discarded backends (the fresh-per-call instances) don't leak — once GC'd and
#: their atexit shutdown fires, they drop out on their own.
_LIVE_BACKENDS: "weakref.WeakSet" = weakref.WeakSet()
_reaper_started = False
_reaper_lock = threading.Lock()


def reap_idle_sidecars(timeout_s: float | None = None) -> int:
    """Shut down sidecars idle longer than ``timeout_s``. Returns the count
    reaped. A non-positive timeout disables reaping (returns 0). Safe to call
    from any thread — it never touches a backend that is mid-op (it acquires
    the backend lock non-blockingly and skips on contention)."""
    timeout = _resolve_sidecar_idle_timeout() if timeout_s is None else timeout_s
    if timeout <= 0:
        return 0
    reaped = 0
    for b in list(_LIVE_BACKENDS):
        proc = getattr(b, "_proc", None)
        if proc is None or proc.poll() is not None:
            continue  # no live sidecar to reap
        if b.idle_seconds() < timeout:
            continue
        if not b._lock.acquire(blocking=False):
            continue  # an op holds the lock → not idle; skip this round
        try:
            # Re-check under the lock: an op may have just spawned/used it.
            proc = b._proc
            if proc is not None and proc.poll() is None and b.idle_seconds() >= timeout:
                logger.info(
                    "[%s] reaping idle sidecar (idle %.0fs ≥ %.0fs) to free its "
                    "process/VRAM; next request respawns it",
                    b.id, b.idle_seconds(), timeout,
                )
                b.shutdown()  # shutdown() does not take _lock, so no re-entrancy
                reaped += 1
        finally:
            b._lock.release()
    return reaped


def _force_reap(predicate) -> int:
    """Shut down every live sidecar matching ``predicate`` *now*, ignoring idle
    time. Returns the count shut down. Busy-guarded exactly like the idle
    reaper — a sidecar mid-op (lock held) is skipped, never interrupted; the
    next request transparently respawns whatever was shut down. This backs the
    user-initiated "free engine VRAM now" path (parity Action 13), distinct
    from the time-based auto-reaper."""
    reaped = 0
    for b in list(_LIVE_BACKENDS):
        proc = getattr(b, "_proc", None)
        if proc is None or proc.poll() is not None:
            continue  # no live sidecar
        if not predicate(b):
            continue
        if not b._lock.acquire(blocking=False):
            continue  # an op holds the lock → busy; skip (caller may retry)
        try:
            proc = b._proc
            if proc is not None and proc.poll() is None:
                logger.info(
                    "[%s] manual sidecar unload (freeing process/VRAM on "
                    "request); next request respawns it", b.id,
                )
                b.shutdown()  # shutdown() does not take _lock, so no re-entrancy
                reaped += 1
        finally:
            b._lock.release()
    return reaped


def list_live_sidecars() -> list[dict]:
    """Snapshot of subprocess engines with a currently-running sidecar, for the
    loaded-models panel. Each entry: ``{id, pid, idle_seconds}``. Lets a user
    see (and free) sidecar VRAM the same way they unload the in-process TTS
    model."""
    out: list[dict] = []
    for b in list(_LIVE_BACKENDS):
        proc = getattr(b, "_proc", None)
        if proc is None or proc.poll() is not None:
            continue
        out.append({
            "id": b.id,
            "pid": proc.pid,
            "idle_seconds": round(b.idle_seconds(), 1),
            "vram_mb": round(getattr(b, "_vram_mb", 0.0), 1),  # MM2-08; 0 = CPU/unmeasured
        })
    return out


def unload_sidecar(engine_id: str) -> int:
    """Force-shut a specific engine's sidecar now (busy-guarded). Returns the
    number shut down (0 if it wasn't running or was busy)."""
    return _force_reap(lambda b: b.id == engine_id)


def unload_all_sidecars() -> int:
    """Force-shut every live sidecar now (busy-guarded). Returns the count."""
    return _force_reap(lambda b: True)


def _reaper_loop() -> None:
    while True:
        time.sleep(_REAPER_INTERVAL_S)
        try:
            reap_idle_sidecars()
        except Exception:  # pragma: no cover - defensive; a reap error must not kill the thread
            logger.exception("sidecar idle reaper error")


def _ensure_reaper_running() -> None:
    """Start the daemon reaper thread once, lazily, on first sidecar spawn."""
    global _reaper_started
    if _reaper_started or _resolve_sidecar_idle_timeout() <= 0:
        return
    with _reaper_lock:
        if _reaper_started:
            return
        threading.Thread(
            target=_reaper_loop, name="sidecar-idle-reaper", daemon=True,
        ).start()
        _reaper_started = True


# ── Base class ─────────────────────────────────────────────────────────────


#: How often to prove liveness while an engine's venv is being resolved.
#: Matches the sidecar's own cold-load cadence (#1367) so the guarded waiter
#: sees the same rhythm from both steps.
_RESOLVE_HEARTBEAT_S = 5.0


@contextlib.contextmanager
def _heartbeat_while_resolving(engine_id: str):
    """Report progress while a slow engine-venv resolution runs (#1414).

    A probe that spawns interpreters, or a bootstrap that installs torch, can
    outlast the generate budget on its own. Both are demonstrably *working*
    the whole time, so the deadline should extend rather than expire — which
    is what the execution clock's load heartbeat is for.

    Two details that are easy to get wrong:

    * **Pool jobs only.** An off-pool caller never runs on a thread the clock
      tracks, and heartbeating from one would credit an ident a pool worker
      might later reuse — up to a grace period of unearned extension, which
      is the #1379 lesson.
    * **The resolving thread's ident, not the beater's.** The heartbeat runs
      on a helper thread so it can tick while resolution blocks, but the job
      the clock is watching is the caller's. Capturing the ident up front is
      what makes the extension land on the right job.

    Never raises: a failed heartbeat must not fail a generation.
    """
    try:
        from services.model_manager import running_on_gpu_pool

        on_pool = running_on_gpu_pool()
    except Exception:  # noqa: BLE001 — best-effort by construction
        on_pool = False
    if not on_pool:
        yield
        return

    ident = threading.get_ident()
    stop = threading.Event()

    def _beat():
        try:
            from services.model_manager import (
                MODEL_LOAD_HEARTBEAT_GRACE_S, _MODEL_LOAD_ACTIVITY,
            )
        except Exception:  # noqa: BLE001
            return
        while not stop.wait(_RESOLVE_HEARTBEAT_S):
            try:
                _MODEL_LOAD_ACTIVITY[ident] = (
                    time.monotonic(), MODEL_LOAD_HEARTBEAT_GRACE_S,
                )
            except Exception:  # noqa: BLE001
                return

    t = threading.Thread(
        target=_beat, name=f"{engine_id}-resolve-heartbeat", daemon=True,
    )
    t.start()
    try:
        yield
    finally:
        stop.set()
        # Join, don't just signal. `_beat()` can be past its `stop.wait()` and
        # already committed to a write at the moment the flag is set, so
        # signalling alone lets that write land at an arbitrary later point.
        #
        # That matters because of what runs next: `_run_on_gpu_pool`'s `_job`
        # pops this ident from `_MODEL_LOAD_ACTIVITY` in its `finally`
        # (model_manager.py) precisely so a stale beat cannot vouch for a
        # future job — GPU-pool idents are reused. A write arriving after that
        # pop resurrects the entry, and the next job scheduled onto this
        # worker inherits a heartbeat it never emitted: the wedge detector
        # reads it as live progress and keeps extending a job that is stuck.
        #
        # Joining orders the last write BEFORE the pop, so the pop clears it.
        # This must not be bounded: returning while the helper is still alive
        # would recreate the late-write race this join closes.  The helper's
        # only work after ``wait`` is an in-memory mapping assignment guarded
        # by its own broad exception handler, so there is no blocking external
        # operation to time out here.
        t.join()


class SubprocessBackend(TTSBackend):
    """Long-lived sidecar-process TTS backend. Subclasses provide
    ``venv_python()`` and ``sidecar_script()``; the base class owns
    spawn/shutdown/_send/_recv/generate + GPU-slot acquire/release.
    """

    # Stable marker so `list_backends()` can detect subprocess-isolated
    # backends without relying on `issubclass()`. ``issubclass`` fails when
    # test fixtures purge `sys.modules["services"]` (as the token_resolver
    # tests do for DB isolation) — the re-imported SubprocessBackend would
    # be a different class object from the one the subclass closed over.
    # A duck-typed marker survives that.
    _is_subprocess_isolated: bool = True
    spawn_ready_timeout_s: float = SPAWN_READY_TIMEOUT_S

    # Generation happens in the sidecar: parent-side accelerator counters
    # can't see its allocations (see TTSBackend.runs_out_of_process).
    runs_out_of_process: bool = True

    # Default sample rate; subclasses override.
    _DEFAULT_SAMPLE_RATE = 24000

    # Per-engine recv timeout for generate(): how long the parent waits for the
    # sidecar's audio frame before the watchdog hard-kills the child and reclaims
    # its VRAM/device. Default is the conservative RECV_TIMEOUT_S (60s). A
    # subclass whose legitimate generates run longer overrides it (or exposes it
    # as a property) so a slow-but-valid synth is not falsely killed, while a
    # genuinely wedged one is still reclaimed. health_check() keeps using
    # RECV_TIMEOUT_S directly, since a ping must stay fast.
    recv_timeout_s: float = RECV_TIMEOUT_S

    # ── instance state (initialised in __init__) ───────────────────────────

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        # A failed bounded reap must retain ownership and forbid reuse. This
        # lock is separate from _lock: the receive owner joins its watchdog.
        self._timeout_quarantine: list[subprocess.Popen] = []
        self._timeout_quarantine_lock = threading.Lock()
        # Single lock serialises spawn + every send/recv pair so two threads
        # can't interleave half-frames on the same pipe.
        self._lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        # The current sidecar's last stderr lines and whether the last
        # receive hit its deadline, so a failed ready handshake can say
        # which way it failed (#2026).
        self._stderr_tail: collections.deque = collections.deque(maxlen=_STDERR_TAIL_LINES)
        self._last_recv_timed_out = False
        # Monotonic timestamp of the last sidecar activity, for the idle reaper
        # (parity Action 13). Registered in the weak live-backend set so the
        # reaper can find this instance's sidecar.
        self._last_used = time.monotonic()
        # Last-known GPU memory the sidecar self-reported in a pong (MM2-08).
        # 0 = CPU-only or not yet measured. The parent can't measure a child's
        # VRAM, so this is the only source of a real figure.
        self._vram_mb = 0.0
        _LIVE_BACKENDS.add(self)
        # Idempotent atexit shutdown (Pitfall 6 layer 1). If the interpreter
        # exits without an explicit shutdown call, this still tears down the
        # sidecar tree.
        atexit.register(self.shutdown)

    def _touch(self) -> None:
        """Mark the sidecar as just-used so the idle reaper leaves it alone."""
        self._last_used = time.monotonic()

    def _validate_generate_authorization(self) -> None:
        """Revalidate subclass-specific authorization after queueing.

        Called while ``_lock`` is held and immediately before sidecar access so
        a request that waited behind another synthesis cannot outlive a revoked
        capability. Most subprocess engines have no extra authorization.
        """

    def idle_seconds(self) -> float:
        """Seconds since the last sidecar activity (spawn or frame I/O)."""
        return time.monotonic() - self._last_used

    def unload(self) -> None:
        """Release this engine's sidecar (MM2-02). Routes to the same
        force-reap path the manual /model/unload endpoint uses, so a busy
        sidecar (mid-synth) is skipped, not interrupted. Idempotent: a no-op
        when no sidecar is running. Inherited by every subprocess engine."""
        try:
            unload_sidecar(self.id)
        except Exception:
            pass

    # ── subclass contract ──────────────────────────────────────────────────

    @classmethod
    def venv_python(cls) -> Path:
        """Path to the Python interpreter that runs the sidecar.

        Subclasses point at their engine's dedicated venv. The echo sidecar
        and unit tests point at ``sys.executable`` so they run under the
        bare parent interpreter.
        """
        raise NotImplementedError

    @classmethod
    def sidecar_script(cls) -> Path:
        """Path to the sidecar entrypoint (`backend/engines/<id>/main.py`)."""
        raise NotImplementedError

    # ── lifecycle ──────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        """Launch the sidecar if not already running. Blocks on the ready
        handshake. Caller must hold self._lock."""
        if not self._retry_timeout_cleanup():
            raise RuntimeError(
                f"{self.id} sidecar is still stopping after a timeout; retry once it exits"
            )
        if self._proc is not None and self._proc.poll() is None:
            return  # already up

        # Env forwarding contract (Locked Decision D5):
        #   - Inherit the parent's full env via os.environ.copy().
        #   - The parent's env already carries HF_TOKEN (injected by the
        #     Phase 1 AUTH-04 launch sites that call
        #     ``token_resolver.resolve()``), HF_HOME, HF_ENDPOINT, and
        #     HF_HUB_CACHE.
        #   - PYTHONUNBUFFERED=1 keeps the sidecar's stdout from buffering
        #     past our length-prefix reads.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
            "bufsize": 0,  # unbuffered binary pipes
        }
        # `venv_python()` resolves the engine's interpreter, and on a cold
        # first run that is not cheap: it spawns each candidate to import the
        # engine (bounded, but tens of seconds on a slow disk), and if none is
        # installed it can run the whole `uv venv` + `uv pip install`
        # bootstrap — minutes, by design.
        #
        # All of that happens on a GPU-pool worker, inside a generate request
        # whose execution budget is 300s by default. Nothing along the way
        # reported progress, so the budget expired mid-install and the job was
        # abandoned and blamed on the machine's compute (#1414). The sidecar's
        # own cold load already heartbeats for exactly this reason (#1367);
        # resolution is the step before it that never did.
        with _heartbeat_while_resolving(self.id):
            python_path = str(self.venv_python())
        script_path = str(self.sidecar_script())
        # #1172 class: validate the interpreter before exec so a broken /
        # half-installed engine venv (0-byte or truncated python, dangling
        # symlink) surfaces as a typed, actionable error instead of an
        # OSError "[Errno 8] Exec format error" at spawn time.
        from services.binary_preflight import InvalidBinaryError, validate_executable
        _venv_hint = (
            f"the '{self.id}' engine's private environment is broken — "
            f"reinstall the engine from Model Catalogue"
        )
        validate_executable(python_path, hint=_venv_hint)
        # Basenames only — absolute paths embed the user's home directory,
        # and these lines flow into the UI log viewer / pasted bug reports.
        logger.info(
            "[%s] spawning sidecar: %s %s",
            self.id, Path(python_path).name, Path(script_path).name,
        )
        try:
            self._proc = spawn_owned([python_path, script_path], **kwargs)
        except OSError as exc:
            raise InvalidBinaryError(
                python_path,
                f"the OS refused to execute it ({_os_exec_refusal(exc)})",
                _venv_hint,
            ) from exc

        # Drain stderr in a background thread so the sidecar can't block on
        # a full pipe. Lines flow into the root logger; AUTH-05's
        # HFTokenRedactor (already installed in Phase 1) strips token bytes.
        # See T-02-03.
        # A fresh buffer per process, owned by its drain thread: a previous
        # process's drain that is still finishing writes to its own buffer,
        # never into the one this start-up failure will quote.
        self._stderr_tail = collections.deque(maxlen=_STDERR_TAIL_LINES)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
            args=(self._proc, self._stderr_tail),
            name=f"{self.id}-stderr-drain",
        )
        self._stderr_thread.start()

        # Block on the ready handshake. A sidecar that fails to emit ready
        # within SPAWN_READY_TIMEOUT_S is killed and the failure is raised.
        try:
            frame = self._recv_with_timeout(self.spawn_ready_timeout_s)
        except Exception:
            self._force_kill()
            raise
        if not frame or frame.get("op") != "ready":
            # Read the cause before the kill: afterwards every child has
            # an exit code, and it is ours.
            reason = self._ready_failure_reason(frame)
            self._force_kill()
            raise RuntimeError(f"{self.id} sidecar did not signal ready: {reason}")
        logger.info("[%s] sidecar ready", self.id)
        self._touch()
        _ensure_reaper_running()

    def _ready_failure_reason(self, frame: Optional[dict]) -> str:
        """Say which of the three handshake failures happened (#2026).

        ``_recv`` returns None on EOF, and a watchdog kill closes stdout just
        like a sidecar that crashed on its own, so the frame alone reads
        ``None`` for both. The deadline flag, the child's exit code and its
        last stderr lines tell them apart.
        """
        from core.scrub import scrub_text

        if frame:
            op = frame.get("op")
            if op == "error":
                message = scrub_text(str(frame.get("message") or ""))[:_STDERR_TAIL_CHARS]
                reason = f"it reported an error instead: {message}"
            else:
                reason = f"it sent op={op!r} instead of 'ready' (a protocol mismatch)"
        elif self._last_recv_timed_out:
            reason = (
                f"no ready frame within {self.spawn_ready_timeout_s:g}s, so it was stopped"
            )
        else:
            code = None
            proc = self._proc
            if proc is not None:
                try:
                    code = proc.wait(timeout=2)
                except Exception:
                    code = proc.poll()
            reason = (
                f"it exited with code {code} before signalling ready"
                if code is not None
                else "it closed its output before signalling ready"
            )
        tail = self._stderr_tail_text()
        return f"{reason}. Last stderr: {tail}" if tail else f"{reason} (no stderr output)"

    def _stderr_tail_text(self) -> str:
        """The sidecar's last stderr lines, scrubbed for a user-visible error."""
        from core.scrub import scrub_text

        thread = self._stderr_thread
        if thread is not None and thread.is_alive():
            # The child is gone or going; let the drain catch its last lines.
            thread.join(timeout=1.0)
        lines = list(getattr(self, "_stderr_tail", ()))
        if not lines:
            return ""
        text = scrub_text(" | ".join(lines))
        if len(text) > _STDERR_TAIL_CHARS:
            text = "…" + text[-_STDERR_TAIL_CHARS:]
        return text

    def shutdown(self) -> None:
        """Idempotent. Sends {op:shutdown}; falls back to terminate/kill."""
        proc = self._proc
        if proc is None:
            self._retry_timeout_cleanup()
            return
        try:
            try:
                # Best effort — sidecar may already be dead.
                self._send({"op": "shutdown"})
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "[%s] sidecar did not exit on shutdown frame; terminating",
                    self.id,
                )
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "[%s] sidecar did not exit on SIGTERM; killing",
                        self.id,
                    )
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            self._proc = None
            self._retry_timeout_cleanup()

    def _force_kill(self) -> None:
        """Internal: kill a sidecar that never reached the ready state."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass
        finally:
            self._proc = None

    def unload(self) -> None:
        """TTSBackend.unload override — idempotent shutdown."""
        self.shutdown()

    # ── health check + generate ────────────────────────────────────────────

    def health_check(self) -> tuple[bool, str]:
        """Send ping, expect pong. Spawns the sidecar if needed.

        Returns (True, "pong") on success, (False, "<exc>") on any failure.
        Never raises — health checks are called from places (engine picker,
        Compat Matrix UI) that must keep working even when an engine is sick.
        """
        try:
            with self._lock:
                self._spawn()
                self._send({"op": "ping"})
                reply = self._recv_with_timeout(RECV_TIMEOUT_S)
            if reply and reply.get("op") == "pong":
                # Sidecars may self-report their GPU memory (MM2-08) — the parent
                # can't measure a child's VRAM. Stash the last-known figure so
                # list_live_sidecars() can surface a real number instead of 0.
                if "vram_mb" in reply:
                    try:
                        self._vram_mb = float(reply["vram_mb"] or 0)
                    except (TypeError, ValueError):
                        pass
                return True, "pong"
            return False, f"unexpected reply: {reply!r}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def generate(self, text: str, **kw) -> torch.Tensor:
        """Synthesize one utterance through the sidecar.

        Returns a tensor of shape (1, n_samples) at the sidecar-reported
        sample rate. Decodes the int16 PCM the sidecar returns into float32
        in [-1, 1].
        """
        # On-pool callers (every HTTP/dub/batch generate, dispatched via
        # run_on_gpu_pool_guarded) already own a pool slot; re-acquiring would
        # self-deadlock on a 1-worker (MPS) pool, so skip it. Off-pool callers
        # (the deep-synth diagnostic probe in diagnose.py; the Settings
        # self-test rejects subprocess-isolated engines with a 400) hold a real
        # slot for the whole synthesis via _occupy so they serialize against
        # pool jobs instead of over-subscribing the GPU.
        from services.model_manager import running_on_gpu_pool
        _held = None
        # Bound before the branch: only the off-pool path assigns a real
        # future, and `_held is not None` already implies that — but CodeQL
        # (py/uninitialized-local-variable) reads the two as independent, and
        # so would anyone adding a third exit path later.
        slot_future = None
        if not running_on_gpu_pool():
            # Lazy-import the GPU pool so importing this module doesn't pull in
            # the entire model_manager + torch ecosystem at registry-listing time.
            from services.model_manager import _get_gpu_pool
            pool = _get_gpu_pool()
            _held = threading.Event()
            _acquired = threading.Event()

            def _occupy():
                _acquired.set()
                _held.wait()

            slot_future = pool.submit(_occupy)

        try:
            if _held is not None and not _acquired.wait(timeout=10):
                if slot_future is not None:
                    slot_future.cancel()
                raise TimeoutError("timed out waiting for a free GPU worker")
            with self._lock:
                self._validate_generate_authorization()
                self._spawn()
                # getattr keeps lightweight protocol-loop test doubles valid;
                # real instances always initialise _proc in __init__.
                proc = getattr(self, "_proc", None)
                msg = {"op": "synthesize", "text": text}
                # Filter kwargs to JSON-safe primitives. Tensor / Path / etc.
                # don't survive json.dumps and are silently dropped — the
                # sidecar can't use them anyway.
                for k, v in kw.items():
                    if _is_jsonable(v):
                        msg[k] = v
                try:
                    self._send(msg)
                    reply = self._recv_with_timeout(self.recv_timeout_s)
                    timed_out = self._last_recv_timed_out
                except (RuntimeError, OSError):
                    # A broken or malformed protocol stream cannot be reused.
                    # Reap it before releasing the request lock so an immediate
                    # retry cannot race poll() and write to the same dead pipe.
                    self._reap_unusable_process(proc)
                    raise
                # A cold sidecar may emit non-terminal {"op": "progress"} frames
                # (during a model load, etc.) before the terminal audio frame.
                # Each recv re-arms the watchdog, so a long-but-active load
                # survives while a silent wedge is still killed at the deadline.
                #
                # Each frame is also reported to the GPU pool's execution clock
                # (#1367): the sidecar heartbeats every ~5s precisely to prove a
                # cold download is healthy, and without this the outer 300s
                # generate budget expired mid-download and blamed the hardware.
                while reply is not None and reply.get("op") == "progress":
                    try:
                        from services.model_manager import (
                            report_model_load_activity, running_on_gpu_pool,
                        )
                        # Pool jobs only: an off-pool caller (the diagnostic
                        # probe) never runs _job(), so its thread ident would
                        # never be cleared — and a pool worker later reusing
                        # that ident would inherit up to a grace period of
                        # unearned extension (CodeRabbit on #1379).
                        if running_on_gpu_pool():
                            report_model_load_activity()
                    except Exception:
                        pass  # the heartbeat is best-effort; never fail a synth over it
                    try:
                        reply = self._recv_with_timeout(self.recv_timeout_s)
                        timed_out = self._last_recv_timed_out
                    except (RuntimeError, OSError):
                        self._reap_unusable_process(proc)
                        raise
                if not reply:
                    self._reap_unusable_process(proc)
                    if timed_out:
                        env_key = f"OMNIVOICE_{self.id.upper().replace('-', '_')}_RECV_TIMEOUT_S"
                        raise RuntimeError(
                            f"{self.id} sidecar exceeded receive timeout "
                            f"({self.recv_timeout_s:g}s); killed mid-generate "
                            f"— retry or raise {env_key}."
                        )
                    raise RuntimeError(f"{self.id} sidecar closed pipe mid-generate")
            if reply.get("op") == "error":
                stage = str(reply.get("stage") or "unknown")
                message = str(reply.get("message") or "unknown sidecar error")
                traceback_text = str(reply.get("traceback") or "").strip()
                logger.error(
                    "[%s] sidecar %s error: %s%s",
                    self.id,
                    stage,
                    message,
                    f"\n{traceback_text[:20_000]}" if traceback_text else "",
                )
                raise RuntimeError(
                    f"{self.id} sidecar {stage} error: {message}"
                )
            if reply.get("op") != "audio":
                raise RuntimeError(
                    f"{self.id} sidecar returned unexpected op: {reply.get('op')!r}"
                )
            pcm_b64 = reply.get("audio_pcm_b64", "")
            pcm = base64.b64decode(pcm_b64)
            arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(arr.copy()).unsqueeze(0)
            return tensor
        finally:
            # Release the held GPU-pool worker (off-pool path only). _occupy
            # blocks the worker until this fires, so the slot is held for the
            # whole synthesis even though this thread isn't the pool worker.
            if _held is not None:
                _held.set()

    # ── wire protocol ──────────────────────────────────────────────────────

    def _send(self, msg: dict) -> None:
        """Length-prefixed JSON over the sidecar's stdin. Caller holds lock."""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"{self.id} sidecar not running")
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES:
            raise IOError(f"outbound frame too large: {len(body)}")
        try:
            self._proc.stdin.write(struct.pack("!I", len(body)))
            self._proc.stdin.write(body)
            self._proc.stdin.flush()  # Pitfall 2 — mandatory flush
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(
                f"{self.id} sidecar pipe closed: {exc}"
            ) from exc

    def _recv(self) -> Optional[dict]:
        """Read one frame from the sidecar's stdout. Returns None on EOF.

        Op allowlist is enforced here: unknown ops are logged and dropped,
        and we tail-recurse to read the next frame. See T-02-04.
        """
        if self._proc is None or self._proc.stdout is None:
            return None
        stdout = self._proc.stdout
        header = _read_exact(stdout, 4)
        if header is None:
            return None
        (n,) = struct.unpack("!I", header)
        if n > MAX_FRAME_BYTES:
            # T-02-01 — refuse to allocate before the body even arrives.
            raise IOError(f"frame too large: {n}")
        body = _read_exact(stdout, n)
        if body is None or len(body) != n:
            raise IOError("short read")
        try:
            msg = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise IOError(f"malformed sidecar frame: {exc}") from exc
        op = msg.get("op") if isinstance(msg, dict) else None
        if op not in PARENT_INBOUND_OPS:
            # T-02-04 — refuse to act on unknown ops. Log and read the
            # next frame so we don't desync.
            logger.warning(
                "[%s] dropped sidecar frame with disallowed op=%r",
                self.id, op,
            )
            return self._recv()
        return msg

    def _recv_with_timeout(self, timeout_s: float) -> Optional[dict]:
        """Read one frame, finishing timeout cleanup before the caller can retry.

        A watchdog closes the pipe on timeout; Windows cannot select on pipes.
        EOF alone does not prove the owned process/supervisor has exited.
        """
        # On Unix we could use selectors; on Windows the pipe is not
        # selectable. Use a watchdog thread that kills the sidecar on
        # timeout — that triggers EOF on stdout, so _recv returns None
        # and the caller raises.
        proc = self._proc
        fired = threading.Event()

        def _on_timeout() -> None:
            fired.set()
            self._timeout_kill(proc)

        watchdog = threading.Timer(timeout_s, _on_timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            return self._recv()
        finally:
            watchdog.cancel()
            # cancel() cannot stop an already-running callback. Finish its
            # bounded reap before another receive or generation starts.
            watchdog.join()
            # EOF reads the same after a deadline kill and after a crash;
            # this is what tells the two apart (#2026).
            self._last_recv_timed_out = fired.is_set()
            self._touch()  # any reply (or attempt) counts as recent activity

    def _timeout_kill(self, proc: Optional[subprocess.Popen]) -> None:
        """Kill only the child this receive captured, then reap its owner."""
        if proc is None:
            return
        logger.error("[%s] sidecar exceeded recv timeout; killing", self.id)
        try:
            proc.kill()
        except Exception:
            # A raced exit can make kill fail, but its owner still needs reaping.
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            # Do not discard a possibly live owner, or replace a newer _proc.
            with self._timeout_quarantine_lock:
                if not any(item is proc for item in self._timeout_quarantine):
                    self._timeout_quarantine.append(proc)
        else:
            with self._timeout_quarantine_lock:
                self._timeout_quarantine = [
                    item for item in self._timeout_quarantine if item is not proc
                ]

    def _reap_unusable_process(self, proc: Optional[subprocess.Popen]) -> None:
        """Synchronously retire a sidecar whose protocol pipe is unusable."""
        if proc is None:
            return
        try:
            proc.wait(timeout=0.25)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                with self._timeout_quarantine_lock:
                    if not any(item is proc for item in self._timeout_quarantine):
                        self._timeout_quarantine.append(proc)
                return
        with self._timeout_quarantine_lock:
            self._timeout_quarantine = [
                item for item in self._timeout_quarantine if item is not proc
            ]

    def _retry_timeout_cleanup(self) -> bool:
        """Retry bounded cleanup, retaining every owner that could still be live."""
        with self._timeout_quarantine_lock:
            pending = tuple(self._timeout_quarantine)
        for proc in pending:
            self._timeout_kill(proc)
        with self._timeout_quarantine_lock:
            return not self._timeout_quarantine

    # ── stderr drain ───────────────────────────────────────────────────────

    def _drain_stderr(
        self,
        proc: Optional[subprocess.Popen] = None,
        tail: Optional[collections.deque] = None,
    ) -> None:
        """Pump sidecar stderr lines into the parent logger.

        Prefixes each line with `[<engine_id>]`. The HFTokenRedactor filter
        installed at the root logger in Phase 1 redacts any token bytes
        that slip through. See T-02-03.
        """
        # Bound at spawn: a drain thread that starts late must still read
        # its own process, never a replacement published since (#2026).
        if proc is None:
            proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    line = repr(raw)
                if line:
                    logger.info("[%s] %s", self.id, line)
                    if tail is not None:
                        tail.append(line)
        except Exception as exc:
            logger.debug("[%s] stderr drain ended: %s", self.id, exc)


# ── helpers ────────────────────────────────────────────────────────────────


def _read_exact(stream, n: int) -> Optional[bytes]:
    """Read exactly n bytes from a BufferedReader, or return None on EOF.

    BufferedReader.read(n) is allowed to return fewer than n bytes when
    the underlying file descriptor is a pipe — we loop until we have
    all of them or EOF.
    """
    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            if not out:
                return None
            return bytes(out)
        out.extend(chunk)
    return bytes(out)


def _is_jsonable(v) -> bool:
    """Quick filter for kwargs that survive json.dumps. Lists/dicts are
    accepted only if their contents are themselves jsonable."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return True
    if isinstance(v, (list, tuple)):
        return all(_is_jsonable(x) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_jsonable(x) for k, x in v.items())
    return False


__all__ = [
    "SubprocessBackend",
    "MAX_FRAME_BYTES",
    "PARENT_INBOUND_OPS",
    "SIDECAR_INBOUND_OPS",
    "SIDECAR_IDLE_TIMEOUT_S",
    "reap_idle_sidecars",
    "list_live_sidecars",
    "unload_sidecar",
    "unload_all_sidecars",
]
