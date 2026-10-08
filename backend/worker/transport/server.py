"""Control-plane gRPC service.

Translates the wire into scheduler calls and back. The rules it enforces here
are the ones that must hold at the *boundary*, before anything reaches the
domain:

  * authentication — an enrollment token once, then proof of key possession
  * fencing — one active session per worker, newest epoch wins, stale epochs
    dropped rather than merged
  * ordering — persist a result before acknowledging it
  * integrity — an artifact is verified against its declared digest before it
    is renamed into place, and only an explicit last chunk commits one

Everything else is delegated. If this file starts making scheduling decisions,
something has been put in the wrong place.

The control stream runs as two independent loops rather than a single
request/response generator. That is not stylistic: a worker uploading its
status while the server is trying to push an assignment would otherwise
deadlock behind its own reader, and the heartbeats that prove the worker is
alive are exactly what must never queue behind anything else.
"""
from __future__ import annotations

import asyncio
import errno
import functools
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Callable, Optional

import grpc

from core.path_security import UnsafePath, resolve_within, safe_filename
from worker import identity, registry, task_store
from worker.async_utils import (
    to_thread_and_defer_cancellation,
    to_thread_and_drain_on_cancel,
)
from worker.capacity import MAX_CONCURRENT_TASKS, clamp_concurrency
from worker.errors import ErrorClass, WorkerError
from worker.lifecycle import Attempt, Task, TaskState
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.scheduler import Scheduler
from worker.transport import codec

logger = logging.getLogger("omnivoice.worker")

PROTOCOL_VERSION = 2
# How far back a peer may be and still be served. Beta ships continuously, so
# skew is the normal case rather than the exception.
# Version 2 makes durable registration a two-phase handshake. Mixing it with a
# v1 peer can publish enrollment before the node has saved its identity (or
# leave a v1 control plane's Register-time ghost), so this boundary is not
# backward compatible in either direction.
MIN_SUPPORTED_VERSION = 2

# Semantic changes that remained additive on the protobuf wire but are not
# safe to ignore. In particular, accepting a clone without task inputs can
# return plausible wrong audio as SUCCESS, so absence is a registration error
# rather than an execution-time fallback.
REQUIRED_FEATURES = frozenset({
    "task_progress_v1",
    "task_inputs_v1",
    "remote_model_download_v1",
    "remote_model_cancel_v1",
    # A generic backend.generate() call accepts the same wire shape but drops
    # profile conditioning controls. Require the canonical worker render path
    # so an older peer cannot successfully return a different voice.
    "remote_tts_render_v1",
    # Register signs a challenge the control plane issued (IssueChallenge, or
    # Attach metadata inbound). A self-chosen challenge lets a recorded
    # Register be replayed, so a peer without this is refused, by name.
    identity.SERVER_CHALLENGE_FEATURE,
})


# Register refusal for a missing, spent or expired challenge. Retryable: the
# worker fetches a new challenge rather than treating it as a verdict.
CHALLENGE_EXPIRED = "CHALLENGE_EXPIRED"


class ControlPlaneBindError(RuntimeError):
    """The configured control-plane address is already owned."""

# Metadata key carrying the session token when a worker opens its stream.
SESSION_METADATA_KEY = "x-omnivoice-session"

# Bytes above which a result must be uploaded rather than inlined on the
# control stream. Kept well under gRPC's 4 MB default message cap: a large
# payload here head-of-line blocks the heartbeats that prove the worker alive.
INLINE_RESULT_THRESHOLD = 256 * 1024

# Ceilings on what a remote peer may stream into our filesystem. They are not
# derived from anything the worker says: ``ArtifactRef.size_bytes`` narrows the
# cap when it is declared, but can never widen it. A gibibyte is roughly six
# hours of the 24 kHz PCM16 WAV the executor writes — past any single render,
# far short of a disk.
MAX_ARTIFACT_BYTES = 1024**3
# And a budget across every artifact one task delivers, so retries and
# redeliveries cannot walk past the per-artifact cap one upload at a time.
MAX_TASK_ARTIFACT_BYTES = 2 * 1024**3
# Completed task artifacts remain for the retention window after their task is
# terminal. Bound that whole retained set, not just one active task/transfer.
MAX_STORED_ARTIFACT_BYTES_PER_WORKER = 2 * 1024**3
MAX_STORED_ARTIFACT_BYTES_TOTAL = 8 * 1024**3

_HEARTBEAT_INTERVAL_SECONDS = 20
# Heartbeats update the live pool on every accepted frame, but ``last_seen`` is
# only informational durability.  Writing SQLite at wire speed would let one
# authenticated peer block the event loop and monopolise the database.
_LAST_SEEN_PERSIST_INTERVAL_SECONDS = 60.0
# Capability discovery changes rarely. Apply/persist at a bounded cadence and
# coalesce a burst to its newest snapshot.
_CAPABILITY_UPDATE_INTERVAL_SECONDS = 5.0
_MAX_CAPABILITY_ENTRIES = 256
_MAX_CAPABILITY_UPDATE_BYTES = 256 * 1024
# A Register RPC creates credentials, but the worker is not live until it opens
# its control stream after durably accepting them. Bound abandoned handshakes so
# they cannot retain session tokens indefinitely.
_REGISTRATION_OPEN_TIMEOUT_SECONDS = 30.0
# A pending registration already owns one durable epoch. Retries reuse it;
# after a real Control activation, admit at most one new epoch per interval so
# an authenticated connect/close loop cannot turn SQLite fsync into a wire-rate
# operation.
_MIN_ACTIVATED_REGISTRATION_INTERVAL_SECONDS = 1.0
_MAX_CONCURRENT_REGISTRATIONS = 8
# How often the control plane times a round trip to each worker. Frequent
# enough that the latency shown in the UI is current, rare enough to be free.
_PING_INTERVAL_SECONDS = 5.0
# Read size when serving an input. Two orders of magnitude under the 8 MiB
# message cap, so a large input is many small frames rather than one that the
# receiver refuses outright.
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_REHASH_BLOCK_BYTES = 1024 * 1024
# Resume is useful across a short transport blip, not an unbounded ownership
# claim on the worker session and its .part file.
_PARTIAL_UPLOAD_TTL_SECONDS = 15 * 60.0
# A transient Windows file lock can make startup cleanup incomplete. Retry a
# bounded number on each production artifact sweep rather than retaining those
# unreachable resumable generations forever.
_ORPHAN_UPLOAD_RETRY_LIMIT = 1000


def _fsync_file(path: str) -> None:
    """Make bytes already written to ``path`` survive a successful ACK."""
    # Windows' _commit rejects read-only descriptors with EBADF. Every path
    # passed here is a worker-owned artifact, so reopen it write-capable before
    # asking the platform to flush the bytes.
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_parent_directory(directory: str) -> None:
    """Persist a rename/create where the platform supports directory fsync."""
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        descriptor = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in unsupported:
            raise
    finally:
        os.close(descriptor)


def _durable_makedirs(directory: str) -> None:
    """Create every missing level and persist each new parent entry.

    Fsyncing the final file's directory persists that file, but not the task
    directory entry one level above it. A success ACK is allowed to outlive a
    crash only when both directory entries do.
    """
    target = os.path.abspath(directory)
    missing: list[str] = []
    current = target
    while not os.path.isdir(current):
        if os.path.exists(current):
            if os.path.isdir(current):
                break
            raise NotADirectoryError(current)
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    for path in reversed(missing):
        try:
            os.mkdir(path)
        except FileExistsError:
            if not os.path.isdir(path):
                raise
        _fsync_parent_directory(os.path.dirname(path) or ".")

    if not missing:
        # A previous attempt may have created the entry and failed its
        # barrier. Retry it instead of treating existence as durability.
        _fsync_parent_directory(os.path.dirname(target) or ".")


def _durable_replace(source: str, destination: str) -> None:
    """Publish a completed file only after its bytes and rename are durable."""
    _fsync_file(source)
    os.replace(source, destination)
    _fsync_parent_directory(os.path.dirname(destination) or ".")


def _make_existing_artifact_durable(path: str) -> None:
    _fsync_file(path)
    _fsync_parent_directory(os.path.dirname(path) or ".")


def _write_inline_artifact(path: str, payload: bytes) -> None:
    try:
        _durable_makedirs(os.path.dirname(path) or ".")
        with open(path, "wb") as handle:
            remaining = memoryview(payload)
            while remaining:
                written = handle.write(remaining)
                if not written:
                    raise OSError("inline artifact write made no progress")
                remaining = remaining[written:]
        _make_existing_artifact_durable(path)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def _write_all(handle, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = handle.write(remaining)
        if not written:
            raise OSError("artifact write made no progress")
        remaining = remaining[written:]


def _remove_quietly(path: str) -> bool:
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _missing_artifact_paths(paths: tuple[str, ...]) -> set[str]:
    return {path for path in paths if not os.path.isfile(path)}


def _discover_stored_artifacts(root: str) -> dict[str, _StoredArtifact]:
    """Account retained results, including generations surviving a restart."""
    owners: dict[tuple[str, str], str] = {}
    try:
        tasks = task_store.list_tasks(limit=100_000)
    except Exception:
        tasks = []
    for task in tasks:
        for attempt in task.attempts:
            owners[(task.task_id, attempt.attempt_id)] = attempt.worker_id

    stored: dict[str, _StoredArtifact] = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(directory, name))
        ]
        relative_directory = os.path.relpath(directory, root)
        if relative_directory == os.curdir or os.sep in relative_directory:
            continue
        task_id = relative_directory
        for name in files:
            marker = name.find(".bin")
            if marker <= 0:
                continue
            attempt_id = name[:marker]
            path = os.path.join(directory, name)
            try:
                size = max(0, int(os.path.getsize(path)))
            except OSError:
                continue
            stored[path] = _StoredArtifact(
                worker_id=owners.get((task_id, attempt_id), ""),
                size_bytes=size,
            )
    return stored


def _discover_orphaned_upload_parts(root: str) -> set[str]:
    """Find resumable generations no live transport can still own."""
    orphaned: set[str] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        # Never traverse a directory symlink planted inside the data dir.
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(directory, name))
        ]
        for name in files:
            if name.endswith(".part"):
                orphaned.add(os.path.join(directory, name))
    return orphaned


def _capability_payload_allowed(
    capabilities, *, serialized_bytes: Optional[int] = None
) -> bool:
    """Bound model metadata before converting it into Python containers."""
    if len(capabilities) > _MAX_CAPABILITY_ENTRIES:
        return False
    if (
        serialized_bytes is not None
        and serialized_bytes > _MAX_CAPABILITY_UPDATE_BYTES
    ):
        return False
    total = 0
    for capability in capabilities:
        total += capability.ByteSize()
        if total > _MAX_CAPABILITY_UPDATE_BYTES:
            return False
    return True


def _upload_refused(
    code: str,
    message: str,
    *,
    bytes_received: int = 0,
    error_class: int = pb.ERROR_CLASS_PROTOCOL,
) -> pb.ResultAck:
    """A terminal ack that commits nothing and says why.

    Refusals are answered rather than aborted: the ack is the only frame this
    RPC ever sends back, so aborting the call would leave the worker knowing
    the upload failed and nothing about whether to retry, resume, or re-render.
    """
    return pb.ResultAck(
        bytes_received=bytes_received,
        committed=False,
        error=pb.Error(error_class=error_class, code=code, message=message),
    )


class _Upload:
    """One in-progress result transfer.

    Bytes land in an attempt-scoped ``.part`` file and are renamed into place
    only once the declared digest matches what actually arrived, so a
    truncated, reordered, or corrupted transfer can never be mistaken for a
    finished result. Every rule here exists because the sender is remote: the
    offset is checked against what we hold rather than trusted as a hint, the
    total is capped whether or not a size was declared, and an iterator that
    simply stops commits nothing.
    """

    def __init__(
        self,
        *,
        session: "_Session",
        attempt: Attempt,
        artifact_id: str,
        final: str,
        limit: int,
        declared_size: int,
        declared_sha256: str,
        reservation_owner: object,
        on_commit: Callable[[Attempt, int], None],
        on_finished: Callable[["_Upload"], None],
    ) -> None:
        self.session = session
        self.attempt = attempt
        self.artifact_id = artifact_id
        self.final = final
        self.part = f"{final}.part"
        self.limit = limit
        self.declared_size = declared_size
        self.declared_sha256 = declared_sha256.strip().lower()
        self.reservation_owner = reservation_owner
        self._on_commit = on_commit
        self._on_finished = on_finished
        self._digest = hashlib.sha256()
        self._handle = None
        self.received = 0
        self._discarded = False

    def held_bytes(self) -> int:
        try:
            return os.path.getsize(self.part)
        except OSError:
            return 0

    async def start(self, offset: int) -> Optional[pb.ResultAck]:
        """Open the part file at ``offset``, or refuse with what we hold."""
        held = await to_thread_and_drain_on_cancel(self.held_bytes)
        if offset == 0:
            self._handle = await to_thread_and_drain_on_cancel(
                open, self.part, "wb"
            )
            return None
        if offset == held and 0 < held <= self.limit:
            # The digest has to cover the bytes already on disk, or the
            # verification at commit would attest only to the resumed tail —
            # which is exactly the case a resume exists to protect.
            await to_thread_and_drain_on_cancel(self._rehash_held)
            self.received = held
            self._handle = await to_thread_and_drain_on_cancel(
                open, self.part, "ab"
            )
            return None
        return _upload_refused(
            "OFFSET_MISMATCH",
            "Resume from the byte count in this ack.",
            bytes_received=held,
            error_class=pb.ERROR_CLASS_TRANSIENT,
        )

    def _rehash_held(self) -> None:
        with open(self.part, "rb") as fh:
            for block in iter(lambda: fh.read(_REHASH_BLOCK_BYTES), b""):
                self._digest.update(block)

    async def write(self, chunk) -> Optional[pb.ResultAck]:
        """Append one chunk. Non-None means the transfer is over."""
        if int(chunk.offset) != self.received:
            # Not a resume point: a gap or an overlap inside a live stream is
            # a sender that has lost track of what it sent, and appending it
            # would produce a file that hashes to nothing anybody expected.
            return _upload_refused(
                "OFFSET_MISMATCH",
                "Resume from the byte count in this ack.",
                bytes_received=self.received,
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        data = bytes(chunk.data)
        if self.received + len(data) > self.limit:
            await self.discard_async()
            return _upload_refused(
                "ARTIFACT_TOO_LARGE",
                "This result is larger than the control plane accepts.",
            )
        await to_thread_and_drain_on_cancel(_write_all, self._handle, data)
        if self._discarded or self.session.revoked or self.attempt.state.terminal:
            logger.warning(
                "Refusing result upload for task %s attempt %s "
                "(attempt=%s, session_revoked=%s, discarded=%s, error=%s)",
                self.attempt.task_id,
                self.attempt.attempt_id,
                self.attempt.state.value,
                self.session.revoked,
                self._discarded,
                getattr(self.attempt.error, "code", None),
            )
            await self.discard_async()
            return _upload_refused(
                "ATTEMPT_NOT_LIVE",
                "This attempt stopped accepting a result during upload "
                f"(attempt={self.attempt.state.value}, "
                f"error={getattr(self.attempt.error, 'code', None)}).",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        self._digest.update(data)
        self.received += len(data)
        return None

    async def commit(self) -> pb.ResultAck:
        """Verify, then rename. Never the other way round."""
        await self.close_async()
        if self.declared_size and self.received != self.declared_size:
            await self.discard_async()
            return _upload_refused(
                "SIZE_MISMATCH",
                "The transfer did not deliver the number of bytes it declared.",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        if self._digest.hexdigest() != self.declared_sha256:
            # Keeping the part file would let the next resume append onto
            # bytes already known to be wrong.
            await self.discard_async()
            return _upload_refused(
                "DIGEST_MISMATCH",
                "The uploaded result does not match its declared sha256.",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        if self._discarded or self.session.revoked or self.attempt.state.terminal:
            logger.warning(
                "Refusing result commit for task %s attempt %s "
                "(attempt=%s, session_revoked=%s, discarded=%s, error=%s)",
                self.attempt.task_id,
                self.attempt.attempt_id,
                self.attempt.state.value,
                self.session.revoked,
                self._discarded,
                getattr(self.attempt.error, "code", None),
            )
            await self.discard_async()
            return _upload_refused(
                "ATTEMPT_NOT_LIVE",
                "This attempt is no longer accepting a result "
                f"(attempt={self.attempt.state.value}, "
                f"error={getattr(self.attempt.error, 'code', None)}).",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        try:
            await to_thread_and_drain_on_cancel(
                _durable_replace, self.part, self.final
            )
        except BaseException:
            self._discarded = True
            for path in (self.part, self.final):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self._on_finished(self)
            raise
        # Revocation/cancellation can publish while the durability barrier is
        # running in its thread. It must win before the commit callback spends
        # budget or this RPC licenses the worker to forget its only copy.
        if self._discarded or self.session.revoked or self.attempt.state.terminal:
            logger.warning(
                "Refusing result after durable write for task %s attempt %s "
                "(attempt=%s, session_revoked=%s, discarded=%s, error=%s)",
                self.attempt.task_id,
                self.attempt.attempt_id,
                self.attempt.state.value,
                self.session.revoked,
                self._discarded,
                getattr(self.attempt.error, "code", None),
            )
            try:
                os.remove(self.final)
            except OSError:
                pass
            self._discarded = True
            self._on_finished(self)
            return _upload_refused(
                "ATTEMPT_NOT_LIVE",
                "This attempt stopped accepting a result during commit "
                f"(attempt={self.attempt.state.value}, "
                f"error={getattr(self.attempt.error, 'code', None)}).",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            )
        self._on_finished(self)
        self._on_commit(self.attempt, self.received)
        return pb.ResultAck(
            artifact_id=self.artifact_id, bytes_received=self.received, committed=True
        )

    def incomplete(self) -> pb.ResultAck:
        """The stream ended with no terminal chunk.

        The part file survives for a resume and nothing is renamed. This used
        to return ``committed=True`` over whatever bytes happened to arrive.
        """
        return _upload_refused(
            "UPLOAD_INCOMPLETE",
            "The upload ended before its last chunk; resume from the byte count in this ack.",
            bytes_received=self.received,
            error_class=pb.ERROR_CLASS_TRANSIENT,
        )

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.close()

    async def close_async(self) -> None:
        await to_thread_and_drain_on_cancel(self.close)

    async def discard_async(self) -> None:
        self._discarded = True
        try:
            await self.close_async()
        finally:
            try:
                await to_thread_and_drain_on_cancel(os.remove, self.part)
            except OSError:
                pass
            self._on_finished(self)

    def discard(self) -> None:
        self._discarded = True
        try:
            self.close()
        finally:
            try:
                os.remove(self.part)
            except OSError:
                pass
            finally:
                # Physical deletion or close can transiently fail. Neither may
                # retain the logical lease and wedge every later retry.
                self._on_finished(self)


class _RevokedTransfer(RuntimeError):
    """Internal wake-up for a transfer whose session was revoked."""


class _ActivationRefused(RuntimeError):
    """The durable worker row stopped authorising a pending activation."""


@dataclass(frozen=True)
class _StoredArtifact:
    worker_id: str
    size_bytes: int


@dataclass
class _ArtifactReservation:
    worker_id: str
    size_bytes: int
    owners: dict[object, int]


@dataclass
class _ResultPublicationGate:
    lock: asyncio.Lock
    users: int = 0


class _Session:
    """Server-side view of one connected worker's stream."""

    def __init__(self, worker_id: str, epoch: int, session: identity.Session) -> None:
        self.worker_id = worker_id
        self.epoch = epoch
        self.session = session
        self.outbox: asyncio.Queue[pb.ServerMessage] = asyncio.Queue()
        self.stream_open = False
        self.activated = False
        self.revoked = False
        self.egress_fenced = False
        self.terminated = asyncio.Event()
        # Tasks that can publish a server frame. Revocation cancels them in the
        # same event-loop turn that marks the session dead, before a queued
        # assignment or acknowledgement can escape on either transport.
        self.egress_tasks: set[asyncio.Task] = set()
        self.registration: Optional[dict] = None
        self.open_timeout: Optional[asyncio.Task] = None
        # Set only in inbound mode, where artifacts move over RPCs this side
        # initiates. None means outbound, where the worker calls UploadResult
        # and DownloadArtifact itself and there is nothing to hold here.
        self.connection = None
        # nonce → monotonic send time, for the outstanding ping.
        self.pending_pings: dict[int, float] = {}
        # Reconciliation can reserve a terminal attempt until its CancelAck.
        # The worker's first heartbeat no longer counts that just-cancelled
        # wrapper, so keep the pending authority release explicit until ACK.
        self.pending_claim_cancels: set[str] = set()
        # Attempt ids alone are not authority. Keep the exact cancel reference
        # and whether activation actually reserved capacity for it, so a stale
        # generation or a lookalike ACK cannot release the current worker.
        self.pending_claim_cancel_refs: dict[str, tuple[str, int]] = {}
        self.pending_claim_reservations: set[str] = set()
        self.pending_unknown_claim_cancels: set[str] = set()
        # Low-priority durable metadata is coalesced per authenticated
        # generation. These tasks never outlive their stream: cancellation
        # drains an already-running SQLite thread before teardown completes.
        self.maintenance_tasks: set[asyncio.Task] = set()
        self.heartbeat_touch_task: Optional[asyncio.Task] = None
        self.heartbeat_touch_pending = False
        self.last_heartbeat_touch_at: Optional[float] = None
        self.capability_update_task: Optional[asyncio.Task] = None
        self.pending_capabilities: Optional[list[dict]] = None
        self.last_capability_apply_at: Optional[float] = None
        self.next_nonce = 1

    async def send(self, message: pb.ServerMessage) -> None:
        if not self.revoked and not self.egress_fenced:
            self.outbox.put_nowait(message)


class WorkerServicer(pb_grpc.WorkerServiceServicer):
    """Implements ``WorkerService`` on top of the scheduler and registry."""

    def __init__(
        self,
        scheduler: Scheduler,
        pool: WorkerPool,
        *,
        artifact_dir: str,
        cert_fingerprint: str = "",
        max_stored_artifact_bytes_per_worker: int = (
            MAX_STORED_ARTIFACT_BYTES_PER_WORKER
        ),
        max_stored_artifact_bytes_total: int = MAX_STORED_ARTIFACT_BYTES_TOTAL,
    ) -> None:
        self.scheduler = scheduler
        self.pool = pool
        self.artifact_dir = artifact_dir
        self.cert_fingerprint = cert_fingerprint
        # Activated sessions remain authoritative while a replacement proves
        # that it durably accepted registration. Pending sessions are indexed
        # separately so a failed replacement cannot strand the live worker.
        self._sessions: dict[str, _Session] = {}
        self._pending_sessions: dict[str, _Session] = {}
        self._by_token: dict[str, _Session] = {}
        self._registration_locks: dict[str, asyncio.Lock] = {}
        self._registration_auth_slots = asyncio.Semaphore(
            _MAX_CONCURRENT_REGISTRATIONS
        )
        self._challenges = identity.ChallengeBook()
        self._last_stream_activation_at: dict[str, float] = {}
        # Control teardown intentionally invalidates the token, but an RPC that
        # was already authorised can still hold the session object. Retain those
        # generations until their transfers finish so a later durable revoke
        # reaches them too.
        self._transfer_sessions: dict[str, dict[_Session, int]] = {}
        # A resumable upload deliberately outlives its RPC. Keep its owning
        # worker until the bytes commit or are discarded, otherwise a Control
        # disconnect can erase every session index before DELETE gets a chance
        # to remove the attempt-scoped partial.
        self._partial_uploads: dict[str, dict[str, _Upload]] = {}
        self._partial_upload_expiries: dict[str, asyncio.TimerHandle] = {}
        # Only one RPC may own an attempt path at a time. Separate from the
        # resumable index above: an incomplete RPC releases this live lease but
        # deliberately leaves its closed partial available to the next one.
        self._active_uploads: dict[str, _Upload] = {}
        # task_id → attempt_id → committed artifact bytes. Per attempt rather
        # than a running total, so a redelivered upload of the same attempt
        # replaces its own entry instead of spending the task's budget twice.
        self._artifact_bytes: dict[str, dict[str, int]] = {}
        self._artifact_reservations: dict[str, _ArtifactReservation] = {}
        # A retained old Control/Attach generation may redeliver the same
        # attempt while its replacement also reports it.  Serialize the whole
        # fetch/write/commit/cleanup verdict, not just Scheduler.on_result, so
        # a losing generation can never unlink or overwrite the winner's path.
        self._result_publications: dict[
            tuple[str, str], _ResultPublicationGate
        ] = {}
        self._stored_artifacts: dict[str, _StoredArtifact] = {}
        self._artifact_capacity_lock = asyncio.Lock()
        self._max_stored_artifact_bytes_per_worker = max(
            0, int(max_stored_artifact_bytes_per_worker)
        )
        self._max_stored_artifact_bytes_total = max(
            0, int(max_stored_artifact_bytes_total)
        )
        _durable_makedirs(artifact_dir)
        # Resume ownership is process-local. A .part generation that survived
        # a process restart cannot be active or indexed, so keeping it only
        # spends disk. Failed Windows unlinks remain queued for the periodic
        # production sweep.
        self._orphaned_upload_parts = _discover_orphaned_upload_parts(
            artifact_dir
        )
        self._stored_artifacts.update(_discover_stored_artifacts(artifact_dir))
        self.sweep_orphaned_upload_parts(limit=None)

    def sweep_orphaned_upload_parts(
        self, *, limit: Optional[int] = _ORPHAN_UPLOAD_RETRY_LIMIT
    ) -> int:
        """Retry deletion of crash-surviving resumable upload files."""
        removed = 0
        for path in list(self._orphaned_upload_parts):
            if limit is not None and removed >= limit:
                break
            # Defensive for embedded callers which may invoke this after the
            # server accepted work: indexed partials still belong to a retry.
            if path in self._active_uploads or any(
                path in uploads for uploads in self._partial_uploads.values()
            ):
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                continue
            self._orphaned_upload_parts.discard(path)
            self._stored_artifacts.pop(path, None)
            removed += 1
        return removed

    # ── Registration ──────────────────────────────────────────────────────

    async def IssueChallenge(
        self, request: pb.ChallengeRequest, context
    ) -> pb.ChallengeResponse:
        """A single-use challenge for the caller's next Register to sign."""
        return pb.ChallengeResponse(
            challenge=self._challenges.issue(),
            expires_in_seconds=int(self._challenges.ttl_seconds),
        )

    async def Register(self, request: pb.RegisterRequest, context) -> pb.RegisterResponse:
        if request.protocol_version_max < MIN_SUPPORTED_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker speaks an older protocol than the control plane supports. "
                "Update OmniVoice on the worker machine, then reconnect.",
            )
        if request.protocol_version_min > PROTOCOL_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker is newer than the control plane. Update OmniVoice on this "
                "machine, then reconnect.",
            )
        missing_features = sorted(REQUIRED_FEATURES.difference(request.features))
        if missing_features:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "This worker is missing required protocol features "
                f"({', '.join(missing_features)}). Update VoiceStudio on the worker "
                "machine, then reconnect; no task was run.",
            )
        if not _capability_payload_allowed(request.capabilities):
            return self._refuse(
                "CAPABILITIES_TOO_LARGE",
                "This worker advertised more model metadata than the control plane accepts.",
            )

        # Spent before authentication, whatever its outcome: a challenge is
        # good for one attempt, so a recorded Register is dead on arrival.
        if not self._challenges.consume(bytes(request.challenge)):
            return self._refuse(
                CHALLENGE_EXPIRED,
                "The registration challenge expired or was already used. The "
                "worker will fetch a fresh one and retry.",
            )

        async with self._registration_auth_slots:
            worker = await to_thread_and_drain_on_cancel(
                self._authenticate, request
            )
        if worker is None:
            # Deliberately one message for every failure mode: unknown key,
            # revoked worker, bad signature, spent token. Distinguishing them
            # tells an attacker which half of the guess was right.
            return self._refuse(
                "AUTH_FAILED",
                "This worker could not be authenticated. Generate a new enrollment "
                "token in Settings → System → Remote workers and add the worker again.",
            )

        # The address the worker actually reached us from — what the UI shows
        # as ip:port. Self-reported endpoints would be guesses; this is fact.
        return await self.establish_session(
            worker, request, address=_peer_address(context)
        )

    async def establish_session(
        self, worker: registry.RemoteWorker, request: pb.RegisterRequest, *, address: str
    ) -> pb.RegisterResponse:
        """Everything registration does once the worker is known to be genuine.

        Split out because inbound mode (NodeService.Attach) reaches this point
        by a different road — the panel dialled, and admission was an API key
        rather than an enrollment token — but must arrive in exactly the same
        state. A second copy of session issue, capability application and
        in-flight reconciliation is a second thing to keep in step forever, and
        the half that gets forgotten is always the reconciliation.
        """
        lock = self._registration_locks.setdefault(worker.id, asyncio.Lock())
        async with lock:
            previous = self._pending_sessions.get(worker.id)
            if previous is not None and not previous.revoked:
                # A repeated, freshly authenticated Register most commonly
                # means the first response was lost. Reuse the one pending
                # durable epoch/session instead of rotating a token and fsyncing
                # another epoch for every retry.
                self._refresh_registration_timeout(previous)
                return self._registration_response(previous)

            activated_at = self._last_stream_activation_at.get(worker.id)
            if activated_at is not None:
                delay = (
                    activated_at
                    + _MIN_ACTIVATED_REGISTRATION_INTERVAL_SECONDS
                    - time.monotonic()
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            epoch = await to_thread_and_drain_on_cancel(
                registry.begin_session, worker.id
            )
            return self._publish_pending_session(
                worker, request, address=address, epoch=epoch
            )

    def _publish_pending_session(
        self,
        worker: registry.RemoteWorker,
        request: pb.RegisterRequest,
        *,
        address: str,
        epoch: int,
    ) -> pb.RegisterResponse:
        session = identity.issue_session(worker_id=worker.id, key_id=worker.key_id, epoch=epoch)
        host = codec.host_from_pb(request.host)
        backend = host["gpus"][0].get("backend", "") if host.get("gpus") else ""
        capabilities = [
            codec.capability_from_pb(c, fallback_backend=backend)
            for c in request.capabilities
        ]
        claimed_refs = {
            ref.attempt_id: codec.task_ref(
                ref.task_id, ref.attempt_id, ref.session_epoch
            )
            for ref in request.in_flight
        }
        claimed = set(claimed_refs)
        # A finished result the worker never had acknowledged is work it is
        # still holding the only copy of. Reconciliation writes off anything
        # the worker does not claim (lifecycle.reconcile), so leaving these out
        # marks a completed render LOST moments before it is redelivered.
        unacked = {ref.attempt_id for ref in request.completed_unacked}

        # Supersede only an earlier *pending* registration. The activated
        # session remains authoritative until this replacement opens its
        # stream; registration persistence can fail after this response.
        previous = self._pending_sessions.pop(worker.id, None)
        if previous is not None:
            self._by_token.pop(previous.session.token, None)
            if previous.open_timeout is not None:
                previous.open_timeout.cancel()
                previous.open_timeout = None

        active = self.pool.get(worker.id)
        if active is not None:
            # Do not dispatch work omitted from the pending registration's
            # recovery snapshot. Existing work can still finish and report.
            active.registration_pending = True

        live = _Session(worker.id, epoch, session)
        live.registration = {
            "worker": worker,
            "max_concurrent_tasks": clamp_concurrency(
                request.max_concurrent_tasks or 1
            ),
            "backend": backend,
            "in_flight": claimed,
            "in_flight_refs": claimed_refs,
            "reconcile": claimed | unacked,
            "address": address,
            "capabilities": capabilities,
            "host": host,
        }
        self._pending_sessions[worker.id] = live
        self._by_token[session.token] = live
        self._refresh_registration_timeout(live)

        logger.info("Worker %s registered on epoch %d", worker.name, epoch)
        return self._registration_response(live)

    def _refresh_registration_timeout(self, session: _Session) -> None:
        timeout = session.open_timeout
        if timeout is not None:
            timeout.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Embedded synchronous callers can activate explicitly.
            session.open_timeout = None
            return
        session.open_timeout = loop.create_task(
            self._expire_unopened_session(session),
            name=f"worker-registration-{session.worker_id}",
        )

    def _registration_response(self, live: _Session) -> pb.RegisterResponse:
        return pb.RegisterResponse(
            worker_id=live.worker_id,
            session_token=live.session.token,
            session_epoch=live.epoch,
            protocol_version=PROTOCOL_VERSION,
            session_expires_at_unix=int(live.session.expires_at),
            heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
            authoritative_in_flight=self._authoritative_refs(live.worker_id),
        )

    def _activate_session(self, session: _Session):
        """Publish a registered worker only once its stream is opening."""
        if session.revoked:
            return None
        pending_sessions = getattr(self, "_pending_sessions", {})
        is_pending = pending_sessions.get(session.worker_id) is session
        is_current = self._sessions.get(session.worker_id) is session
        if not is_pending and not is_current:
            return None
        if session.activated:
            return self.pool.get(session.worker_id) if is_current else None
        registration = session.registration
        if registration is None:
            # Compatibility for tests and embedded callers that construct a
            # session around an already-connected pool entry.
            worker = self.pool.get(session.worker_id)
            session.activated = worker is not None
            return worker
        with registry.authority_guard():
            return self._activate_pending_session(
                session, registration, pending_sessions, is_pending=is_pending
            )

    def _activate_pending_session(
        self,
        session: _Session,
        registration: dict,
        pending_sessions: dict[str, _Session],
        *,
        is_pending: bool,
    ):
        """Publish one pending generation under the registry authority lock."""
        fresh_worker = self._load_activation_worker(session, registration)
        if fresh_worker is None:
            return None
        previous_worker = self.pool.get(session.worker_id)
        try:
            worker = self._connect_activation_worker(
                session, registration, fresh_worker, previous_worker
            )
            # Reconcile before any new work is dispatched: the worker may be
            # holding tasks this control plane forgot across a restart. Unacked
            # results count as held here but not as occupied slots above.
            def persist_registration(conn) -> None:
                registry.update_capabilities(
                    session.worker_id,
                    capabilities=registration["capabilities"],
                    host=registration["host"],
                    max_concurrent_tasks=registration["max_concurrent_tasks"],
                    _conn=conn,
                )

            zombies = self.scheduler.on_reconnected(
                session.worker_id,
                in_flight=registration["reconcile"],
                before_persist=persist_registration,
            )
            return self._finish_session_activation(
                session,
                registration,
                pending_sessions,
                is_pending=is_pending,
                worker=worker,
                zombies=zombies,
            )
        except Exception:
            self._restore_activation_pool(session.worker_id, previous_worker)
            raise

    async def _activate_session_async(self, session: _Session):
        """Durably reconcile off-loop, then publish on the owning loop."""
        locks = getattr(self, "_registration_locks", None)
        if locks is None:
            return self._activate_session(session)
        lock = locks.setdefault(
            session.worker_id, asyncio.Lock()
        )
        async with lock:
            if session.revoked:
                return None
            pending_sessions = getattr(self, "_pending_sessions", {})
            is_pending = pending_sessions.get(session.worker_id) is session
            is_current = self._sessions.get(session.worker_id) is session
            if not is_pending and not is_current:
                return None
            if session.activated:
                return self.pool.get(session.worker_id) if is_current else None
            registration = session.registration
            if registration is None:
                return self._activate_session(session)

            previous_session = self._sessions.get(session.worker_id)
            if previous_session is not None and previous_session is not session:
                await self._cancel_session_maintenance(previous_session)

            with registry.authority_guard():
                fresh_worker = self._load_activation_worker(
                    session, registration
                )
                if fresh_worker is None:
                    return None
                previous_worker = self.pool.get(session.worker_id)
                try:
                    worker = self._connect_activation_worker(
                        session, registration, fresh_worker, previous_worker
                    )
                except BaseException:
                    self._restore_activation_pool(
                        session.worker_id, previous_worker
                    )
                    raise
                # The staged pool record is visible while SQLite runs, so it
                # must remain ineligible until the durable generation and
                # session indexes publish together below.
                worker.registration_pending = True

            def persist_registration(conn) -> None:
                row = conn.execute(
                    "SELECT revoked FROM remote_workers WHERE id = ?",
                    (session.worker_id,),
                ).fetchone()
                if row is None or bool(row["revoked"]):
                    raise _ActivationRefused
                registry.update_capabilities(
                    session.worker_id,
                    capabilities=registration["capabilities"],
                    host=registration["host"],
                    max_concurrent_tasks=registration["max_concurrent_tasks"],
                    _conn=conn,
                )

            included: set[str] = set()
            cancellation_requested = False
            try:
                while True:
                    generation = self.scheduler.prepare_reconnected(
                        session.worker_id,
                        in_flight=registration["reconcile"],
                        include_task_ids=included,
                    )
                    _, cancelled = await to_thread_and_defer_cancellation(
                        functools.partial(
                            self.scheduler.persist_reconciliation,
                            generation,
                            before_persist=persist_registration,
                        )
                    )
                    cancellation_requested = cancellation_requested or cancelled
                    if self.scheduler.reconciliation_is_current(generation):
                        break
                    # A cancellation/sweep that raced the write owns the newer
                    # live state. Include its task on the retry even if it is
                    # now terminal, so the next transaction repairs any stale
                    # row the completed generation may have written last.
                    included.update(
                        task.task_id for task in generation.originals
                    )

                with registry.authority_guard():
                    still_pending = (
                        pending_sessions.get(session.worker_id) is session
                    )
                    fresh_worker = self._load_activation_worker(
                        session, registration
                    )
                    if (
                        not still_pending
                        or session.revoked
                        or fresh_worker is None
                    ):
                        if self.pool.get(session.worker_id) is worker:
                            if fresh_worker is None:
                                self.pool.disconnect(session.worker_id)
                            else:
                                self._restore_activation_pool(
                                    session.worker_id, previous_worker
                                )
                        activated = None
                    else:
                        worker.record = fresh_worker
                        zombies = self.scheduler.apply_reconnected(generation)
                        worker.registration_pending = False
                        activated = self._finish_session_activation(
                            session,
                            registration,
                            pending_sessions,
                            is_pending=True,
                            worker=worker,
                            zombies=zombies,
                        )
            except _ActivationRefused:
                # The transaction observed the durable revoke/missing row.
                # Fence the staged pool entry immediately; the management
                # route will retire every transport generation in the same
                # event-loop turn after its tombstone write returns.
                with registry.authority_guard():
                    session.revoked = True
                    if self.pool.get(session.worker_id) is worker:
                        self.pool.disconnect(session.worker_id)
                return None
            except BaseException:
                with registry.authority_guard():
                    if (
                        not session.activated
                        and self.pool.get(session.worker_id) is worker
                    ):
                        if session.revoked:
                            self.pool.disconnect(session.worker_id)
                        else:
                            self._restore_activation_pool(
                                session.worker_id, previous_worker
                            )
                raise

            if cancellation_requested:
                raise asyncio.CancelledError
            return activated

    def _load_activation_worker(
        self, session: _Session, registration: dict
    ):
        """Read final durable worker authority under ``authority_guard``."""
        fresh_worker = registry.get(session.worker_id)
        if fresh_worker is None or fresh_worker.revoked:
            if not session.stream_open:
                self.discard_unopened_session(
                    session.worker_id, session_token=session.session.token
                )
            return None
        # Defend even against a re-entrant authority mutation hidden inside a
        # registry read (and make the durable flags, not a cached row, final).
        if registry.is_revoked(fresh_worker.key_id):
            if not session.stream_open:
                self.discard_unopened_session(
                    session.worker_id, session_token=session.session.token
                )
            return None
        return replace(
            fresh_worker,
            enabled=registry.is_enabled(session.worker_id),
            host=registration["host"],
            capabilities=registration["capabilities"],
            max_concurrent_tasks=registration["max_concurrent_tasks"],
        )

    def _connect_activation_worker(
        self,
        session: _Session,
        registration: dict,
        fresh_worker,
        previous_worker,
    ):
        worker = self.pool.connect(
            fresh_worker,
            session=session.session,
            epoch=session.epoch,
            max_concurrent_tasks=registration["max_concurrent_tasks"],
            backend=registration["backend"],
            in_flight=registration["in_flight"],
            address=registration["address"],
        )
        self.pool.apply_capabilities(
            session.worker_id, registration["capabilities"]
        )
        if previous_worker is not None:
            # A real drain/goodbye that arrived during the handshake is state,
            # not a transport lock; carry it onto the new session.
            worker.draining = previous_worker.draining
        return worker

    def _restore_activation_pool(self, worker_id: str, previous_worker) -> None:
        if previous_worker is None:
            self.pool.disconnect(worker_id)
        else:
            self.pool.restore_connection(previous_worker)

    def _finish_session_activation(
        self,
        session: _Session,
        registration: dict,
        pending_sessions: dict[str, _Session],
        *,
        is_pending: bool,
        worker,
        zombies: list[str],
    ):
        reserved_claims: set[str] = set()
        unknown_claims: set[str] = set()
        # A fresh capacity record starts empty, but a reconnect may claim work
        # that is already running. Seed both counters before publication.
        claimed = registration["in_flight"]
        seeded: set[str] = set()
        for task in self.scheduler.tasks_for_worker(session.worker_id):
            attempt = task.active_attempt
            if (
                attempt is None
                or attempt.attempt_id not in claimed
                or attempt.attempt_id in seeded
                or len(seeded) >= worker.capacity.max_concurrent_tasks
            ):
                continue
            worker.capacity.reserve(task.engine, task.model_id)
            seeded.add(attempt.attempt_id)
        for attempt_id in sorted(claimed - seeded):
            if len(seeded) >= worker.capacity.max_concurrent_tasks:
                break
            claimed_ref = registration["in_flight_refs"].get(attempt_id)
            claimed_task = (
                self.scheduler.get(claimed_ref.task_id)
                if claimed_ref is not None
                else None
            )
            claimed_attempt = (
                claimed_task.get_attempt(attempt_id)
                if claimed_task is not None
                else None
            )
            if (
                claimed_attempt is not None
                and claimed_attempt.worker_id == session.worker_id
            ):
                worker.capacity.reserve(
                    claimed_task.engine, claimed_task.model_id
                )
            else:
                worker.capacity.reserve_unknown()
                unknown_claims.add(attempt_id)
            reserved_claims.add(attempt_id)
            seeded.add(attempt_id)

        previous_session = self._sessions.get(session.worker_id)
        if previous_session is not None and previous_session is not session:
            self._fence_session_egress(previous_session)
        session.activated = True
        self._sessions[session.worker_id] = session
        if is_pending:
            pending_sessions.pop(session.worker_id, None)
        if session.open_timeout is not None:
            session.open_timeout.cancel()
            session.open_timeout = None
        for attempt_id in zombies:
            ref = registration["in_flight_refs"].get(attempt_id)
            if ref is not None:
                session.pending_claim_cancels.add(attempt_id)
                session.pending_claim_cancel_refs[attempt_id] = (
                    ref.task_id,
                    ref.session_epoch,
                )
                if attempt_id in reserved_claims:
                    session.pending_claim_reservations.add(attempt_id)
                if attempt_id in unknown_claims:
                    session.pending_unknown_claim_cancels.add(attempt_id)
                session.outbox.put_nowait(
                    pb.ServerMessage(
                        cancel=pb.TaskCancel(
                            ref=ref,
                            reason="The control plane no longer owns this attempt.",
                        )
                    )
                )
        return worker

    def _fence_session_egress(self, session: _Session) -> None:
        """Stop a superseded generation sending while preserving its reads."""
        session.egress_fenced = True
        while True:
            try:
                session.outbox.get_nowait()
            except asyncio.QueueEmpty:
                break
        connection = session.connection
        fence_connection = getattr(connection, "fence_session_egress", None)
        if callable(fence_connection):
            fence_connection(session)
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in list(session.egress_tasks):
            if task is not current:
                task.cancel()
        for task in list(session.maintenance_tasks):
            if task is not current:
                task.cancel()

    async def _expire_unopened_session(self, session: _Session) -> None:
        try:
            await asyncio.sleep(_REGISTRATION_OPEN_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        self.discard_unopened_session(
            session.worker_id, session_token=session.session.token
        )

    def discard_unopened_session(
        self, worker_id: str, *, session_token: str = ""
    ) -> bool:
        """Forget a registration whose worker never confirmed acceptance."""
        if session_token:
            session = self._by_token.get(session_token)
        else:
            session = self._pending_sessions.get(worker_id)
        if session is None or session.stream_open or session.activated:
            return False
        if session.worker_id != worker_id:
            return False
        if session_token and session.session.token != session_token:
            return False
        if self._pending_sessions.get(worker_id) is not session:
            return False
        self._pending_sessions.pop(worker_id, None)
        self._by_token.pop(session.session.token, None)
        active_session = self._sessions.get(worker_id)
        active = self.pool.get(worker_id)
        if (
            active_session is not None
            and active is not None
            and active.epoch == active_session.epoch
        ):
            active.registration_pending = False
        timeout = session.open_timeout
        session.open_timeout = None
        if timeout is not None:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if timeout is not current:
                timeout.cancel()
        return True

    def _authenticate(self, request: pb.RegisterRequest) -> Optional[registry.RemoteWorker]:
        public_key = bytes(request.public_key)
        if len(public_key) != 32:
            return None
        key_id = identity.key_id_for(public_key)

        if request.enrollment_token:
            # First contact: spend the join token, then bind this key to it.
            try:
                token = identity.EnrollmentToken.decode(request.enrollment_token)
            except ValueError:
                return None
            if registry.is_revoked(key_id):
                return None
            enrolled = (
                None
                if token.expired()
                else registry.enroll_with_token(
                    token,
                    name=request.host.hostname or key_id,
                    public_key=public_key,
                    consent_granted=True,
                )
            )
            if enrolled is not None:
                return enrolled

            # Register may have committed the identity before its response was
            # lost. Recover that exact enrollment, including after the token's
            # original window, but only with proof of the private key. Matching
            # public bytes alone would turn an observed key plus a spent bearer
            # token into a reusable credential.
            recovered = registry.recover_enrollment_with_token(
                token, public_key=public_key
            )
            if recovered is None or request.worker_id not in ("", recovered.id):
                return None
            proof = identity.challenge_message(
                challenge=bytes(request.challenge),
                worker_id=request.worker_id,
                session_epoch=request.envelope.sequence,
                nonce=bytes(request.nonce),
            )
            if not identity.verify_signature(
                public_key, proof, bytes(request.challenge_signature)
            ):
                return None
            return recovered

        if registry.is_revoked(key_id):
            return None
        return registry.authenticate(
            key_id=key_id,
            public_key=public_key,
            challenge=bytes(request.challenge),
            signature=bytes(request.challenge_signature),
            nonce=bytes(request.nonce),
            session_epoch=request.envelope.sequence,
        )

    def _authoritative_refs(self, worker_id: str) -> list[pb.TaskRef]:
        """What this control plane believes the worker is running.

        Anything the worker holds that is not in this list is a zombie it must
        stop, which is the other half of reconciliation.
        """
        refs = []
        for task in self.scheduler.tasks_for_worker(worker_id):
            attempt = task.active_attempt
            if attempt is not None:
                refs.append(codec.ref_for(attempt))
        return refs

    @staticmethod
    def _refuse(code: str, message: str) -> pb.RegisterResponse:
        return pb.RegisterResponse(
            error=pb.Error(error_class=pb.ERROR_CLASS_PROTOCOL, code=code, message=message)
        )

    # ── Control stream ────────────────────────────────────────────────────

    async def _disconnect_session_async(self, session: _Session) -> None:
        """Fence scheduling now, then persist grace windows off-loop."""
        locks = getattr(self, "_registration_locks", None)
        if locks is None:
            try:
                if (
                    self._sessions.get(session.worker_id) is session
                    and session.activated
                ):
                    self.scheduler.on_disconnected(session.worker_id)
            finally:
                if self._sessions.get(session.worker_id) is session:
                    self._sessions.pop(session.worker_id, None)
                token = getattr(getattr(session, "session", None), "token", "")
                if token:
                    getattr(self, "_by_token", {}).pop(token, None)
            return
        lock = locks.setdefault(
            session.worker_id, asyncio.Lock()
        )
        async with lock:
            is_current = (
                self._sessions.get(session.worker_id) is session
                and session.activated
            )
            if not is_current:
                token = getattr(getattr(session, "session", None), "token", "")
                if token:
                    self.discard_unopened_session(
                        session.worker_id, session_token=token
                    )
                    self._by_token.pop(token, None)
                return

            # The synchronous implementation disconnected in a finally while
            # SQLite held the event loop. Once persistence moves off-loop, the
            # pool must be fenced first so no assignment enters that window.
            with registry.authority_guard():
                if self._sessions.get(session.worker_id) is session:
                    self.pool.disconnect(session.worker_id)

            included: set[str] = set()
            cancellation_requested = False
            try:
                while True:
                    generation = self.scheduler.prepare_disconnected(
                        session.worker_id, include_task_ids=included
                    )
                    _, cancelled = await to_thread_and_defer_cancellation(
                        functools.partial(
                            self.scheduler.persist_reconciliation, generation
                        )
                    )
                    cancellation_requested = cancellation_requested or cancelled
                    if self.scheduler.reconciliation_is_current(generation):
                        break
                    included.update(
                        task.task_id for task in generation.originals
                    )

                with registry.authority_guard():
                    if self._sessions.get(session.worker_id) is session:
                        self.scheduler.apply_disconnected(generation)
            finally:
                with registry.authority_guard():
                    if self._sessions.get(session.worker_id) is session:
                        self._sessions.pop(session.worker_id, None)
                    self._by_token.pop(session.session.token, None)

            if cancellation_requested:
                raise asyncio.CancelledError

    async def Control(self, request_iterator, context) -> None:
        """Bidirectional control stream.

        A coroutine (not an async generator) so that reads and writes can run
        as independent tasks: outbound assignments must not wait on an inbound
        message, and heartbeats must not queue behind an outbound one.
        """
        session = self._session_from_metadata(context)
        if session is None:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Register before opening a control stream.",
            )
            return
        if session.stream_open:
            await context.abort(
                grpc.StatusCode.ALREADY_EXISTS, "This session already has an open stream."
            )
            return

        from worker.executor import INLINE_LIMIT_BYTES  # noqa: PLC0415

        reader = writer = pinger = terminator = None
        session.stream_open = True
        try:
            worker = await self._activate_session_async(session)
            if worker is None:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "This worker is no longer connected; register again.",
                )
                return
            activations = getattr(self, "_last_stream_activation_at", None)
            if activations is not None:
                activations[session.worker_id] = time.monotonic()
            await session.send(
                pb.ServerMessage(
                    config=pb.ConfigUpdate(
                        heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                        max_concurrent_tasks=clamp_concurrency(
                            worker.capacity.max_concurrent_tasks
                        ),
                        inline_result_threshold_bytes=INLINE_LIMIT_BYTES,
                    )
                )
            )
            writer = asyncio.create_task(self._write_loop(session, context))
            session.egress_tasks.add(writer)
            reader = asyncio.create_task(self._read_loop(session, request_iterator))
            pinger = asyncio.create_task(self._ping_loop(session))
            terminator = asyncio.create_task(session.terminated.wait())
            done, pending = await asyncio.wait(
                {reader, writer, pinger, terminator},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Superseding a generation cancels its blocked writer immediately,
            # but the read half remains useful: a result already rendered by
            # that generation must still be allowed to commit. Keep reading
            # until the peer closes or durable revocation terminates it.
            if session.egress_fenced and not session.revoked and reader not in done:
                for task in (writer, pinger):
                    if task is not None and not task.done():
                        task.cancel()
                read_done, read_pending = await asyncio.wait(
                    {reader, terminator}, return_when=asyncio.FIRST_COMPLETED
                )
                done.update(read_done)
                pending = read_pending
            for task in pending:
                task.cancel()
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.debug("Control stream ended for %s: %s", session.worker_id, exc)
        finally:
            session.stream_open = False
            tasks = [
                task for task in (reader, writer, pinger, terminator) if task is not None
            ]
            for task in tasks:
                session.egress_tasks.discard(task)
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_session_maintenance(session)
            # A dropped stream starts grace windows; it fails nothing. The
            # worker may be seconds away from delivering a finished result.
            await self._disconnect_session_async(session)
            logger.info("Worker %s disconnected", session.worker_id)

    # ── Inbound mode ──────────────────────────────────────────────────────
    #
    # The panel dialled the node instead of the other way round. Admission was
    # an API key rather than an enrollment token, and the frames arrive on a
    # client stream rather than a servicer context — but this is still the
    # control plane, so everything between those two edges is the same code.

    def session_for(
        self, worker_id: str, *, session_token: str = ""
    ) -> Optional[_Session]:
        """Resolve the exact registration response an inbound node accepted."""
        if not session_token:
            return self._sessions.get(worker_id)
        session = self._by_token.get(session_token)
        if (
            session is None
            or session.worker_id != worker_id
            or session.session.expired()
        ):
            return None
        return session

    async def register_inbound(
        self, worker: registry.RemoteWorker, request: pb.RegisterRequest, *, address: str
    ) -> pb.RegisterResponse:
        """Register a node this panel dialled.

        The version and feature gates run here too. Skipping them for inbound
        would let an out-of-date node register cleanly and then ignore task
        inputs — the failure that returned a clone with no reference audio,
        reported as success.
        """
        refusal = self.validate_inbound_request(request)
        if refusal is not None:
            return refusal
        return await self.establish_session(worker, request, address=address)

    def validate_inbound_request(
        self, request: pb.RegisterRequest
    ) -> Optional[pb.RegisterResponse]:
        """Apply inbound compatibility gates without issuing or mutating a session."""
        if request.protocol_version_max < MIN_SUPPORTED_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine speaks an older protocol than this app supports. "
                "Update VoiceStudio there, then reconnect.",
            )
        if request.protocol_version_min > PROTOCOL_VERSION:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine is newer than this app. Update VoiceStudio here, "
                "then reconnect.",
            )
        missing_features = sorted(REQUIRED_FEATURES.difference(request.features))
        if missing_features:
            return self._refuse(
                "UPGRADE_REQUIRED",
                "That GPU machine is missing required protocol features "
                f"({', '.join(missing_features)}). Update VoiceStudio there, then "
                "reconnect; no task was run.",
            )
        if not _capability_payload_allowed(request.capabilities):
            return self._refuse(
                "CAPABILITIES_TOO_LARGE",
                "That GPU machine advertised more model metadata than this app accepts.",
            )
        return None

    async def run_inbound_stream(self, session: _Session, frames, connection) -> None:
        """Drive one dialled session until it ends.

        Mirrors ``Control``'s task set minus the writer: outbound writes to a
        servicer context, while here the connector drains the same outbox onto
        its request generator. The teardown is deliberately identical — a
        dropped stream starts grace windows and fails nothing, because the node
        may be seconds away from delivering a finished result.
        """
        if session.stream_open:
            raise RuntimeError("This session already has an open stream.")
        from worker.executor import INLINE_LIMIT_BYTES  # noqa: PLC0415

        reader = pinger = terminator = None
        session.stream_open = True
        try:
            worker = await self._activate_session_async(session)
            if worker is None:
                return
            activations = getattr(self, "_last_stream_activation_at", None)
            if activations is not None:
                activations[session.worker_id] = time.monotonic()
            session.connection = connection
            await session.send(
                pb.ServerMessage(
                    config=pb.ConfigUpdate(
                        heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
                        max_concurrent_tasks=clamp_concurrency(
                            worker.capacity.max_concurrent_tasks
                        ),
                        inline_result_threshold_bytes=INLINE_LIMIT_BYTES,
                    )
                )
            )
            confirm_registration = getattr(connection, "confirm_registration", None)
            if callable(confirm_registration):
                confirm_registration(session)
            reader = asyncio.create_task(self._read_loop(session, frames))
            pinger = asyncio.create_task(self._ping_loop(session))
            terminator = asyncio.create_task(session.terminated.wait())
            done, pending = await asyncio.wait(
                {reader, pinger, terminator}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.debug("Inbound stream ended for %s: %s", session.worker_id, exc)
        finally:
            session.stream_open = False
            session.connection = None
            tasks = [task for task in (reader, pinger, terminator) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._cancel_session_maintenance(session)
            await self._disconnect_session_async(session)
            logger.info("GPU machine %s disconnected", session.worker_id)

    def _session_from_metadata(
        self, context, *, allow_active_expired: bool = False
    ) -> Optional[_Session]:
        for key, value in context.invocation_metadata() or ():
            if key.lower() == SESSION_METADATA_KEY:
                session = self._by_token.get(value)
                if (
                    session is not None
                    and not session.revoked
                    and (
                        not session.session.expired()
                        or (
                            allow_active_expired
                            and session.activated
                            and session.stream_open
                            and self._sessions.get(session.worker_id) is session
                        )
                    )
                ):
                    return session
                return None
        return None

    async def _read_loop(self, session: _Session, request_iterator) -> None:
        async for message in request_iterator:
            kind = message.WhichOneof("payload")
            try:
                await self._handle(session, message)
            except Exception:
                if kind == "result":
                    # The worker retains an unacknowledged result, but only
                    # redelivers that pending frame during registration.  If
                    # durability failed and this read loop stayed healthy, the
                    # finished bytes would remain stranded until some unrelated
                    # network drop. End this generation so reconnect performs
                    # the at-least-once delivery the missing ACK requires.
                    raise
                # One unusable frame is not a broken session. A late or
                # out-of-order message raises LifecycleError from the domain,
                # and letting that end the reader would win the asyncio.wait in
                # Control() and disconnect a worker that is mid-render.
                logger.warning(
                    "Dropping unusable %s frame from worker %s",
                    kind,
                    session.worker_id,
                    exc_info=True,
                )

    async def _ping_loop(self, session: _Session) -> None:
        """Time a round trip periodically so the UI can show real latency."""
        while True:
            await asyncio.sleep(_PING_INTERVAL_SECONDS)
            nonce = session.next_nonce
            session.next_nonce += 1
            # Monotonic: a wall-clock jump (NTP, sleep/wake) must not turn into
            # a nonsense latency reading.
            session.pending_pings[nonce] = time.monotonic()
            # Never let unanswered pings accumulate on a wedged worker.
            if len(session.pending_pings) > 20:
                for stale in sorted(session.pending_pings)[:-5]:
                    session.pending_pings.pop(stale, None)
            await session.send(pb.ServerMessage(ping=pb.Ping(nonce=nonce)))

    async def _write_loop(self, session: _Session, context) -> None:
        while not session.revoked and not session.egress_fenced:
            message = await session.outbox.get()
            if session.revoked or session.egress_fenced:
                return
            await context.write(message)

    def _session_is_current(self, session: _Session) -> bool:
        return (
            not session.revoked
            and self._sessions.get(session.worker_id) is session
        )

    def _maintenance_finished(
        self, session: _Session, task: asyncio.Task, attribute: str
    ) -> None:
        session.maintenance_tasks.discard(task)
        if getattr(session, attribute) is task:
            setattr(session, attribute, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning(
                "Worker %s metadata maintenance failed",
                session.worker_id,
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    def _start_maintenance(
        self, session: _Session, coroutine, *, attribute: str, name: str
    ) -> None:
        task = asyncio.create_task(coroutine, name=name)
        setattr(session, attribute, task)
        session.maintenance_tasks.add(task)
        task.add_done_callback(
            lambda done: self._maintenance_finished(session, done, attribute)
        )

    async def _cancel_session_maintenance(self, session: _Session) -> None:
        tasks = list(getattr(session, "maintenance_tasks", ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _persist_touch_if_current(self, session: _Session) -> bool:
        # Activation and durable authority changes use this same lock. The
        # current-generation check and write are therefore one transaction
        # with respect to replacement, even though this runs off-loop.
        with registry.authority_guard():
            if not self._session_is_current(session):
                return False
            registry.touch(session.worker_id)
            return True

    async def _heartbeat_touch_loop(self, session: _Session) -> None:
        while session.heartbeat_touch_pending:
            if not self._session_is_current(session):
                return
            last = session.last_heartbeat_touch_at
            if last is not None:
                delay = (
                    last
                    + _LAST_SEEN_PERSIST_INTERVAL_SECONDS
                    - time.monotonic()
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            if not self._session_is_current(session):
                return
            session.heartbeat_touch_pending = False
            session.last_heartbeat_touch_at = time.monotonic()
            try:
                persisted = await to_thread_and_drain_on_cancel(
                    self._persist_touch_if_current, session
                )
            except Exception:
                # Retain one coalesced retry. A broken DB must not turn an
                # authenticated heartbeat flood into a busy retry loop.
                session.heartbeat_touch_pending = True
                logger.warning(
                    "Could not persist worker %s last-seen time",
                    session.worker_id,
                    exc_info=True,
                )
                continue
            if not persisted:
                return

    def _queue_heartbeat_touch(self, session: _Session) -> None:
        session.heartbeat_touch_pending = True
        task = session.heartbeat_touch_task
        if task is not None and not task.done():
            return
        self._start_maintenance(
            session,
            self._heartbeat_touch_loop(session),
            attribute="heartbeat_touch_task",
            name=f"worker-last-seen-{session.worker_id}",
        )

    def _persist_capabilities_if_current(
        self, session: _Session, capabilities: list[dict]
    ) -> bool:
        with registry.authority_guard():
            if not self._session_is_current(session):
                return False
            registry.update_capabilities(
                session.worker_id, capabilities=capabilities
            )
            return True

    async def _capability_update_loop(self, session: _Session) -> None:
        while session.pending_capabilities is not None:
            if not self._session_is_current(session):
                return
            last = session.last_capability_apply_at
            if last is not None:
                delay = (
                    last
                    + _CAPABILITY_UPDATE_INTERVAL_SECONDS
                    - time.monotonic()
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            if not self._session_is_current(session):
                return
            capabilities = session.pending_capabilities
            session.pending_capabilities = None
            self.pool.apply_capabilities(session.worker_id, capabilities)
            session.last_capability_apply_at = time.monotonic()
            try:
                persisted = await to_thread_and_drain_on_cancel(
                    self._persist_capabilities_if_current,
                    session,
                    capabilities,
                )
            except Exception:
                # Preserve a newer frame if one arrived while SQLite was
                # running; otherwise retry this snapshot at the normal rate.
                if session.pending_capabilities is None:
                    session.pending_capabilities = capabilities
                logger.warning(
                    "Could not persist worker %s capabilities",
                    session.worker_id,
                    exc_info=True,
                )
                continue
            if not persisted:
                return

    def _queue_capability_update(
        self, session: _Session, capabilities: list[dict]
    ) -> None:
        session.pending_capabilities = capabilities
        task = session.capability_update_task
        if task is not None and not task.done():
            return
        self._start_maintenance(
            session,
            self._capability_update_loop(session),
            attribute="capability_update_task",
            name=f"worker-capabilities-{session.worker_id}",
        )

    async def _handle(self, session: _Session, message: pb.WorkerMessage) -> None:
        """Serialize a worker's frames with its durable lifecycle handoff."""
        locks = getattr(self, "_registration_locks", None)
        if locks is None:
            return await self._handle_frame(session, message)
        lock = locks.setdefault(session.worker_id, asyncio.Lock())
        async with lock:
            return await self._handle_frame(session, message)

    async def _handle_frame(
        self, session: _Session, message: pb.WorkerMessage
    ) -> None:
        kind = message.WhichOneof("payload")
        if kind is None or session.revoked:
            return

        if kind == "goodbye":
            # A superseded inbound generation is still entitled to confirm
            # its own terminal shutdown. Do this before the current-session
            # fence below, without letting its Goodbye drain the replacement.
            connection = session.connection
            confirm_shutdown = getattr(connection, "confirm_remote_shutdown", None)
            if callable(confirm_shutdown):
                confirm_shutdown(session)

        # Register fences the previous connection immediately, but its read
        # coroutine may still deliver frames while an old result is finishing.
        # Task frames remain valid when their recorded attempt epoch owns them;
        # connection state must never bleed from that coroutine into the new
        # session for the same worker id.
        if kind in {
            "heartbeat",
            "capabilities",
            "download_progress",
            "goodbye",
            "pong",
        } and self._sessions.get(session.worker_id) is not session:
            return

        if kind == "heartbeat":
            beat = message.heartbeat
            pending_cancels = len(
                getattr(
                    session,
                    "pending_claim_reservations",
                    session.pending_claim_cancels,
                )
            )
            active_tasks = clamp_concurrency(
                beat.active_tasks + pending_cancels, allow_zero=True
            )
            available_slots = clamp_concurrency(
                max(0, beat.available_slots - pending_cancels), allow_zero=True
            )
            available_slots = min(
                available_slots, MAX_CONCURRENT_TASKS - active_tasks
            )
            self.pool.heartbeat(
                session.worker_id,
                active_tasks=active_tasks,
                available_slots=available_slots,
                resident_models=set(beat.resident_models),
                free_memory_bytes=beat.free_memory_bytes if beat.HasField("free_memory_bytes") else None,
                cpu_percent=beat.cpu_percent if beat.HasField("cpu_percent") else None,
                gpu_utilization_percent=beat.gpu_utilization_percent if beat.HasField("gpu_utilization_percent") else None,
            )
            self._queue_heartbeat_touch(session)
            return

        if kind == "capabilities":
            update = message.capabilities
            if not _capability_payload_allowed(
                update.capabilities, serialized_bytes=update.ByteSize()
            ):
                logger.warning(
                    "Dropping oversized capability update from worker %s",
                    session.worker_id,
                )
                return
            worker = self.pool.get(session.worker_id)
            fallback_backend = (
                worker.capacity.backend if worker is not None else ""
            )
            caps = [
                codec.capability_from_pb(c, fallback_backend=fallback_backend)
                for c in update.capabilities
            ]
            self._queue_capability_update(session, caps)
            return

        if kind == "download_progress":
            try:
                event = json.loads(message.download_progress.event_json)
                if not isinstance(event, dict):
                    raise ValueError("progress event is not an object")
                # The authenticated session, never the worker payload, is the
                # authoritative target identity.
                event["target"] = session.worker_id
                from services import gpu_gateway  # noqa: PLC0415
                from utils import hf_progress  # noqa: PLC0415

                gpu_gateway.record_remote_download_progress(session.worker_id, event)
                hf_progress.emit(event)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Worker %s sent malformed download progress", session.worker_id)
            return

        if kind == "goodbye":
            # A clean shutdown is a drain, not a failure.
            worker = self.pool.get(session.worker_id)
            if worker is not None:
                worker.draining = True
            return

        if kind == "pong":
            sent_at = session.pending_pings.pop(message.pong.nonce, None)
            if sent_at is not None:
                self.pool.record_latency(
                    session.worker_id, (time.monotonic() - sent_at) * 1000.0
                )
            return

        if kind == "cancel_ack":
            ref = message.cancel_ack.ref
            pending_ref = getattr(
                session, "pending_claim_cancel_refs", {}
            ).get(ref.attempt_id)
            if pending_ref is not None:
                exact = pending_ref == (ref.task_id, ref.session_epoch)
                current = self._sessions.get(session.worker_id) is session
                if exact and current:
                    worker = self.pool.get(session.worker_id)
                    reserved = ref.attempt_id in getattr(
                        session, "pending_claim_reservations", ()
                    )
                    unknown = ref.attempt_id in getattr(
                        session, "pending_unknown_claim_cancels", ()
                    )
                    if reserved:
                        self.scheduler.on_cancel_ack(
                            ref.task_id,
                            ref.attempt_id,
                            epoch=ref.session_epoch,
                        )
                        if unknown and worker is not None:
                            worker.capacity.release_unknown()
                            worker.in_flight.discard(ref.attempt_id)
                    elif worker is not None:
                        # Registration carries every peer claim into
                        # in_flight, including claims beyond the advertised
                        # ceiling that activation deliberately did not seed.
                        # Clearing that marker is safe; asking the scheduler
                        # to release it is not — it would decrement the live
                        # task that happens to share this terminal claim's
                        # model slot.
                        worker.in_flight.discard(ref.attempt_id)
                    session.pending_claim_cancels.discard(ref.attempt_id)
                    session.pending_claim_cancel_refs.pop(ref.attempt_id, None)
                    session.pending_claim_reservations.discard(ref.attempt_id)
                    session.pending_unknown_claim_cancels.discard(ref.attempt_id)
                    self._discard_terminal_partial(ref.task_id, ref.attempt_id)
                return
            if self._owns(session, ref):
                self.scheduler.on_cancel_ack(
                    ref.task_id, ref.attempt_id, epoch=ref.session_epoch
                )
                session.pending_claim_cancels.discard(ref.attempt_id)
                self._discard_terminal_partial(ref.task_id, ref.attempt_id)
            return

        if kind == "result":
            # Deliberately ahead of the epoch fence. A result is a statement
            # about a *past* epoch by construction — the work was assigned in
            # the session the reconnect just replaced — so fencing it on the
            # live epoch drops finished renders. Ownership is checked against
            # the attempt's recorded epoch instead, inside _on_result.
            await self._on_result(session, message.result)
            return

        ref = getattr(message, kind).ref
        if not self._owns(session, ref):
            return

        if kind == "accepted":
            self.scheduler.on_accepted(ref.task_id, ref.attempt_id, epoch=ref.session_epoch)
        elif kind == "rejected":
            error = codec.error_from_pb(message.rejected.error) or WorkerError(
                error_class=ErrorClass.CAPACITY,
                code="WORKER_AT_CAPACITY",
                message="The worker declined the task.",
            )
            self.scheduler.on_failed(ref.task_id, ref.attempt_id, error, epoch=ref.session_epoch)
            self._discard_terminal_partial(ref.task_id, ref.attempt_id)
        elif kind == "model_loading":
            self.scheduler.on_model_loading(
                ref.task_id,
                ref.attempt_id,
                progress=message.model_loading.progress,
                detail=message.model_loading.detail,
                epoch=ref.session_epoch,
            )
        elif kind == "started":
            self.scheduler.on_started(ref.task_id, ref.attempt_id, epoch=ref.session_epoch)
        elif kind == "progress":
            # The lease arithmetic lives in the scheduler, which owns the
            # phase budgets; the transport only reports what arrived. A
            # keepalive frame renews without claiming any work was done.
            self.scheduler.on_progress(
                ref.task_id,
                ref.attempt_id,
                progress=message.progress.progress,
                stage=message.progress.stage,
                keepalive=message.progress.keepalive,
                epoch=ref.session_epoch,
            )
        elif kind == "failed":
            error = codec.error_from_pb(message.failed.error) or WorkerError(
                error_class=ErrorClass.TRANSIENT,
                code="WORKER_FAILED",
                message="The worker reported a failure with no detail.",
            )
            self.scheduler.on_failed(ref.task_id, ref.attempt_id, error, epoch=ref.session_epoch)
            self._discard_terminal_partial(ref.task_id, ref.attempt_id)

    def _owns(self, session: _Session, ref: pb.TaskRef) -> bool:
        """May this session speak for the attempt the frame names?

        Ownership, deliberately not an epoch comparison. ``ref.session_epoch``
        is stamped once at dispatch and echoed verbatim by the worker for the
        life of the task, while ``registry.begin_session`` bumps the session
        epoch on every reconnect. Fencing task frames against the *live* epoch
        therefore discarded every liveness frame from a worker that dropped and
        resumed — so the control plane expired a task whose GPU was still
        rendering it, and swallowed the failure report when it went wrong.

        Staleness is still fenced, one layer down and per attempt:
        ``Scheduler._fenced`` compares the frame's epoch against the epoch the
        *attempt* was assigned under, which is the question that actually
        matters. What only this layer can check is that the session on the
        stream is the worker the attempt was handed to.
        """
        attempt, foreign = self._attempt_and_owner(session, ref)
        if foreign:
            # Not a routine race: unguessable ids and no listing RPC mean a
            # worker should never see another's attempt id.
            logger.warning(
                "Worker %s sent a frame for an attempt owned by another worker; dropping",
                session.worker_id,
            )
            return False
        if attempt is None:
            logger.debug("Dropping frame for unknown attempt on task %s", ref.task_id)
            return False
        return True

    async def _on_result(self, session: _Session, result: pb.TaskResult) -> None:
        """Serialize publication of one attempt across retained generations."""
        ref = result.ref
        async with self._result_publication(ref.task_id, ref.attempt_id):
            await self._on_result_owned(session, result)

    @asynccontextmanager
    async def _result_publication(self, task_id: str, attempt_id: str):
        """Own one attempt's final path through publication and cleanup."""
        key = (task_id, attempt_id)
        gate = self._result_publications.get(key)
        if gate is None:
            gate = _ResultPublicationGate(lock=asyncio.Lock())
            self._result_publications[key] = gate
        gate.users += 1
        try:
            async with gate.lock:
                yield
        finally:
            gate.users -= 1
            if gate.users == 0 and self._result_publications.get(key) is gate:
                self._result_publications.pop(key, None)

    async def _on_result_owned(
        self, session: _Session, result: pb.TaskResult
    ) -> None:
        """Commit, then acknowledge — never the other way round.

        The acknowledgement is the worker's licence to forget a finished
        render, so it is sent only once this control plane holds a durable
        verdict. Acking a frame we could not place — an attempt we have no
        record of, a task still being restored — silently destroys the only
        copy of work that succeeded.
        """
        ref = result.ref
        attempt, foreign = self._attempt_and_owner(session, ref)
        if foreign:
            # Committing here would mark the task done with no artifact, and
            # the owning worker's real delivery would then arrive as a
            # duplicate and be discarded — losing the render this whole
            # redelivery path exists to protect. No ack either: nothing was
            # placed, so nothing has earned the licence to forget.
            logger.warning(
                "Worker %s reported a result for an attempt owned by another worker; dropping",
                session.worker_id,
            )
            return
        task = self.scheduler.get(ref.task_id)
        if self._settled(False, task, ref.task_id):
            # At-least-once delivery means an ACK can be lost after the panel
            # committed. In inbound mode the node may already have served the
            # staged bytes once; fetching a duplicate directly onto the final
            # path would truncate the only committed copy before NOT_FOUND.
            if session.connection is None and result.artifacts:
                uploaded = self._contained_artifact(result.artifacts[0].artifact_id)
                if uploaded is not None and (
                    task is None or task.result_ref != uploaded
                ):
                    await self._discard_fetched_artifact(attempt, uploaded)
            await session.send(pb.ServerMessage(result_ack=pb.ResultAckMessage(ref=ref)))
            return
        payload = None
        if result.result_json:
            try:
                payload = json.loads(result.result_json)
            except ValueError:
                payload = {"raw": result.result_json}

        artifact = None
        declared_artifact = bool(result.artifacts or result.inline_payload)
        if result.artifacts:
            if session.connection is not None:
                # Inbound: the node cannot call us, so a result it "delivered"
                # is only staged on its own disk until we pull it. Without this
                # the task commits with an artifact path that was never
                # written, and the job fails with "finished the job but its
                # audio did not arrive" — which is exactly what it did on
                # hardware before this existed.
                artifact = await self._fetch_inbound_artifact(
                    session, attempt, result.artifacts[0]
                )
            else:
                artifact = self._contained_artifact(result.artifacts[0].artifact_id)
        # No attempt record, no place to put it: the payload of a task we
        # cannot identify has nothing to be attached to, and the worker keeps
        # its copy because nothing below will acknowledge it.
        if session.revoked:
            await self._discard_fetched_artifact(attempt, artifact)
            return
        if result.inline_payload and attempt is not None:
            artifact = await self._store_inline(
                attempt, bytes(result.inline_payload)
            )
            if session.revoked:
                await self._discard_fetched_artifact(attempt, artifact)
                return
        if declared_artifact and artifact is None:
            # The artifact reference is a promise that bytes exist. Committing
            # without them would acknowledge the node's only copy and turn a
            # recoverable delivery failure into a permanently incomplete
            # successful task. A non-empty inline payload above is the only
            # valid fallback.
            logger.warning(
                "Withholding result acknowledgement for task %s because its "
                "declared artifact was not available",
                ref.task_id,
            )
            return

        # Returns only after the commit is durable, which is what makes the
        # acknowledgement below safe to send. The epoch on the wire is the one
        # the attempt was assigned under, and that is what the scheduler
        # compares against — not whichever session happens to be live now.
        committed, task = self.scheduler.on_result(
            ref.task_id,
            ref.attempt_id,
            result_ref=artifact,
            result=payload,
            epoch=ref.session_epoch,
        )
        settled = self._settled(committed, task, ref.task_id)
        if (
            not committed
            and artifact is not None
            and (task is None or task.result_ref != artifact)
        ):
            # Fetch/upload can finish after cancellation or after a sibling won.
            # The ACK lets the node/worker delete its source copy, so remove the
            # attempt-scoped local loser and its byte budget at the same verdict.
            await self._discard_fetched_artifact(attempt, artifact)
        if settled:
            await session.send(pb.ServerMessage(result_ack=pb.ResultAckMessage(ref=ref)))

    def _settled(self, committed: bool, task: Optional[Task], task_id: str) -> bool:
        """May the worker drop its copy of this result?

        Only against a durable verdict: this commit, an earlier one that won
        the race, or — after a restart that never reloaded the task — the fact
        of completion on disk. Anything else is redelivered, which costs one
        frame per reconnect and is the only thing standing between a dropped
        message and a lost render.
        """
        if committed:
            return True
        if task is not None:
            # FAILED/TIMEOUT describe what the control plane inferred before
            # the late bytes arrived. Lifecycle deliberately lets that proof
            # of success win. Cancellation is authoritative; completion is
            # already durable.
            return task.state in {TaskState.COMPLETED, TaskState.CANCELLED}
        try:
            return task_store.is_committed(task_id)
        except Exception:
            logger.debug("Could not check the committed state of %s", task_id, exc_info=True)
            return False

    def _attempt_for(self, session: _Session, ref) -> Optional[Attempt]:
        """This control plane's own record of the attempt a frame names.

        Every artifact path is minted from what this returns rather than from
        the frame, because the ids on the wire are remote input: ``os.path.join``
        silently discards its prefix the moment one of them is absolute.
        """
        attempt, _foreign = self._attempt_and_owner(session, ref)
        return attempt

    def _attempt_and_owner(self, session: _Session, ref) -> tuple[Optional[Attempt], bool]:
        """``(attempt, foreign)`` — the attempt, and whether another worker owns it.

        The two None cases must not be collapsed. "No record" is ordinary and
        recoverable: a task not yet restored after a restart still has a
        durable verdict on disk, so a result naming it is redelivered rather
        than lost. "Another worker's attempt" is neither — accepting it lets a
        frame from the wrong worker commit the task, after which the owning
        worker's real delivery arrives as a duplicate and its audio is
        discarded. Returning one None for both is how that got through.
        """
        task = self.scheduler.get(ref.task_id)
        if task is None:
            return None, False
        attempt = task.get_attempt(ref.attempt_id)
        if attempt is None:
            return None, False
        if attempt.worker_id != session.worker_id:
            return None, True
        return attempt, False

    def _artifact_path(self, task_id: str, attempt_id: str) -> Optional[str]:
        """Resolve attempt-scoped storage for one result without filesystem I/O.

        Attempt-scoped, not task-scoped: two attempts of one task must never
        share a path, or a superseded straggler overwrites the result that won.
        """
        try:
            relative = os.path.join(safe_filename(task_id), f"{safe_filename(attempt_id)}.bin")
            path = resolve_within(self.artifact_dir, relative)
        except UnsafePath:
            logger.warning("Refusing to store a result outside the artifact directory")
            return None
        return str(path)

    async def _fetch_inbound_artifact(
        self, session: _Session, attempt: Optional[Attempt], ref: pb.ArtifactRef
    ) -> Optional[str]:
        """Pull a staged result down from a node this control plane dialled.

        Returns the local path, or None — and None is not a silent loss: the
        commit below records no artifact, the task fails with a message naming
        the machine, and the node keeps its copy because nothing acknowledges
        a result we could not fetch.
        """
        if attempt is None:
            return None
        path = self._artifact_path(attempt.task_id, attempt.attempt_id)
        if path is None:
            return None
        declared = int(ref.size_bytes)
        prior = self._artifact_bytes.get(attempt.task_id, {}).get(
            attempt.attempt_id, 0
        )
        spent = max(0, self._artifact_bytes_spent(attempt.task_id) - prior)
        remaining = MAX_TASK_ARTIFACT_BYTES - spent
        if declared > MAX_ARTIFACT_BYTES or declared > remaining or remaining <= 0:
            logger.warning(
                "Refusing an oversized result for task %s from %s",
                attempt.task_id,
                session.worker_id,
            )
            return None
        limit = min(MAX_ARTIFACT_BYTES, remaining)
        partial = f"{path}.{uuid.uuid4().hex}.part"
        path_exists = await to_thread_and_drain_on_cancel(os.path.isfile, path)
        if ref.sha256 and path_exists:
            matches = await to_thread_and_drain_on_cancel(
                self._artifact_matches, path, declared, ref.sha256
            )
            if session.revoked:
                return None
            if matches:
                # A prior delivery may have renamed successfully but failed
                # its directory barrier and therefore received no ACK. Retry
                # that barrier before licensing the node to drop its copy.
                try:
                    await to_thread_and_drain_on_cancel(
                        _make_existing_artifact_durable, path
                    )
                except OSError as exc:
                    logger.warning(
                        "Could not make the result for task %s durable: %s",
                        attempt.task_id,
                        exc,
                    )
                    return None
                if session.revoked:
                    return None
                received = await to_thread_and_drain_on_cancel(os.path.getsize, path)
                self._record_artifact_bytes(attempt, received)
                return path
        reservation_size = declared or limit
        reservation_owner = object()
        if not await self._reserve_artifact_capacity(
            attempt, path, reservation_size, owner=reservation_owner
        ):
            logger.warning(
                "Refusing a result for task %s because retained artifact storage is full",
                attempt.task_id,
            )
            return None
        published = False
        try:
            await to_thread_and_drain_on_cancel(
                _durable_makedirs, os.path.dirname(path) or "."
            )
            if session.revoked:
                return None
            await session.connection.fetch_result(ref, partial, max_bytes=limit)
            received = await to_thread_and_drain_on_cancel(
                os.path.getsize, partial
            )
            if declared and received != declared:
                raise RuntimeError(
                    f"the result contained {received} bytes, expected {declared}"
                )
            if session.revoked:
                await to_thread_and_drain_on_cancel(_remove_quietly, partial)
                return None
            await to_thread_and_drain_on_cancel(_durable_replace, partial, path)
            published = True
            if session.revoked:
                await to_thread_and_drain_on_cancel(_remove_quietly, path)
                self._stored_artifacts.pop(path, None)
                return None
        except asyncio.CancelledError:
            await to_thread_and_drain_on_cancel(_remove_quietly, partial)
            if published:
                await to_thread_and_drain_on_cancel(_remove_quietly, path)
                self._stored_artifacts.pop(path, None)
            raise
        except Exception as exc:
            await to_thread_and_drain_on_cancel(_remove_quietly, partial)
            logger.warning(
                "Could not fetch the result for task %s from %s: %s",
                attempt.task_id,
                session.worker_id,
                exc,
            )
            return None
        finally:
            self._release_artifact_reservation(path, owner=reservation_owner)
        self._record_artifact_bytes(attempt, received)
        return path

    @staticmethod
    def _artifact_matches(path: str, declared: int, sha256: str) -> bool:
        try:
            if declared and os.path.getsize(path) != declared:
                return False
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(_REHASH_BLOCK_BYTES), b""):
                    digest.update(block)
            return digest.hexdigest() == sha256.strip().lower()
        except OSError:
            return False

    async def _discard_fetched_artifact(
        self, attempt: Optional[Attempt], artifact: Optional[str]
    ) -> None:
        if artifact is not None:
            removed = await to_thread_and_drain_on_cancel(
                _remove_quietly, artifact
            )
            if removed:
                self._stored_artifacts.pop(artifact, None)
                # Any reservation on this path belongs to a distinct transfer
                # generation and must be released by that owner, not by cleanup
                # of the already-published loser.
        if attempt is None:
            return
        attempts = self._artifact_bytes.get(attempt.task_id)
        if attempts is None:
            return
        attempts.pop(attempt.attempt_id, None)
        if not attempts:
            self._artifact_bytes.pop(attempt.task_id, None)

    def _contained_artifact(self, artifact_id: str) -> Optional[str]:
        """An artifact the worker names is only ever a reference into our own
        store, and is resolved as one."""
        if not artifact_id:
            return None
        try:
            path = str(resolve_within(self.artifact_dir, artifact_id))
        except UnsafePath:
            logger.warning("Refusing an artifact reference outside the artifact directory")
            return None
        return path if os.path.isfile(path) else None

    async def _store_inline(
        self, attempt: Attempt, payload: bytes
    ) -> Optional[str]:
        """Write a small inline result to attempt-scoped storage."""
        path = self._artifact_path(attempt.task_id, attempt.attempt_id)
        if path is None:
            return None
        reservation_owner = object()
        if not await self._reserve_artifact_capacity(
            attempt, path, len(payload), owner=reservation_owner
        ):
            logger.warning(
                "Refusing inline result for task %s because retained artifact storage is full",
                attempt.task_id,
            )
            return None
        try:
            await to_thread_and_drain_on_cancel(
                _write_inline_artifact, path, payload
            )
        except asyncio.CancelledError:
            await to_thread_and_drain_on_cancel(_remove_quietly, path)
            self._release_artifact_reservation(path, owner=reservation_owner)
            self._stored_artifacts.pop(path, None)
            raise
        except BaseException:
            self._release_artifact_reservation(path, owner=reservation_owner)
            raise
        self._record_artifact_bytes(attempt, len(payload))
        self._release_artifact_reservation(path, owner=reservation_owner)
        return path

    # ── Dispatch out ──────────────────────────────────────────────────────

    async def dispatch(self, assignment) -> bool:
        """Send an assignment to its worker. False if the stream is gone."""
        session = self._sessions.get(assignment.worker.worker_id)
        if session is None or not session.activated:
            return False
        build_message = functools.partial(
            codec.assignment_to_pb,
            assignment.task,
            assignment.attempt,
            assignment.deadlines,
            artifact_root=self.artifact_dir,
        )
        message = await to_thread_and_drain_on_cancel(build_message)
        if session.connection is not None and message.inputs:
            # Inbound: the node cannot pull, so its inputs have to be here
            # BEFORE the assignment is. The executor asks for them as soon as
            # it starts, and an assignment that overtakes its own reference
            # audio fails on a file that is merely late.
            try:
                if not await self._push_inbound_inputs_until_terminated(
                    session, message
                ):
                    return False
            except Exception as exc:
                logger.warning(
                    "Could not send task inputs to %s: %s", session.worker_id, exc
                )
                return False
        # Uploading an inbound input yields to the event loop. In that gap the
        # session can be replaced/revoked, or the attempt can be cancelled or
        # swept. Revalidate the exact generation, then enqueue without another
        # yield so no stale assignment follows its own cancellation.
        with registry.authority_guard():
            live = self.pool.get(assignment.worker.worker_id)
            if (
                self._sessions.get(assignment.worker.worker_id) is not session
                or not session.activated
                or session.revoked
                or live is not assignment.worker
                or not live.record.schedulable
                or live.draining
                or live.registration_pending
                or assignment.task.active_attempt is not assignment.attempt
            ):
                return False
            session.outbox.put_nowait(pb.ServerMessage(assignment=message))
            return True

    async def _push_inbound_inputs_until_terminated(self, session, message) -> bool:
        """Stop an in-flight user-input upload in the revocation turn."""
        upload = asyncio.create_task(self._push_inbound_inputs(session, message))
        session.egress_tasks.add(upload)
        terminated = asyncio.create_task(session.terminated.wait())
        try:
            done, _pending = await asyncio.wait(
                {upload, terminated}, return_when=asyncio.FIRST_COMPLETED
            )
            if terminated in done or session.revoked or session.egress_fenced:
                return False
            upload.result()
            return True
        finally:
            session.egress_tasks.discard(upload)
            for task in (upload, terminated):
                if not task.done():
                    task.cancel()
            await asyncio.gather(upload, terminated, return_exceptions=True)

    async def _push_inbound_inputs(self, session: _Session, message) -> None:
        """Upload every declared input, replacing each ref with what landed."""
        pushed = []
        for ref in message.inputs:
            local = self._contained_artifact(ref.artifact_id)
            if local is None:
                raise ValueError("task input is not inside the artifact directory")
            pushed.append(await session.connection.push_input(ref, local))
        del message.inputs[:]
        message.inputs.extend(pushed)

    async def cancel(self, worker_id: str, task_id: str, attempt_id: str, epoch: int) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        await session.send(
            pb.ServerMessage(cancel=pb.TaskCancel(ref=codec.task_ref(task_id, attempt_id, epoch)))
        )
        return True

    async def drain(self, worker_id: str, *, deadline_seconds: int = 300) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        worker = self.pool.get(worker_id)
        if worker is not None:
            worker.draining = True
        await session.send(
            pb.ServerMessage(drain=pb.Drain(deadline_seconds=deadline_seconds))
        )
        return True

    async def prewarm(
        self, worker_id: str, *, engine: str, model_id: str = "", download_if_missing: bool = False
    ) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        await session.send(pb.ServerMessage(prewarm=pb.PrewarmRequest(
            engine=engine, model_id=model_id, download_if_missing=download_if_missing,
        )))
        return True

    async def cancel_model_install(self, worker_id: str, *, model_id: str) -> bool:
        session = self._sessions.get(worker_id)
        if session is None:
            return False
        await session.send(
            pb.ServerMessage(
                model_install_cancel=pb.ModelInstallCancelRequest(model_id=model_id)
            )
        )
        return True

    def revoke_worker_sessions(self, worker_id: str) -> int:
        """Invalidate every transport generation for a durably revoked worker."""
        sessions = {
            session
            for session in self._by_token.values()
            if session.worker_id == worker_id
        }
        current = self._sessions.pop(worker_id, None)
        pending = self._pending_sessions.pop(worker_id, None)
        transfers = getattr(self, "_transfer_sessions", {}).pop(worker_id, {})
        sessions.update(transfers)
        if current is not None:
            sessions.add(current)
        if pending is not None:
            sessions.add(pending)
        for token, session in list(self._by_token.items()):
            if session.worker_id == worker_id:
                self._by_token.pop(token, None)
        for session in sessions:
            session.revoked = True
            while True:
                try:
                    session.outbox.get_nowait()
                except asyncio.QueueEmpty:
                    break
            connection = session.connection
            revoke_connection = getattr(connection, "revoke_session", None)
            if callable(revoke_connection):
                revoke_connection(session)
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            for task in list(session.egress_tasks):
                if task is not current:
                    task.cancel()
            for task in list(session.maintenance_tasks):
                if task is not current:
                    task.cancel()
            session.terminated.set()
            if session.open_timeout is not None:
                session.open_timeout.cancel()
                session.open_timeout = None
        for upload in list(self._partial_uploads.get(worker_id, {}).values()):
            if self._active_uploads.get(upload.part) is upload:
                upload._discarded = True
            else:
                upload.discard()
        self._partial_uploads.pop(worker_id, None)
        return len(sessions)

    def _remember_partial_upload(self, upload: _Upload) -> None:
        expiry = self._partial_upload_expiries.pop(upload.part, None)
        if expiry is not None:
            expiry.cancel()
        uploads = self._partial_uploads.setdefault(upload.session.worker_id, {})
        previous = uploads.get(upload.part)
        if previous is not None and previous is not upload:
            # Resume transfers adopt the same partial generation. Its retained
            # byte claim moves to the new RPC instead of leaking one owner per
            # reconnect forever.
            self._release_artifact_reservation(
                previous.final, owner=previous.reservation_owner
            )
        uploads[upload.part] = upload

    def _forget_partial_upload(self, upload: _Upload) -> None:
        uploads = self._partial_uploads.get(upload.session.worker_id)
        if uploads is None or uploads.get(upload.part) is not upload:
            return
        uploads.pop(upload.part, None)
        expiry = self._partial_upload_expiries.pop(upload.part, None)
        if expiry is not None:
            expiry.cancel()
        if not uploads:
            self._partial_uploads.pop(upload.session.worker_id, None)

    def _finish_upload(self, upload: _Upload) -> None:
        self._forget_partial_upload(upload)
        self._deactivate_upload(upload)
        self._release_artifact_reservation(
            upload.final, owner=upload.reservation_owner
        )
        try:
            held = max(0, int(os.path.getsize(upload.part)))
        except OSError:
            held = 0
        if held:
            self._stored_artifacts[upload.part] = _StoredArtifact(
                worker_id=upload.attempt.worker_id, size_bytes=held
            )
            self._orphaned_upload_parts.add(upload.part)

    def _deactivate_upload(self, upload: _Upload) -> None:
        if self._active_uploads.get(upload.part) is upload:
            self._active_uploads.pop(upload.part, None)

    def _expire_partial_upload(self, upload: _Upload) -> None:
        self._partial_upload_expiries.pop(upload.part, None)
        uploads = self._partial_uploads.get(upload.session.worker_id)
        if (
            uploads is not None
            and uploads.get(upload.part) is upload
            and self._active_uploads.get(upload.part) is not upload
        ):
            upload.discard()

    def _schedule_partial_expiry(self, upload: _Upload) -> None:
        uploads = self._partial_uploads.get(upload.session.worker_id)
        if uploads is None or uploads.get(upload.part) is not upload:
            return
        old = self._partial_upload_expiries.pop(upload.part, None)
        if old is not None:
            old.cancel()
        self._partial_upload_expiries[upload.part] = (
            asyncio.get_running_loop().call_later(
                _PARTIAL_UPLOAD_TTL_SECONDS,
                self._expire_partial_upload,
                upload,
            )
        )

    def _discard_terminal_partial(self, task_id: str, attempt_id: str) -> None:
        task = self.scheduler.get(task_id)
        attempt = task.get_attempt(attempt_id) if task is not None else None
        if attempt is None or not attempt.state.terminal:
            return
        for uploads in list(self._partial_uploads.values()):
            for upload in list(uploads.values()):
                if upload.attempt is attempt:
                    if self._active_uploads.get(upload.part) is upload:
                        upload._discarded = True
                    else:
                        upload.discard()

    def _retain_transfer_session(self, session: _Session) -> None:
        transfers = getattr(self, "_transfer_sessions", None)
        if transfers is None:
            transfers = self._transfer_sessions = {}
        generations = transfers.setdefault(session.worker_id, {})
        generations[session] = generations.get(session, 0) + 1

    def _release_transfer_session(self, session: _Session) -> None:
        transfers = getattr(self, "_transfer_sessions", None)
        if not transfers:
            return
        generations = transfers.get(session.worker_id)
        if not generations or session not in generations:
            return
        remaining = generations[session] - 1
        if remaining:
            generations[session] = remaining
        else:
            generations.pop(session, None)
        if not generations:
            transfers.pop(session.worker_id, None)

    # ── Artifact transfer ─────────────────────────────────────────────────

    async def UploadResult(self, request_iterator, context) -> pb.ResultAck:
        """Receive a result artifact in chunks, resumably.

        Nothing the sender says is taken on trust. Every chunk must name the
        exact offset this control plane already holds, the total is bounded per
        artifact and per task, the digest declared in ``ArtifactRef.sha256``
        must match the bytes that arrived, and only an explicit ``last`` chunk
        renames the ``.part`` file into place. An iterator that simply stops
        leaves the partial file for a resume and commits nothing — it used to
        commit, which is how a truncated transfer became a finished render.

        Resume is real, and this is where it is reported. The call is
        client-streaming with a single terminal ack, so there is no mid-stream
        channel for "bytes already held": a chunk whose offset disagrees with
        what we hold is answered with ``committed=False`` and
        ``bytes_received`` set to the authoritative held count, and the worker
        restarts from there. That ack is the bytes-held probe the proto
        promised and no RPC provided.
        """
        upload: Optional[_Upload] = None
        retained = self._session_from_metadata(
            context, allow_active_expired=True
        )
        if retained is not None:
            self._retain_transfer_session(retained)
        iterator = request_iterator.__aiter__()
        try:
            while True:
                try:
                    chunk = await self._next_upload_chunk(iterator, retained)
                except StopAsyncIteration:
                    break
                except _RevokedTransfer:
                    if upload is not None:
                        await upload.discard_async()
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This worker was revoked.",
                    )
                    return _upload_refused(
                        "UNAUTHENTICATED", "This worker was revoked."
                    )
                if retained is not None and retained.revoked:
                    if upload is not None:
                        await upload.discard_async()
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This worker was revoked.",
                    )
                    return _upload_refused(
                        "UNAUTHENTICATED", "This worker was revoked."
                    )
                if upload is not None and upload.session.revoked:
                    await upload.discard_async()
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This worker was revoked.",
                    )
                    return _upload_refused(
                        "UNAUTHENTICATED", "This worker was revoked."
                    )
                if upload is None:
                    upload, refusal, opened_session = await self._open_upload(
                        context, chunk, retained_session=retained
                    )
                    if opened_session is not None and opened_session is not retained:
                        if retained is not None:
                            self._release_transfer_session(retained)
                        retained = opened_session
                    if upload is None:
                        if refusal is None:
                            await context.abort(
                                grpc.StatusCode.UNAUTHENTICATED,
                                "Unknown or expired session.",
                            )
                            return _upload_refused(
                                "UNAUTHENTICATED", "Unknown or expired session."
                            )
                        return refusal
                refusal = await upload.write(chunk)
                if refusal is not None:
                    return refusal
                self._renew_upload_lease(upload.attempt)
                if chunk.last:
                    async with self._result_publication(
                        upload.attempt.task_id, upload.attempt.attempt_id
                    ):
                        return await upload.commit()
        finally:
            try:
                if upload is not None:
                    await upload.close_async()
            finally:
                if upload is not None:
                    self._deactivate_upload(upload)
                    self._schedule_partial_expiry(upload)
                if retained is not None:
                    self._release_transfer_session(retained)
        if upload is None:
            return _upload_refused("EMPTY_UPLOAD", "The upload carried no chunks.")
        return upload.incomplete()

    @staticmethod
    async def _next_upload_chunk(request_iterator, session: Optional[_Session]):
        """Wait for a chunk or revoke, whichever publishes first."""
        if session is None:
            return await anext(request_iterator)
        chunk = asyncio.ensure_future(anext(request_iterator))
        terminated = asyncio.create_task(session.terminated.wait())
        try:
            done, _pending = await asyncio.wait(
                {chunk, terminated}, return_when=asyncio.FIRST_COMPLETED
            )
            if terminated in done:
                raise _RevokedTransfer
            return chunk.result()
        finally:
            for task in (chunk, terminated):
                if not task.done():
                    task.cancel()
            await asyncio.gather(chunk, terminated, return_exceptions=True)

    async def _open_upload(
        self, context, chunk, *, retained_session: Optional[_Session] = None
    ) -> tuple[Optional[_Upload], Optional[pb.ResultAck], Optional[_Session]]:
        """Authorise the first chunk and open its destination.

        ``(None, None, None)`` means unauthenticated — the one failure answered with
        a gRPC abort rather than an ack, because a caller we cannot identify
        has no business being told anything about the task it named.
        """
        ref = chunk.ref
        session = self._session_for(context, ref) or self._session_for(context, chunk)
        if session is None:
            return None, None, None
        # Same rule as an inline result: the destination is minted from our own
        # attempt record, never assembled from the ids in the request.
        attempt = self._attempt_for(session, ref)
        final = (
            self._artifact_path(attempt.task_id, attempt.attempt_id)
            if attempt is not None
            else None
        )
        if attempt is None or final is None:
            return None, _upload_refused(
                "UNKNOWN_ATTEMPT", "No such attempt is running for this worker."
            ), None
        if not ref.sha256:
            # Refused before a single byte is accepted. An upload with no
            # declared digest cannot be verified, and committing it would make
            # the whole verification path decorative.
            return None, _upload_refused(
                "DIGEST_REQUIRED", "Declare ArtifactRef.sha256 before uploading a result."
            ), None
        declared = int(ref.size_bytes)
        if declared > MAX_ARTIFACT_BYTES:
            return None, _upload_refused(
                "ARTIFACT_TOO_LARGE", "This result is larger than the control plane accepts."
            ), None
        # A declared size narrows the cap; an undeclared one gets the ceiling.
        limit = declared or MAX_ARTIFACT_BYTES
        if self._artifact_bytes_spent(attempt.task_id) + limit > MAX_TASK_ARTIFACT_BYTES:
            return None, _upload_refused(
                "TASK_BUDGET_EXCEEDED",
                "This task has delivered as many artifact bytes as it is allowed.",
            ), None
        part = f"{final}.part"
        active = self._active_uploads.get(part)
        if active is not None:
            return None, _upload_refused(
                "UPLOAD_IN_PROGRESS",
                "Another transfer already owns this attempt; retry after it finishes.",
                bytes_received=active.held_bytes(),
                error_class=pb.ERROR_CLASS_TRANSIENT,
            ), None
        reservation_owner = object()
        if not await self._reserve_artifact_capacity(
            attempt, final, limit, owner=reservation_owner
        ):
            return None, _upload_refused(
                "STORAGE_QUOTA_EXCEEDED",
                "This control plane has filled its retained-result allowance.",
                error_class=pb.ERROR_CLASS_TRANSIENT,
            ), None
        try:
            await to_thread_and_drain_on_cancel(
                _durable_makedirs, os.path.dirname(final) or "."
            )
        except BaseException:
            self._release_artifact_reservation(
                final, owner=reservation_owner
            )
            raise
        if session.revoked:
            self._release_artifact_reservation(
                final, owner=reservation_owner
            )
            return None, None, session
        # Directory creation yielded. A concurrent RPC may have claimed this
        # attempt while its barrier ran, so admission must be repeated.
        active = self._active_uploads.get(part)
        if active is not None:
            self._release_artifact_reservation(
                final, owner=reservation_owner
            )
            return None, _upload_refused(
                "UPLOAD_IN_PROGRESS",
                "Another transfer already owns this attempt; retry after it finishes.",
                bytes_received=active.held_bytes(),
                error_class=pb.ERROR_CLASS_TRANSIENT,
            ), session
        newly_retained = session is not retained_session
        if newly_retained:
            self._retain_transfer_session(session)
        # Last, and only once the request is known to be one we would accept:
        # a refusal must not leave a task parked in RESULT_UPLOADING with no
        # transfer under way. Cancelled, timed out, or already committed by
        # another attempt all fail here, because accepting these bytes would
        # overwrite the artifact of whichever attempt actually won.
        upload: Optional[_Upload] = None
        try:
            if not self._begin_uploading(attempt):
                task = self.scheduler.get(attempt.task_id)
                self._release_artifact_reservation(
                    final, owner=reservation_owner
                )
                return None, _upload_refused(
                    "ATTEMPT_NOT_LIVE",
                    "This attempt is no longer accepting a result "
                    f"(task={getattr(getattr(task, 'state', None), 'value', 'missing')}, "
                    f"attempt={attempt.state.value}, "
                    f"error={getattr(attempt.error, 'code', None)}).",
                ), session
            upload = _Upload(
                session=session,
                attempt=attempt,
                artifact_id=self._artifact_id_for(final),
                final=final,
                limit=limit,
                declared_size=declared,
                declared_sha256=ref.sha256,
                reservation_owner=reservation_owner,
                on_commit=self._record_artifact_bytes,
                on_finished=self._finish_upload,
            )
            self._active_uploads[upload.part] = upload
            self._remember_partial_upload(upload)
            refusal = await upload.start(int(chunk.offset))
            # Resuming re-hashes existing bytes off-thread. Revocation can publish
            # while that await is in flight, so the authorization made above is no
            # longer sufficient once the destination has actually been opened.
            if session.revoked:
                await upload.discard_async()
                return None, None, session
            if refusal is not None:
                await upload.close_async()
                self._deactivate_upload(upload)
                held = await to_thread_and_drain_on_cancel(upload.held_bytes)
                if held == 0 or held > upload.limit:
                    await upload.discard_async()
                else:
                    # _remember_partial_upload cancelled the previous lease
                    # before probing the resume offset. A refused probe still
                    # owns bytes, so it needs a fresh expiry or this one call
                    # turns a bounded partial into a permanent file.
                    self._schedule_partial_expiry(upload)
                return None, refusal, session
            return upload, None, session
        except BaseException:
            if upload is not None:
                await upload.discard_async()
            else:
                self._release_artifact_reservation(
                    final, owner=reservation_owner
                )
            if newly_retained:
                self._release_transfer_session(session)
            raise

    def _begin_uploading(self, attempt: Attempt) -> bool:
        """Put the task into RESULT_UPLOADING for the length of the transfer.

        Without this transition ``Task.uploading`` has no callers, so
        RESULT_UPLOADING is unreachable and the entire delivery of a large
        result runs under the 120 s progress lease while the 900 s
        ``result_delivery_seconds`` budget sits unused because nothing ever
        entered the state it applies to.
        """
        task = self.scheduler.get(attempt.task_id)
        if task is None or task.state.terminal or attempt.state.terminal:
            logger.warning(
                "Refusing result upload admission for task %s attempt %s "
                "(task=%s, attempt=%s, error=%s)",
                attempt.task_id,
                attempt.attempt_id,
                getattr(getattr(task, "state", None), "value", "missing"),
                attempt.state.value,
                getattr(attempt.error, "code", None),
            )
            return False
        try:
            task.uploading(attempt.attempt_id, session_epoch=attempt.session_epoch)
        except Exception:
            logger.debug(
                "Refusing an upload for attempt %s: not in a state that can deliver",
                attempt.attempt_id,
                exc_info=True,
            )
            return False
        self._renew_upload_lease(attempt)
        return True

    def _renew_upload_lease(self, attempt: Attempt) -> None:
        """Renew the lease from upload progress, under the delivery budget.

        Routed through the scheduler rather than computed here: it owns the
        phase budgets, and ``on_progress(keepalive=True)`` already caps a
        renewal at the current phase's ceiling — which, now that the task is in
        RESULT_UPLOADING, is ``result_delivery_seconds``. A keepalive and not a
        progress frame: bytes on the wire prove the worker is alive, not that
        the render advanced, and overwriting a finished 100% with a transfer's
        zero is a UI that goes backwards.
        """
        self.scheduler.on_progress(
            attempt.task_id,
            attempt.attempt_id,
            progress=0.0,
            keepalive=True,
            epoch=attempt.session_epoch,
        )

    def _artifact_bytes_spent(self, task_id: str) -> int:
        """How much of this task's artifact budget is already committed."""
        for known in list(self._artifact_bytes):
            task = self.scheduler.get(known)
            if task is None or task.state.terminal:
                self._artifact_bytes.pop(known, None)
        return sum(self._artifact_bytes.get(task_id, {}).values())

    async def _reserve_artifact_capacity(
        self,
        attempt: Attempt,
        path: str,
        size_bytes: int,
        *,
        owner: Optional[object] = None,
    ) -> bool:
        """Reserve retained-result capacity before accepting any new bytes."""
        wanted = max(0, int(size_bytes))
        claim = owner if owner is not None else object()
        async with self._artifact_capacity_lock:
            known = dict(self._stored_artifacts)
            known_paths = tuple(known)
            if known_paths:
                missing = await to_thread_and_drain_on_cancel(
                    _missing_artifact_paths, known_paths
                )
                for missing_path in missing:
                    if self._stored_artifacts.get(missing_path) is known[missing_path]:
                        self._stored_artifacts.pop(missing_path, None)

            existing = self._artifact_reservations.get(path)
            if existing is not None:
                if existing.worker_id != attempt.worker_id:
                    return False
                claimed_sizes = dict(existing.owners)
                claimed_sizes[claim] = wanted
                reserved_size = max(claimed_sizes.values(), default=0)
                additional = max(0, reserved_size - existing.size_bytes)
            else:
                claimed_sizes = {claim: wanted}
                reserved_size = wanted
                additional = wanted

            worker_used = sum(
                artifact.size_bytes
                for artifact in self._stored_artifacts.values()
                if artifact.worker_id == attempt.worker_id
            ) + sum(
                reservation.size_bytes
                for reservation in self._artifact_reservations.values()
                if reservation.worker_id == attempt.worker_id
            )
            total_used = sum(
                artifact.size_bytes for artifact in self._stored_artifacts.values()
            ) + sum(
                reservation.size_bytes
                for reservation in self._artifact_reservations.values()
            )
            if (
                worker_used + additional
                > self._max_stored_artifact_bytes_per_worker
                or total_used + additional > self._max_stored_artifact_bytes_total
            ):
                return False
            if existing is None:
                self._artifact_reservations[path] = _ArtifactReservation(
                    worker_id=attempt.worker_id,
                    size_bytes=reserved_size,
                    owners=claimed_sizes,
                )
            else:
                existing.owners = claimed_sizes
                existing.size_bytes = reserved_size
            return True

    def _release_artifact_reservation(
        self, path: str, *, owner: Optional[object] = None
    ) -> None:
        reservation = self._artifact_reservations.get(path)
        if reservation is None:
            return
        if owner is None:
            self._artifact_reservations.pop(path, None)
            return
        reservation.owners.pop(owner, None)
        if not reservation.owners:
            self._artifact_reservations.pop(path, None)
            return
        reservation.size_bytes = max(reservation.owners.values())

    def _record_stored_artifact(
        self, attempt: Attempt, path: str, count: int
    ) -> None:
        # Publishing one generation must not release another RPC's claim on the
        # same attempt path. The publishing owner releases itself explicitly;
        # contenders admitted before it became active still need their reserved
        # headroom until they either publish or withdraw.
        self._stored_artifacts[path] = _StoredArtifact(
            worker_id=attempt.worker_id, size_bytes=max(0, int(count))
        )

    def _record_artifact_bytes(self, attempt: Attempt, count: int) -> None:
        self._artifact_bytes.setdefault(attempt.task_id, {})[attempt.attempt_id] = count
        path = self._artifact_path(attempt.task_id, attempt.attempt_id)
        if path is not None:
            self._record_stored_artifact(attempt, path, count)

    def _artifact_id_for(self, path: str) -> str:
        """The store-relative id a worker may name this artifact by.

        Relative, not the absolute path it lives at: the id travels back on the
        control stream as ``TaskResult.artifacts[0].artifact_id`` and is
        re-resolved against the artifact directory, and handing a remote peer
        our filesystem layout buys nothing that resolution does not already do.
        """
        try:
            return os.path.relpath(path, self.artifact_dir)
        except ValueError:  # different drive on Windows; cannot happen, but
            return path

    async def DownloadArtifact(self, request: pb.ArtifactRef, context):
        """Stream a task input (reference audio, source video) to a worker.

        Bound to the attempt that needs the input, not merely to a live
        session. Until artifacts started flowing inwards, ``inputs`` carried
        nothing and "any authenticated worker may read any staged file" was a
        distinction without a difference; from here those files are the user's
        own reference audio, staged from their voice library.
        """
        session = self._session_for(context, request)
        if session is None:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown or expired session.")
            return
        self._retain_transfer_session(session)
        handler = asyncio.current_task()
        if handler is not None:
            session.egress_tasks.add(handler)
        chunks: asyncio.Queue[pb.ArtifactChunk] = asyncio.Queue(maxsize=1)
        producer = asyncio.create_task(
            self._produce_download(session, request, context, chunks)
        )
        session.egress_tasks.add(producer)
        producer.add_done_callback(self._consume_download_exception)
        try:
            while True:
                chunk = await self._next_download_chunk(
                    session, producer, chunks, context
                )
                if chunk is None:
                    return
                yield chunk
        finally:
            if handler is not None:
                session.egress_tasks.discard(handler)
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            self._release_transfer_session(session)

    async def _produce_download(self, session, request, context, chunks) -> None:
        try:
            async for chunk in self._download_artifact_for_session(
                session, request, context
            ):
                await chunks.put(chunk)
        finally:
            session.egress_tasks.discard(asyncio.current_task())

    @staticmethod
    def _consume_download_exception(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _next_download_chunk(session, producer, chunks, context):
        if session.revoked:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "This worker was revoked."
            )
            return None
        queued = asyncio.create_task(chunks.get())
        terminated = asyncio.create_task(session.terminated.wait())
        try:
            done, _pending = await asyncio.wait(
                {queued, producer, terminated}, return_when=asyncio.FIRST_COMPLETED
            )
            if terminated in done or session.revoked:
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED, "This worker was revoked."
                )
                return None
            if queued in done:
                return queued.result()
            if not chunks.empty():
                return chunks.get_nowait()
            await producer
            return None
        finally:
            for task in (queued, terminated):
                if not task.done():
                    task.cancel()
            await asyncio.gather(queued, terminated, return_exceptions=True)

    async def _download_artifact_for_session(self, session, request, context):
        if not self._may_read_input(session, request):
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED,
                "This input belongs to a task that is not running on this worker.",
            )
            return
        path = await to_thread_and_drain_on_cancel(
            self._resolve_input, request.artifact_id
        )
        if path is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Artifact not found.")
            return
        # A ref minted here rather than the caller's echoed back: the request
        # carries the worker's session token, and nothing goes back out that
        # did not have to go out.
        served = pb.ArtifactRef(
            artifact_id=request.artifact_id,
            task_id=request.task_id,
            attempt_id=request.attempt_id,
            filename=os.path.basename(path),
            content_type=request.content_type,
            size_bytes=await to_thread_and_drain_on_cancel(os.path.getsize, path),
        )
        offset = 0
        fh = None
        try:
            fh = await to_thread_and_drain_on_cancel(open, path, "rb")
            while True:
                if session.revoked:
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This worker was revoked.",
                    )
                    return
                data = await to_thread_and_drain_on_cancel(
                    fh.read, _DOWNLOAD_CHUNK_BYTES
                )
                if session.revoked:
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This worker was revoked.",
                    )
                    return
                if not data:
                    break
                yield pb.ArtifactChunk(ref=served, offset=offset, data=data, last=False)
                offset += len(data)
        finally:
            if fh is not None:
                await to_thread_and_drain_on_cancel(fh.close)
        if session.revoked:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This worker was revoked.",
            )
            return
        yield pb.ArtifactChunk(ref=served, offset=offset, data=b"", last=True)

    def _may_read_input(self, session: _Session, ref) -> bool:
        """Does this live attempt's task declare the exact requested input?"""
        task = self.scheduler.get(ref.task_id) if ref.task_id else None
        if task is None or not self._task_declares_input(task, ref.artifact_id):
            return False
        if ref.attempt_id:
            attempt, foreign = self._attempt_and_owner(session, ref)
            return attempt is not None and not foreign and attempt.state.live
        return any(
            attempt.worker_id == session.worker_id and attempt.state.live
            for attempt in task.attempts
        )

    @staticmethod
    def _task_declares_input(task: Task, artifact_id: str) -> bool:
        """Bind a store id to task authority, even when another task is live."""
        from worker.task_store import INPUTS_PARAM_KEY  # noqa: PLC0415

        params = task.params if isinstance(task.params, dict) else {}
        entries = params.get(INPUTS_PARAM_KEY)
        if not artifact_id or not isinstance(entries, list):
            return False
        return any(
            isinstance(entry, dict)
            and str(entry.get("artifact_id") or "") == artifact_id
            for entry in entries
        )

    def _session_for(self, context, ref) -> Optional[_Session]:
        """The live session a transfer belongs to, by ref token or by metadata."""
        token = getattr(ref, "session_token", "") or ""
        if token and token in self._by_token:
            session = self._by_token[token]
            active_stream = (
                session.activated
                and session.stream_open
                and self._sessions.get(session.worker_id) is session
            )
            return (
                None
                if session.revoked
                or not session.activated
                or (
                    session.session.expired()
                    and not active_stream
                )
                else session
            )
        session = self._session_from_metadata(
            context, allow_active_expired=True
        )
        return session if session is not None and session.activated else None

    def _resolve_input(self, artifact_id: str) -> Optional[str]:
        """Resolve an input reference to a path inside the artifact directory.

        Containment is enforced rather than assumed: a worker is a remote peer,
        and an artifact id is attacker-controlled input, so ``../`` must not be
        able to read arbitrary files off the control plane. One containment
        implementation for the whole file — a second, hand-rolled one is how
        the two directions came to disagree in the first place.
        """
        path = self._contained_artifact(artifact_id)
        return path if path and os.path.isfile(path) else None


async def serve(
    servicer: WorkerServicer,
    *,
    host: str = "0.0.0.0",
    port: int = 7443,
    certificate_pem: bytes,
    private_key_pem: bytes,
) -> grpc.aio.Server:
    """Start the control-plane server. TLS is not optional."""
    server = grpc.aio.server(
        options=[
            # gRPC enables SO_REUSEPORT by default where the platform supports
            # it. That is useful for replicated stateless services, but two
            # VoiceStudio control planes have independent worker registries
            # and schedulers: sharing this port sends each connection to an
            # arbitrary app instance.
            ("grpc.so_reuseport", 0),
            ("grpc.max_receive_message_length", 8 * 1024 * 1024),
            ("grpc.max_send_message_length", 8 * 1024 * 1024),
            # Consumer NAT/CGNAT mappings expire silently after 30–120s, and a
            # dead mapping looks exactly like a healthy idle connection until
            # something asks. Keepalives make the difference observable.
            ("grpc.keepalive_time_ms", 25_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            # The client above sends an HTTP/2 ping every 25 seconds while its
            # long-lived Control RPC is idle. gRPC's server default permits
            # only two idle pings and then sends ENHANCE_YOUR_CALM
            # ("too_many_pings"), evicting every healthy worker. Accept the
            # interval this protocol itself configures; zero means no count
            # ceiling, while the minimum interval still rate-limits peers.
            ("grpc.http2.min_ping_interval_without_data_ms", 20_000),
            ("grpc.http2.max_pings_without_data", 0),
        ]
    )
    pb_grpc.add_WorkerServiceServicer_to_server(servicer, server)
    credentials = grpc.ssl_server_credentials([(private_key_pem, certificate_pem)])
    try:
        bound_port = server.add_secure_port(f"{host}:{port}", credentials)
    except RuntimeError as exc:
        raise ControlPlaneBindError(
            f"Another VoiceStudio instance is already accepting remote workers "
            f"on port {port}. Close the other instance, or set "
            "OMNIVOICE_WORKER_PORT to a different port and restart VoiceStudio."
        ) from exc
    # add_secure_port() reports bind failure as 0; awaiting start() is not the
    # documented place to discover it and historically let this pass unseen.
    if bound_port == 0:
        raise ControlPlaneBindError(
            f"Another VoiceStudio instance is already accepting remote workers "
            f"on port {port}. Close the other instance, or set "
            "OMNIVOICE_WORKER_PORT to a different port and restart VoiceStudio."
        )
    await server.start()
    logger.info("Worker control plane listening on %s:%d (TLS)", host, port)
    return server


def _peer_address(context) -> str:
    """Turn gRPC's peer string into a plain ip:port.

    gRPC reports "ipv4:192.168.0.5:54321" or "ipv6:[::1]:54321"; neither is
    something to show a user.
    """
    try:
        peer = context.peer() or ""
    except Exception:
        return ""
    if peer.startswith("ipv4:"):
        return peer[5:]
    if peer.startswith("ipv6:"):
        return peer[5:]
    return peer


__all__ = [
    "ControlPlaneBindError",
    "INLINE_RESULT_THRESHOLD",
    "MAX_ARTIFACT_BYTES",
    "MAX_TASK_ARTIFACT_BYTES",
    "CHALLENGE_EXPIRED",
    "MIN_SUPPORTED_VERSION",
    "PROTOCOL_VERSION",
    "SESSION_METADATA_KEY",
    "WorkerServicer",
    "serve",
]
