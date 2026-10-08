"""The panel's half of inbound mode: dial a node and hold the session open.

The mirror of ``transport/client.py``'s reconnect loop, from the other side.
This one dials, but it is still the *control plane*: it sends assignments and
receives results, and every frame it handles goes through the same
``WorkerServicer`` methods the outbound path uses. Only who opened the socket
changed.

Admission is the API key, in call metadata. Identity is still the node's
Ed25519 key: the first connection to a given node records its public key, and
every later one must present the same. The key admits, the keypair identifies —
conflating them would mean anyone who copied the key could impersonate the
machine to a panel that had already trusted it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import socket
import ssl
from typing import BinaryIO, Optional

import grpc

from worker import identity, registry, tls
from worker.async_utils import to_thread_and_drain_on_cancel
from worker.inbound.connection_string import Connection
from worker.inbound.listener import CHALLENGE_METADATA_KEY, KEY_METADATA_KEY
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.protocol.gen import worker_v1_pb2_grpc as pb_grpc
from worker.transport.client import (
    MAX_MESSAGE_BYTES,
    TerminalRegistrationError,
    backoff_delay,
)

logger = logging.getLogger(__name__)

_PUSH_CHUNK_BYTES = 1024 * 1024
# Register remains provisional until the node confirms that it durably saved
# the panel-assigned identity.  Match the control plane's provisional-session
# lifetime so a peer that stops after Register cannot strand this connector.
_REGISTRATION_CONFIRMATION_TIMEOUT_SECONDS = 30.0
_REMOTE_SHUTDOWN_TIMEOUT_SECONDS = 30.0

_FileVersion = tuple[int, int, int, int, int]


def _write_all(handle: BinaryIO, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = handle.write(remaining)
        if not written:
            raise OSError("result write made no progress")
        remaining = remaining[written:]


def _remove_quietly(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


class InboundConnectionError(RuntimeError):
    """A pasted inbound connection could not be validated or activated."""


class InboundConnectionRollbackError(InboundConnectionError):
    """A failed connection change could not restore its prior generation."""


class RemoteShutdownUnavailable(InboundConnectionError):
    """The node may retain work, but no live stream can revoke it safely."""


def _file_version(stat: os.stat_result) -> _FileVersion:
    """Fields that identify both a staged path and the bytes hashed from it."""
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _hash_staged_input(path: str) -> tuple[int, str, _FileVersion]:
    """Hash one stable generation without ever allocating the whole file."""
    digest = hashlib.sha256()
    received = 0
    with open(path, "rb") as handle:
        before = _file_version(os.fstat(handle.fileno()))
        while True:
            block = handle.read(_PUSH_CHUNK_BYTES)
            if not block:
                break
            received += len(block)
            digest.update(block)
        after = _file_version(os.fstat(handle.fileno()))
    if before != after or received != before[2]:
        raise RuntimeError("the staged task input changed while it was being hashed")
    return received, digest.hexdigest(), before


def _validate_staged_input(path: str, expected: _FileVersion) -> None:
    """Reject a replacement or in-place edit between hashing and streaming."""
    try:
        current = _file_version(os.stat(path))
    except OSError as exc:
        raise RuntimeError("the staged task input is no longer available") from exc
    if current != expected:
        raise RuntimeError("the staged task input changed before it could be sent")


def _validate_open_staged_input(
    handle: BinaryIO, path: str, expected: _FileVersion
) -> None:
    """The open generation and its path must still be the bytes we hashed."""
    if _file_version(os.fstat(handle.fileno())) != expected:
        raise RuntimeError("the staged task input changed before it could be sent")
    _validate_staged_input(path, expected)


def _fetch_pinned_certificate(
    connection: Connection, *, timeout: float = 10.0
) -> bytes:
    """Fetch the node certificate, then accept it only when its pin matches.

    The first TLS handshake is intentionally CA-agnostic because the node uses
    a self-signed certificate. The copied fingerprint is the trust anchor; the
    verified leaf is then the sole root trusted by the real gRPC channel.
    """
    context = tls.unverified_client_context()
    with socket.create_connection(
        (connection.host, connection.port), timeout=timeout
    ) as raw, context.wrap_socket(raw, server_hostname=connection.host) as secured:
        certificate_der = secured.getpeercert(binary_form=True)
    if not certificate_der or not tls.pin_matches(
        certificate_der, connection.fingerprint
    ):
        raise RuntimeError(
            "That GPU machine presented a different certificate fingerprint. "
            "Remove this connection and paste a newly created connection string."
        )
    return ssl.DER_cert_to_PEM_cert(certificate_der).encode("ascii")


class NodeConnection:
    """One panel→node session, reconnecting until told to stop."""

    def __init__(self, servicer, connection: Connection, *, label: str = "") -> None:
        self._servicer = servicer
        self._connection = connection
        self._label = label or connection.host
        self._outbox: asyncio.Queue[pb.ServerMessage] = asyncio.Queue()
        self._active_session = None
        self._stub: Optional[pb_grpc.NodeServiceStub] = None
        self._worker_id = ""
        self._stop = asyncio.Event()
        self._session_closed = asyncio.Event()
        self._session_closed.set()
        self._shutdown_confirmed = asyncio.Event()
        self._registration_ready = asyncio.Event()
        self._remote_protocol_retained = False
        self._last_error = ""
        # The single-use challenge sent on the current Attach. The node must
        # sign exactly this, so a recorded registration cannot be replayed.
        self._attach_challenge = b""

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def last_error(self) -> str:
        return self._last_error

    def _channel(self, certificate_pem: bytes) -> grpc.aio.Channel:
        credentials = grpc.ssl_channel_credentials(root_certificates=certificate_pem)
        return grpc.aio.secure_channel(
            self._connection.endpoint,
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
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except TerminalRegistrationError as exc:
                self._remote_protocol_retained = False
                self._last_error = str(exc)
                raise
            except Exception:
                attempt += 1
                self._last_error = "Connection failed; check the backend log for details."
                delay = backoff_delay(attempt)
                logger.warning("Inbound worker connection failed; retry scheduled.")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def stop(self) -> None:
        if self._stop.is_set():
            return
        if self._shutdown_confirmed.is_set() and not self._remote_protocol_retained:
            self._stop.set()
            return
        if self._active_session is None:
            if self._remote_protocol_retained:
                raise RemoteShutdownUnavailable(
                    "That GPU machine is offline and may still be running work. "
                    "Reconnect it, then remove the connection again."
                )
            self._stop.set()
            return
        # EOF is indistinguishable from a network blip and deliberately keeps
        # node execution alive for reconnect.  Send an explicit terminal frame
        # and wait for the node to drain before removal reports success.
        self._shutdown_confirmed.clear()
        await self._outbox.put(
            pb.ServerMessage(
                shutdown=pb.Shutdown(reason="This GPU-machine connection was removed.")
            )
        )
        confirmed = asyncio.create_task(self._shutdown_confirmed.wait())
        disconnected = asyncio.create_task(self._session_closed.wait())
        try:
            done, _pending = await asyncio.wait(
                {confirmed, disconnected},
                timeout=_REMOTE_SHUTDOWN_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if confirmed not in done and not self._shutdown_confirmed.is_set():
                raise RemoteShutdownUnavailable(
                    "The GPU machine disconnected before it confirmed shutdown. "
                    "Reconnect it, then remove the connection again."
                )
        finally:
            confirmed.cancel()
            disconnected.cancel()
            await asyncio.gather(confirmed, disconnected, return_exceptions=True)
        self._remote_protocol_retained = False
        self._stop.set()

    async def close(self) -> None:
        """End this process without revoking reconnectable remote work."""
        self._stop.set()

    def confirm_remote_shutdown(self, session) -> None:
        if self._active_session is not session:
            return
        self._remote_protocol_retained = False
        self._shutdown_confirmed.set()

    def confirm_registration(self, session) -> None:
        """Publish readiness only after the shared servicer activated the session."""
        if self._active_session is session:
            self._registration_ready.set()

    async def wait_until_registered(
        self, task: asyncio.Task, *, timeout: float = 30.0
    ) -> None:
        """Wait for activation or surface a terminal/background dial failure."""
        ready = asyncio.create_task(self._registration_ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {ready, task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if ready in done:
                return
            if task in done:
                if task.cancelled():
                    raise InboundConnectionError(
                        "The GPU-machine connection stopped before it became ready."
                    )
                exc = task.exception()
                if exc is not None:
                    raise InboundConnectionError(str(exc)) from exc
            raise InboundConnectionError(
                "That GPU machine did not finish connecting in time."
            )
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)

    async def probe(self) -> None:
        """Authenticate a replacement paste without publishing a worker session."""
        try:
            certificate_pem = await asyncio.to_thread(
                _fetch_pinned_certificate, self._connection
            )
            challenge = identity.new_challenge()
            async with self._channel(certificate_pem) as channel:
                stub = pb_grpc.NodeServiceStub(channel)
                stream = stub.Attach(
                    self._outbound(), metadata=self._attach_metadata(challenge)
                )
                try:
                    first = await asyncio.wait_for(
                        stream.read(),
                        timeout=_REGISTRATION_CONFIRMATION_TIMEOUT_SECONDS,
                    )
                finally:
                    stream.cancel()
        except InboundConnectionError:
            raise
        except grpc.aio.AioRpcError as exc:
            detail = exc.details() or "The GPU machine rejected this connection."
            raise InboundConnectionError(detail) from exc
        except asyncio.TimeoutError as exc:
            raise InboundConnectionError(
                "That GPU machine did not answer in time."
            ) from exc
        except Exception as exc:
            raise InboundConnectionError(str(exc)) from exc

        if first == grpc.aio.EOF or first.WhichOneof("payload") != "register":
            raise InboundConnectionError(
                "That machine answered, but not as a VoiceStudio GPU node."
            )
        request = first.register
        validate = getattr(self._servicer, "validate_inbound_request", None)
        refusal = validate(request) if callable(validate) else None
        if refusal is not None and refusal.error.code:
            raise InboundConnectionError(
                f"{refusal.error.code}: {refusal.error.message}"
            )
        public_key = bytes(request.public_key)
        if len(public_key) != 32:
            raise InboundConnectionError("That machine sent no usable identity.")
        key_id = identity.key_id_for(public_key)
        if registry.is_revoked(key_id):
            raise InboundConnectionError(
                "This GPU machine was removed from this app. Add it again to use it."
            )
        known = registry.get_by_key_id(key_id)
        if not _is_challenge(request, challenge) or (
            known is not None and not self._proves_key_possession(request, known)
        ):
            raise InboundConnectionError(
                "That machine could not prove its saved identity."
            )

    async def _connect_once(self) -> None:
        # A fresh outbox per attempt. The queue used to be built once and
        # reused, so anything a dying session left behind became the NEXT
        # attach's first frame — the node then saw something other than the
        # registration it requires first, aborted the call, and the pair span
        # at full speed: on hardware this reached session epoch 2445 inside a
        # second, with the log reading "Locally aborted" over and over.
        self._worker_id = ""
        self._stub = None
        self._outbox = asyncio.Queue()
        self._active_session = None
        certificate_pem = await asyncio.to_thread(
            _fetch_pinned_certificate, self._connection
        )
        self._attach_challenge = identity.new_challenge()
        async with self._channel(certificate_pem) as channel:
            stub = pb_grpc.NodeServiceStub(channel)
            stream = stub.Attach(
                self._outbound(),
                metadata=self._attach_metadata(self._attach_challenge),
            )

            # The node speaks first: it is the side with capabilities to
            # declare, whichever side dialled.
            first = await stream.read()
            if first == grpc.aio.EOF or first.WhichOneof("payload") != "register":
                raise RuntimeError(
                    "That machine answered, but not as a VoiceStudio GPU node."
                )

            response = await self._register(first.register)
            if response.error.code:
                # A refusal here is a decision, not a blip: the node is a
                # different machine than the one this key was trusted for, or
                # its version cannot work with ours. Reconnecting cannot fix
                # either. Deliver the verdict before surfacing it locally so
                # the node can retire work retained across the dead stream;
                # closing first strands that executor with nobody left able to
                # cancel it.
                await self._outbox.put(pb.ServerMessage(registered=response))
                try:
                    await asyncio.wait_for(
                        stream.read(),
                        timeout=_REGISTRATION_CONFIRMATION_TIMEOUT_SECONDS,
                    )
                except (asyncio.TimeoutError, grpc.aio.AioRpcError):
                    pass
                raise TerminalRegistrationError(
                    f"{response.error.code}: {response.error.message}"
                )

            await self._outbox.put(pb.ServerMessage(registered=response))
            try:
                await self._complete_registration(stream, response, stub)
            finally:
                # Idempotent after activation; essential before it. A user can
                # remove this connection while the node is still persisting
                # identity, and cancellation must release the old worker's
                # scheduling gate immediately rather than wait for expiry.
                self._servicer.discard_unopened_session(
                    response.worker_id, session_token=response.session_token
                )

    async def _complete_registration(self, stream, response, stub) -> None:
        """Validate durable acceptance, then run the exact issued session."""
        try:
            confirmation = await asyncio.wait_for(
                stream.read(), timeout=_REGISTRATION_CONFIRMATION_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "That GPU machine did not confirm registration in time."
            ) from exc
        except grpc.aio.AioRpcError as exc:
            detail = exc.details() or ""
            error_code = detail.partition(":")[0].strip()
            if exc.code() == grpc.StatusCode.FAILED_PRECONDITION and error_code in {
                "AUTH_FAILED",
                "LOCAL_STATE",
                "UPGRADE_REQUIRED",
            }:
                raise TerminalRegistrationError(detail) from exc
            raise
        if confirmation == grpc.aio.EOF:
            raise RuntimeError(
                "That GPU machine disconnected before confirming registration."
            )
        if confirmation.WhichOneof("payload") != "heartbeat":
            raise RuntimeError(
                "That GPU machine sent an invalid registration confirmation."
            )

        session = self._servicer.session_for(
            response.worker_id, session_token=response.session_token
        )
        if session is None:
            raise RuntimeError("the session went away before the stream opened")

        self._worker_id = response.worker_id
        self._stub = stub
        self._last_error = ""
        self._active_session = session
        self._remote_protocol_retained = True
        self._shutdown_confirmed.clear()
        self._session_closed.clear()

        pump = asyncio.create_task(self._pump_outbound(session))
        try:
            await self._servicer.run_inbound_stream(
                session, _Frames(stream, first=confirmation), self
            )
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            self._stub = None
            self._worker_id = ""
            if self._active_session is session:
                self._active_session = None
            self._session_closed.set()

    async def _register(self, request: pb.RegisterRequest) -> pb.RegisterResponse:
        """Trust on first sight, then require the same key forever after.

        Pasting the connection string is the consent — the user went to the
        machine, generated a key and brought it here, which is a stronger
        statement of intent than any dialog this could show. What it is *not*
        is a licence for a different machine to answer at that address later,
        which is why the key is bound on first contact.
        """
        refusal = self._servicer.validate_inbound_request(request)
        if refusal is not None:
            return refusal
        worker, refusal = await to_thread_and_drain_on_cancel(
            self._authenticate_registration, request
        )
        if refusal is not None:
            return refusal
        return await self._servicer.establish_session(
            worker, request, address=self._connection.endpoint
        )

    def _attach_metadata(self, challenge: bytes) -> tuple[tuple[str, str], ...]:
        return (
            (KEY_METADATA_KEY, self._connection.secret),
            (CHALLENGE_METADATA_KEY, challenge.hex()),
        )

    def _authenticate_registration(self, request: pb.RegisterRequest):
        """Resolve inbound identity without running SQLite on the app loop."""
        public_key = bytes(request.public_key)
        if len(public_key) != 32:
            return None, self._servicer._refuse(
                "AUTH_FAILED", "That machine sent no usable identity."
            )
        # Spent here: one Attach, one registration.
        challenge, self._attach_challenge = self._attach_challenge, b""
        if not _is_challenge(request, challenge):
            return None, self._servicer._refuse(
                "AUTH_FAILED",
                "That machine did not sign this connection's challenge.",
            )
        key_id = identity.key_id_for(public_key)
        if registry.is_revoked(key_id):
            return None, self._servicer._refuse(
                "AUTH_FAILED",
                "This GPU machine was removed from this app. Add it again to use it.",
            )

        known = registry.get_by_key_id(key_id)
        if known is None:
            worker = registry.enroll_worker(
                name=request.host.hostname or self._label,
                public_key=public_key,
                endpoint=self._connection.endpoint,
                consent_granted=True,
            )
        else:
            worker = registry.authenticate(
                key_id=key_id,
                public_key=public_key,
                challenge=bytes(request.challenge),
                signature=bytes(request.challenge_signature),
                nonce=bytes(request.nonce),
                session_epoch=request.envelope.sequence,
            )
            if worker is None and self._proves_key_possession(request, known):
                # The node holds the right private key but signed over a
                # different worker id than this panel recorded — which is what
                # happens whenever a node meets a panel it has enrolled with
                # before but whose id it no longer has (a re-issued key, a
                # reset data dir). Possession of the key is the security
                # property; the id is only binding, and refusing here would
                # strand a machine that is provably the right one with no way
                # back except deleting it from both sides.
                logger.info(
                    "Re-adopting GPU machine %s: it proved its key but had lost its id here",
                    known.name,
                )
                worker = known
            if worker is None:
                return None, self._servicer._refuse(
                    "AUTH_FAILED",
                    "That machine could not prove it is the one this key was added for.",
                )

        return worker, None

    @staticmethod
    def _proves_key_possession(request: pb.RegisterRequest, known) -> bool:
        """Does this signature verify against the id the NODE thinks it has?

        Deliberately narrow: the public key must already be the one enrolled
        for this worker, so this can only ever re-adopt a machine this panel
        already trusts. It cannot admit a new key.
        """
        public_key = bytes(request.public_key)
        if known is None or known.revoked or known.public_key != public_key:
            return False
        message = identity.challenge_message(
            challenge=bytes(request.challenge),
            worker_id=request.worker_id,
            session_epoch=request.envelope.sequence,
            nonce=bytes(request.nonce),
        )
        return identity.verify_signature(
            public_key, message, bytes(request.challenge_signature)
        )

    def _outbound(self):
        # grpc closes request iterators itself when the peer ends a stream. An
        # async generator can still be suspended in ``Queue.get`` at that
        # point, making its concurrent ``aclose`` fail and leak teardown into
        # the next channel. A plain async iterator has no generator-finalizer
        # race and keeps the same one-frame-at-a-time backpressure.
        return _OutboundFrames(self)

    def fence_session_egress(self, session) -> None:
        """Drop frames copied before a replacement generation activated."""
        if self._active_session is not session:
            return
        while True:
            try:
                self._outbox.get_nowait()
            except asyncio.QueueEmpty:
                break

    def revoke_session(self, session) -> None:
        """Synchronously fence frames already copied into the request queue."""
        if self._active_session is not session:
            return
        while True:
            try:
                self._outbox.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._outbox.put_nowait(None)

    async def _pump_outbound(self, session) -> None:
        """Move the servicer's per-session outbox onto the dialled stream.

        Outbound mode writes straight to the gRPC context; here the frames have
        to cross into the request generator instead, because this side is the
        caller.
        """
        task = asyncio.current_task()
        if task is not None:
            session.egress_tasks.add(task)
        try:
            while not session.revoked and not getattr(session, "egress_fenced", False):
                message = await session.outbox.get()
                if session.revoked or getattr(session, "egress_fenced", False):
                    return
                await self._outbox.put(message)
        finally:
            if task is not None:
                session.egress_tasks.discard(task)

    # ── Artifacts ─────────────────────────────────────────────────────────

    async def push_input(self, ref: pb.ArtifactRef, path: str) -> pb.ArtifactRef:
        """Send one task input up to the node before the assignment goes out.

        Pushed rather than pulled because the node cannot call us. Ordering
        matters: the executor asks for its inputs as soon as it starts, so an
        assignment that overtakes its own inputs fails on a file that is merely
        late.
        """
        stub = self._stub
        if stub is None:
            raise RuntimeError("that GPU machine is not connected")

        size, digest, version = await to_thread_and_drain_on_cancel(
            _hash_staged_input, path
        )
        # Hashing and the gRPC request are separate operations. Re-resolve the
        # path immediately before handing the iterator to gRPC so a replaced
        # staging file is never described by the old generation's digest.
        await to_thread_and_drain_on_cancel(_validate_staged_input, path, version)

        declared = pb.ArtifactRef()
        declared.CopyFrom(ref)
        declared.size_bytes = size
        declared.sha256 = digest
        if not declared.filename:
            declared.filename = os.path.basename(path)

        async def chunks():
            offset = 0
            handle = await to_thread_and_drain_on_cancel(open, path, "rb")
            try:
                await to_thread_and_drain_on_cancel(
                    _validate_open_staged_input, handle, path, version
                )
                while offset < size:
                    data = await to_thread_and_drain_on_cancel(
                        handle.read, min(_PUSH_CHUNK_BYTES, size - offset)
                    )
                    if not data:
                        raise RuntimeError(
                            "the staged task input changed before it could be sent"
                        )
                    offset += len(data)
                    last = offset == size
                    if last:
                        # Do not publish the terminal frame until both the open
                        # generation and its path still match what was hashed.
                        await to_thread_and_drain_on_cancel(
                            _validate_open_staged_input, handle, path, version
                        )
                    yield pb.ArtifactChunk(
                        ref=declared,
                        offset=offset - len(data),
                        data=data,
                        last=last,
                    )
            finally:
                await to_thread_and_drain_on_cancel(handle.close)

        ack = await stub.PushInput(
            chunks(), metadata=((KEY_METADATA_KEY, self._connection.secret),)
        )
        if not ack.committed:
            raise RuntimeError(
                ack.error.message or "that GPU machine did not accept the input"
            )
        return declared

    async def fetch_result(
        self, ref: pb.ArtifactRef, destination: str, *, max_bytes: Optional[int] = None
    ) -> None:
        """Pull a finished result down, verifying it against its declared hash."""
        stub = self._stub
        if stub is None:
            raise RuntimeError("that GPU machine is not connected")

        request = pb.ArtifactRef()
        request.CopyFrom(ref)
        digest = hashlib.sha256()
        offset = 0
        complete = False
        try:
            handle = None
            try:
                handle = await to_thread_and_drain_on_cancel(
                    open, destination, "wb"
                )
                async for chunk in stub.FetchResult(
                    request, metadata=((KEY_METADATA_KEY, self._connection.secret),)
                ):
                    if int(chunk.offset) != offset:
                        raise RuntimeError(
                            f"result offset {chunk.offset} did not match {offset} bytes received"
                        )
                    if max_bytes is not None and offset + len(chunk.data) > max_bytes:
                        raise RuntimeError(
                            "the result is larger than the control plane accepts"
                        )
                    data = bytes(chunk.data)
                    await to_thread_and_drain_on_cancel(_write_all, handle, data)
                    digest.update(data)
                    offset += len(data)
                    if chunk.last:
                        complete = True
                        break
            finally:
                if handle is not None:
                    await to_thread_and_drain_on_cancel(handle.close)
        except asyncio.CancelledError:
            await to_thread_and_drain_on_cancel(_remove_quietly, destination)
            raise
        except Exception:
            await to_thread_and_drain_on_cancel(_remove_quietly, destination)
            raise

        # A truncated file that is renamed into place and called done is the
        # exact failure the upload path was hardened against; the pull
        # direction gets the same treatment.
        if not complete:
            await to_thread_and_drain_on_cancel(_remove_quietly, destination)
            raise RuntimeError("the result ended before its final chunk")
        if ref.sha256 and digest.hexdigest() != ref.sha256:
            await to_thread_and_drain_on_cancel(_remove_quietly, destination)
            raise RuntimeError(
                "the result did not match the checksum that machine declared"
            )


class _Frames:
    """Adapts a gRPC client stream to the ``async for`` the read loop expects."""

    def __init__(self, stream, *, first=None) -> None:
        self._stream = stream
        self._first = first

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._first is not None:
            message = self._first
            self._first = None
            return message
        message = await self._stream.read()
        if message == grpc.aio.EOF:
            raise StopAsyncIteration
        return message


class _OutboundFrames:
    """Cancellation-safe request iterator for the inverted Attach stream."""

    def __init__(self, connection: NodeConnection) -> None:
        self._connection = connection

    def __aiter__(self):
        return self

    async def __anext__(self):
        connection = self._connection
        while True:
            message = await connection._outbox.get()
            if message is None:
                raise StopAsyncIteration
            session = connection._active_session
            if session is not None:
                if session.revoked:
                    raise StopAsyncIteration
                if (
                    getattr(session, "egress_fenced", False)
                    and message.WhichOneof("payload") != "shutdown"
                ):
                    continue
            return message


def _is_challenge(request: pb.RegisterRequest, challenge: bytes) -> bool:
    """Did the node sign the challenge this panel sent on this Attach?"""
    return bool(challenge) and hmac.compare_digest(bytes(request.challenge), challenge)
