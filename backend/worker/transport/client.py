"""Worker-side gRPC client.

Runs on the machine with the GPU. Its job is to stay connected, report what it
can do honestly, and execute what it is given.

Two things here are load-bearing:

**Certificate pinning.** The control plane is a desktop with a self-signed
certificate, so the enrollment token's fingerprint is the trust anchor. The
pinned certificate is supplied as the *only* trusted root, which means an
attacker on the same network cannot substitute their own — and there is no
flag to turn that off.

**Reconnect with backoff and jitter.** Home networks drop. A worker that
reconnects instantly and in lockstep with its siblings turns a thirty-second
outage into a thundering herd, so the delay grows and is jittered. Crucially
the worker keeps any unacknowledged result across the reconnect and redelivers
it: that is the half of at-least-once delivery that lives on this side.

**Liveness is this side's job.** The control plane fails an attempt that goes
silent for a progress lease, and the longest silence in a task's life — the
cold model load — happens *after* the worker says it started. So every running
task carries a timer that renews the lease, marked ``keepalive`` so the server
can tell "still working" from "still ticking" and bound it by the phase budget.

**Bulk bytes never ride the control stream.** A result above the negotiated
inline threshold goes over UploadResult on a second RPC, and the control
stream carries only its ``ArtifactRef``. What is left on that stream is split
again into control and bulk queues, because the heartbeat this whole liveness
model rests on must not queue behind a payload — including the one payload,
``result_json``, that has no size cliff to catch it.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import platform
import random
import socket
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

import grpc

from worker.async_utils import drain_task, to_thread_and_drain_on_cancel
from worker import errors as worker_errors
from worker import identity
from worker.capacity import clamp_concurrency
from worker.errors import ErrorClass, WorkerError
from worker.identity import EnrollmentToken, WorkerKeypair
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.transport import codec
from worker.transport.server import (
    CHALLENGE_EXPIRED,
    PROTOCOL_VERSION,
    REQUIRED_FEATURES,
    SESSION_METADATA_KEY,
)

logger = logging.getLogger("omnivoice.worker")

_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0
_HEARTBEAT_SECONDS = 20.0
# A malformed/legacy installer may return without emitting a terminal progress
# frame. Never park the worker's prewarm task forever in that state.
_FALLBACK_MODEL_LOAD_SECONDS = 1800.0

# The gRPC frame ceiling, matched by the server's receive limit. A result that
# does not fit in one frame cannot be delivered on the control stream at all —
# see _oversized_result_error for why that has to be a failure and not a retry.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


def _heartbeat_resources() -> tuple[Optional[float], Optional[int], Optional[float]]:
    """Sample cheap host telemetry without making a heartbeat depend on CUDA."""
    cpu_percent = free_memory_bytes = gpu_utilization_percent = None
    try:
        import psutil

        cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:
        logger.debug("Could not sample worker CPU usage", exc_info=True)
    try:
        import torch

        if torch.cuda.is_available():
            free_memory_bytes = int(torch.cuda.mem_get_info()[0])
    except Exception:
        logger.debug("Could not sample worker free VRAM", exc_info=True)
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_utilization_percent = float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
    except Exception:
        logger.debug("Could not sample worker GPU usage", exc_info=True)
    return cpu_percent, free_memory_bytes, gpu_utilization_percent

# Room left for result_json, the ref, and protobuf framing when a payload does
# ride inline. The inline decision is made on the payload alone, so without a
# reserve a payload sized exactly at the frame cap would overflow it.
_INLINE_FRAME_HEADROOM_BYTES = 64 * 1024

# One upload chunk. Small enough that a chunk boundary — and therefore a lease
# renewal — comes round often on a slow uplink, large enough that a 100 MB dub
# is a hundred frames rather than a hundred thousand.
_UPLOAD_CHUNK_BYTES = 1024 * 1024

# How many times a single upload may be asked to resume before the worker
# calls the receiver broken. Generous for a genuinely flaky uplink — each
# resume restarts from a real byte count, so honest progress needs very few —
# and low enough that a receiver stuck in a resume loop costs one attempt
# rather than this worker's whole session.
_MAX_UPLOAD_RESUMES = 8

# The progress stage a result upload reports under. The control plane keys
# RESULT_UPLOADING (and its much longer delivery budget) off this, so it is a
# wire constant, not a cosmetic label.
UPLOAD_STAGE = "uploading"


class ArtifactTransport(Protocol):
    """Inbound node staging operations used by a worker client."""

    async def publish(
        self, ref: pb.TaskRef, payload: bytes, meta: dict
    ) -> pb.ArtifactRef: ...

    async def stage_in(self, ref: pb.ArtifactRef, destination: str) -> None: ...

    def result_acked(self, artifacts: list[pb.ArtifactRef]) -> None: ...

# Used when an assignment carries no lease (an older control plane, or a test).
# Mirrors deadlines.py's _HEARTBEAT_GRACE_S * 4.
_DEFAULT_PROGRESS_LEASE_SECONDS = 120.0

# Purely a busy-loop guard against a malformed lease, not a policy: a server
# that asks for a 0.001s lease should not spin this process.
_MIN_KEEPALIVE_INTERVAL_SECONDS = 0.05

# Reporter keywords the client offers the executor, per task.
_EXECUTOR_KWARGS = frozenset({"on_progress", "on_model_loading", "fetch_input"})


def _write_all(handle, payload: bytes) -> None:
    """Write a complete chunk, including through short-writing file wrappers."""
    remaining = memoryview(payload)
    while remaining:
        written = handle.write(remaining)
        if written is None or written <= 0:
            raise OSError("input destination made no write progress")
        remaining = remaining[written:]


def _close_and_remove(handle, destination: str) -> None:
    """Finish file cleanup as one blocking operation after cancellation."""
    if handle is not None:
        with contextlib.suppress(OSError):
            handle.close()
    with contextlib.suppress(OSError):
        os.remove(destination)


class TerminalRegistrationError(RuntimeError):
    """A registration failure that reconnecting cannot repair."""


def keepalive_interval(lease_seconds: float) -> float:
    """How often a running task must renew its progress lease.

    A third of the lease, so two consecutive frames can be lost — to a stalled
    outbox, a reconnect, or a GIL-bound moment — before the attempt expires.
    """
    lease = float(lease_seconds or 0.0)
    if lease <= 0:
        lease = _DEFAULT_PROGRESS_LEASE_SECONDS
    return max(lease / 3.0, _MIN_KEEPALIVE_INTERVAL_SECONDS)


def _accepted_reporter_kwargs(execute: Callable) -> frozenset[str]:
    """Which reporter keywords the injected executor will accept.

    Probed once rather than assumed. The executor is injected and the transport
    tests pass a bare ``async def (assignment)``; a client that always passed
    the reporters would raise TypeError inside _run, where the generic handler
    would report a transport mismatch as a failed generation.
    """
    try:
        parameters = inspect.signature(execute).parameters
    except (TypeError, ValueError):  # C-implemented or otherwise unintrospectable
        return frozenset()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return _EXECUTOR_KWARGS
    return frozenset(name for name in _EXECUTOR_KWARGS if name in parameters)


def backoff_delay(attempt: int, *, jitter: Optional[Callable[[], float]] = None) -> float:
    """Exponential backoff with full jitter, bounded.

    Full jitter rather than a fixed fraction: with several workers behind the
    same router, a deterministic delay reconnects them all in the same instant
    and the control plane sees a spike exactly when it is least able to absorb
    one.
    """
    ceiling = min(_MAX_BACKOFF_SECONDS, _BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1)))
    roll = jitter() if jitter is not None else random.random()
    return ceiling * roll


class _Outbox:
    """Two queues behind one interface: control frames overtake bulk ones.

    The liveness model is built on the heartbeat arriving every 20 s, but a
    single FIFO puts that heartbeat *behind* whatever result frame is being
    written — and a result frame is the one message with no small upper bound
    on its size. The worker then looks dead while it is in fact busy delivering
    exactly the work it was asked for.

    Splitting by class rather than shrinking the payload is the durable fix:
    the upload path below already moves the big bytes off this stream, but
    ``result_json`` has no size cliff to catch, and the next bulk message added
    to the protocol would reintroduce the stall silently.

    Strict priority, not fair queuing: control frames are small, bounded in
    number by the number of running tasks, and only ever *reduce* work — there
    is nothing here for a bulk frame to be starved by for long.
    """

    def __init__(self) -> None:
        self.control: asyncio.Queue[pb.WorkerMessage] = asyncio.Queue()
        self.bulk: asyncio.Queue[pb.WorkerMessage] = asyncio.Queue()
        self._arrival = asyncio.Event()

    def put_nowait(self, message: pb.WorkerMessage, *, bulk: bool = False) -> None:
        (self.bulk if bulk else self.control).put_nowait(message)
        self._arrival.set()

    async def put(self, message: pb.WorkerMessage, *, bulk: bool = False) -> None:
        self.put_nowait(message, bulk=bulk)

    async def get(self) -> pb.WorkerMessage:
        while True:
            if not self.control.empty():
                return self.control.get_nowait()
            if not self.bulk.empty():
                return self.bulk.get_nowait()
            # Cleared before the wait and set by every put, with no await in
            # between: on a single loop that ordering cannot lose a wakeup.
            self._arrival.clear()
            await self._arrival.wait()

    def qsize(self) -> int:
        return self.control.qsize() + self.bulk.qsize()

    def empty(self) -> bool:
        return self.qsize() == 0


@dataclass
class PendingResult:
    """A finished result the server has not acknowledged yet.

    Held until RESULT_ACK arrives, across reconnects. Dropping it early is how
    a completed forty-minute dub disappears with no error anywhere.

    Anything over the inline threshold is uploaded first and represented here
    by its ``ArtifactRef`` alone — the bytes are already durable on the control
    plane, so a redelivery costs one small frame instead of re-sending a
    payload that may not even fit in one (#B9).
    """

    ref: pb.TaskRef
    result_json: str = ""
    inline_payload: bytes = b""
    artifacts: list[pb.ArtifactRef] = field(default_factory=list)
    usage: Optional[pb.UsageReport] = None


@dataclass
class WorkerConfig:
    """Everything the worker needs to reach and prove itself to a server."""

    endpoint: str
    cert_fingerprint: str
    certificate_pem: bytes
    keypair: WorkerKeypair
    worker_id: str = ""
    enrollment_token: str = ""
    max_concurrent_tasks: int = 1
    capabilities: list[dict] = field(default_factory=list)
    host: dict = field(default_factory=dict)


def describe_host() -> dict:
    """Static facts about this machine, for registration."""
    try:
        from core.version import APP_VERSION  # noqa: PLC0415
    except Exception:
        APP_VERSION = ""
    try:
        import psutil  # noqa: PLC0415

        system_memory_bytes = int(psutil.virtual_memory().total)
    except Exception:
        system_memory_bytes = 0
    return {
        "hostname": socket.gethostname(),
        "os": {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux"),
        "arch": platform.machine(),
        "worker_version": APP_VERSION,
        "cpu_count": os.cpu_count() or 0,
        "system_memory_bytes": system_memory_bytes,
    }


class WorkerClient:
    """Maintains one worker's connection to a control plane."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        execute: Callable[[pb.TaskAssignment], Awaitable[dict]],
        cancel: Optional[Callable[[str], Awaitable[None]]] = None,
        capability_probe: Optional[Callable[[], list[dict]]] = None,
        on_registered: Optional[Callable[[str], None]] = None,
        on_activated: Optional[Callable[[str], None]] = None,
        artifacts: Optional["ArtifactTransport"] = None,
        drain_active_work: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.config = config
        self.config.max_concurrent_tasks = clamp_concurrency(
            self.config.max_concurrent_tasks
        )
        self._execute = execute
        self._cancel = cancel
        self._capability_probe = capability_probe
        # Discovery imports engine adapters and inspects model storage. Share
        # one off-loop probe across reconnect/task/prewarm/idle refresh races;
        # a cancelled waiter drains it before returning so no detached probe
        # can mutate global engine state after authority is gone.
        self._capability_probe_task: Optional[asyncio.Task] = None
        # Outbound mode moves artifacts with RPCs this side initiates
        # (UploadResult / DownloadArtifact), which is only possible because
        # this side dialled. In inbound mode the node cannot call the panel at
        # all, so both directions are driven from the panel and this hook
        # swaps in the staging that makes that work. None means outbound.
        self._artifacts = artifacts
        self._drain_active_work = drain_active_work
        # Lets the agent persist the server-assigned id. Without it a restarted
        # worker signs its challenge with an empty worker_id, the signature
        # never matches, and reconnecting needs a fresh enrollment token —
        # which would make key-based identity pointless.
        self._on_registered = on_registered
        # Register only reserves a provisional server generation. Readiness is
        # published separately, after ConfigUpdate proves Control activated it.
        self._on_activated = on_activated
        self._activation_confirmed = False
        self._reporter_kwargs = _accepted_reporter_kwargs(execute)
        self._outbox = _Outbox()
        self._pending: dict[str, PendingResult] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._keepalives: dict[str, asyncio.Task] = {}
        self._maintenance: set[asyncio.Task] = set()
        self._telemetry: tuple[Optional[float], Optional[int], Optional[float]] = (None, None, None)
        # A driver query can hang indefinitely. Keep that one query owned
        # rather than cancelling its awaiter and starting a fresh thread at
        # every heartbeat.
        self._telemetry_task: Optional[asyncio.Future] = None
        self._prewarms: dict[str, asyncio.Task] = {}
        self._prewarm_cancellations: dict[str, asyncio.Task] = {}
        self._epoch = 0
        self._session_token = ""
        # Negotiated by ConfigUpdate; None means "use the executor's own
        # preference", so the threshold is never spelled twice.
        self._inline_threshold: Optional[int] = None
        # The live stub, kept so the result upload can use a second RPC on the
        # same channel rather than the control stream.
        self._stub = None
        self._stop = asyncio.Event()
        # Drain is a graceful reconnect, not terminal shutdown. This event
        # half-closes only the current stream after every active result is ACKed
        # while ``_stop`` remains reserved for cancelling the agent itself.
        self._reconnect_requested = asyncio.Event()
        self._draining = False
        self._accepting_assignments = True

    # ── Connection ────────────────────────────────────────────────────────

    def _channel(self) -> grpc.aio.Channel:
        """A channel that trusts exactly one certificate — the pinned one."""
        credentials = grpc.ssl_channel_credentials(root_certificates=self.config.certificate_pem)
        return grpc.aio.secure_channel(
            self.config.endpoint,
            credentials,
            options=[
                ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
                ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
                ("grpc.keepalive_time_ms", 25_000),
                ("grpc.keepalive_timeout_ms", 10_000),
                ("grpc.keepalive_permit_without_calls", 1),
            ],
        )

    async def run_forever(self) -> None:
        """Connect, serve, and reconnect until stopped."""
        if not self._stop.is_set():
            self._accepting_assignments = True
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except TerminalRegistrationError:
                # The control plane has made a durable decision that this
                # identity may not reconnect. Work deliberately survives an
                # ordinary network drop, but must not survive revocation and
                # keep using the GPU with no authority able to cancel it.
                await self._cancel_active_work()
                raise
            except Exception as exc:
                attempt += 1
                delay = backoff_delay(attempt)
                logger.warning(
                    "Worker connection failed (%s). Reconnecting in %.1fs.", exc, delay
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        self._stop.set()
        self._reconnect_requested.set()
        await self._cancel_active_work()

    async def _cancel_active_work(self) -> None:
        """Cancel retained tasks without permanently disabling reconnect."""
        # Close admission before taking any snapshots. Attach/Control may still
        # have a frame ready while revocation drains an uninterruptible GPU
        # call; accepting that frame here lets it escape the snapshot entirely.
        self._accepting_assignments = False
        maintenance = list(self._maintenance)
        for task in maintenance:
            task.cancel()
        running = list(self._running.items())
        keepalives = list(self._keepalives.values())
        for key, task in running:
            self._stop_keepalive(key)
            task.cancel()
        for keepalive in keepalives:
            keepalive.cancel()
        cancel_callbacks = []
        if self._cancel is not None:
            cancel_callbacks = [
                asyncio.create_task(self._cancel(key.split("/")[0]))
                for key, _task in running
            ]
        # Cancel every maintenance, task, and keepalive wrapper before awaiting
        # any uninterruptible one.  A prewarm stuck in a model-load thread must
        # not delay revocation of active user renders.
        draining = [
            *maintenance,
            *(task for _key, task in running),
            *keepalives,
            *cancel_callbacks,
        ]
        if draining:
            await asyncio.gather(
                *draining, return_exceptions=True
            )
        self._maintenance.clear()
        self._prewarms.clear()
        self._prewarm_cancellations.clear()
        for key, task in running:
            if self._running.get(key) is task:
                self._running.pop(key, None)
        if self._drain_active_work is not None:
            await self._drain_active_work()
        self._keepalives.clear()
        self._pending.clear()

    async def _connect_once(self) -> None:
        async with self._channel() as channel:
            stub = pb_grpc.WorkerServiceStub(channel)
            response = await self._register(stub)
            # An authentication or version refusal is not something a retry
            # loop fixes; `accept_registration` raises rather than reconnecting
            # forever.
            await self.accept_registration(response)

            metadata = ((SESSION_METADATA_KEY, self._session_token),)
            stream = stub.Control(self._outbound(), metadata=metadata)
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(response.heartbeat_interval_seconds or _HEARTBEAT_SECONDS)
            )
            # Published only once the session is established: an upload before
            # this point would carry a token the server has not issued yet.
            self._stub = stub
            try:
                async for message in stream:
                    await self._on_server_message(message)
                    if self._stop.is_set():
                        break
            finally:
                heartbeat.cancel()
                # The channel closes with this block, so a stub kept past it
                # would fail every upload with a confusing "channel closed"
                # instead of the honest "no session".
                self._stub = None

    # ── Session seams ─────────────────────────────────────────────────────
    #
    # Outbound owns its whole connection: dial, Register, stream, repeat. A
    # node being dialled owns none of that — the gRPC servicer does — so these
    # three expose the parts that are about the PROTOCOL rather than about who
    # opened the socket. Outbound calls them through `_connect_once` exactly as
    # before; inbound calls them from the Attach handler. Neither mode gets its
    # own copy of registration, zombie reconciliation or redelivery.

    async def _probe_capabilities(self) -> list[dict]:
        if self._capability_probe is None:
            return list(self.config.capabilities or [])
        task = self._capability_probe_task
        if task is None or task.done():
            task = asyncio.create_task(
                to_thread_and_drain_on_cancel(self._capability_probe),
                name="worker-capability-probe",
            )
            self._capability_probe_task = task
        try:
            capabilities = await asyncio.shield(task)
        except asyncio.CancelledError:
            await drain_task(task)
            raise
        finally:
            if task.done() and self._capability_probe_task is task:
                self._capability_probe_task = None
        return list(capabilities or [])

    async def build_register_request(
        self,
        challenge: Optional[bytes] = None,
        *,
        issue_challenge: Optional[Callable[[], Awaitable[Optional[bytes]]]] = None,
    ) -> pb.RegisterRequest:
        """This worker's self-description. Identical in both modes.

        ``challenge`` is the control plane's (Attach metadata inbound);
        ``issue_challenge`` fetches one (IssueChallenge outbound) and runs only
        after the capability probe, so a slow probe cannot outlive its expiry.
        A self-chosen challenge remains only for control planes that predate
        server-issued ones; current ones refuse it.
        """
        capabilities = await self._probe_capabilities()
        self.config.capabilities = capabilities
        if not challenge and issue_challenge is not None:
            challenge = await issue_challenge()
        challenge = bytes(challenge or identity.new_challenge())
        nonce = identity.new_challenge()
        signature = self.config.keypair.sign(
            identity.challenge_message(
                challenge=challenge,
                worker_id=self.config.worker_id,
                session_epoch=self._epoch,
                nonce=nonce,
            )
        )
        return pb.RegisterRequest(
            envelope=pb.Envelope(sequence=self._epoch),
            protocol_version_min=PROTOCOL_VERSION,
            protocol_version_max=PROTOCOL_VERSION,
            enrollment_token=self.config.enrollment_token,
            worker_id=self.config.worker_id,
            public_key=self.config.keypair.public_bytes(),
            challenge=challenge,
            challenge_signature=signature,
            nonce=nonce,
            key_id=self.config.keypair.key_id,
            host=codec.host_to_pb(self.config.host or describe_host()),
            capabilities=[codec.capability_to_pb(c) for c in capabilities],
            max_concurrent_tasks=clamp_concurrency(
                self.config.max_concurrent_tasks
            ),
            in_flight=[
                codec.task_ref(t.split("/")[0], t.split("/")[1], self._epoch)
                for t in self._running
            ],
            completed_unacked=[p.ref for p in self._pending.values()],
            features=sorted(REQUIRED_FEATURES),
        )

    async def accept_registration(self, response: pb.RegisterResponse) -> None:
        """Adopt the control plane's answer and recover in-flight state."""
        if response.error.code == CHALLENGE_EXPIRED:
            # Not a verdict on this worker: reconnect fetches a fresh one.
            raise RuntimeError(f"{response.error.code}: {response.error.message}")
        if response.error.code:
            raise TerminalRegistrationError(
                f"{response.error.code}: {response.error.message}"
            )
        # An enrollment token is already spent when this response arrives.
        # Commit the reconnect identity before adopting the live session; if
        # local durable state cannot be written, retrying the spent token can
        # never repair the worker and must reach the caller immediately.
        if self._on_registered is not None:
            try:
                await to_thread_and_drain_on_cancel(
                    self._on_registered, response.worker_id
                )
            except Exception as exc:
                raise TerminalRegistrationError(
                    "LOCAL_STATE: accepted enrollment could not be persisted"
                ) from exc

        self._draining = False
        self._reconnect_requested.clear()
        if not self._stop.is_set():
            self._accepting_assignments = True
        self._activation_confirmed = False
        self._epoch = response.session_epoch
        self._session_token = response.session_token
        self.config.worker_id = response.worker_id
        # The token is spent; every later connection proves key possession.
        self.config.enrollment_token = ""

        authoritative = {ref.attempt_id for ref in response.authoritative_in_flight}
        await self._cancel_zombies(authoritative)
        await self._redeliver_pending()

    def confirm_activation(self) -> None:
        """Publish readiness once Control proves the provisional session live."""
        if self._activation_confirmed:
            return
        if self._on_activated is not None:
            try:
                self._on_activated(self.config.worker_id)
            except Exception as exc:
                raise TerminalRegistrationError(
                    "LOCAL_STATE: activated enrollment could not be published"
                ) from exc
        self._activation_confirmed = True

    async def next_outbound(self) -> pb.WorkerMessage:
        """The next frame this worker wants to send."""
        return await self._outbox.get()

    def prepare_inbound_session(self) -> None:
        """Start a fresh stream while retaining running and pending work.

        An inbound listener creates the protocol owner once per panel key, not
        once per transport generation. Frames queued for the dead stream are
        stale, but `_running` and `_pending` are precisely the state the next
        Register must reconcile and redeliver.
        """
        self._outbox = _Outbox()
        self._session_token = ""
        self._stub = None
        self._draining = False
        self._reconnect_requested.clear()
        if not self._stop.is_set():
            self._accepting_assignments = True

    @property
    def reconnect_requested(self) -> bool:
        return self._reconnect_requested.is_set()

    @property
    def outbound_pending(self) -> bool:
        """Whether a terminal/control frame still needs transport delivery."""
        return not self._outbox.empty()

    def start_heartbeat(self, response: pb.RegisterResponse) -> asyncio.Task:
        """Begin the heartbeat this session's liveness depends on.

        Separate from `accept_registration` because the task has to live and
        die with the stream, not with the registration. Outbound starts the
        same loop inside `_connect_once`; inbound has no such place, and
        leaving it out is invisible for exactly as long as the grace window —
        which is why it survived every sub-second test and only showed up on
        hardware, as a worker that registered, went quiet, was declared dead
        ~90s later, reconnected, and flapped forever.
        """
        return asyncio.create_task(
            self._heartbeat_loop(
                response.heartbeat_interval_seconds or _HEARTBEAT_SECONDS
            ),
            name="inbound-heartbeat",
        )

    async def handle_server_message(self, message: pb.ServerMessage) -> None:
        await self._on_server_message(message)

    async def _register(self, stub) -> pb.RegisterResponse:
        async def issue_challenge() -> Optional[bytes]:
            try:
                response = await stub.IssueChallenge(pb.ChallengeRequest())
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                    return None  # an older control plane; it accepts our own
                raise
            return bytes(response.challenge)

        return await stub.Register(
            await self.build_register_request(issue_challenge=issue_challenge)
        )

    # ── Outbound ──────────────────────────────────────────────────────────

    async def _outbound(self):
        while not self._reconnect_requested.is_set():
            message = asyncio.create_task(self._outbox.get())
            reconnect = asyncio.create_task(self._reconnect_requested.wait())
            try:
                done, _pending = await asyncio.wait(
                    {message, reconnect}, return_when=asyncio.FIRST_COMPLETED
                )
                if message not in done:
                    return
                yield message.result()
                self._maybe_finish_drain()
            finally:
                for task in (message, reconnect):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(message, reconnect, return_exceptions=True)

    async def _send(self, message: pb.WorkerMessage, *, bulk: bool = False) -> None:
        """Enqueue a frame. ``bulk`` is for anything that can be large.

        Only result frames qualify today. Everything else — heartbeat, pong,
        progress, accept/reject, started/failed — is the control plane's view
        of whether this worker is alive, and must not queue behind a payload.
        """
        await self._outbox.put(message, bulk=bulk)

    async def _heartbeat_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._refresh_telemetry()
            await self._send(self.heartbeat_message())

    async def _refresh_telemetry(self) -> None:
        """Publish completed samples and retain one non-blocking probe.

        CUDA/NVML calls may wedge in a driver.  A timed ``to_thread`` await
        only cancels the awaiter, leaving that thread alive; retaining this
        task prevents later heartbeats from accumulating more blocked probes.
        """
        if not self._accepting_assignments or self._stop.is_set():
            return
        task = self._telemetry_task
        if task is not None and task.done():
            try:
                sampled = task.result()
            except Exception:
                logger.debug("Could not sample worker telemetry", exc_info=True)
            else:
                # A partial failed sample must not erase an independent last
                # good value.  Presence on the heartbeat remains honest until
                # that individual metric can next be measured.
                self._telemetry = tuple(
                    current if value is None else value
                    for current, value in zip(self._telemetry, sampled)
                )
            self._telemetry_task = None

        if self._telemetry_task is None:
            # Read-only driver probes cannot be interrupted. Keep one across
            # reconnects, outside assignment drain and the shared executor
            # (whose shutdown would otherwise wait forever for a wedged driver).
            result = Future()
            self._telemetry_task = asyncio.wrap_future(result)

            def sample() -> None:
                try:
                    result.set_result(_heartbeat_resources())
                except Exception:
                    logger.debug("Could not sample worker telemetry", exc_info=True)
                    result.set_result((None, None, None))

            threading.Thread(
                target=sample, name="worker-telemetry-probe", daemon=True,
            ).start()

    def heartbeat_message(self) -> pb.WorkerMessage:
        """Build the worker's current liveness/capacity frame."""
        cpu_percent, free_memory_bytes, gpu_utilization_percent = self._telemetry
        telemetry = {}
        if cpu_percent is not None: telemetry["cpu_percent"] = cpu_percent
        if free_memory_bytes is not None: telemetry["free_memory_bytes"] = free_memory_bytes
        if gpu_utilization_percent is not None: telemetry["gpu_utilization_percent"] = gpu_utilization_percent
        return pb.WorkerMessage(
            heartbeat=pb.Heartbeat(
                active_tasks=len(self._running),
                available_slots=max(
                    0, self.config.max_concurrent_tasks - len(self._running)
                ),
                resident_models=self._resident_models(),
                **telemetry,
            )
        )

    def _resident_models(self) -> list[str]:
        return [
            f"{c.get('engine')}:{c.get('model_id')}"
            for c in (self.config.capabilities or [])
            if c.get("resident")
        ]

    async def refresh_capabilities(self) -> None:
        """Re-probe and publish after model/download/residency changes."""
        if self._capability_probe is None:
            return
        try:
            capabilities = await self._probe_capabilities()
            self.config.capabilities = capabilities
            await self._send(pb.WorkerMessage(capabilities=pb.CapabilityUpdate(
                capabilities=[codec.capability_to_pb(c) for c in capabilities]
            )))
        except Exception:
            logger.warning("Could not refresh worker capabilities", exc_info=True)

    async def _redeliver_pending(self) -> None:
        """Re-send anything the server never acknowledged."""
        for pending in list(self._pending.values()):
            logger.info("Redelivering unacknowledged result for task %s", pending.ref.task_id)
            await self._send(_result_message(pending), bulk=True)

    async def _cancel_zombies(self, authoritative: set[str]) -> None:
        """Stop work the control plane no longer believes in."""
        for key in list(self._running):
            attempt_id = key.split("/")[1]
            if attempt_id not in authoritative:
                logger.info("Cancelling task %s — the server no longer expects it", key)
                await self._abandon(key)

    async def _abandon(self, key: str) -> None:
        # Silenced here as well as in _run's finally: cancelling a task does
        # not run its finally until the loop next schedules it, and one more
        # keepalive for an attempt the server has disowned is exactly the
        # frame that resurrects a cancelled task.
        self._stop_keepalive(key)
        task = self._running.get(key)
        if task is not None:
            task.cancel()
            # CancelAck releases server capacity. Do not send it while an
            # uninterruptible synthesis/load thread still owns the GPU, or the
            # replacement assignment can overlap and corrupt or OOM the worker.
            await asyncio.gather(task, return_exceptions=True)
            self._running.pop(key, None)
        if self._cancel is not None:
            await self._cancel(key.split("/")[0])

    # ── Inbound ───────────────────────────────────────────────────────────

    async def _on_server_message(self, message: pb.ServerMessage) -> None:
        kind = message.WhichOneof("payload")
        if kind == "assignment":
            await self._on_assignment(message.assignment)
        elif kind == "cancel":
            await self._abandon(self._key(message.cancel.ref))
            await self._send(
                pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=message.cancel.ref))
            )
        elif kind == "result_ack":
            # Only now is it safe to forget the result.
            pending = self._pending.pop(self._key(message.result_ack.ref), None)
            if pending is not None and self._artifacts is not None:
                result_acked_async = getattr(
                    self._artifacts, "result_acked_async", None
                )
                if callable(result_acked_async):
                    await result_acked_async(pending.artifacts)
                else:
                    result_acked = getattr(self._artifacts, "result_acked", None)
                    if callable(result_acked):
                        result_acked(pending.artifacts)
            self._maybe_finish_drain()
        elif kind == "config":
            if message.config.max_concurrent_tasks:
                self.config.max_concurrent_tasks = clamp_concurrency(
                    message.config.max_concurrent_tasks
                )
            if message.config.inline_result_threshold_bytes:
                # Negotiated, so the two sides cannot drift: the control plane
                # is the one that knows how much it is willing to take on the
                # control stream, and it may lower this at any time.
                self._inline_threshold = int(message.config.inline_result_threshold_bytes)
            self.confirm_activation()
        elif kind == "ping":
            # Answer immediately; the server times the round trip.
            await self._send(pb.WorkerMessage(pong=pb.Pong(nonce=message.ping.nonce)))
        elif kind == "drain":
            self._accepting_assignments = False
            self._draining = True
            if message.drain.reconnect_to:
                self.config.endpoint = message.drain.reconnect_to
            self._maybe_finish_drain()
        elif kind == "shutdown":
            await self._cancel_active_work()
            self._stop.set()
            if self._artifacts is not None:
                await self._send(
                    pb.WorkerMessage(
                        goodbye=pb.WorkerGoodbye(
                            reason="The control-plane connection was removed."
                        )
                    )
                )
            # Publish the terminal acknowledgement before asking the inbound
            # Attach loop to leave.  Reversing these lets that loop observe
            # reconnect_requested and close the stream with Goodbye still
            # queued, so the control plane cannot prove remote work drained.
            self._reconnect_requested.set()
        elif kind == "prewarm":
            if not self._accepting_assignments:
                return
            model_id = message.prewarm.model_id
            existing = self._prewarms.get(model_id)
            if model_id and existing is not None and not existing.done():
                return
            task = asyncio.create_task(
                self._on_prewarm(message.prewarm), name="worker-prewarm"
            )
            self._maintenance.add(task)
            if model_id:
                self._prewarms[model_id] = task
            task.add_done_callback(self._maintenance_finished)
        elif kind == "model_install_cancel":
            await self._cancel_model_install(message.model_install_cancel)

    def _maintenance_finished(self, task: asyncio.Task) -> None:
        self._maintenance.discard(task)
        for tasks in (self._prewarms, self._prewarm_cancellations):
            for model_id, current in tuple(tasks.items()):
                if current is task:
                    tasks.pop(model_id, None)
        self._maybe_finish_drain()

    async def _cancel_model_install(
        self, request: pb.ModelInstallCancelRequest
    ) -> None:
        """Cancel one explicit catalogue install without blocking control I/O."""
        model_id = request.model_id.strip()
        capability = next(
            (
                cap
                for cap in (self.config.capabilities or [])
                if cap.get("model_id") == model_id
            ),
            None,
        )
        repo_ids = list((capability or {}).get("repo_ids") or [])
        if len(repo_ids) != 1:
            logger.warning("Ignoring model cancellation for unknown model %s", model_id)
            return
        repo_id = repo_ids[0]
        task = self._prewarms.get(model_id)
        if task is None or task.done():
            await self._send_model_install_terminal(
                repo_id,
                "install_done"
                if bool((capability or {}).get("downloaded"))
                else "install_cancelled",
            )
            return
        existing = self._prewarm_cancellations.get(model_id)
        if existing is not None and not existing.done():
            return
        task.cancel()
        confirmation = asyncio.create_task(
            self._confirm_model_install_cancel(task, repo_id),
            name="worker-model-install-cancel",
        )
        self._maintenance.add(confirmation)
        self._prewarm_cancellations[model_id] = confirmation
        confirmation.add_done_callback(self._maintenance_finished)

    async def _confirm_model_install_cancel(
        self, task: asyncio.Task, repo_id: str
    ) -> None:
        await asyncio.gather(task, return_exceptions=True)
        capability = next(
            (
                cap
                for cap in (self.config.capabilities or [])
                if repo_id in (cap.get("repo_ids") or [])
            ),
            None,
        )
        await self._send_model_install_terminal(
            repo_id,
            "install_done"
            if bool((capability or {}).get("downloaded"))
            else "install_cancelled",
        )

    async def _send_model_install_terminal(self, repo_id: str, phase: str) -> None:
        event = {
            "repo_id": repo_id,
            "filename": repo_id,
            "downloaded": 0,
            "total": 0,
            "pct": 0.0,
            "phase": phase,
        }
        await self._send(
            pb.WorkerMessage(
                download_progress=pb.DownloadProgress(
                    event_json=json.dumps(
                        event, separators=(",", ":"), ensure_ascii=False
                    )
                )
            )
        )

    def _maybe_finish_drain(self) -> None:
        if (
            self._draining
            and not self._running
            and not self._pending
            and not self._maintenance
            and self._outbox.empty()
        ):
            self._reconnect_requested.set()

    async def _on_prewarm(self, request: pb.PrewarmRequest) -> None:
        """Load/download a catalog model, then report the resulting capability."""
        engine = request.engine
        capability = next(
            (
                cap for cap in (self.config.capabilities or [])
                if request.model_id and cap.get("model_id") == request.model_id
            ),
            None,
        )
        if not engine and capability is not None:
            engine = str(capability.get("engine") or "")
        try:
            if not engine:
                raise ValueError("the requested catalog model has no worker engine")
            if request.download_if_missing:
                repo_ids = list((capability or {}).get("repo_ids") or [])
                if len(repo_ids) != 1:
                    raise ValueError("the requested worker model has no single catalog repository")
                await self._install_catalog_repo(repo_ids[0])
            from worker.executor import TaskExecutor  # noqa: PLC0415

            await to_thread_and_drain_on_cancel(TaskExecutor._load_backend, engine)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Prewarm failed for %s", engine or request.model_id, exc_info=True)
        await self.refresh_capabilities()

    async def _install_catalog_repo(self, repo_id: str) -> None:
        """Run the existing setup installer and pipe its hf_progress upstream."""
        from api.routers.setup.download import (  # noqa: PLC0415
            InstallModelRequest,
            cancel_install_and_wait,
            install_model,
        )
        from utils import download_aggregator, hf_progress  # noqa: PLC0415

        hf_progress.install()
        download_aggregator.install()
        loop = asyncio.get_running_loop()
        terminal = loop.create_future()

        def listener(event: dict) -> None:
            if event.get("repo_id") != repo_id:
                return
            payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
            loop.call_soon_threadsafe(
                asyncio.create_task,
                self._send(pb.WorkerMessage(
                    download_progress=pb.DownloadProgress(event_json=payload)
                )),
            )
            if event.get("phase") in {
                "install_done", "install_error", "install_cancelled",
            }:
                def _finish(result=event) -> None:
                    if not terminal.done():
                        terminal.set_result(result)

                loop.call_soon_threadsafe(_finish)

        listener_id = hf_progress.register_listener(listener)
        started_install = False
        install_completed = False
        try:
            response = await install_model(
                InstallModelRequest(repo_id=repo_id, target="local")
            )
            started_install = response.get("status") == "install_started"
            event = await asyncio.wait_for(terminal, timeout=_FALLBACK_MODEL_LOAD_SECONDS)
            if event.get("phase") != "install_done":
                raise RuntimeError(event.get("error") or "model install did not complete")
            install_completed = True
        finally:
            if started_install and not install_completed:
                await cancel_install_and_wait(repo_id)
            hf_progress.unregister_listener(listener_id)

    @staticmethod
    def _key(ref: pb.TaskRef) -> str:
        return f"{ref.task_id}/{ref.attempt_id}"

    async def _on_assignment(self, assignment: pb.TaskAssignment) -> None:
        key = self._key(assignment.ref)
        # Assignment delivery is at-least-once. A reconnect or a control-stream
        # retry may repeat the exact same attempt while it is still running or
        # waiting for its result acknowledgement. Treating that repeat as a
        # capacity rejection terminalizes the original attempt underneath its
        # result upload; starting it again spends the GPU twice. Reaffirm the
        # live claim, or redeliver the result we already hold.
        if key in self._running:
            await self._send(
                pb.WorkerMessage(accepted=pb.TaskAccepted(ref=assignment.ref))
            )
            return
        pending = self._pending.get(key)
        if pending is not None:
            await self._send(_result_message(pending), bulk=True)
            return
        if not self._accepting_assignments or self._stop.is_set():
            await self._send(
                pb.WorkerMessage(
                    rejected=pb.TaskRejected(
                        ref=assignment.ref,
                        error=pb.Error(
                            error_class=pb.ERROR_CLASS_TRANSIENT,
                            code="WORKER_STOPPING",
                            message="The worker is relinquishing this control plane.",
                        ),
                    )
                )
            )
            return
        if len(self._running) >= self.config.max_concurrent_tasks:
            # Declining because we are full is normal and penalty-free; the
            # scheduler's view of our capacity is only ever advisory.
            await self._send(
                pb.WorkerMessage(
                    rejected=pb.TaskRejected(
                        ref=assignment.ref,
                        error=pb.Error(
                            error_class=pb.ERROR_CLASS_CAPACITY,
                            code="WORKER_AT_CAPACITY",
                            message="The worker has no free slot.",
                        ),
                    )
                )
            )
            return
        # Reserve the slot BEFORE the accept-send await: awaiting yields to
        # the event loop, and a concurrently delivered assignment would read
        # the un-reserved counter and over-accept past capacity (#1536 — a
        # capacity-1 worker accepted a second task on a slow runner). Message
        # order on the stream survives the swap: _send enqueues synchronously
        # (put_nowait before any suspension), so ACCEPTED is in the outbox
        # before this handler ever yields to the just-created _run task.
        self._running[key] = asyncio.create_task(self._run(assignment))
        try:
            await self._send(pb.WorkerMessage(accepted=pb.TaskAccepted(ref=assignment.ref)))
        except BaseException:
            # BaseException, not Exception: a handler CANCELLED mid-send must
            # release the slot too, or the reserved task keeps running work
            # the scheduler never saw accepted — and double-executes after
            # reassignment. The stream-death case lands here as well.
            task = self._running.get(key)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._running.pop(key, None)
            raise

    async def _run(self, assignment: pb.TaskAssignment) -> None:
        key = self._key(assignment.ref)
        try:
            await self._send(pb.WorkerMessage(started=pb.TaskStarted(ref=assignment.ref)))
            # Armed before the executor is called, not after it reports its
            # first progress: the cold model load sits between the two and is
            # the single longest silence a task ever has.
            self._keepalives[key] = asyncio.create_task(
                self._keepalive_loop(
                    assignment.ref,
                    keepalive_interval(assignment.deadlines.progress_lease_seconds),
                ),
                name=f"worker-keepalive-{assignment.ref.attempt_id}",
            )
            result = await self._execute(assignment, **self._executor_kwargs(assignment))

            meta = result.get("meta", {}) or {}
            payload = result.get("payload", b"") or b""
            artifacts: list[pb.ArtifactRef] = []
            if self._should_upload(payload):
                # The keepalive timer is still armed here on purpose: the
                # upload runs under the same attempt, and the renewals it
                # sends per chunk (below) are what buy it the delivery budget.
                artifacts, payload = await self._deliver_out_of_band(
                    assignment.ref, payload, meta
                )

            # Stopped before the terminal frame so no keepalive can arrive
            # claiming an attempt the server has already settled.
            self._stop_keepalive(key)

            pending = PendingResult(
                ref=assignment.ref,
                result_json=json.dumps(meta),
                inline_payload=payload,
                artifacts=artifacts,
            )
            oversized = _oversized_result_error(pending)
            if oversized is not None:
                # Reachable now only through an enormous result_json: bulk
                # bytes take the upload path above. Deliberately NOT recorded
                # in _pending — an over-cap frame is rejected identically on
                # every reconnect, so remembering it would redeliver a frame
                # that can never be accepted and tear the session down each
                # time (#B9), taking every other task on this worker with it.
                logger.warning(
                    "Result for task %s is too large to deliver inline; failing it",
                    assignment.ref.task_id,
                )
                await self._fail(assignment.ref, oversized)
                return

            # Recorded BEFORE sending: if the connection dies mid-send we must
            # still know to redeliver.
            self._pending[key] = pending
            await self._send(_result_message(pending), bulk=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._stop_keepalive(key)
            # An executor that already classified the failure knows more than
            # a generic exception sniff can recover — keep its verdict, so a
            # "this input is bad" does not get retried around the whole fleet.
            from worker.executor import TaskFailure  # noqa: PLC0415

            failure: WorkerError = (
                exc.error if isinstance(exc, TaskFailure) else worker_errors.from_exception(exc)
            )
            if (
                failure.error_class is ErrorClass.TIMEOUT
                and self._drain_active_work is not None
            ):
                # A timeout only ends the coroutine's wait; Python cannot
                # cancel the GPU thread underneath it. Do not send FAILED or
                # release this admission slot until the executor proves that
                # work relinquished the device, otherwise a capacity-1 worker
                # can accept a replacement on top of the timed-out render.
                await self._drain_active_work()
            await self._fail(assignment.ref, failure)
        finally:
            # Also covers the abnormal exits — cancellation, a crash between
            # the two _stop_keepalive calls above — so the timer can never
            # outlive the task that owns it.
            self._stop_keepalive(key)
            # Discovery may inspect the just-used backend. Keep the admission
            # reservation until that off-loop probe has drained, otherwise a
            # heartbeat can advertise the slot while this generation still
            # owns task-finalization work. The inner finally also releases it
            # when shutdown cancels the refresh waiter.
            try:
                await self.refresh_capabilities()
            finally:
                self._running.pop(key, None)
                self._maybe_finish_drain()

    async def _fail(self, ref: pb.TaskRef, error: WorkerError) -> None:
        await self._send(
            pb.WorkerMessage(failed=pb.TaskFailed(ref=ref, error=codec.error_to_pb(error)))
        )

    # ── Result delivery ───────────────────────────────────────────────────

    def inline_limit(self) -> int:
        """How many payload bytes may ride the control stream.

        The negotiated value when the control plane has stated one, otherwise
        the executor's own preference — read from the executor rather than
        copied, so there is exactly one default in the tree.

        Clamped to what a frame can actually hold in either case: a control
        plane that negotiates a threshold above the frame ceiling would
        otherwise turn every large result into RESULT_TOO_LARGE, which is the
        precise failure this phase exists to remove.
        """
        if self._inline_threshold is not None:
            limit = self._inline_threshold
        else:
            from worker.executor import INLINE_LIMIT_BYTES  # noqa: PLC0415

            limit = INLINE_LIMIT_BYTES
        return max(0, min(int(limit), MAX_MESSAGE_BYTES - _INLINE_FRAME_HEADROOM_BYTES))

    def _should_upload(self, payload: bytes) -> bool:
        return bool(payload) and len(payload) > self.inline_limit()

    async def _deliver_out_of_band(
        self, ref: pb.TaskRef, payload: bytes, meta: dict
    ) -> tuple[list[pb.ArtifactRef], bytes]:
        """Upload the payload, returning ``([ref], b"")`` on success.

        Falls back to inline delivery — ``([], payload)`` — only when the
        payload would still fit in a frame. That fallback is what keeps an
        older control plane (no UploadResult) and a one-off network stumble
        from destroying a render that already succeeded; above the frame
        ceiling there is no such option, and the attempt fails TRANSIENT so a
        retry can find a working path rather than looping on a dead one.
        """
        try:
            return [await self._upload_result(ref, payload, meta)], b""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if len(payload) <= MAX_MESSAGE_BYTES - _INLINE_FRAME_HEADROOM_BYTES:
                logger.warning(
                    "Uploading the result for task %s failed (%s); sending it inline instead",
                    ref.task_id,
                    exc,
                )
                return [], payload
            from worker.executor import TaskFailure  # noqa: PLC0415

            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.TRANSIENT,
                    code="RESULT_UPLOAD_FAILED",
                    message=(
                        f"The result ({len(payload) / (1024 * 1024):.1f} MiB) could not be "
                        f"uploaded to the control plane: {exc}"
                    ),
                    hint="Check the connection between this worker and the control plane.",
                )
            ) from exc

    async def _upload_result(
        self, ref: pb.TaskRef, payload: bytes, meta: dict
    ) -> pb.ArtifactRef:
        """Stream one result over UploadResult and return its committed ref.

        ``sha256`` and ``size_bytes`` are stated up front so the receiver can
        refuse a transfer that arrives short or corrupted instead of renaming a
        truncated file into place and calling the task done.
        """
        if self._artifacts is not None:
            # Inbound: nothing is pushed. The result is staged here and the
            # panel fetches it after the TaskResult frame names it.
            return await self._artifacts.publish(ref, payload, meta)

        stub = self._stub
        if stub is None:
            raise RuntimeError("no session is established")

        artifact = pb.ArtifactRef(
            task_id=ref.task_id,
            attempt_id=ref.attempt_id,
            filename=str(meta.get("filename") or f"{ref.attempt_id}.wav"),
            content_type=str(meta.get("content_type") or "audio/wav"),
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            session_token=self._session_token,
        )
        # Sent before the first chunk so the control plane can move the attempt
        # into RESULT_UPLOADING — and onto its delivery budget — before a slow
        # uplink has had a chance to burn the ordinary progress lease.
        await self._report_upload(ref, 0.0)

        offset = 0
        metadata = ((SESSION_METADATA_KEY, self._session_token),)
        for _ in range(_MAX_UPLOAD_RESUMES):
            ack = await stub.UploadResult(
                self._result_chunks(ref, artifact, payload, offset),
                metadata=metadata,
            )
            if ack.committed:
                break
            resumed = int(ack.bytes_received)
            if ack.error.code and ack.error.code != "OFFSET_MISMATCH":
                detail = ack.error.message or "the control plane refused the upload"
                raise RuntimeError(f"{ack.error.code}: {detail}")
            if resumed < 0 or resumed > len(payload) or resumed == offset:
                raise RuntimeError(ack.error.message or "the control plane could not resume the upload")
            offset = resumed
        else:
            # Bounded, because "did this make progress" cannot be answered by
            # comparing against the previous offset alone: a receiver that
            # alternates between two byte counts satisfies `resumed != offset`
            # forever, and one that advances a few bytes per round would retry
            # once per byte of a 100 MB dub. Either way the worker stops
            # rendering anything else while it spins.
            raise RuntimeError(
                f"the control plane asked to resume the upload more than "
                f"{_MAX_UPLOAD_RESUMES} times without committing it"
            )
        if ack.bytes_received and ack.bytes_received != len(payload):
            raise RuntimeError(
                f"the control plane received {ack.bytes_received} of {len(payload)} bytes"
            )
        # The final renewal cannot come from the chunk loop: the receiver stops
        # pulling at ``last``, so the generator is closed before the code after
        # that yield ever runs.
        await self._report_upload(ref, 1.0)

        committed = pb.ArtifactRef()
        committed.CopyFrom(artifact)
        if ack.artifact_id:
            committed.artifact_id = ack.artifact_id
        # The control stream is already authenticated; echoing the session
        # token back on it would only widen where the token is written.
        committed.ClearField("session_token")
        return committed

    async def _result_chunks(
        self, ref: pb.TaskRef, artifact: pb.ArtifactRef, payload: bytes, offset: int = 0
    ):
        """Chunks in order, each ``offset`` equal to the bytes already sent.

        The receiver checks that equality against the length it holds, so this
        is a contract and not a hint. Exactly one chunk carries ``last``, and
        only that one licenses the commit.
        """
        total = len(payload)
        while offset < total:
            data = payload[offset : offset + _UPLOAD_CHUNK_BYTES]
            offset += len(data)
            yield pb.ResultChunk(
                ref=artifact,
                offset=offset - len(data),
                data=data,
                last=offset >= total,
                session_token=self._session_token,
            )
            # Per chunk, not per timer: a lease renewed by real transfer
            # progress cannot keep an attempt alive over a stalled upload.
            await self._report_upload(ref, offset / total)

    async def _report_upload(self, ref: pb.TaskRef, fraction: float) -> None:
        await self._send(
            pb.WorkerMessage(
                progress=pb.TaskProgress(
                    ref=ref,
                    progress=float(fraction),
                    stage=UPLOAD_STAGE,
                    # Upload bytes are liveness, not synthesis progress. Mark
                    # them keepalive so the server applies the phase ceiling
                    # and does not replace an already-finished 100% with 0%.
                    keepalive=True,
                )
            )
        )

    # ── Liveness ──────────────────────────────────────────────────────────

    async def _keepalive_loop(self, ref: pb.TaskRef, interval: float) -> None:
        """Renew one task's progress lease until it is cancelled.

        ``keepalive=True`` is the whole point: this frame proves the worker
        process is alive, not that the GPU is making headway, so the server
        must renew on it only up to the phase's absolute budget.
        """
        while True:
            await asyncio.sleep(interval)
            await self._send(
                pb.WorkerMessage(progress=pb.TaskProgress(ref=ref, keepalive=True))
            )

    def _stop_keepalive(self, key: str) -> None:
        timer = self._keepalives.pop(key, None)
        if timer is not None:
            timer.cancel()

    async def _fetch_input(self, ref: pb.ArtifactRef, destination: str) -> None:
        """Download one declared input with authenticated, ordered chunks."""
        if self._artifacts is not None:
            # Inbound: the panel pushed this before it sent the assignment, so
            # there is nothing to pull — only a staged file to hand over.
            return await self._artifacts.stage_in(ref, destination)

        if self._stub is None:
            raise RuntimeError("no session is established")
        request = pb.ArtifactRef()
        request.CopyFrom(ref)
        request.session_token = self._session_token
        offset = 0
        complete = False
        handle = None
        try:
            handle = await to_thread_and_drain_on_cancel(open, destination, "wb")
            async for chunk in self._stub.DownloadArtifact(request):
                if int(chunk.offset) != offset:
                    raise RuntimeError(
                        f"input offset {chunk.offset} did not match {offset} bytes received"
                    )
                await to_thread_and_drain_on_cancel(_write_all, handle, chunk.data)
                offset += len(chunk.data)
                if chunk.last:
                    complete = True
                    break
            await to_thread_and_drain_on_cancel(handle.close)
            handle = None
        except asyncio.CancelledError:
            await to_thread_and_drain_on_cancel(_close_and_remove, handle, destination)
            raise
        except Exception:
            await to_thread_and_drain_on_cancel(_close_and_remove, handle, destination)
            raise
        if not complete:
            await to_thread_and_drain_on_cancel(_close_and_remove, None, destination)
            raise RuntimeError("input download ended before its final chunk")

    def _executor_kwargs(self, assignment: pb.TaskAssignment) -> dict[str, Callable]:
        """Per-task progress callbacks for the executor.

        Bound to this assignment's ref rather than installed on the executor
        once, because a worker with more than one slot has no other way to say
        which task a progress fraction belongs to.
        """
        ref = assignment.ref

        async def on_progress(fraction: float, stage: str = "", detail: str = "") -> None:
            await self._send(
                pb.WorkerMessage(
                    progress=pb.TaskProgress(
                        ref=ref,
                        progress=float(fraction),
                        stage=stage,
                        detail=detail,
                        keepalive=False,
                    )
                )
            )

        async def on_model_loading(fraction: float, detail: str = "") -> None:
            await self._send(
                pb.WorkerMessage(
                    model_loading=pb.TaskModelLoading(
                        ref=ref,
                        engine=assignment.engine,
                        progress=float(fraction),
                        detail=detail,
                    )
                )
            )

        available = {
            "on_progress": on_progress,
            "on_model_loading": on_model_loading,
            "fetch_input": self._fetch_input,
        }
        return {k: v for k, v in available.items() if k in self._reporter_kwargs}


def _result_message(pending: PendingResult) -> pb.WorkerMessage:
    """The one spelling of a result frame.

    First delivery and redelivery build it here so they cannot diverge — the
    size check below is only trustworthy if it measures the frame that is
    actually sent, on both paths.
    """
    return pb.WorkerMessage(
        result=pb.TaskResult(
            ref=pending.ref,
            result_json=pending.result_json,
            inline_payload=pending.inline_payload,
            artifacts=pending.artifacts,
        )
    )


def _oversized_result_error(pending: PendingResult) -> Optional[WorkerError]:
    """Refuse a result that cannot fit in a control-stream frame.

    Measured on the serialized frame rather than on the payload alone, so a
    modest waveform under a large ``result_json`` is caught by the same gate.

    TERMINAL, not TRANSIENT: the size is a property of the output, so every
    worker in the fleet would produce the same frame and be rejected the same
    way. Retrying it burns the whole fleet's slots to arrive back here.

    A last line of defence rather than the size policy it once was: bulk bytes
    now take the UploadResult path (``WorkerClient.inline_limit``), so what
    still reaches this is a ``result_json`` — a transcript, a segment list —
    that on its own will not fit in a frame.
    """
    size = _result_message(pending).ByteSize()
    if size <= MAX_MESSAGE_BYTES:
        return None
    return WorkerError(
        error_class=ErrorClass.TERMINAL,
        code="RESULT_TOO_LARGE",
        message=(
            f"The result is {size / (1024 * 1024):.1f} MiB, over the "
            f"{MAX_MESSAGE_BYTES // (1024 * 1024)} MiB limit for a result "
            "delivered on the control stream."
        ),
        hint="Split this into shorter jobs, or run it locally.",
    )


def verify_pin(certificate_pem: bytes, expected_fingerprint: str) -> bool:
    """Check a server certificate against the fingerprint from the token."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    from worker.tls import pin_matches  # noqa: PLC0415

    certificate = x509.load_pem_x509_certificate(certificate_pem)
    return pin_matches(
        certificate.public_bytes(serialization.Encoding.DER), expected_fingerprint
    )


def config_from_token(
    token_text: str, *, keypair: WorkerKeypair, certificate_pem: bytes
) -> WorkerConfig:
    """Build a worker configuration from a pasted enrollment token.

    Refuses outright if the presented certificate does not match the token's
    fingerprint — that mismatch is exactly what pinning exists to catch, and
    there is no override.
    """
    token: EnrollmentToken = EnrollmentToken.decode(token_text)
    if token.expired():
        raise ValueError("This enrollment token has expired. Generate a new one.")
    if not verify_pin(certificate_pem, token.cert_fingerprint):
        raise ValueError(
            "The server's certificate does not match this enrollment token. "
            "Do not continue — generate a fresh token on the control plane."
        )
    return WorkerConfig(
        endpoint=token.endpoint,
        cert_fingerprint=token.cert_fingerprint,
        certificate_pem=certificate_pem,
        keypair=keypair,
        enrollment_token=token_text,
    )


__all__ = [
    "MAX_MESSAGE_BYTES",
    "UPLOAD_STAGE",
    "PendingResult",
    "TerminalRegistrationError",
    "WorkerClient",
    "WorkerConfig",
    "backoff_delay",
    "config_from_token",
    "describe_host",
    "keepalive_interval",
    "verify_pin",
]
