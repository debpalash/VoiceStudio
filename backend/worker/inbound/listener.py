"""The node's inbound listener: a gRPC server hosting NodeService.

Off by default. Enabling it is the consent surface — there is no per-job
approval prompt, because a prompt per job makes a shared GPU unusable and
trains people to click yes. What replaces it is visibility: every attach,
refusal and disconnect is in the connection log, and any session can be kicked.

Binds to 127.0.0.1 unless the user separately and explicitly widens it. That
default is nearly useless on its own, which is the point: reaching a node from
another machine should be a decision someone made, not a side effect of turning
on a feature.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import inspect
import logging
import os
import uuid
import weakref
from typing import Callable, Optional

import grpc

from worker.async_utils import (
    drain_task,
    to_thread_and_defer_cancellation,
    to_thread_and_drain_on_cancel,
)
from worker.inbound.artifacts import ArtifactQuotaExceeded, ArtifactStore
from worker.inbound.connection_log import ConnectionLog
from worker.inbound.keys import KeyStore
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.transport.client import (
    ArtifactTransport,
    MAX_MESSAGE_BYTES,
    TerminalRegistrationError,
    WorkerClient,
)
from worker.tls import ServerCredentials

logger = logging.getLogger(__name__)

# The panel presents its key here. Lower-case because gRPC normalises metadata
# keys and a mixed-case constant silently never matches.
KEY_METADATA_KEY = "x-omnivoice-node-key"
# The panel's single-use challenge (hex) for the node's opening Register. The
# node speaks first on Attach, so the challenge cannot ride a frame.
CHALLENGE_METADATA_KEY = "x-omnivoice-challenge"

DEFAULT_PORT = 7444
DEFAULT_BIND = "127.0.0.1"

_FETCH_CHUNK_BYTES = 1024 * 1024
# A peer declaration may narrow this ceiling, never widen it. This mirrors the
# control plane's per-artifact limit and prevents one authenticated stream from
# consuming the node's disk without bound.
MAX_INPUT_ARTIFACT_BYTES = 1024**3


def _metadata_challenge(context) -> bytes:
    """The panel's challenge from Attach metadata; empty for older panels."""
    try:
        metadata = {k.lower(): v for k, v in (context.invocation_metadata() or ())}
        return bytes.fromhex(str(metadata.get(CHALLENGE_METADATA_KEY, "")))[:64]
    except (AttributeError, TypeError, ValueError):
        return b""


def _peer_of(context) -> str:
    """A loggable source address. gRPC formats these as ipv4:1.2.3.4:5678."""
    try:
        raw = context.peer() or ""
    except Exception:
        return ""
    for prefix in ("ipv4:", "ipv6:"):
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def _revoked_goodbye() -> pb.WorkerMessage:
    return pb.WorkerMessage(
        goodbye=pb.WorkerGoodbye(
            reason="The owner of this GPU machine revoked this panel key."
        )
    )


class NodeServicer(pb_grpc.NodeServiceServicer):
    """Serves one node to any number of panels."""

    def __init__(
        self,
        *,
        keys: KeyStore,
        log: ConnectionLog,
        artifacts: ArtifactStore,
        client_factory: Callable[[ArtifactTransport, str], WorkerClient],
    ) -> None:
        self._keys = keys
        self._log = log
        self._artifacts = artifacts
        self._client_factory = client_factory
        # Live clients, one per attached panel. Kept so a freed engine can be
        # re-advertised to everyone rather than only to whoever asks next.
        self._clients: set = set()
        # Protocol/task state outlives a transport generation. A render keeps
        # running when a panel's stream blips, and its result must be claimed
        # and redelivered by the reconnect rather than orphaned with the dead
        # Attach handler.
        self._protocols: dict[str, WorkerClient] = {}
        self._attached_keys: set[str] = set()
        # A key can be revoked while Attach is idle. Each session gets its own
        # wakeup so revoking one panel ends all of that panel's live streams
        # without disturbing anyone else.
        self._revocations: dict[str, set[asyncio.Event]] = {}
        # Revocation is intentionally a synchronous authority commit, matching
        # KeyStore.revoke and the API surface that calls it.  Worker shutdown
        # is async, so keep the resulting tasks owned and drain them when the
        # listener stops instead of leaving an unobserved fire-and-forget task.
        self._protocol_stops: dict[WorkerClient, asyncio.Task] = {}
        self._key_retirements: dict[str, asyncio.Task] = {}
        self._retired_protocols = weakref.WeakSet()
        self._artifact_tasks: dict[str, set[asyncio.Task]] = {}
        self._artifact_idle: dict[str, asyncio.Event] = {}
        # All session/protocol state belongs to the gRPC event loop.  The
        # registration durability hook intentionally runs in a worker thread,
        # and integrations may revoke a key from that hook.  Keep that sync
        # authority operation thread-safe without trying to create asyncio
        # tasks on the worker thread.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self) -> None:
        """Remember the event loop that owns session and protocol state."""
        self._loop = asyncio.get_running_loop()

    def _retire_revoked_key(self, key_id: str) -> None:
        for event in list(self._revocations.get(key_id, ())):
            event.set()
        client = self._protocols.pop(key_id, None)
        if client is not None:
            self._clients.discard(client)
        self._retire_key(key_id, client)

    def revoke_key(self, key_id: str) -> bool:
        """Commit a key revocation and wake every session admitted by it."""
        failure = None
        try:
            revoked = self._keys.revoke(key_id)
        except Exception as exc:
            # KeyStore leaves a failed durable revoke fail-closed in this
            # process. Retire that authority's retained work even though the
            # API must still report that persistence needs retrying.
            revoked = False
            failure = exc
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if self._loop is None and running_loop is not None:
            self._loop = running_loop
        if running_loop is self._loop:
            self._retire_revoked_key(key_id)
        elif self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._retire_revoked_key, key_id)
        else:
            # The key is already durably fail-closed.  Surface the lifecycle
            # error rather than attempting asyncio.Event/Task operations from
            # an unowned thread and leaking the retained protocol.
            raise RuntimeError("the inbound listener event loop is unavailable")
        if failure is not None:
            raise failure
        return revoked

    async def revoke_key_and_wait(self, key_id: str) -> bool:
        """Publish revocation, then wait until retained work is cancelled."""
        failure = None
        try:
            revoked = self.revoke_key(key_id)
        except Exception as exc:
            revoked = False
            failure = exc
        cleanup = self._key_retirements.get(key_id)
        if cleanup is not None:
            await asyncio.shield(cleanup)
        if failure is not None:
            raise failure
        return revoked

    def _track_artifact_rpc(self, key_id: str) -> Optional[asyncio.Task]:
        task = asyncio.current_task()
        if task is not None:
            self._artifact_tasks.setdefault(key_id, set()).add(task)
            self._artifact_idle.setdefault(key_id, asyncio.Event()).clear()
        return task

    async def _untrack_artifact_rpc(
        self, key_id: str, task: Optional[asyncio.Task]
    ) -> None:
        if task is None:
            return
        tasks = self._artifact_tasks.get(key_id)
        if tasks is None:
            return
        tasks.discard(task)
        if not tasks:
            self._artifact_tasks.pop(key_id, None)
            # ResultAck can race the tail of FetchResult on Windows, where the
            # open handle transiently rejects unlink.  The final RPC teardown
            # is the deterministic retry point even if this node stays idle.
            try:
                await to_thread_and_drain_on_cancel(
                    self._artifacts.retry_result_acks, key_id
                )
            finally:
                # Cleanup yielded while its filesystem work ran. A newly
                # admitted RPC may now own this key, so only publish idle after
                # revalidating the loop-owned task set.
                if not self._artifact_tasks.get(key_id):
                    self._artifact_idle.setdefault(
                        key_id, asyncio.Event()
                    ).set()

    def _retire_protocol(self, client: WorkerClient) -> Optional[asyncio.Task]:
        """Start one owned shutdown for a protocol, safe from duplicate paths."""
        task = self._protocol_stops.get(client)
        if task is not None:
            return task
        if client in self._retired_protocols:
            return None
        self._retired_protocols.add(client)

        async def stop_client() -> None:
            try:
                await client.stop()
            except Exception:
                # Let a concurrent Attach cleanup retry rather than treating
                # a failed stop as a permanently retired protocol.
                self._retired_protocols.discard(client)
                logger.warning("Could not stop a retired panel protocol", exc_info=True)

        task = asyncio.create_task(stop_client(), name="inbound-protocol-stop")
        self._protocol_stops[client] = task

        def forget(completed: asyncio.Task) -> None:
            if self._protocol_stops.get(client) is completed:
                self._protocol_stops.pop(client, None)

        task.add_done_callback(forget)
        return task

    def _retire_key(
        self, key_id: str, client: Optional[WorkerClient]
    ) -> asyncio.Task:
        """Drain one panel generation, then remove only its staged bytes."""
        existing = self._key_retirements.get(key_id)
        if existing is not None:
            if client is None:
                return existing
            stop = self._retire_protocol(client)
            if stop is None:
                return existing

            async def extend_retirement() -> None:
                await asyncio.gather(existing, stop, return_exceptions=True)
                await to_thread_and_drain_on_cancel(
                    self._artifacts.purge_key, key_id
                )

            cleanup = asyncio.create_task(
                extend_retirement(), name="inbound-key-retirement"
            )
            self._key_retirements[key_id] = cleanup

            def forget_extended(completed: asyncio.Task) -> None:
                if self._key_retirements.get(key_id) is completed:
                    self._key_retirements.pop(key_id, None)

            cleanup.add_done_callback(forget_extended)
            return cleanup
        stop = self._retire_protocol(client) if client is not None else None
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        artifact_tasks = [
            task
            for task in self._artifact_tasks.get(key_id, ())
            if task is not current
        ]
        for task in artifact_tasks:
            task.cancel()
        artifact_idle = self._artifact_idle.setdefault(key_id, asyncio.Event())
        if not self._artifact_tasks.get(key_id):
            artifact_idle.set()

        async def finish_retirement() -> None:
            waits = [artifact_idle.wait()]
            if stop is not None:
                waits.append(stop)
            if waits:
                await asyncio.gather(*waits, return_exceptions=True)
            # FetchResult may hold the file open on Windows.  Purging only
            # after every artifact RPC and protocol-owned task has drained
            # makes revocation terminal without racing those handles.
            await to_thread_and_drain_on_cancel(
                self._artifacts.purge_key, key_id
            )

        cleanup = asyncio.create_task(
            finish_retirement(), name="inbound-key-retirement"
        )
        self._key_retirements[key_id] = cleanup

        def forget_key(completed: asyncio.Task) -> None:
            if self._key_retirements.get(key_id) is completed:
                self._key_retirements.pop(key_id, None)

        cleanup.add_done_callback(forget_key)
        return cleanup

    async def refresh_all(self) -> None:
        for client in list(self._clients):
            try:
                await client.refresh_capabilities()
            except Exception:
                logger.debug(
                    "Could not refresh capabilities for a panel", exc_info=True
                )

    # ── Admission ─────────────────────────────────────────────────────────

    async def _authenticate(
        self, context, *, record_seen: bool = True
    ) -> Optional[tuple[str, str]]:
        """Return (key_id, label) or None, logging the refusal either way."""
        peer = _peer_of(context)
        metadata = {k.lower(): v for k, v in (context.invocation_metadata() or ())}
        secret = metadata.get(KEY_METADATA_KEY, "")

        def authenticate_key():
            if self._keys.locked_out(peer):
                return None, True
            key = self._keys.authenticate(
                secret, peer=peer, record_seen=record_seen
            )
            return ((key.key_id, key.label) if key is not None else None), False

        admitted, locked_out = await to_thread_and_drain_on_cancel(
            authenticate_key
        )
        if locked_out:
            self._log.rejected(peer=peer, detail="too many failed keys")
            return None
        if admitted is not None and admitted[0] in self._key_retirements:
            self._log.rejected(peer=peer, detail="panel session is retiring")
            return None
        if admitted is not None and self._log.cooling_down(admitted[0]):
            self._log.rejected(peer=peer, detail="recently disconnected by the owner")
            return None
        if admitted is None:
            self._log.rejected(
                peer=peer, detail="no key" if not secret else "key not recognised"
            )
            return None
        return admitted

    async def _admit(
        self, context, *, record_seen: bool = True
    ) -> Optional[tuple[str, str]]:
        """Await production auth while keeping simple test doubles compatible."""
        admitted = (
            self._authenticate(context)
            if record_seen
            else self._authenticate(context, record_seen=False)
        )
        if inspect.isawaitable(admitted):
            admitted = await admitted
        return admitted

    # ── Attach ────────────────────────────────────────────────────────────

    async def Attach(self, request_iterator, context):
        admitted = await self._admit(context)
        if admitted is None:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This GPU machine did not recognise that key.",
            )
            return
        key_id, label = admitted

        if key_id in self._attached_keys:
            await context.abort(
                grpc.StatusCode.ALREADY_EXISTS,
                "This panel key already has an open session.",
            )
            return
        self._attached_keys.add(key_id)

        session_id = uuid.uuid4().hex
        peer = _peer_of(context)
        revoked = asyncio.Event()
        self._revocations.setdefault(key_id, set()).add(revoked)
        client: Optional[WorkerClient] = None
        reader: Optional[asyncio.Task] = None
        heartbeat: Optional[asyncio.Task] = None
        outbound: Optional[asyncio.Task] = None
        revocation: Optional[asyncio.Task] = None
        registration: Optional[asyncio.Task] = None
        is_new_protocol = False
        terminal_refusal = False
        explicit_shutdown = asyncio.Event()
        logged = False
        try:
            self._log.opened(
                session_id=session_id, key_id=key_id, label=label, peer=peer
            )
            logged = True
            # Built per key, not per node: each panel keeps its own registry, so
            # the same machine has a different worker id to each of them, and the
            # node signs its challenge over that id. Handing every panel the same
            # client would make the signature match at most one of them.
            client = self._protocols.get(key_id)
            if client is None:
                client = self._client_factory(self._artifacts.for_key(key_id), key_id)
                if inspect.isawaitable(client):
                    client = await client
                is_new_protocol = True
            client.prepare_inbound_session()
            self._clients.add(client)
            revocation = asyncio.create_task(revoked.wait())
            # The node speaks first even though the panel dialled: it is still
            # the side with capabilities to declare, and the panel cannot
            # schedule anything until it knows them.
            challenge = _metadata_challenge(context)
            register = (
                client.build_register_request(challenge=challenge)
                if challenge
                else client.build_register_request()
            )
            if inspect.isawaitable(register):
                register = await register
            yield pb.WorkerMessage(register=register)

            # A client is allowed to pause forever before answering Register.
            # Key revocation must still close that admitted stream immediately.
            registration = asyncio.create_task(_next_frame(request_iterator))
            done, _pending = await asyncio.wait(
                {registration, revocation}, return_when=asyncio.FIRST_COMPLETED
            )
            if revocation in done or not self._keys.is_active(key_id):
                registration.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await registration
                registration = None
                yield _revoked_goodbye()
                return
            first = registration.result()
            registration = None
            if first is None or first.WhichOneof("payload") != "registered":
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Expected the control plane to answer with a registration.",
                )
                return
            await client.accept_registration(first.registered)
            # accept_registration awaits zombie cancellation and pending-result
            # redelivery.  Revocation can land during either one and must win
            # before the acknowledgement publishes this worker to the panel.
            if revocation.done() or not self._keys.is_active(key_id):
                yield _revoked_goodbye()
                return

            confirmation = client.heartbeat_message()
            heartbeat = client.start_heartbeat(first.registered)
            if revocation.done() or not self._keys.is_active(key_id):
                yield _revoked_goodbye()
                return
            if is_new_protocol:
                # A brand-new protocol is retained only after every local
                # registration setup step succeeds.  A failed prepare/build/
                # heartbeat must get a fresh client on the next Attach.
                self._protocols[key_id] = client

            # This first post-registration frame is the acknowledgement the
            # dialling panel uses before publishing the worker to its live
            # pool. It is emitted only after the node's worker id is durable.
            yield confirmation
            reader = asyncio.create_task(
                self._pump_incoming(
                    client,
                    request_iterator,
                    session_id,
                    key_id,
                    explicit_shutdown,
                )
            )
            while True:
                # A reconnect request normally means the protocol outbox is
                # drained.  Terminal shutdown is the exception: it publishes
                # Goodbye and requests reconnect in the same event-loop turn.
                # Do not close the stream while that proof of remote drain is
                # still queued.
                if client.reconnect_requested and not client.outbound_pending:
                    return
                if revocation.done() or not self._keys.is_active(key_id):
                    yield _revoked_goodbye()
                    return
                if self._log.disconnect_requested(session_id):
                    explicit_shutdown.set()
                    yield pb.WorkerMessage(
                        goodbye=pb.WorkerGoodbye(
                            reason="The owner of this GPU machine ended the session."
                        )
                    )
                    return
                if reader.done():
                    reader.result()
                    if explicit_shutdown.is_set():
                        # The reader drains the executor before it returns. It
                        # may also finish before this loop dequeues the
                        # Worker's Goodbye, so acknowledge terminal shutdown
                        # directly rather than leaving DELETE waiting forever.
                        yield pb.WorkerMessage(
                            goodbye=pb.WorkerGoodbye(
                                reason="The control-plane connection was removed."
                            )
                        )
                    return
                # Bounded so a kick lands within a second even on an idle
                # session, where nothing else would wake this loop.
                outbound = asyncio.create_task(client.next_outbound())
                done, _pending = await asyncio.wait(
                    {outbound, revocation},
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if revocation in done or not self._keys.is_active(key_id):
                    outbound.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await outbound
                    yield _revoked_goodbye()
                    return
                if outbound not in done:
                    outbound.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await outbound
                    outbound = None
                    continue
                yield outbound.result()
                outbound = None
        except asyncio.CancelledError:
            raise
        except TerminalRegistrationError as exc:
            terminal_refusal = True
            logger.warning(
                "Inbound registration from %s failed permanently: %s",
                peer or "a panel",
                exc,
            )
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        except Exception as exc:
            logger.warning("Inbound session from %s ended: %s", peer or "a panel", exc)
        finally:
            for task in (reader, heartbeat, outbound, registration, revocation):
                if task is None:
                    continue
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if client is not None:
                self._clients.discard(client)
            sessions = self._revocations.get(key_id)
            if sessions is not None:
                sessions.discard(revoked)
                if not sessions:
                    self._revocations.pop(key_id, None)
            if client is not None:
                cached = self._protocols.get(key_id)
                must_retire = not self._keys.is_active(key_id)
                must_retire = must_retire or (is_new_protocol and cached is not client)
                must_retire = must_retire or terminal_refusal
                must_retire = must_retire or explicit_shutdown.is_set()
                if must_retire:
                    if cached is client:
                        self._protocols.pop(key_id, None)
                    cleanup = self._retire_key(key_id, client)
                    if cleanup is not None:
                        with contextlib.suppress(asyncio.CancelledError):
                            await asyncio.shield(cleanup)
            self._attached_keys.discard(key_id)
            if logged:
                self._log.closed(session_id)

    async def stop(self) -> None:
        """Stop retained protocol owners when the listener itself shuts down."""
        clients = list(self._protocols.items())
        self._protocols.clear()
        self._clients.clear()
        self._attached_keys.clear()
        for tasks in self._artifact_tasks.values():
            for task in list(tasks):
                task.cancel()
        retired_keys = set()
        for key_id, client in clients:
            retired_keys.add(key_id)
            self._retire_key(key_id, client)
        for key_id in list(self._artifact_tasks):
            if key_id not in retired_keys:
                self._retire_key(key_id, None)
        while self._key_retirements:
            await asyncio.gather(
                *list(self._key_retirements.values()), return_exceptions=True
            )
        self._artifact_idle.clear()
        while self._protocol_stops:
            await asyncio.gather(
                *list(self._protocol_stops.values()), return_exceptions=True
            )

    async def _pump_incoming(
        self,
        client: WorkerClient,
        request_iterator,
        session_id: str,
        key_id: str,
        explicit_shutdown: asyncio.Event,
    ) -> None:
        async for message in request_iterator:
            # Revocation applies to the already-open stream too. In
            # particular, do not accept one last assignment while the outer
            # Attach loop is waking to send its goodbye.
            if not self._keys.is_active(key_id):
                return
            kind = message.WhichOneof("payload")
            if kind == "assignment":
                self._log.task_started(session_id)
            if kind == "registered":
                # A second registration on a live stream is a control plane
                # bug, not a re-handshake. Ignoring it is safer than adopting
                # a new epoch mid-session and fencing the work in flight.
                logger.warning("Ignoring a repeated registration on a live session")
                continue
            if kind == "shutdown":
                # Record intent before draining. If the panel drops the socket
                # while a GPU thread is still unwinding, Attach's finally must
                # still retire rather than cache this protocol as a network blip.
                explicit_shutdown.set()
            await client.handle_server_message(message)
            if kind == "shutdown":
                return

    # ── Artifacts ─────────────────────────────────────────────────────────

    async def FetchResult(self, request, context):
        admitted = await self._admit(context, record_seen=False)
        if admitted is None:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This GPU machine did not recognise that key.",
            )
            return
        key_id, _label = admitted
        task = self._track_artifact_rpc(key_id)
        try:
            async for chunk in self._fetch_result_for_key(request, context, key_id):
                yield chunk
        finally:
            await self._untrack_artifact_rpc(key_id, task)

    async def _fetch_result_for_key(self, request, context, key_id: str):
        open_result = functools.partial(
            self._artifacts.open_result, request.artifact_id, key_id=key_id
        )
        staged = await to_thread_and_drain_on_cancel(open_result)
        if staged is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND, "That result is no longer on this machine."
            )
            return

        # Always from the start. `size_bytes` on the incoming ref is the
        # artifact's TOTAL size, not a resume point — reading it as one seeks
        # straight to EOF, yields no chunks, and the fetch fails with "the
        # result ended before its final chunk" while the render sits complete
        # on disk. ArtifactRef carries no resume field, so resumption needs a
        # protocol addition rather than a reinterpreted one.
        offset = 0
        handle = None
        try:
            handle = await to_thread_and_drain_on_cancel(open, staged.path, "rb")
            while True:
                if not self._keys.is_active(key_id):
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This panel key was revoked.",
                    )
                    return
                data = await to_thread_and_drain_on_cancel(
                    handle.read, _FETCH_CHUNK_BYTES
                )
                # Revocation may publish while the bounded read is in its
                # thread. Fence the bytes again before they can leave.
                if not self._keys.is_active(key_id):
                    await context.abort(
                        grpc.StatusCode.UNAUTHENTICATED,
                        "This panel key was revoked.",
                    )
                    return
                if not data:
                    break
                chunk = pb.ResultChunk(
                    ref=pb.ArtifactRef(
                        artifact_id=request.artifact_id,
                        task_id=request.task_id,
                        attempt_id=request.attempt_id,
                        filename=os.path.basename(staged.path),
                        size_bytes=staged.size_bytes,
                        sha256=staged.sha256,
                    ),
                    offset=offset,
                    data=data,
                )
                offset += len(data)
                chunk.last = offset >= staged.size_bytes
                yield chunk
        except OSError as exc:
            await context.abort(
                grpc.StatusCode.INTERNAL, f"Could not read the result: {exc}"
            )
            return
        finally:
            if handle is not None:
                await to_thread_and_drain_on_cancel(handle.close)
        if not self._keys.is_active(key_id):
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This panel key was revoked.",
            )
            return
        # Keep the staged copy until ResultAck comes back on Attach. EOF only
        # proves that bytes left this process; the panel can still fail before
        # its task commit, or lose the acknowledgement and refetch on reconnect.

    async def PushInput(self, request_iterator, context):
        admitted = await self._admit(context, record_seen=False)
        if admitted is None:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This GPU machine did not recognise that key.",
            )
            return pb.ArtifactAck()
        key_id, _label = admitted
        task = self._track_artifact_rpc(key_id)
        state = {"path": "", "finalized": False}
        try:
            return await self._push_input_for_key(
                request_iterator, context, key_id, state
            )
        finally:
            path = state["path"]
            if path and not state["finalized"]:
                await to_thread_and_drain_on_cancel(
                    self._artifacts.discard_input, path
                )
            await self._untrack_artifact_rpc(key_id, task)

    async def _push_input_for_key(
        self, request_iterator, context, key_id: str, state: dict
    ):

        ref: Optional[pb.ArtifactRef] = None
        path = ""
        handle = None
        digest = hashlib.sha256()
        received = 0
        committed = False
        revoked = False
        declared_size = 0
        try:
            async for chunk in request_iterator:
                if not self._keys.is_active(key_id):
                    revoked = True
                    break
                if ref is None:
                    ref = pb.ArtifactRef()
                    ref.CopyFrom(chunk.ref)
                    declared_size = int(ref.size_bytes)
                    if declared_size > MAX_INPUT_ARTIFACT_BYTES:
                        return pb.ArtifactAck(
                            artifact_id=ref.artifact_id,
                            error=pb.Error(
                                code="INPUT_TOO_LARGE",
                                message="the input is larger than this node accepts",
                            ),
                        )
                    try:
                        reused = await self._artifacts.reuse_committed_input_async(
                            ref, key_id=key_id
                        )
                    except ValueError as exc:
                        return pb.ArtifactAck(
                            artifact_id=ref.artifact_id,
                            error=pb.Error(
                                code="INPUT_ID_CONFLICT", message=str(exc)
                            ),
                        )
                    except OSError as exc:
                        return pb.ArtifactAck(
                            artifact_id=ref.artifact_id,
                            error=pb.Error(
                                code="INPUT_COMMIT_FAILED", message=str(exc)
                            ),
                        )
                    if reused:
                        if not self._keys.is_active(key_id):
                            await context.abort(
                                grpc.StatusCode.UNAUTHENTICATED,
                                "This panel key was revoked.",
                            )
                            return pb.ArtifactAck()
                        state["finalized"] = True
                        return pb.ArtifactAck(
                            artifact_id=ref.artifact_id,
                            bytes_received=declared_size,
                            committed=True,
                        )
                    try:
                        begin_input = functools.partial(
                            self._artifacts.begin_input,
                            ref,
                            key_id=key_id,
                            reserve_bytes=declared_size
                            or MAX_INPUT_ARTIFACT_BYTES,
                        )
                        path, cancelled = (
                            await to_thread_and_defer_cancellation(begin_input)
                        )
                        if cancelled:
                            await to_thread_and_drain_on_cancel(
                                self._artifacts.discard_input, path
                            )
                            raise asyncio.CancelledError
                    except ArtifactQuotaExceeded as exc:
                        return pb.ArtifactAck(
                            artifact_id=ref.artifact_id,
                            error=pb.Error(
                                code="INPUT_QUOTA_EXCEEDED", message=str(exc)
                            ),
                        )
                    state["path"] = path
                    handle = await to_thread_and_drain_on_cancel(open, path, "xb")
                if int(chunk.offset) != received:
                    return pb.ArtifactAck(
                        artifact_id=ref.artifact_id if ref else "",
                        bytes_received=received,
                        error=pb.Error(
                            code="OFFSET_MISMATCH",
                            message=f"expected offset {received}, got {chunk.offset}",
                        ),
                    )
                data = bytes(chunk.data)
                next_size = received + len(data)
                if next_size > MAX_INPUT_ARTIFACT_BYTES:
                    return pb.ArtifactAck(
                        artifact_id=ref.artifact_id,
                        bytes_received=received,
                        error=pb.Error(
                            code="INPUT_TOO_LARGE",
                            message="the input is larger than this node accepts",
                        ),
                    )
                if declared_size and next_size > declared_size:
                    return pb.ArtifactAck(
                        artifact_id=ref.artifact_id,
                        bytes_received=received,
                        error=pb.Error(
                            code="INPUT_SIZE_MISMATCH",
                            message="the input delivered more bytes than it declared",
                        ),
                    )
                await to_thread_and_drain_on_cancel(_write_all, handle, data)
                if not self._keys.is_active(key_id):
                    revoked = True
                    break
                digest.update(data)
                received = next_size
                if chunk.last:
                    committed = True
                    break
        except Exception as exc:
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id if ref else "",
                bytes_received=received,
                error=pb.Error(code="INPUT_WRITE_FAILED", message=str(exc)),
            )
        finally:
            if handle is not None:
                await to_thread_and_drain_on_cancel(handle.close)

        if revoked or not self._keys.is_active(key_id):
            if path:
                await to_thread_and_drain_on_cancel(
                    self._artifacts.discard_input, path
                )
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "This panel key was revoked.",
            )
            return pb.ArtifactAck()

        if ref is None or not committed:
            # An iterator that simply ends is a truncated transfer, not a
            # finished one. Committing here is exactly the bug that let a
            # short upload be renamed into place and called done.
            if path:
                await to_thread_and_drain_on_cancel(
                    self._artifacts.discard_input, path
                )
            return pb.ArtifactAck(
                bytes_received=received,
                error=pb.Error(
                    code="INPUT_INCOMPLETE",
                    message="the input ended before its final chunk",
                ),
            )

        if declared_size and received != declared_size:
            await to_thread_and_drain_on_cancel(
                self._artifacts.discard_input, path
            )
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id,
                bytes_received=received,
                error=pb.Error(
                    code="INPUT_SIZE_MISMATCH",
                    message="the input did not deliver the number of bytes it declared",
                ),
            )

        actual = digest.hexdigest()
        if ref.sha256 and actual != ref.sha256:
            await to_thread_and_drain_on_cancel(
                self._artifacts.discard_input, path
            )
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id,
                bytes_received=received,
                error=pb.Error(
                    code="INPUT_CHECKSUM_MISMATCH",
                    message="the input did not match the checksum the control plane declared",
                ),
            )

        try:
            await self._artifacts.commit_input_async(
                ref, path, actual, received, key_id=key_id
            )
        except ValueError as exc:
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id,
                bytes_received=received,
                error=pb.Error(code="INPUT_ID_CONFLICT", message=str(exc)),
            )
        except ArtifactQuotaExceeded as exc:
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id,
                bytes_received=received,
                error=pb.Error(code="INPUT_QUOTA_EXCEEDED", message=str(exc)),
            )
        except OSError as exc:
            return pb.ArtifactAck(
                artifact_id=ref.artifact_id,
                bytes_received=received,
                error=pb.Error(code="INPUT_COMMIT_FAILED", message=str(exc)),
            )
        state["finalized"] = True
        return pb.ArtifactAck(
            artifact_id=ref.artifact_id, bytes_received=received, committed=True
        )


async def _next_frame(request_iterator):
    try:
        return await request_iterator.__anext__()
    except StopAsyncIteration:
        return None


def _write_all(handle, payload: bytes) -> None:
    """Write a complete chunk even if the filesystem reports a short write."""
    remaining = memoryview(payload)
    while remaining:
        written = handle.write(remaining)
        if not written:
            raise OSError("input write made no progress")
        remaining = remaining[written:]


class NodeListener:
    """Owns the gRPC server. Started only when the user turns inbound on."""

    def __init__(
        self,
        *,
        keys: KeyStore,
        log: ConnectionLog,
        artifacts: ArtifactStore,
        client_factory: Callable[[ArtifactTransport, str], WorkerClient],
        credentials: ServerCredentials,
    ) -> None:
        if not credentials:
            raise ValueError("TLS credentials are required for the inbound listener.")
        self._servicer = NodeServicer(
            keys=keys, log=log, artifacts=artifacts, client_factory=client_factory
        )
        self._artifacts = artifacts
        self._credentials = credentials
        self._server: Optional[grpc.aio.Server] = None
        self._bound_port = 0

    async def refresh_all(self) -> None:
        """Re-advertise capabilities to every attached panel."""
        await self._servicer.refresh_all()

    def revoke_key(self, key_id: str) -> bool:
        """Revoke one panel and end every stream authenticated by its key."""
        return self._servicer.revoke_key(key_id)

    async def revoke_key_and_wait(self, key_id: str) -> bool:
        """Revoke one panel and await cancellation of retained work."""
        return await self._servicer.revoke_key_and_wait(key_id)

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        """The port actually bound, which is not always the one requested."""
        return self._bound_port

    @property
    def credentials(self) -> ServerCredentials:
        return self._credentials

    async def start(self, *, host: str = DEFAULT_BIND, port: int = DEFAULT_PORT) -> int:
        if self._server is not None:
            return self._bound_port
        self._servicer.bind_loop()
        server = grpc.aio.server(
            options=[
                ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
                ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
                # A panel's channel pings on an idle Attach stream exactly as a
                # worker's does outbound. Without these the server answers
                # too_many_pings and evicts the healthy panels it was waiting
                # for — the same eviction that cost this feature a day when the
                # control plane did it.
                ("grpc.keepalive_permit_without_calls", 1),
                ("grpc.http2.min_ping_interval_without_data_ms", 20_000),
                ("grpc.http2.max_pings_without_data", 0),
            ]
        )
        pb_grpc.add_NodeServiceServicer_to_server(self._servicer, server)
        bind = (
            f"[{host}]:{port}"
            if ":" in host and not host.startswith("[")
            else f"{host}:{port}"
        )
        credentials = grpc.ssl_server_credentials(
            [(self._credentials.private_key_pem, self._credentials.certificate_pem)]
        )
        bound = server.add_secure_port(bind, credentials)
        if not bound:
            raise RuntimeError(
                f"Could not listen on {bind}. Another program may already be using that port."
            )
        starting = asyncio.create_task(server.start())
        try:
            await asyncio.shield(starting)
        except BaseException as operation:
            await drain_task(starting)
            stopping = asyncio.create_task(server.stop(grace=0))
            try:
                await asyncio.shield(stopping)
            except BaseException:
                await drain_task(stopping)
            if stopping.cancelled() or stopping.exception() is not None:
                # Cleanup failed after bind. Retain the only handle so a later
                # stop can still close the live socket.
                self._server = server
                self._bound_port = bound
            raise operation
        self._server = server
        self._bound_port = bound
        logger.info(
            "Inbound node listener accepting pinned TLS connections on %s", bind
        )
        return bound

    async def stop(self) -> None:
        server = self._server

        async def shutdown() -> None:
            if server is not None:
                await server.stop(grace=1.0)
            await self._servicer.stop()
            await to_thread_and_drain_on_cancel(self._artifacts.purge)

        stopping = asyncio.create_task(shutdown(), name="inbound-listener-stop")
        try:
            await asyncio.shield(stopping)
        except asyncio.CancelledError:
            await drain_task(stopping)
            if stopping.cancelled():
                raise
            failure = stopping.exception()
            if failure is not None:
                raise failure
            if self._server is server:
                self._server = None
                self._bound_port = 0
            raise
        except BaseException:
            await drain_task(stopping)
            raise
        if self._server is server:
            self._server = None
            self._bound_port = 0
