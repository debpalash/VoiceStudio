"""Inbound mode end to end: a panel dials a node and runs a task on it.

Everything else about this feature can pass unit tests while the thing itself
does not connect — which is exactly how this subsystem has failed before. These
tests stand up a real listener on a real socket, dial it with the real
connector, and assert on the state the scheduler ends up in.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

ENGINE, MODEL, OP = "indextts", "IndexTTS-2", "tts"


def _worker_modules():
    """Resolve app modules at test runtime, after isolation fixtures run."""
    from worker import registry, tls
    from worker.identity import WorkerKeypair
    from worker.inbound.artifacts import ArtifactStore
    from worker.inbound.connection_log import ConnectionLog
    from worker.inbound.connection_string import format_connection, parse_connection
    from worker.inbound.connector import NodeConnection
    from worker.inbound.keys import KeyStore
    from worker.inbound.listener import NodeListener
    from worker.pool import WorkerPool
    from worker.scheduler import Scheduler
    from worker.transport.client import WorkerClient, WorkerConfig
    from worker.transport.server import WorkerServicer

    return SimpleNamespace(
        ArtifactStore=ArtifactStore,
        ConnectionLog=ConnectionLog,
        KeyStore=KeyStore,
        NodeConnection=NodeConnection,
        NodeListener=NodeListener,
        Scheduler=Scheduler,
        WorkerClient=WorkerClient,
        WorkerConfig=WorkerConfig,
        WorkerKeypair=WorkerKeypair,
        WorkerPool=WorkerPool,
        WorkerServicer=WorkerServicer,
        format_connection=format_connection,
        parse_connection=parse_connection,
        registry=registry,
        tls=tls,
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _capabilities():
    return [
        {
            "engine": ENGINE,
            "model_id": MODEL,
            "operations": [OP],
            "supported": True,
            "installed": True,
            "downloaded": True,
            "resident": False,
            "derived_concurrency": 1,
            "repo_ids": ["test/repo"],
        }
    ]


class _ObservedConnectionLog:
    """ConnectionLog with concrete async signals instead of wall-clock waits."""

    def __init__(self, *, now):
        self._inner = _worker_modules().ConnectionLog(now=now)
        self.rejected_event = asyncio.Event()
        self.closed_event = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def rejected(self, *, peer, detail):
        self._inner.rejected(peer=peer, detail=detail)
        self.rejected_event.set()

    def closed(self, session_id, *, detail=""):
        self._inner.closed(session_id, detail=detail)
        self.closed_event.set()


class _InboundHarness:
    """A node listening on loopback, plus the panel that dials it."""

    def __init__(self, tmp_path):
        worker = _worker_modules()
        self.worker = worker
        # Node side.
        self.keys = worker.KeyStore(str(tmp_path / "inbound-keys.json"))
        self.clock = [100.0]
        self.log = _ObservedConnectionLog(now=lambda: self.clock[0])
        self.artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
        self.keypair = worker.WorkerKeypair.generate()
        self.credentials = worker.tls.generate_self_signed(
            hostnames=["localhost", "127.0.0.1"]
        )
        self.executed: list[str] = []
        self.listener = worker.NodeListener(
            keys=self.keys,
            log=self.log,
            artifacts=self.artifacts,
            client_factory=self._client,
            credentials=self.credentials,
        )
        # Panel side.
        self.pool = worker.WorkerPool()
        self.scheduler = worker.Scheduler(self.pool, persist=False)
        self.servicer = worker.WorkerServicer(
            self.scheduler, self.pool, artifact_dir=str(tmp_path / "artifacts")
        )
        self.connection = None
        self.connector_task = None
        self.panel_key_id = ""
        self.port = 0

    def _client(self, artifacts, key_id):
        async def execute(assignment, **kwargs):
            self.executed.append(assignment.ref.task_id)
            return {"result_json": "{}", "payload": b"", "meta": {}}

        return self._client_for(artifacts, key_id, execute)

    def _client_for(self, artifacts, key_id, execute):
        return self.worker.WorkerClient(
            self.worker.WorkerConfig(
                endpoint="",
                cert_fingerprint="",
                certificate_pem=b"",
                keypair=self.keypair,
                worker_id=self.keys.worker_id_for(key_id),
                enrollment_token="",
                max_concurrent_tasks=1,
                capabilities=_capabilities(),
                host={
                    "hostname": "gpu-node",
                    "os": "linux",
                    "arch": "x86_64",
                    "gpus": [],
                },
            ),
            execute=execute,
            artifacts=artifacts,
            on_registered=lambda wid: self.keys.remember_worker_id(key_id, wid),
        )

    async def start_node(self):
        self.port = await self.listener.start(host="127.0.0.1", port=_free_port())
        return self.port

    async def connect_panel(self, secret=None, *, wait=True):
        if secret is None:
            issued = self.keys.issue("Test panel")
            secret = issued.secret
            self.panel_key_id = issued.key.key_id
        else:
            from worker.identity import hash_secret

            self.panel_key_id = hash_secret(secret)[:12]
        text = self.worker.format_connection(
            host="127.0.0.1",
            port=self.port,
            secret=secret,
            fingerprint=self.credentials.fingerprint,
        )
        connection = self.worker.parse_connection(text)
        self.connection = self.worker.NodeConnection(self.servicer, connection)
        self.connector_task = asyncio.create_task(self.connection.run_forever())
        if wait:
            def worker_is_activated():
                if len(self.pool) != 1:
                    return False
                live = next(iter(self.pool))
                return (
                    live.record.schedulable
                    and not live.registration_pending
                    and live.supports(engine=ENGINE, model_id=MODEL, operation=OP)
                )

            await _until(worker_is_activated)
        return self.connection

    async def stop(self):
        if self.connection is not None:
            # Fixture teardown is a local transport cleanup, not a request to
            # prove that the remote node durably acknowledged shutdown.  A
            # test may deliberately leave that node unavailable.
            close = getattr(self.connection, "close", None)
            if callable(close):
                await close()
            else:
                await self.connection.stop()
        if self.connector_task is not None:
            self.connector_task.cancel()
            await asyncio.gather(self.connector_task, return_exceptions=True)
        await self.listener.stop()


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


@pytest_asyncio.fixture
async def inbound(tmp_path, db):
    h = _InboundHarness(tmp_path)
    await h.start_node()
    try:
        yield h
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_a_panel_that_dials_a_node_ends_up_with_a_schedulable_worker(inbound):
    """The whole feature in one assertion: paste a string, get a usable GPU."""
    await inbound.connect_panel()

    assert len(inbound.pool) == 1
    worker = next(iter(inbound.pool))
    assert worker.record.schedulable is True
    assert worker.registration_pending is False
    assert worker.capacity.can_accept(ENGINE, MODEL)
    # Capabilities crossed the inverted stream, so the scheduler can actually
    # pick this worker rather than merely knowing it exists.
    assert worker.supports(engine=ENGINE, model_id=MODEL, operation=OP)


@pytest.mark.asyncio
async def test_reconnect_claims_the_same_running_inbound_execution(inbound):
    issued = inbound.keys.issue("Test panel")
    execution_started = asyncio.Event()
    release_execution = asyncio.Event()
    executions = []

    async def execute(assignment, **_kwargs):
        executions.append(assignment.ref.task_id)
        execution_started.set()
        await release_execution.wait()
        return {"result_json": "{}", "payload": b"", "meta": {}}

    inbound.listener._servicer._client_factory = (
        lambda artifacts, key_id: inbound._client_for(artifacts, key_id, execute)
    )
    await inbound.connect_panel(secret=issued.secret)
    task = inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(execution_started.wait(), timeout=HANG_GUARD_S)

    inbound.connector_task.cancel()
    await asyncio.gather(inbound.connector_task, return_exceptions=True)
    await _until(lambda: len(inbound.pool) == 0)
    await inbound.connect_panel(secret=issued.secret)

    live = next(iter(inbound.pool))
    assert live.capacity.active_tasks == 1
    release_execution.set()
    await _until(lambda: task.state.value == "completed")
    assert executions == [task.task_id]


@pytest.mark.asyncio
async def test_reconnect_redelivers_an_inbound_result_not_yet_acknowledged(
    inbound, monkeypatch
):
    issued = inbound.keys.issue("Test panel")
    await inbound.connect_panel(secret=issued.secret)
    result_seen = asyncio.Event()
    hold_result = asyncio.Event()
    blocked_once = False
    real_handle = inbound.servicer._handle

    async def interrupt_first_result(session, message):
        nonlocal blocked_once
        if message.WhichOneof("payload") == "result" and not blocked_once:
            blocked_once = True
            result_seen.set()
            await hold_result.wait()
        return await real_handle(session, message)

    monkeypatch.setattr(inbound.servicer, "_handle", interrupt_first_result)
    task = inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(result_seen.wait(), timeout=HANG_GUARD_S)

    inbound.connector_task.cancel()
    await asyncio.gather(inbound.connector_task, return_exceptions=True)
    await _until(lambda: len(inbound.pool) == 0)
    hold_result.set()
    await inbound.connect_panel(secret=issued.secret)

    await _until(lambda: task.state.value == "completed")
    protocol = inbound.listener._servicer._protocols[inbound.panel_key_id]
    await _until(lambda: not protocol._pending)
    assert inbound.executed == [task.task_id]


@pytest.mark.asyncio
async def test_lost_result_ack_refetch_keeps_the_committed_inbound_artifact(
    inbound, monkeypatch
):
    from worker.inbound.artifacts import KeyedArtifactTransport
    cleanup_done = asyncio.Event()
    real_cleanup = KeyedArtifactTransport.result_acked_async
    async def observe_cleanup(transport, artifacts):
        await real_cleanup(transport, artifacts)
        cleanup_done.set()
    monkeypatch.setattr(KeyedArtifactTransport, "result_acked_async", observe_cleanup)
    issued = inbound.keys.issue("Test panel")
    payload = b"rendered audio" * 24_000

    async def execute(assignment, **_kwargs):
        inbound.executed.append(assignment.ref.task_id)
        return {
            "result_json": "{}",
            "payload": payload,
            "meta": {"filename": "result.wav", "content_type": "audio/wav"},
        }

    inbound.listener._servicer._client_factory = (
        lambda artifacts, key_id: inbound._client_for(artifacts, key_id, execute)
    )
    await inbound.connect_panel(secret=issued.secret)
    ack_dropped = asyncio.Event()
    real_handle = inbound.servicer._handle
    dropped = False

    async def lose_first_result_ack(session, message):
        nonlocal dropped
        await real_handle(session, message)
        if message.WhichOneof("payload") != "result" or dropped:
            return
        for index, queued in enumerate(session.outbox._queue):
            if queued.WhichOneof("payload") == "result_ack":
                del session.outbox._queue[index]
                dropped = True
                ack_dropped.set()
                break

    monkeypatch.setattr(inbound.servicer, "_handle", lose_first_result_ack)
    task = inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(ack_dropped.wait(), timeout=HANG_GUARD_S)
    assert task.state.value == "completed"
    committed = task.result_ref
    assert committed and open(committed, "rb").read() == payload

    protocol = inbound.listener._servicer._protocols[inbound.panel_key_id]
    pending = next(iter(protocol._pending.values()))
    artifact_id = pending.artifacts[0].artifact_id
    assert inbound.artifacts.open_result(
        artifact_id, key_id=inbound.panel_key_id
    ) is not None

    inbound.connector_task.cancel()
    await asyncio.gather(inbound.connector_task, return_exceptions=True)
    await _until(lambda: len(inbound.pool) == 0)
    await inbound.connect_panel(secret=issued.secret)
    await _until(lambda: not protocol._pending)

    assert open(committed, "rb").read() == payload
    # The ACK removes the pending item before awaiting filesystem cleanup.
    # Observe completion of that asynchronous operation, not its queue marker.
    await asyncio.wait_for(cleanup_done.wait(), timeout=HANG_GUARD_S)
    assert inbound.artifacts.open_result(
        artifact_id, key_id=inbound.panel_key_id
    ) is None
    assert inbound.executed == [task.task_id]


@pytest.mark.asyncio
async def test_a_panel_refuses_a_node_whose_tls_certificate_misses_the_pin(inbound):
    secret = inbound.keys.issue("Test panel").secret
    connection = inbound.worker.parse_connection(
        inbound.worker.format_connection(
            host="127.0.0.1",
            port=inbound.port,
            secret=secret,
            fingerprint=inbound.credentials.fingerprint,
        )
    )
    impostor_pin = "0" * 64
    assert impostor_pin != inbound.credentials.fingerprint
    dialer = inbound.worker.NodeConnection(
        inbound.servicer, replace(connection, fingerprint=impostor_pin)
    )

    with pytest.raises(RuntimeError, match="certificate fingerprint"):
        await asyncio.wait_for(dialer._connect_once(), timeout=HANG_GUARD_S)

    assert len(inbound.pool) == 0


@pytest.mark.asyncio
async def test_the_node_is_enrolled_by_its_own_key_not_by_the_api_key(inbound):
    """The API key admits a panel; identity stays with the node's keypair. If
    these were conflated, anyone who copied the key could impersonate the
    machine to a panel that had already trusted it."""
    await inbound.connect_panel()

    stored = inbound.worker.registry.list_workers()
    assert len(stored) == 1
    assert stored[0].key_id == inbound.keypair.key_id


@pytest.mark.asyncio
async def test_two_panels_can_use_one_node_at_the_same_time(tmp_path, db, inbound):
    """The reason inbound exists. Outbound is 1:1 by construction, so this is
    the case it can never serve."""
    second = _InboundHarness(tmp_path / "second")
    # A second panel, its own scheduler and registry view, same node.
    second.listener = inbound.listener
    second.port = inbound.port

    await inbound.connect_panel()
    alice = next(iter(inbound.pool)).worker_id

    bob_secret = inbound.keys.issue("Bob").secret
    connection = inbound.worker.parse_connection(
        inbound.worker.format_connection(
            host="127.0.0.1",
            port=inbound.port,
            secret=bob_secret,
            fingerprint=inbound.credentials.fingerprint,
        )
    )
    bob = inbound.worker.NodeConnection(second.servicer, connection)
    task = asyncio.create_task(bob.run_forever())
    try:
        await _until(lambda: len(second.pool) == 1)
        assert next(iter(second.pool)).worker_id == alice
        # Both sessions are live on the node at once, which is what a
        # one-at-a-time design would have prevented.
        assert len(inbound.log.snapshot()["sessions"]) == 2
    finally:
        await bob.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_panel_with_no_key_never_reaches_the_worker_pool(inbound):
    await inbound.connect_panel(secret="ovnode_" + "z" * 40, wait=False)
    await asyncio.wait_for(inbound.log.rejected_event.wait(), timeout=HANG_GUARD_S)

    assert len(inbound.pool) == 0
    kinds = [e["kind"] for e in inbound.log.snapshot()["events"]]
    assert "rejected" in kinds


@pytest.mark.asyncio
async def test_terminal_panel_registration_refusal_is_not_retried(
    inbound, monkeypatch
):
    from worker.transport.client import TerminalRegistrationError

    connection = inbound.worker.NodeConnection(
        inbound.servicer, SimpleNamespace(host="gpu-node")
    )
    attempts = 0

    async def refuse_once():
        nonlocal attempts
        attempts += 1
        raise TerminalRegistrationError("AUTH_FAILED: registration rejected")

    monkeypatch.setattr(connection, "_connect_once", refuse_once)

    with pytest.raises(TerminalRegistrationError, match="AUTH_FAILED"):
        await asyncio.wait_for(connection.run_forever(), timeout=HANG_GUARD_S)
    assert attempts == 1
    assert connection.last_error.startswith("AUTH_FAILED")


@pytest.mark.asyncio
async def test_inbound_worker_id_persistence_failure_is_terminal(
    inbound, monkeypatch
):
    from worker.transport.client import TerminalRegistrationError

    attempts = 0

    def fail_persistence(_key_id, _worker_id):
        nonlocal attempts
        attempts += 1
        raise OSError("disk full")

    monkeypatch.setattr(inbound.keys, "remember_worker_id", fail_persistence)
    connection = await inbound.connect_panel(wait=False)

    with pytest.raises(TerminalRegistrationError, match="LOCAL_STATE"):
        await asyncio.wait_for(inbound.connector_task, timeout=HANG_GUARD_S)

    assert attempts == 1
    assert len(inbound.pool) == 0
    assert inbound.servicer._sessions == {}
    assert connection.last_error.startswith("LOCAL_STATE")


@pytest.mark.asyncio
async def test_registration_confirmation_timeout_discards_the_provisional_session(
    monkeypatch,
):
    """A node silent after Register must release its handshake and retry."""
    from worker.inbound import connector as connector_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    class Stream:
        def __init__(self):
            self.reads = 0

        async def read(self):
            self.reads += 1
            if self.reads == 1:
                return pb.WorkerMessage(register=pb.RegisterRequest())
            await asyncio.Event().wait()

    stream = Stream()

    class Stub:
        def Attach(self, _frames, metadata=()):
            return stream

    class Channel:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    discarded = []
    servicer = SimpleNamespace(
        discard_unopened_session=lambda worker_id, *, session_token="": discarded.append(
            (worker_id, session_token)
        )
    )
    connection = connector_module.NodeConnection(
        servicer,
        SimpleNamespace(
            host="gpu-node",
            endpoint="gpu-node:7444",
            secret="ovnode_test",
        ),
    )
    response = pb.RegisterResponse(worker_id="worker-1", session_token="session-1")
    monkeypatch.setattr(connector_module, "_fetch_pinned_certificate", lambda _c: b"cert")
    monkeypatch.setattr(connector_module.pb_grpc, "NodeServiceStub", lambda _c: Stub())
    monkeypatch.setattr(
        connector_module, "_REGISTRATION_CONFIRMATION_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(connection, "_channel", lambda _certificate: Channel())
    async def register(_request):
        return response

    monkeypatch.setattr(connection, "_register", register)

    with pytest.raises(RuntimeError, match="did not confirm registration"):
        await asyncio.wait_for(connection._connect_once(), timeout=HANG_GUARD_S)

    assert discarded == [("worker-1", "session-1")]
    assert connection.worker_id == ""


@pytest.mark.asyncio
async def test_missing_exact_registration_session_is_never_reported_connected(
    monkeypatch,
):
    from worker.inbound import connector as connector_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    frames = iter(
        [
            pb.WorkerMessage(register=pb.RegisterRequest()),
            pb.WorkerMessage(heartbeat=pb.Heartbeat()),
        ]
    )

    class Stream:
        async def read(self):
            return next(frames)

    class Stub:
        def Attach(self, _frames, metadata=()):
            return Stream()

    class Channel:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    servicer = SimpleNamespace(
        discard_unopened_session=lambda *_args, **_kwargs: None,
        session_for=lambda *_args, **_kwargs: None,
    )
    connection = connector_module.NodeConnection(
        servicer,
        SimpleNamespace(
            host="gpu-node",
            endpoint="gpu-node:7444",
            secret="ovnode_test",
        ),
    )
    response = pb.RegisterResponse(worker_id="worker-1", session_token="session-1")
    monkeypatch.setattr(connector_module, "_fetch_pinned_certificate", lambda _c: b"cert")
    monkeypatch.setattr(connector_module.pb_grpc, "NodeServiceStub", lambda _c: Stub())
    monkeypatch.setattr(connection, "_channel", lambda _certificate: Channel())
    async def register(_request):
        return response

    monkeypatch.setattr(connection, "_register", register)

    with pytest.raises(RuntimeError, match="session went away"):
        await connection._connect_once()

    assert connection.worker_id == ""
    assert connection._stub is None


@pytest.mark.asyncio
async def test_attach_ends_when_its_incoming_reader_stops(tmp_path, monkeypatch):
    """A dead reader must not leave a node advertising a healthy session."""
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()

    class FakeClient:
        def prepare_inbound_session(self):
            pass

        def build_register_request(self):
            return pb.RegisterRequest()

        async def accept_registration(self, _response):
            pass

        def heartbeat_message(self):
            return pb.WorkerMessage(heartbeat=pb.Heartbeat())

        def start_heartbeat(self, _response):
            return asyncio.create_task(asyncio.sleep(60))

        async def next_outbound(self):
            await asyncio.Event().wait()

        async def stop(self):
            pass

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ()

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    servicer = worker.NodeListener(
        keys=worker.KeyStore(str(tmp_path / "keys.json")),
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda _artifacts, _key_id: FakeClient(),
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    monkeypatch.setattr(servicer, "_authenticate", lambda _context: ("panel-a", "A"))
    monkeypatch.setattr(servicer._keys, "is_active", lambda _key_id: True)

    async def frames():
        yield pb.ServerMessage(registered=pb.RegisterResponse())

    stream = servicer.Attach(frames(), Context())
    first = await anext(stream)
    assert first.WhichOneof("payload") == "register"
    confirmation = await anext(stream)
    assert confirmation.WhichOneof("payload") == "heartbeat"
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_first_attach_key_persistence_does_not_block_other_sessions(
    tmp_path, monkeypatch
):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    auth_started = threading.Event()
    release_auth = threading.Event()
    auth_thread = []
    real_save = keys._save_locked

    def blocked_save():
        auth_thread.append(threading.current_thread())
        auth_started.set()
        assert release_auth.wait(timeout=BARRIER_WATCHDOG_S)
        real_save()

    monkeypatch.setattr(keys, "_save_locked", blocked_save)

    class FakeClient:
        def prepare_inbound_session(self):
            pass

        def build_register_request(self):
            return pb.RegisterRequest()

        async def stop(self):
            pass

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda *_args: FakeClient(),
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    async def frames():
        if False:
            yield

    stream = servicer.Attach(frames(), Context())
    opening = asyncio.create_task(anext(stream))
    while not auth_started.is_set():
        await asyncio.sleep(0)

    try:
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        assert not opening.done()
        assert auth_thread[0] is not threading.current_thread()
    finally:
        release_auth.set()

    assert (await opening).WhichOneof("payload") == "register"
    await stream.aclose()


@pytest.mark.asyncio
async def test_first_attach_capability_probe_does_not_block_other_sessions(
    tmp_path, monkeypatch
):
    from worker import agent, capabilities
    from worker.inbound import listener as listener_module
    from worker.inbound import service as inbound_service

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    node = inbound_service.InboundNode()
    node._keys = keys
    monkeypatch.setattr(
        agent,
        "_paths",
        lambda: {
            "root": str(tmp_path / "worker"),
            "worker_key": str(tmp_path / "worker" / "worker-key.json"),
        },
    )
    probe_started = threading.Event()
    release_probe = threading.Event()
    probe_thread = []

    def blocked_discover(*, include_unavailable):
        assert include_unavailable is True
        probe_thread.append(threading.current_thread())
        probe_started.set()
        assert release_probe.wait(timeout=BARRIER_WATCHDOG_S)
        return []

    monkeypatch.setattr(capabilities, "discover", blocked_discover)
    monkeypatch.setattr(capabilities, "describe_gpus", lambda: [])

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=node._client_factory,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    async def frames():
        if False:
            yield

    stream = servicer.Attach(frames(), Context())
    opening = asyncio.create_task(anext(stream))
    while not probe_started.is_set():
        await asyncio.sleep(0)

    try:
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        assert not opening.done()
        assert probe_thread[0] is not threading.current_thread()
    finally:
        release_probe.set()

    assert (await opening).WhichOneof("payload") == "register"
    await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["prepare", "register"])
async def test_attach_setup_failure_releases_the_key_for_a_retry(
    tmp_path, monkeypatch, failure_point
):
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    calls = 0
    stopped = 0

    class BrokenClient:
        def prepare_inbound_session(self):
            if failure_point == "prepare":
                raise RuntimeError("client prepare failed")

        def build_register_request(self):
            if failure_point == "register":
                raise RuntimeError("client register setup failed")
            return pb.RegisterRequest()

        async def stop(self):
            nonlocal stopped
            stopped += 1

    def broken_factory(_artifacts, _key_id):
        nonlocal calls
        calls += 1
        return BrokenClient()

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ()

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    log = worker.ConnectionLog()
    servicer = worker.NodeListener(
        keys=worker.KeyStore(str(tmp_path / "keys.json")),
        log=log,
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=broken_factory,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    monkeypatch.setattr(servicer, "_authenticate", lambda _context: ("panel-a", "A"))

    async def frames():
        if False:
            yield

    for _ in range(2):
        with pytest.raises(StopAsyncIteration):
            await anext(servicer.Attach(frames(), Context()))

    assert calls == 2
    assert stopped == 2
    assert not servicer._attached_keys
    assert not servicer._revocations
    assert not servicer._protocols
    assert not servicer._clients
    assert log.snapshot()["sessions"] == []


@pytest.mark.asyncio
async def test_revocation_ends_an_attach_stalled_before_registration(tmp_path):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    waiting_for_registration = asyncio.Event()
    stopped = asyncio.Event()

    class FakeClient:
        def prepare_inbound_session(self):
            pass

        def build_register_request(self):
            return pb.RegisterRequest()

        async def stop(self):
            stopped.set()

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda _artifacts, _key_id: FakeClient(),
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    async def frames():
        waiting_for_registration.set()
        await asyncio.Event().wait()
        yield pb.ServerMessage()

    stream = servicer.Attach(frames(), Context())
    assert (await anext(stream)).WhichOneof("payload") == "register"
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(waiting_for_registration.wait(), timeout=HANG_GUARD_S)

    assert servicer.revoke_key(issued.key.key_id) is True
    goodbye = await asyncio.wait_for(pending, timeout=HANG_GUARD_S)
    assert goodbye.WhichOneof("payload") == "goodbye"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert stopped.is_set()
    assert issued.key.key_id not in servicer._protocols
    assert not servicer._attached_keys


@pytest.mark.asyncio
async def test_revocation_during_registration_is_not_confirmed(tmp_path):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    confirmation_built = False
    stopped = asyncio.Event()
    servicer = None

    class FakeClient:
        def prepare_inbound_session(self):
            pass

        def build_register_request(self):
            return pb.RegisterRequest()

        async def accept_registration(self, _response):
            assert servicer.revoke_key(issued.key.key_id) is True

        def heartbeat_message(self):
            nonlocal confirmation_built
            confirmation_built = True
            return pb.WorkerMessage(heartbeat=pb.Heartbeat())

        async def stop(self):
            stopped.set()

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            raise AssertionError(f"unexpected abort: {code}: {message}")

    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda _artifacts, _key_id: FakeClient(),
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    async def frames():
        yield pb.ServerMessage(registered=pb.RegisterResponse())

    stream = servicer.Attach(frames(), Context())
    assert (await anext(stream)).WhichOneof("payload") == "register"
    assert (await anext(stream)).WhichOneof("payload") == "goodbye"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert confirmation_built is False
    assert stopped.is_set()
    assert issued.key.key_id not in servicer._protocols


@pytest.mark.asyncio
async def test_a_revoked_key_stops_working_without_disturbing_the_others(inbound):
    alice = inbound.keys.issue("Alice")
    inbound.keys.revoke(alice.key.key_id)

    await inbound.connect_panel(secret=alice.secret, wait=False)
    await asyncio.wait_for(inbound.log.rejected_event.wait(), timeout=HANG_GUARD_S)

    assert len(inbound.pool) == 0


@pytest.mark.asyncio
async def test_key_revoked_during_registration_is_never_confirmed(
    inbound, monkeypatch
):
    """Revocation wins even after admission but before identity is durable."""
    from worker.transport.client import TerminalRegistrationError

    real_remember = inbound.keys.remember_worker_id

    def revoke_before_persist(key_id, worker_id):
        assert inbound.listener.revoke_key(key_id) is True
        real_remember(key_id, worker_id)

    monkeypatch.setattr(inbound.keys, "remember_worker_id", revoke_before_persist)
    await inbound.connect_panel(wait=False)

    with pytest.raises(TerminalRegistrationError, match="LOCAL_STATE"):
        await asyncio.wait_for(inbound.connector_task, timeout=HANG_GUARD_S)

    assert len(inbound.pool) == 0
    assert inbound.servicer._sessions == {}
    assert inbound.keys.worker_id_for(inbound.panel_key_id) == ""


@pytest.mark.asyncio
async def test_revoking_a_live_key_ends_every_session_it_authorized(inbound):
    await inbound.connect_panel()
    assert len(inbound.pool) == 1

    assert inbound.listener.revoke_key(inbound.panel_key_id) is True

    await asyncio.wait_for(inbound.log.closed_event.wait(), timeout=HANG_GUARD_S)
    await _until(lambda: len(inbound.pool) == 0)
    assert inbound.log.snapshot()["sessions"] == []


@pytest.mark.asyncio
async def test_revoking_a_live_key_cancels_its_running_executor(inbound):
    issued = inbound.keys.issue("Panel")
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()

    async def execute(_assignment, **_kwargs):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            execution_cancelled.set()
            raise

    inbound.listener._servicer._client_factory = (
        lambda artifacts, key_id: inbound._client_for(artifacts, key_id, execute)
    )
    await inbound.connect_panel(secret=issued.secret)
    task = inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(execution_started.wait(), timeout=HANG_GUARD_S)

    assert inbound.listener.revoke_key(issued.key.key_id) is True

    await asyncio.wait_for(execution_cancelled.wait(), timeout=HANG_GUARD_S)
    await asyncio.wait_for(inbound.log.closed_event.wait(), timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_revoking_a_disconnected_key_cancels_its_retained_executor(inbound):
    issued = inbound.keys.issue("Panel")
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()

    async def execute(_assignment, **_kwargs):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            execution_cancelled.set()
            raise

    inbound.listener._servicer._client_factory = (
        lambda artifacts, key_id: inbound._client_for(artifacts, key_id, execute)
    )
    await inbound.connect_panel(secret=issued.secret)
    inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(execution_started.wait(), timeout=HANG_GUARD_S)

    inbound.connector_task.cancel()
    await asyncio.gather(inbound.connector_task, return_exceptions=True)
    await _until(lambda: not inbound.listener._servicer._attached_keys)
    assert issued.key.key_id in inbound.listener._servicer._protocols
    assert execution_cancelled.is_set() is False

    assert inbound.listener.revoke_key(issued.key.key_id) is True
    assert issued.key.key_id not in inbound.listener._servicer._protocols
    await asyncio.wait_for(execution_cancelled.wait(), timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_terminal_panel_refusal_cancels_retained_inbound_execution(inbound):
    from worker import registry
    from worker.transport.client import TerminalRegistrationError

    issued = inbound.keys.issue("Panel")
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()
    client_box = {}

    async def execute(_assignment, **_kwargs):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            execution_cancelled.set()
            raise

    def client_factory(artifacts, key_id):
        client = inbound._client_for(artifacts, key_id, execute)
        client_box["client"] = client
        return client

    inbound.listener._servicer._client_factory = client_factory
    await inbound.connect_panel(secret=issued.secret)
    worker_id = next(iter(inbound.pool)).worker_id
    inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(execution_started.wait(), timeout=HANG_GUARD_S)

    inbound.connector_task.cancel()
    await asyncio.gather(inbound.connector_task, return_exceptions=True)
    await _until(lambda: not inbound.listener._servicer._attached_keys)
    assert execution_cancelled.is_set() is False
    assert registry.revoke(worker_id) is True

    await inbound.connect_panel(secret=issued.secret, wait=False)
    with pytest.raises(TerminalRegistrationError, match="AUTH_FAILED"):
        await asyncio.wait_for(inbound.connector_task, timeout=HANG_GUARD_S)

    await _until(lambda: not inbound.listener._servicer._attached_keys)
    assert issued.key.key_id not in inbound.listener._servicer._protocols
    assert client_box["client"]._running == {}
    await asyncio.wait_for(execution_cancelled.wait(), timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_revocation_stops_a_result_fetch_before_more_bytes_leave(tmp_path):
    import grpc

    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    result = await artifacts.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"a" * (listener_module._FETCH_CHUNK_BYTES * 2 + 1),
        {"filename": "result.wav"},
        key_id=issued.key.key_id,
    )
    staged = artifacts.open_result(result.artifact_id, key_id=issued.key.key_id)
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    class Aborted(RuntimeError):
        pass

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            assert code == grpc.StatusCode.UNAUTHENTICATED
            raise Aborted(message)

    stream = servicer.FetchResult(
        pb.ArtifactRef(artifact_id=result.artifact_id), Context()
    )
    first = await anext(stream)
    assert len(first.data) == listener_module._FETCH_CHUNK_BYTES

    assert servicer.revoke_key(issued.key.key_id) is True
    with pytest.raises(Aborted, match="revoked"):
        await anext(stream)

    await _until(
        lambda: artifacts.open_result(
            result.artifact_id, key_id=issued.key.key_id
        )
        is None
    )
    assert not os.path.exists(staged.path)


@pytest.mark.asyncio
async def test_blocked_result_read_does_not_stall_key_revocation(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    result = await artifacts.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"rendered audio",
        {"filename": "result.wav"},
        key_id=issued.key.key_id,
    )
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    read_started = Event()
    release_read = Event()
    real_open = open

    class BlockedReader:
        def __init__(self, handle):
            self._handle = handle

        def read(self, size):
            read_started.set()
            if not release_read.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release the artifact read")
            return self._handle.read(size)

        def close(self):
            self._handle.close()

    def blocked_open(*args, **kwargs):
        return BlockedReader(real_open(*args, **kwargs))

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    monkeypatch.setattr(listener_module, "open", blocked_open, raising=False)
    stream = servicer.FetchResult(
        pb.ArtifactRef(artifact_id=result.artifact_id), Context()
    )
    watchdog = Timer(5, release_read.set)
    watchdog.start()
    fetching = asyncio.create_task(anext(stream))

    async def wait_for_read():
        while not read_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_read(), timeout=HANG_GUARD_S)
        assert servicer.revoke_key(issued.key.key_id) is True
        cleanup = servicer._key_retirements[issued.key.key_id]
        assert not release_read.is_set(), "result reading blocked the gRPC event loop"
    finally:
        release_read.set()
        watchdog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetching
    await cleanup


@pytest.mark.asyncio
async def test_revocation_discards_a_partial_input_upload(tmp_path):
    import grpc

    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")

    class Aborted(RuntimeError):
        pass

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, code, message):
            assert code == grpc.StatusCode.UNAUTHENTICATED
            raise Aborted(message)

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=b"first", last=False)
        assert servicer.revoke_key(issued.key.key_id) is True
        yield pb.ArtifactChunk(ref=ref, offset=5, data=b"second", last=True)

    with pytest.raises(Aborted, match="revoked"):
        await servicer.PushInput(chunks(), Context())

    assert not any(files for _root, _dirs, files in os.walk(artifacts._root))


@pytest.mark.asyncio
async def test_blocked_input_write_does_not_stall_key_revocation(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    write_started = Event()
    release_write = Event()
    real_write_all = listener_module._write_all

    def blocked_write(handle, payload):
        write_started.set()
        if not release_write.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release the artifact write")
        real_write_all(handle, payload)

    ref = pb.ArtifactRef(
        artifact_id="input",
        filename="reference.wav",
        size_bytes=5,
        sha256=hashlib.sha256(b"audio").hexdigest(),
    )

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=b"audio", last=True)

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    monkeypatch.setattr(listener_module, "_write_all", blocked_write)
    # Deadlock escape for the regression path. The assertion below checks
    # ordering, not runner speed: revocation must finish before this releases
    # the blocked write.
    watchdog = Timer(5, release_write.set)
    watchdog.start()
    uploading = asyncio.create_task(servicer.PushInput(chunks(), Context()))

    async def wait_for_write():
        while not write_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_write(), timeout=HANG_GUARD_S)
        assert servicer.revoke_key(issued.key.key_id) is True
        cleanup = servicer._key_retirements[issued.key.key_id]
        assert not release_write.is_set(), "input writing blocked the gRPC event loop"
    finally:
        release_write.set()
        watchdog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await uploading
    await cleanup
    assert artifacts._reserved_input_bytes == 0
    assert not any(files for _root, _dirs, files in os.walk(artifacts._root))


@pytest.mark.asyncio
async def test_input_admission_and_mkdir_do_not_block_the_listener_loop(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    admission_started = Event()
    release_admission = Event()
    real_begin = artifacts.begin_input

    def blocked_begin(*args, **kwargs):
        admission_started.set()
        if not release_admission.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release input admission")
        return real_begin(*args, **kwargs)

    monkeypatch.setattr(artifacts, "begin_input", blocked_begin)
    payload = b"audio"
    ref = pb.ArtifactRef(
        artifact_id="input",
        filename="reference.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=payload, last=True)

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    watchdog = Timer(5, release_admission.set)
    watchdog.start()
    uploading = asyncio.create_task(servicer.PushInput(chunks(), Context()))
    try:
        await asyncio.wait_for(
            asyncio.to_thread(admission_started.wait), timeout=HANG_GUARD_S
        )
        assert not release_admission.is_set(), (
            "input admission blocked the gRPC event loop"
        )
    finally:
        release_admission.set()
        watchdog.cancel()
    ack = await uploading
    assert ack.committed is True


@pytest.mark.asyncio
async def test_artifact_untrack_cleanup_does_not_block_the_listener_loop(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import listener as listener_module

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    cleanup_started = Event()
    release_cleanup = Event()
    real_retry = artifacts.retry_result_acks

    def blocked_retry(key_id):
        cleanup_started.set()
        if not release_cleanup.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release ACK retry cleanup")
        real_retry(key_id)

    monkeypatch.setattr(artifacts, "retry_result_acks", blocked_retry)

    async def chunks():
        if False:
            yield None

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    watchdog = Timer(5, release_cleanup.set)
    watchdog.start()
    uploading = asyncio.create_task(servicer.PushInput(chunks(), Context()))
    try:
        await asyncio.wait_for(
            asyncio.to_thread(cleanup_started.wait), timeout=HANG_GUARD_S
        )
        assert not release_cleanup.is_set(), "input cleanup blocked the gRPC event loop"
    finally:
        release_cleanup.set()
        watchdog.cancel()
    ack = await uploading
    assert ack.error.code == "INPUT_INCOMPLETE"


@pytest.mark.asyncio
async def test_revocation_cancels_a_stalled_input_rpc_and_removes_its_partial(tmp_path):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    stalled = asyncio.Event()

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=b"first", last=False)
        stalled.set()
        await asyncio.Event().wait()

    upload = asyncio.create_task(servicer.PushInput(chunks(), Context()))
    await asyncio.wait_for(stalled.wait(), timeout=HANG_GUARD_S)
    assert any(files for _root, _dirs, files in os.walk(artifacts._root))

    assert await servicer.revoke_key_and_wait(issued.key.key_id) is True
    with pytest.raises(asyncio.CancelledError):
        await upload

    assert not any(files for _root, _dirs, files in os.walk(artifacts._root))
    assert servicer._artifact_tasks == {}


@pytest.mark.asyncio
async def test_revocation_cancels_a_backpressured_result_fetch(tmp_path):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    result = await artifacts.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"result bytes",
        {"filename": "result.wav"},
        key_id=issued.key.key_id,
    )
    staged = artifacts.open_result(result.artifact_id, key_id=issued.key.key_id)
    other = await artifacts.publish(
        pb.TaskRef(task_id="other-task", attempt_id="other-attempt"),
        b"other panel result",
        {"filename": "other.wav"},
        key_id="other-panel",
    )
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    backpressured = asyncio.Event()

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

        async def abort(self, _code, message):
            raise RuntimeError(message)

    async def consume():
        async for _chunk in servicer.FetchResult(
            pb.ArtifactRef(artifact_id=result.artifact_id), Context()
        ):
            backpressured.set()
            await asyncio.Event().wait()

    fetch = asyncio.create_task(consume())
    await asyncio.wait_for(backpressured.wait(), timeout=HANG_GUARD_S)

    assert await servicer.revoke_key_and_wait(issued.key.key_id) is True
    with pytest.raises(asyncio.CancelledError):
        await fetch
    await asyncio.sleep(0)

    assert not os.path.exists(staged.path)
    assert artifacts.open_result(result.artifact_id, key_id=issued.key.key_id) is None
    assert artifacts.open_result(other.artifact_id, key_id="other-panel") is not None
    assert servicer._artifact_tasks == {}


@pytest.mark.asyncio
async def test_failed_durable_revoke_still_retires_disconnected_work(
    tmp_path, monkeypatch
):
    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    stopped = asyncio.Event()

    class RetainedClient:
        async def stop(self):
            stopped.set()

    artifacts = worker.ArtifactStore(str(tmp_path / "staged"))
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    retained = RetainedClient()
    servicer._protocols[issued.key.key_id] = retained
    monkeypatch.setattr(
        keys,
        "_save_locked",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        await servicer.revoke_key_and_wait(issued.key.key_id)

    assert stopped.is_set()
    assert issued.key.key_id not in servicer._protocols
    assert not keys.is_active(issued.key.key_id)


@pytest.mark.asyncio
async def test_the_owner_can_see_who_connected_and_kick_them(inbound):
    await inbound.connect_panel()
    sessions = inbound.log.snapshot()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Test panel"

    assert inbound.log.kick(sessions[0]["session_id"]) is True

    # The kick has to land on an idle session too, which is the case a
    # loop that only wakes on outbound traffic would never notice.
    await asyncio.wait_for(inbound.log.closed_event.wait(), timeout=HANG_GUARD_S)
    assert inbound.log.snapshot()["sessions"] == []

    # And it has to STAY landed for a moment. The panel redials on its own, so
    # without a cooldown the person is back within two seconds and the button
    # appears to do nothing — which is what it did on hardware, where the log
    # read disconnected and connected in the same breath.
    assert inbound.log.cooling_down(sessions[0]["key_id"]) is True
    await asyncio.wait_for(inbound.log.rejected_event.wait(), timeout=HANG_GUARD_S)
    assert inbound.log.snapshot()["sessions"] == [], (
        "the kicked panel came straight back"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("initiator", ["panel", "node_owner"])
async def test_explicit_inbound_disconnect_cancels_retained_execution(
    inbound, initiator
):
    execution_started = asyncio.Event()
    execution_cancelled = asyncio.Event()

    async def execute(_assignment, **_kwargs):
        execution_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            execution_cancelled.set()
            raise

    inbound.listener._servicer._client_factory = (
        lambda artifacts, key_id: inbound._client_for(artifacts, key_id, execute)
    )
    await inbound.connect_panel()
    inbound.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = inbound.scheduler.next_assignment()
    assert assignment is not None
    assert await inbound.servicer.dispatch(assignment)
    await asyncio.wait_for(execution_started.wait(), timeout=HANG_GUARD_S)

    if initiator == "panel":
        await asyncio.wait_for(inbound.connection.stop(), timeout=HANG_GUARD_S)
    else:
        session = inbound.log.snapshot()["sessions"][0]
        assert inbound.log.kick(session["session_id"]) is True

    await asyncio.wait_for(execution_cancelled.wait(), timeout=HANG_GUARD_S)
    await _until(
        lambda: inbound.panel_key_id not in inbound.listener._servicer._protocols
    )


@pytest.mark.asyncio
async def test_an_input_pushed_before_the_assignment_is_there_when_the_task_asks(
    inbound, tmp_path
):
    """Inbound reverses the artifact direction, so ordering is a real hazard:
    an assignment that overtakes its own inputs fails on a file that is merely
    late."""
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    source = tmp_path / "reference.wav"
    source.write_bytes(b"reference audio bytes")

    declared = await inbound.connection.push_input(
        pb.ArtifactRef(artifact_id="ref-1", filename="reference.wav"), str(source)
    )

    assert declared.sha256
    destination = tmp_path / "staged-copy.wav"
    await inbound.artifacts.stage_in(
        declared, str(destination), key_id=inbound.panel_key_id
    )
    assert destination.read_bytes() == b"reference audio bytes"


@pytest.mark.asyncio
async def test_a_pushed_input_that_does_not_match_its_checksum_is_refused(
    inbound, tmp_path
):
    """A truncated or corrupted input that gets staged anyway becomes a render
    that succeeds against the wrong audio."""
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    source = tmp_path / "reference.wav"
    source.write_bytes(b"reference audio bytes")

    ref = pb.ArtifactRef(artifact_id="ref-2", filename="reference.wav", sha256="0" * 64)
    # Declared hash wins over the computed one only if the node checks; force
    # the mismatch by pinning a wrong hash on the way in.
    original = inbound.connection.push_input

    async def corrupted(_ref, path):
        return await original(ref, path)

    with pytest.raises(RuntimeError, match="checksum|did not accept"):
        # push_input recomputes the hash, so drive PushInput directly with a
        # ref whose declared hash cannot match.
        await _push_with_declared_hash(inbound, ref, str(source))


@pytest.mark.asyncio
async def test_an_offset_mismatch_removes_the_partial_input(inbound):
    from worker.inbound.listener import KEY_METADATA_KEY
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    ref = pb.ArtifactRef(artifact_id="offset-mismatch", filename="reference.wav")

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=b"first", last=False)
        yield pb.ArtifactChunk(ref=ref, offset=99, data=b"second", last=True)

    ack = await inbound.connection._stub.PushInput(
        chunks(),
        metadata=((KEY_METADATA_KEY, inbound.connection._connection.secret),),
    )

    assert ack.error.code == "OFFSET_MISMATCH"
    assert not any(files for _root, _dirs, files in os.walk(inbound.artifacts._root))


@pytest.mark.asyncio
async def test_input_push_enforces_declared_and_actual_size_limits(
    inbound, monkeypatch
):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    monkeypatch.setattr(listener_module, "MAX_INPUT_ARTIFACT_BYTES", 8)
    metadata = (
        (listener_module.KEY_METADATA_KEY, inbound.connection._connection.secret),
    )

    async def push(ref, data):
        async def chunks():
            yield pb.ArtifactChunk(ref=ref, offset=0, data=data, last=True)

        return await inbound.connection._stub.PushInput(chunks(), metadata=metadata)

    declared_oversize = await push(
        pb.ArtifactRef(
            artifact_id="declared-oversize",
            filename="reference.wav",
            size_bytes=9,
        ),
        b"x",
    )
    streamed_oversize = await push(
        pb.ArtifactRef(
            artifact_id="streamed-oversize", filename="reference.wav"
        ),
        b"123456789",
    )
    declared_short = await push(
        pb.ArtifactRef(
            artifact_id="declared-short",
            filename="reference.wav",
            size_bytes=8,
        ),
        b"1234",
    )

    assert declared_oversize.error.code == "INPUT_TOO_LARGE"
    assert streamed_oversize.error.code == "INPUT_TOO_LARGE"
    assert declared_short.error.code == "INPUT_SIZE_MISMATCH"
    assert not any(files for _root, _dirs, files in os.walk(inbound.artifacts._root))


@pytest.mark.asyncio
async def test_cancelled_result_fetch_removes_its_partial_destination(tmp_path):
    from worker.inbound.connector import NodeConnection
    from worker.protocol.gen import worker_v1_pb2 as pb

    fetch_waiting = asyncio.Event()

    class Stub:
        def FetchResult(self, _request, metadata=()):  # noqa: N802
            async def chunks():
                yield pb.ResultChunk(offset=0, data=b"partial", last=False)
                fetch_waiting.set()
                await asyncio.Event().wait()

            return chunks()

    connector = object.__new__(NodeConnection)
    connector._stub = Stub()
    connector._connection = SimpleNamespace(secret="panel-key")
    destination = tmp_path / "result.bin"
    fetch = asyncio.create_task(
        connector.fetch_result(pb.ArtifactRef(artifact_id="result"), str(destination))
    )
    await asyncio.wait_for(fetch_waiting.wait(), timeout=HANG_GUARD_S)
    assert destination.exists()

    fetch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetch

    assert not destination.exists()


@pytest.mark.asyncio
async def test_input_push_hashes_and_streams_bounded_blocks_off_the_loop(
    tmp_path, monkeypatch
):
    from worker.inbound import connector as connector_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    path = tmp_path / "reference.wav"
    payload = b"reference audio" * 200_000
    path.write_bytes(payload)
    real_open = open
    read_started = threading.Event()
    allow_read = threading.Event()
    read_sizes = []
    block_lock = threading.Lock()
    has_blocked = False

    class ObservedHandle:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def fileno(self):
            return self._handle.fileno()

        def read(self, size=-1):
            nonlocal has_blocked
            read_sizes.append(size)
            assert 0 < size <= connector_module._PUSH_CHUNK_BYTES
            with block_lock:
                should_block = not has_blocked
                has_blocked = True
            if should_block:
                read_started.set()
                assert allow_read.wait(timeout=BARRIER_WATCHDOG_S)
            return self._handle.read(size)

        def close(self):
            return self._handle.close()

    def observed_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return ObservedHandle(handle) if file == str(path) and mode == "rb" else handle

    class Stub:
        async def PushInput(self, chunks, metadata=()):  # noqa: N802
            received = bytearray()
            async for chunk in chunks:
                received.extend(chunk.data)
            assert bytes(received) == payload
            return pb.ResultAck(committed=True)

    connection = object.__new__(connector_module.NodeConnection)
    connection._stub = Stub()
    connection._connection = SimpleNamespace(secret="panel-key")
    monkeypatch.setattr(connector_module, "open", observed_open, raising=False)
    loop_was_responsive = asyncio.Event()

    async def observe_loop():
        while not allow_read.is_set():
            if read_started.is_set():
                loop_was_responsive.set()
            await asyncio.sleep(0)

    observer = asyncio.create_task(observe_loop())
    release = threading.Timer(0.2, allow_read.set)
    release.start()
    try:
        declared = await connection.push_input(
            pb.ArtifactRef(artifact_id="reference"), str(path)
        )
    finally:
        allow_read.set()
        release.cancel()
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)

    assert loop_was_responsive.is_set()
    assert read_sizes and all(
        0 < size <= connector_module._PUSH_CHUNK_BYTES for size in read_sizes
    )
    assert declared.size_bytes == len(payload)
    assert declared.sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_input_push_refuses_a_staged_file_replaced_after_hash(tmp_path):
    from worker.inbound import connector as connector_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    path = tmp_path / "reference.wav"
    path.write_bytes(b"original reference")
    sent = []

    class Stub:
        async def PushInput(self, chunks, metadata=()):  # noqa: N802
            path.write_bytes(b"replacement reference is longer")
            async for chunk in chunks:
                sent.append(chunk)
            return pb.ResultAck(committed=True)

    connection = object.__new__(connector_module.NodeConnection)
    connection._stub = Stub()
    connection._connection = SimpleNamespace(secret="panel-key")

    with pytest.raises(RuntimeError, match="staged task input changed"):
        await connection.push_input(
            pb.ArtifactRef(artifact_id="reference"), str(path)
        )

    assert sent == []


@pytest.mark.asyncio
async def test_staged_artifacts_are_isolated_by_panel_key(tmp_path):
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = _worker_modules().ArtifactStore(str(tmp_path / "staged"))
    result = await store.publish(
        pb.TaskRef(task_id="t1", attempt_id="a1"),
        b"alice audio",
        {"filename": "out.wav"},
        key_id="alice",
    )

    assert store.open_result(result.artifact_id, key_id="bob") is None
    store.result_acked(result.artifact_id, key_id="bob")
    assert store.open_result(result.artifact_id, key_id="alice") is not None

    incoming = pb.ArtifactRef(artifact_id="shared-id", filename="ref.wav")
    payload = b"alice reference"
    path = store.begin_input(incoming, key_id="alice")
    with open(path, "wb") as handle:
        handle.write(payload)
    store.commit_input(
        incoming,
        path,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        key_id="alice",
    )
    with pytest.raises(RuntimeError, match="did not send input"):
        await store.stage_in(incoming, str(tmp_path / "bob.wav"), key_id="bob")

    await store.stage_in(incoming, str(tmp_path / "alice.wav"), key_id="alice")
    assert (tmp_path / "alice.wav").read_bytes() == b"alice reference"

    bob_result = await store.publish(
        pb.TaskRef(task_id="t2", attempt_id="a2"),
        b"bob audio",
        {"filename": "bob.wav"},
        key_id="bob",
    )
    store.purge_key("alice")

    assert store.open_result(result.artifact_id, key_id="alice") is None
    assert store.open_result(bob_result.artifact_id, key_id="bob") is not None
    with pytest.raises(RuntimeError, match="did not send input"):
        await store.stage_in(incoming, str(tmp_path / "purged.wav"), key_id="alice")


@pytest.mark.asyncio
async def test_result_ack_retries_a_transient_delete_failure(tmp_path, monkeypatch):
    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    result = await store.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"rendered audio",
        {"filename": "result.wav"},
        key_id="panel",
    )
    staged = store.open_result(result.artifact_id, key_id="panel")
    real_remove = artifacts_module.os.remove
    attempts = 0

    def transient_remove(path):
        nonlocal attempts
        if path == staged.path and attempts == 0:
            attempts += 1
            raise PermissionError("result is still open")
        return real_remove(path)

    monkeypatch.setattr(artifacts_module.os, "remove", transient_remove)

    store.result_acked(result.artifact_id, key_id="panel")
    assert store.open_result(result.artifact_id, key_id="panel") is staged
    assert os.path.exists(staged.path)

    store.retry_result_acks("panel")
    assert store.open_result(result.artifact_id, key_id="panel") is None
    assert not os.path.exists(staged.path)


@pytest.mark.asyncio
async def test_result_ack_deletion_does_not_block_the_attach_loop(
    inbound, monkeypatch
):
    from threading import Event, Timer

    from worker.protocol.gen import worker_v1_pb2 as pb
    from worker.transport.client import PendingResult

    await inbound.connect_panel()
    protocol = inbound.listener._servicer._protocols[inbound.panel_key_id]
    artifact = await inbound.artifacts.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"rendered audio",
        {"filename": "result.wav"},
        key_id=inbound.panel_key_id,
    )
    staged = inbound.artifacts.open_result(
        artifact.artifact_id, key_id=inbound.panel_key_id
    )
    assert staged is not None
    ref = pb.TaskRef(
        task_id="task", attempt_id="attempt", session_epoch=protocol._epoch
    )
    protocol._pending[protocol._key(ref)] = PendingResult(
        ref=ref,
        result_json="{}",
        inline_payload=b"",
        artifacts=[artifact],
    )
    cleanup_started = Event()
    release_cleanup = Event()
    real_acked = inbound.artifacts.result_acked

    def blocked_acked(artifact_id, *, key_id):
        cleanup_started.set()
        if not release_cleanup.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release result ACK cleanup")
        real_acked(artifact_id, key_id=key_id)

    monkeypatch.setattr(inbound.artifacts, "result_acked", blocked_acked)
    watchdog = Timer(5, release_cleanup.set)
    watchdog.start()
    handling = asyncio.create_task(
        protocol.handle_server_message(
            pb.ServerMessage(result_ack=pb.ResultAckMessage(ref=ref))
        )
    )
    try:
        await asyncio.wait_for(
            asyncio.to_thread(cleanup_started.wait), timeout=HANG_GUARD_S
        )
        assert not release_cleanup.is_set(), (
            "result cleanup blocked the gRPC event loop"
        )
    finally:
        release_cleanup.set()
        watchdog.cancel()
    await handling
    assert not os.path.exists(staged.path)


@pytest.mark.asyncio
async def test_result_acks_prune_every_artifact_and_kind_directory(tmp_path):
    from worker.inbound.artifacts import ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    root = tmp_path / "staged"
    store = ArtifactStore(str(root))
    results = [
        await store.publish(
            pb.TaskRef(task_id=f"task-{index}", attempt_id=f"attempt-{index}"),
            b"rendered audio",
            {"filename": "result.wav"},
            key_id="panel",
        )
        for index in range(10)
    ]

    for result in results:
        store.result_acked(result.artifact_id, key_id="panel")

    assert list(root.iterdir()) == []
    assert store._orphaned_directories == set()


@pytest.mark.asyncio
async def test_failed_empty_directory_prune_is_retried(tmp_path, monkeypatch):
    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    root = tmp_path / "staged"
    store = artifacts_module.ArtifactStore(str(root))
    result = await store.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"rendered audio",
        {"filename": "result.wav"},
        key_id="panel",
    )
    artifact_dir = os.path.dirname(
        store.open_result(result.artifact_id, key_id="panel").path
    )
    real_rmdir = artifacts_module.os.rmdir
    failed = False

    def transient_rmdir(path):
        nonlocal failed
        if path == artifact_dir and not failed:
            failed = True
            raise PermissionError("directory is transiently locked")
        return real_rmdir(path)

    monkeypatch.setattr(artifacts_module.os, "rmdir", transient_rmdir)
    store.result_acked(result.artifact_id, key_id="panel")
    assert artifact_dir in store._orphaned_directories

    store.purge()
    assert list(root.iterdir()) == []
    assert store._orphaned_directories == set()


@pytest.mark.asyncio
async def test_restart_removes_unindexed_staging_generations(tmp_path):
    from worker.inbound.artifacts import ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    root = tmp_path / "staged"
    first = ArtifactStore(str(root))
    result = await first.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"unacknowledged result",
        {"filename": "result.wav"},
        key_id="panel",
    )
    result_path = first.open_result(result.artifact_id, key_id="panel").path
    incoming = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    payload = b"staged input"
    input_path = first.begin_input(incoming, key_id="panel")
    with open(input_path, "wb") as handle:
        handle.write(payload)
    first.commit_input(
        incoming,
        input_path,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        key_id="panel",
    )

    restarted = ArtifactStore(str(root))

    assert not os.path.exists(result_path)
    assert not os.path.exists(input_path)
    assert restarted._orphaned_paths == set()


@pytest.mark.asyncio
async def test_repeated_authenticated_input_calls_hit_the_per_key_disk_quota(
    tmp_path,
):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    artifacts = worker.ArtifactStore(
        str(tmp_path / "staged"),
        max_input_bytes_per_key=10,
        max_input_bytes_total=100,
    )
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

    async def push(artifact_id):
        payload = b"123456"
        ref = pb.ArtifactRef(
            artifact_id=artifact_id,
            filename="reference.wav",
            size_bytes=len(payload),
        )

        async def chunks():
            yield pb.ArtifactChunk(ref=ref, offset=0, data=payload, last=True)

        return await servicer.PushInput(chunks(), Context())

    first = await push("first")
    refused = await push("fresh-id")

    assert first.committed is True
    assert refused.committed is False
    assert refused.error.code == "INPUT_QUOTA_EXCEEDED"
    assert artifacts._committed_input_bytes == 6
    assert artifacts._reserved_input_bytes == 0


@pytest.mark.asyncio
async def test_lost_input_ack_reuses_verified_bytes_at_exact_quota(tmp_path):
    from worker.inbound import listener as listener_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    worker = _worker_modules()
    keys = worker.KeyStore(str(tmp_path / "keys.json"))
    issued = keys.issue("Panel")
    payload = b"123456"
    artifacts = worker.ArtifactStore(
        str(tmp_path / "staged"),
        max_input_bytes_per_key=len(payload),
        max_input_bytes_total=len(payload),
        max_inputs_per_key=1,
        max_inputs_total=1,
    )
    servicer = worker.NodeListener(
        keys=keys,
        log=worker.ConnectionLog(),
        artifacts=artifacts,
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )._servicer
    ref = pb.ArtifactRef(
        artifact_id="same-input",
        filename="reference.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    class Context:
        def peer(self):
            return "ipv4:10.0.0.2:45000"

        def invocation_metadata(self):
            return ((listener_module.KEY_METADATA_KEY, issued.secret),)

    async def push():
        async def chunks():
            yield pb.ArtifactChunk(ref=ref, offset=0, data=payload, last=True)

        return await servicer.PushInput(chunks(), Context())

    assert (await push()).committed is True
    retried = await push()

    assert retried.committed is True
    assert retried.bytes_received == len(payload)
    assert len(artifacts._in) == 1
    assert artifacts._committed_input_bytes == len(payload)
    assert artifacts._reserved_input_bytes == 0


@pytest.mark.asyncio
async def test_repeated_unacked_results_hit_the_per_key_disk_quota(tmp_path):
    from worker.inbound.artifacts import ArtifactQuotaExceeded, ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = ArtifactStore(
        str(tmp_path / "staged"),
        max_result_bytes_per_key=10,
        max_result_bytes_total=100,
    )
    repeated_ref = pb.TaskRef(task_id="same-task", attempt_id="same-attempt")
    first = await store.publish(
        repeated_ref,
        b"123456",
        {"filename": "result.wav"},
        key_id="panel",
    )

    with pytest.raises(ArtifactQuotaExceeded, match="panel"):
        await store.publish(
            repeated_ref,
            b"abcdef",
            {"filename": "result.wav"},
            key_id="panel",
        )

    assert len(store._out) == 1
    assert store._committed_result_bytes == 6
    assert store._reserved_result_bytes == 0

    store.result_acked(first.artifact_id, key_id="panel")
    replacement = await store.publish(
        repeated_ref,
        b"abcdef",
        {"filename": "result.wav"},
        key_id="panel",
    )
    assert store.open_result(replacement.artifact_id, key_id="panel") is not None
    assert store._committed_result_bytes == 6


@pytest.mark.asyncio
async def test_parallel_results_reserve_against_one_global_disk_quota(tmp_path):
    from worker.inbound.artifacts import ArtifactQuotaExceeded, ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = ArtifactStore(
        str(tmp_path / "staged"),
        max_result_bytes_per_key=10,
        max_result_bytes_total=6,
    )

    async def publish(key_id):
        return await store.publish(
            pb.TaskRef(task_id=key_id, attempt_id="attempt"),
            b"123456",
            {"filename": "result.wav"},
            key_id=key_id,
        )

    outcomes = await asyncio.gather(
        publish("alice"), publish("bob"), return_exceptions=True
    )
    admitted = [value for value in outcomes if isinstance(value, pb.ArtifactRef)]
    refused = [
        value for value in outcomes if isinstance(value, ArtifactQuotaExceeded)
    ]

    assert len(admitted) == len(refused) == 1
    assert store._committed_result_bytes == 6
    assert store._reserved_result_bytes == 0


@pytest.mark.asyncio
async def test_unacked_result_count_quota_releases_on_ack_purge_and_ttl(
    tmp_path, monkeypatch
):
    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = artifacts_module.ArtifactStore(
        str(tmp_path / "staged"),
        max_results_per_key=1,
        max_results_total=1,
    )
    ref = pb.TaskRef(task_id="task", attempt_id="attempt")

    async def publish():
        return await store.publish(
            ref, b"result", {"filename": "result.wav"}, key_id="panel"
        )

    first = await publish()
    with pytest.raises(artifacts_module.ArtifactQuotaExceeded):
        await publish()
    store.result_acked(first.artifact_id, key_id="panel")

    second = await publish()
    store.purge_key("panel")
    assert store.open_result(second.artifact_id, key_id="panel") is None

    await publish()
    monkeypatch.setattr(artifacts_module, "_STALE_SECONDS", -1)
    replacement = await publish()
    assert store.open_result(replacement.artifact_id, key_id="panel") is not None
    assert store._committed_result_bytes == len(b"result")


def test_parallel_keys_reserve_against_one_global_input_quota(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from worker.inbound.artifacts import ArtifactQuotaExceeded, ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = ArtifactStore(
        str(tmp_path / "staged"),
        max_input_bytes_per_key=10,
        max_input_bytes_total=10,
    )
    barrier = Barrier(3)

    def reserve(key_id):
        barrier.wait()
        try:
            return store.begin_input(
                pb.ArtifactRef(
                    artifact_id=f"{key_id}-input",
                    filename="reference.wav",
                    size_bytes=6,
                ),
                key_id=key_id,
            )
        except ArtifactQuotaExceeded as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve, key_id) for key_id in ("alice", "bob")]
        barrier.wait()
        outcomes = [future.result(timeout=2) for future in futures]

    admitted = [value for value in outcomes if isinstance(value, str)]
    refused = [
        value for value in outcomes if isinstance(value, ArtifactQuotaExceeded)
    ]
    assert len(admitted) == len(refused) == 1
    assert store._reserved_input_bytes == 6

    store.discard_input(admitted[0])
    assert store._reserved_input_bytes == 0


def test_input_quota_accounting_releases_retries_purges_and_ttl(
    tmp_path, monkeypatch
):
    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = artifacts_module.ArtifactStore(
        str(tmp_path / "staged"),
        max_input_bytes_per_key=12,
        max_input_bytes_total=12,
    )
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    payload = b"123456"
    digest = hashlib.sha256(payload).hexdigest()

    def upload(reference):
        temporary = store.begin_input(
            reference, key_id="panel", reserve_bytes=len(payload)
        )
        with open(temporary, "wb") as handle:
            handle.write(payload)
        return store.commit_input(
            reference, temporary, digest, len(payload), key_id="panel"
        )

    committed = upload(ref)
    assert upload(ref) == committed
    assert store._committed_input_bytes == 6
    assert store._reserved_input_bytes == 0

    store.purge_key("panel")
    assert store._committed_input_bytes == 0

    committed = upload(ref)
    monkeypatch.setattr(artifacts_module, "_STALE_SECONDS", -1)
    replacement = store.begin_input(
        pb.ArtifactRef(artifact_id="new", filename="reference.wav"),
        key_id="panel",
        reserve_bytes=12,
    )
    assert not os.path.exists(committed)
    assert store._committed_input_bytes == 0
    assert store._reserved_input_bytes == 12
    store.discard_input(replacement)


def test_restart_releases_crash_surviving_input_bytes_from_the_quota(tmp_path):
    from worker.inbound.artifacts import ArtifactStore
    from worker.protocol.gen import worker_v1_pb2 as pb

    root = tmp_path / "staged"
    first = ArtifactStore(
        str(root), max_input_bytes_per_key=6, max_input_bytes_total=6
    )
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    payload = b"123456"
    temporary = first.begin_input(ref, key_id="panel", reserve_bytes=6)
    with open(temporary, "wb") as handle:
        handle.write(payload)
    first.commit_input(
        ref,
        temporary,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        key_id="panel",
    )

    restarted = ArtifactStore(
        str(root), max_input_bytes_per_key=6, max_input_bytes_total=6
    )
    reservation = restarted.begin_input(
        pb.ArtifactRef(artifact_id="replacement", filename="reference.wav"),
        key_id="panel",
        reserve_bytes=6,
    )

    assert restarted._committed_input_bytes == 0
    assert restarted._reserved_input_bytes == 6
    restarted.discard_input(reservation)


def test_artifact_store_persists_its_root_directory_entry(tmp_path, monkeypatch):
    from worker.inbound import artifacts as artifacts_module

    fsynced = []
    real_fsync_parent = artifacts_module._fsync_parent_directory

    def record_fsync(directory):
        fsynced.append(os.path.abspath(directory))
        return real_fsync_parent(directory)

    monkeypatch.setattr(
        artifacts_module, "_fsync_parent_directory", record_fsync
    )
    root = tmp_path / "staged"
    artifacts_module.ArtifactStore(str(root))

    assert str(tmp_path) in fsynced


@pytest.mark.asyncio
async def test_cancelled_result_publish_drains_write_before_removing_file(
    tmp_path, monkeypatch
):
    from threading import Event

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    real_open = open
    write_started = Event()
    allow_write = Event()
    write_finished = Event()

    class BlockedHandle:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            try:
                return self._handle.__exit__(*args)
            finally:
                write_finished.set()

        def write(self, payload):
            write_started.set()
            if not allow_write.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release the staged result write")
            return self._handle.write(payload)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def blocked_open(*args, **kwargs):
        return BlockedHandle(real_open(*args, **kwargs))

    async def wait_until_set(event):
        while not event.is_set():
            await asyncio.sleep(0)

    monkeypatch.setattr(artifacts_module, "open", blocked_open, raising=False)
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    publish = asyncio.create_task(
        store.publish(
            pb.TaskRef(task_id="task", attempt_id="attempt"),
            b"rendered audio",
            {"filename": "result.wav"},
            key_id="panel",
        )
    )
    await asyncio.wait_for(wait_until_set(write_started), timeout=HANG_GUARD_S)

    publish.cancel()
    await asyncio.sleep(0)
    cancellation_is_draining = not publish.done()

    allow_write.set()
    with pytest.raises(asyncio.CancelledError):
        await publish
    await asyncio.wait_for(wait_until_set(write_finished), timeout=HANG_GUARD_S)

    assert cancellation_is_draining, "cancellation must wait for the active disk write"
    assert store._out == {}
    assert not any(files for _root, _dirs, files in os.walk(store._root))


@pytest.mark.asyncio
async def test_result_publish_sweep_does_not_block_the_listener_loop(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    sweep_started = Event()
    release_sweep = Event()
    real_sweep = store._sweep_locked

    def blocked_sweep(*args, **kwargs):
        sweep_started.set()
        if not release_sweep.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release the staging sweep")
        real_sweep(*args, **kwargs)

    monkeypatch.setattr(store, "_sweep_locked", blocked_sweep)
    watchdog = Timer(5, release_sweep.set)
    watchdog.start()
    publish = asyncio.create_task(
        store.publish(
            pb.TaskRef(task_id="task", attempt_id="attempt"),
            b"rendered audio",
            {"filename": "result.wav"},
            key_id="panel",
        )
    )
    try:
        await asyncio.wait_for(asyncio.to_thread(sweep_started.wait), timeout=HANG_GUARD_S)
        assert not release_sweep.is_set(), (
            "artifact sweeping blocked the gRPC event loop"
        )
    finally:
        release_sweep.set()
        watchdog.cancel()
    assert (await publish).artifact_id


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["file", "directory"])
async def test_result_publish_fsync_failure_exposes_no_artifact(
    tmp_path, monkeypatch, failure_point
):
    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    def fail_fsync(_value):
        raise OSError("fsync failed")

    if failure_point == "file":
        import stat

        real_fsync = artifacts_module.os.fsync

        def fail_file_fsync(descriptor):
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                fail_fsync(descriptor)
            return real_fsync(descriptor)

        monkeypatch.setattr(artifacts_module.os, "fsync", fail_file_fsync)
    else:
        real_fsync_parent = artifacts_module._fsync_parent_directory
        fsync_calls = 0

        def fail_post_replace_fsync(directory):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 4:
                fail_fsync(directory)
            return real_fsync_parent(directory)

        monkeypatch.setattr(
            artifacts_module,
            "_fsync_parent_directory",
            fail_post_replace_fsync,
        )

    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    with pytest.raises(OSError, match="fsync failed"):
        await store.publish(
            pb.TaskRef(task_id="task", attempt_id="attempt"),
            b"rendered audio",
            {"filename": "result.wav"},
            key_id="panel",
        )

    assert store._out == {}
    assert not any(files for _root, _dirs, files in os.walk(store._root))


@pytest.mark.asyncio
async def test_parallel_input_retry_cannot_truncate_a_staged_reader(
    tmp_path, monkeypatch
):
    from threading import Event

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    payload = b"immutable reference audio"
    digest = hashlib.sha256(payload).hexdigest()
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    first = store.begin_input(ref, key_id="panel")
    with open(first, "wb") as handle:
        handle.write(payload)
    committed = store.commit_input(
        ref, first, digest, len(payload), key_id="panel"
    )

    copy_opened = Event()
    allow_copy = Event()

    def blocked_copyfile(source, destination):
        with open(source, "rb") as source_handle:
            copy_opened.set()
            if not allow_copy.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release the staged input copy")
            with open(destination, "wb") as destination_handle:
                destination_handle.write(source_handle.read())

    async def wait_until_set(event):
        while not event.is_set():
            await asyncio.sleep(0)

    monkeypatch.setattr(artifacts_module.shutil, "copyfile", blocked_copyfile)
    destination = tmp_path / "executor-input.wav"
    staging = asyncio.create_task(
        store.stage_in(ref, str(destination), key_id="panel")
    )
    await asyncio.wait_for(wait_until_set(copy_opened), timeout=HANG_GUARD_S)

    retry = store.begin_input(ref, key_id="panel")
    assert retry != committed
    retry_handle = open(retry, "wb")
    try:
        # Keep the retry destination open and empty while the existing reader
        # consumes its source. A stable shared path would be truncated here.
        allow_copy.set()
        await staging
        assert destination.read_bytes() == payload
        retry_handle.write(payload)
    finally:
        retry_handle.close()

    retry_committed = store.commit_input(
        ref, retry, digest, len(payload), key_id="panel"
    )
    assert retry_committed == committed
    files = [
        os.path.join(root, name)
        for root, _directories, names in os.walk(store._root)
        for name in names
    ]
    assert files == [committed]


@pytest.mark.asyncio
async def test_duplicate_input_validation_never_blocks_other_panel_admission(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    payload = b"immutable reference audio"
    digest = hashlib.sha256(payload).hexdigest()
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    first = store.begin_input(ref, key_id="panel", reserve_bytes=len(payload))
    with open(first, "wb") as handle:
        handle.write(payload)
    committed = store.commit_input(
        ref, first, digest, len(payload), key_id="panel"
    )

    retry = store.begin_input(ref, key_id="panel", reserve_bytes=len(payload))
    with open(retry, "wb") as handle:
        handle.write(payload)
    validation_started = Event()
    release_validation = Event()
    real_matches = artifacts_module._file_matches

    def blocked_matches(path, expected_digest, expected_size):
        if path == committed:
            validation_started.set()
            if not release_validation.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release duplicate validation")
        return real_matches(path, expected_digest, expected_size)

    monkeypatch.setattr(artifacts_module, "_file_matches", blocked_matches)
    watchdog = Timer(5, release_validation.set)
    watchdog.start()
    committing = asyncio.create_task(
        store.commit_input_async(
            ref, retry, digest, len(payload), key_id="panel"
        )
    )

    async def wait_for_validation():
        while not validation_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_validation(), timeout=HANG_GUARD_S)
        other = store.begin_input(
            pb.ArtifactRef(artifact_id="other", filename="reference.wav"),
            key_id="other-panel",
            reserve_bytes=1,
        )
        store.discard_input(other)
        assert not release_validation.is_set(), (
            "duplicate hashing blocked the gRPC event loop"
        )
    finally:
        release_validation.set()
        watchdog.cancel()
    assert await committing == committed


def test_input_commit_fsync_failure_exposes_no_input(tmp_path, monkeypatch):
    import stat

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    payload = b"reference audio"
    digest = hashlib.sha256(payload).hexdigest()
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    temporary = store.begin_input(ref, key_id="panel")
    with open(temporary, "wb") as handle:
        handle.write(payload)

    real_fsync = artifacts_module.os.fsync

    def fail_fsync(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr(artifacts_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        store.commit_input(
            ref, temporary, digest, len(payload), key_id="panel"
        )

    assert store._in == {}
    assert not any(files for _root, _dirs, files in os.walk(store._root))


@pytest.mark.asyncio
async def test_adopted_input_final_is_durable_and_not_swept_as_an_orphan(
    tmp_path, monkeypatch
):
    import stat

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    payload = b"crash-surviving reference"
    digest = hashlib.sha256(payload).hexdigest()
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    final = store._input_final_path(ref, digest, key_id="panel")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    with open(final, "wb") as handle:
        handle.write(payload)
    store._orphaned_paths.add(final)

    temporary = store.begin_input(ref, key_id="panel")
    with open(temporary, "wb") as handle:
        handle.write(payload)

    real_remove = artifacts_module.os.remove
    final_remove_attempts = 0

    def transient_orphan_remove(path):
        nonlocal final_remove_attempts
        if path == final:
            final_remove_attempts += 1
            if final_remove_attempts == 1:
                raise PermissionError("the crash-surviving final is still open")
        return real_remove(path)

    durability_events = []
    real_fsync = artifacts_module.os.fsync

    def record_fsync(descriptor):
        kind = "file" if stat.S_ISREG(os.fstat(descriptor).st_mode) else "directory"
        durability_events.append(kind)
        return real_fsync(descriptor)

    monkeypatch.setattr(artifacts_module.os, "remove", transient_orphan_remove)
    monkeypatch.setattr(artifacts_module.os, "fsync", record_fsync)

    assert (
        store.commit_input(
            ref, temporary, digest, len(payload), key_id="panel"
        )
        == final
    )
    assert final not in store._orphaned_paths
    file_fsync = durability_events.index("file")
    # The directory half of the barrier exists only where the platform has one.
    # `_fsync_parent_directory` returns immediately without `os.O_DIRECTORY`,
    # which Windows does not define — so gate on the same condition the product
    # uses rather than on the OS name, and the two cannot drift apart.
    if hasattr(os, "O_DIRECTORY"):
        assert "directory" in durability_events[file_fsync + 1 :]

    # Any later write runs the orphan sweep. The adopted final must no longer
    # be a deletion candidate once its durability barrier has succeeded.
    await store.publish(
        pb.TaskRef(task_id="task", attempt_id="attempt"),
        b"result",
        {"filename": "result.wav"},
        key_id="panel",
    )
    assert final_remove_attempts == 1
    destination = tmp_path / "staged-input.wav"
    await store.stage_in(ref, str(destination), key_id="panel")
    assert destination.read_bytes() == payload


@pytest.mark.asyncio
async def test_cancelled_input_staging_drains_copy_before_returning(
    tmp_path, monkeypatch
):
    from threading import Event

    from worker.inbound import artifacts as artifacts_module
    from worker.protocol.gen import worker_v1_pb2 as pb

    real_copyfile = artifacts_module.shutil.copyfile
    copy_started = Event()
    allow_copy = Event()
    copy_finished = Event()

    def blocked_copyfile(source, destination):
        copy_started.set()
        if not allow_copy.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release the staged input copy")
        try:
            return real_copyfile(source, destination)
        finally:
            copy_finished.set()

    async def wait_until_set(event):
        while not event.is_set():
            await asyncio.sleep(0)

    monkeypatch.setattr(artifacts_module.shutil, "copyfile", blocked_copyfile)
    store = artifacts_module.ArtifactStore(str(tmp_path / "staged"))
    ref = pb.ArtifactRef(artifact_id="input", filename="reference.wav")
    payload = b"reference audio"
    source = store.begin_input(ref, key_id="panel")
    with open(source, "wb") as handle:
        handle.write(payload)
    store.commit_input(
        ref,
        source,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        key_id="panel",
    )
    destination = tmp_path / "input.part"

    staging = asyncio.create_task(
        store.stage_in(ref, str(destination), key_id="panel")
    )
    await asyncio.wait_for(wait_until_set(copy_started), timeout=HANG_GUARD_S)

    staging.cancel()
    await asyncio.sleep(0)
    cancellation_is_draining = not staging.done()

    allow_copy.set()
    with pytest.raises(asyncio.CancelledError):
        await staging
    await asyncio.wait_for(wait_until_set(copy_finished), timeout=HANG_GUARD_S)

    assert cancellation_is_draining, "cancellation must wait for the active disk copy"
    destination.unlink()
    assert not destination.exists()


async def _push_with_declared_hash(inbound, ref, path):
    """Push bytes while declaring a hash that does not describe them."""
    from worker.inbound.listener import KEY_METADATA_KEY
    from worker.protocol.gen import worker_v1_pb2 as pb

    stub = inbound.connection._stub
    data = open(path, "rb").read()

    async def chunks():
        yield pb.ArtifactChunk(ref=ref, offset=0, data=data, last=True)

    ack = await stub.PushInput(
        chunks(), metadata=((KEY_METADATA_KEY, inbound.connection._connection.secret),)
    )
    if not ack.committed:
        raise RuntimeError(ack.error.message or "refused")


@pytest.mark.asyncio
async def test_a_different_machine_cannot_re_adopt_an_enrolled_workers_identity(
    inbound, tmp_path
):
    """The re-adoption path exists so a node that lost the id this panel gave
    it can still reconnect on proof of key possession. It must not become a way
    for a *different* key to inherit an enrolled worker: an attacker holding
    only the API key would otherwise take over the trusted machine's identity.
    """
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    enrolled = inbound.worker.registry.list_workers()[0]

    # A second machine: valid key, valid self-signature, wrong identity.
    impostor = inbound.worker.WorkerKeypair.generate()
    challenge, nonce = b"c" * 32, b"n" * 32
    forged = pb.RegisterRequest(
        envelope=pb.Envelope(sequence=0),
        worker_id=enrolled.id,
        public_key=impostor.public_bytes(),
        challenge=challenge,
        nonce=nonce,
        challenge_signature=impostor.sign(
            __import__("worker.identity", fromlist=["identity"]).challenge_message(
                challenge=challenge, worker_id=enrolled.id, session_epoch=0, nonce=nonce
            )
        ),
    )

    assert (
        inbound.worker.NodeConnection._proves_key_possession(forged, enrolled) is False
    )


@pytest.mark.asyncio
async def test_the_node_keeps_sending_heartbeats_after_it_registers(
    inbound, monkeypatch
):
    """A session that goes quiet is declared dead and flaps forever.

    Found on hardware, not here: the inbound Attach handler started the read
    pump and the outbound loop but never the heartbeat loop that the outbound
    path starts in `_connect_once`. The node registered, said nothing more, was
    declared dead ~90 seconds later, reconnected, and repeated — while every
    test in this file finished inside three seconds, comfortably within the
    grace window that hid it.

    So this test asserts on the frames themselves rather than on liveness: it
    watches the node's own outbox for a heartbeat, which is the thing that was
    missing, and does not depend on how long the grace window happens to be.
    """
    # The interval the panel advertises, shortened so this asserts on a real
    # emitted frame in a second rather than waiting out the production value.
    from worker.transport import server as server_module

    monkeypatch.setattr(server_module, "_HEARTBEAT_INTERVAL_SECONDS", 1)

    seen = []
    client_box = {}

    original = inbound._client

    def capture(artifacts, key_id):
        client = original(artifacts, key_id)
        client_box["client"] = client
        real_send = client._send

        async def spy(message, **kwargs):
            if message.WhichOneof("payload") == "heartbeat":
                seen.append(message)
            return await real_send(message, **kwargs)

        client._send = spy
        return client

    inbound.listener._servicer._client_factory = capture
    await inbound.connect_panel()

    # Drive the loop rather than waiting out a real interval: the bug is a
    # missing task, not a slow one, so what matters is that something is
    # scheduled to produce these at all.
    await _until(lambda: len(seen) >= 2, timeout=15.0)
    assert len(seen) >= 2, "the node registered and then never sent a heartbeat"


@pytest.mark.asyncio
async def test_a_staged_result_comes_back_whole_when_its_ref_declares_a_size(
    inbound, tmp_path
):
    """A real result ref carries size_bytes, and that must not be read as an
    offset.

    Found on hardware: FetchResult seeked to `request.size_bytes` as if it were
    a resume point, so it started at EOF, yielded nothing, and the fetch failed
    with "the result ended before its final chunk" — while the finished render
    sat on the node's disk. Every earlier test called publish/stage directly and
    never exercised FetchResult with a populated ref, which is why it survived.
    """
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    payload = b"rendered audio bytes" * 1000

    ref = await inbound.artifacts.publish(
        pb.TaskRef(task_id="t1", attempt_id="a1"),
        payload,
        {"filename": "out.wav"},
        key_id=inbound.panel_key_id,
    )
    assert ref.size_bytes == len(payload), "the ref must declare the real size"

    destination = tmp_path / "fetched.wav"
    await inbound.connection.fetch_result(ref, str(destination))

    assert destination.read_bytes() == payload


@pytest.mark.asyncio
async def test_result_pull_stops_at_its_runtime_byte_cap(tmp_path):
    from worker.inbound.connection_string import Connection
    from worker.protocol.gen import worker_v1_pb2 as pb

    connection = _worker_modules().NodeConnection(
        object(),
        Connection(
            host="127.0.0.1",
            port=7444,
            secret="ovnode_" + "s" * 40,
            fingerprint="a" * 64,
        ),
    )

    class Stub:
        async def FetchResult(self, _request, metadata=()):
            yield pb.ResultChunk(offset=0, data=b"too large", last=True)

    connection._stub = Stub()
    destination = tmp_path / "partial.wav"

    with pytest.raises(RuntimeError, match="larger than"):
        await connection.fetch_result(
            pb.ArtifactRef(artifact_id="a1"), str(destination), max_bytes=4
        )
    assert not destination.exists()


@pytest.mark.asyncio
async def test_cancelled_result_pull_drains_off_loop_write_before_unlink(
    tmp_path, monkeypatch
):
    from threading import Event, Timer

    from worker.inbound import connector as connector_module
    from worker.inbound.connection_string import Connection
    from worker.protocol.gen import worker_v1_pb2 as pb

    connection = _worker_modules().NodeConnection(
        object(),
        Connection(
            host="127.0.0.1",
            port=7444,
            secret="ovnode_" + "s" * 40,
            fingerprint="a" * 64,
        ),
    )

    class Stub:
        async def FetchResult(self, _request, metadata=()):
            yield pb.ResultChunk(offset=0, data=b"rendered audio", last=True)

    connection._stub = Stub()
    write_started = Event()
    release_write = Event()
    real_write_all = connector_module._write_all

    def blocked_write(handle, payload):
        write_started.set()
        if not release_write.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release the fetched-result write")
        real_write_all(handle, payload)

    monkeypatch.setattr(connector_module, "_write_all", blocked_write)
    destination = tmp_path / "partial.wav"
    watchdog = Timer(5, release_write.set)
    watchdog.start()
    fetching = asyncio.create_task(
        connection.fetch_result(
            pb.ArtifactRef(artifact_id="a1"), str(destination)
        )
    )

    async def wait_for_write():
        while not write_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_write(), timeout=HANG_GUARD_S)
        fetching.cancel()
        await asyncio.sleep(0)
        assert not fetching.done(), "cancellation abandoned an active file write"
        assert not release_write.is_set(), (
            "fetched-result writing blocked the gRPC event loop"
        )
    finally:
        release_write.set()
        watchdog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetching
    assert not destination.exists()


@pytest.mark.asyncio
async def test_repasting_a_key_for_a_connected_machine_redials_it(inbound, monkeypatch):
    """Re-pasting must replace the live session, not report success against it.

    Found on hardware: `add` saved the new string and then short-circuited
    because a connection to that endpoint already existed. A wrong key
    therefore overwrote a working one, answered 200 with connected=true from
    the stale session, and only failed after a restart — by which point nothing
    pointed back at the paste that caused it.
    """
    from worker.inbound import service as inbound_service

    await inbound.connect_panel()
    outbound = inbound_service.OutboundNodes(inbound.keys)
    saved = []
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )

    first = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret=inbound.keys.issue("One").secret,
        fingerprint=inbound.credentials.fingerprint,
    )
    await outbound.add(first, inbound.servicer)
    original = outbound._connections[f"127.0.0.1:{inbound.port}"]

    second = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret=inbound.keys.issue("Two").secret,
        fingerprint=inbound.credentials.fingerprint,
    )
    await outbound.add(second, inbound.servicer)

    endpoint = f"127.0.0.1:{inbound.port}"
    assert saved == [endpoint], "settings must persist only the non-secret endpoint"
    parsed_second = inbound.worker.parse_connection(second)
    assert inbound.keys.connection_secret(endpoint) == parsed_second.secret
    assert inbound.keys.connection_fingerprint(endpoint) == parsed_second.fingerprint
    assert outbound._connections[f"127.0.0.1:{inbound.port}"] is not original, (
        "the old session must be replaced, not reused"
    )
    await outbound.stop()


@pytest.mark.asyncio
async def test_wrong_replacement_key_preserves_the_working_connection(
    inbound, monkeypatch
):
    from worker.inbound import service as inbound_service
    from worker.inbound.connector import InboundConnectionError

    await inbound.connect_panel()
    outbound = inbound_service.OutboundNodes(inbound.keys)
    saved = []
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )
    issued = inbound.keys.issue("Working")
    working_text = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret=issued.secret,
        fingerprint=inbound.credentials.fingerprint,
    )
    await outbound.add(working_text, inbound.servicer)
    endpoint = f"127.0.0.1:{inbound.port}"
    await _until(lambda: bool(outbound._connections[endpoint].worker_id))
    original = outbound._connections[endpoint]
    original_task = outbound._tasks[endpoint]
    original_saved = list(saved)

    wrong_text = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret="ovnode_" + "x" * 43,
        fingerprint=inbound.credentials.fingerprint,
    )
    with pytest.raises(InboundConnectionError):
        await outbound.add(wrong_text, inbound.servicer)

    assert outbound._connections[endpoint] is original
    assert outbound._tasks[endpoint] is original_task
    assert not original_task.done()
    assert saved == original_saved
    assert inbound.keys.connection_secret(endpoint) == issued.secret
    await outbound.stop()


@pytest.mark.asyncio
async def test_offline_retained_replacement_preserves_old_credentials(
    inbound, monkeypatch
):
    from worker.inbound import service as inbound_service
    from worker.inbound.connector import RemoteShutdownUnavailable

    await inbound.connect_panel()
    outbound = inbound_service.OutboundNodes(inbound.keys)
    saved = []
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )
    old = inbound.keys.issue("Old")
    old_text = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret=old.secret,
        fingerprint=inbound.credentials.fingerprint,
    )
    await outbound.add(old_text, inbound.servicer)
    endpoint = f"127.0.0.1:{inbound.port}"
    await _until(lambda: bool(outbound._connections[endpoint].worker_id))
    original = outbound._connections[endpoint]
    original_task = outbound._tasks[endpoint]
    original_task.cancel()
    await asyncio.gather(original_task, return_exceptions=True)
    assert original._remote_protocol_retained is True

    replacement = inbound.keys.issue("Replacement")
    replacement_text = inbound.worker.format_connection(
        host="127.0.0.1",
        port=inbound.port,
        secret=replacement.secret,
        fingerprint=inbound.credentials.fingerprint,
    )
    with pytest.raises(RemoteShutdownUnavailable):
        await outbound.add(replacement_text, inbound.servicer)

    assert outbound._connections[endpoint] is original
    assert outbound._tasks[endpoint] is original_task
    assert saved == [endpoint]
    assert inbound.keys.connection_secret(endpoint) == old.secret
    await outbound.stop()


@pytest.mark.asyncio
async def test_concurrent_replacements_leave_live_and_durable_keys_in_step(
    inbound, monkeypatch
):
    from worker.inbound import service as inbound_service

    await inbound.connect_panel()
    outbound = inbound_service.OutboundNodes(inbound.keys)
    saved = []
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )
    endpoint = f"127.0.0.1:{inbound.port}"

    def connection_text(label):
        issued = inbound.keys.issue(label)
        return issued, inbound.worker.format_connection(
            host="127.0.0.1",
            port=inbound.port,
            secret=issued.secret,
            fingerprint=inbound.credentials.fingerprint,
        )

    initial, initial_text = connection_text("Initial")
    await outbound.add(initial_text, inbound.servicer)
    await _until(lambda: bool(outbound._connections[endpoint].worker_id))
    first, first_text = connection_text("First")
    second, second_text = connection_text("Second")

    await asyncio.gather(
        outbound.add(first_text, inbound.servicer),
        outbound.add(second_text, inbound.servicer),
    )

    live = outbound._connections[endpoint]
    assert live._connection.secret == second.secret
    assert inbound.keys.connection_secret(endpoint) == second.secret
    assert saved == [endpoint]
    assert initial.secret != first.secret != second.secret
    await outbound.stop()


@pytest.mark.asyncio
async def test_shutdown_disconnect_before_goodbye_fails_without_forgetting_state():
    from worker.inbound.connection_string import Connection
    from worker.inbound.connector import NodeConnection, RemoteShutdownUnavailable

    connection = NodeConnection(
        object(),
        Connection(
            host="127.0.0.1",
            port=7444,
            secret="ovnode_" + "s" * 40,
            fingerprint="a" * 64,
        ),
    )
    connection._active_session = object()
    connection._remote_protocol_retained = True
    connection._session_closed.clear()
    stopping = asyncio.create_task(connection.stop())
    message = await asyncio.wait_for(connection._outbox.get(), timeout=HANG_GUARD_S)
    assert message.WhichOneof("payload") == "shutdown"
    connection._active_session = None
    connection._session_closed.set()

    with pytest.raises(RemoteShutdownUnavailable, match="disconnected"):
        await stopping
    assert connection._remote_protocol_retained is True
    assert not connection._stop.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["drop", "stop"])
async def test_outbound_teardown_waits_for_connector_cleanup(operation):
    """Removal must not return while the old worker is still schedulable."""
    from worker.inbound import service as inbound_service

    endpoint = "gpu-node:7444"
    outbound = inbound_service.OutboundNodes()
    connector_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    worker_is_schedulable = True

    class Connection:
        async def stop(self):
            pass

    async def run_connector():
        nonlocal worker_is_schedulable
        connector_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            worker_is_schedulable = False

    connector_task = asyncio.create_task(run_connector())
    await connector_started.wait()
    outbound._connections[endpoint] = Connection()
    outbound._tasks[endpoint] = connector_task

    if operation == "drop":
        teardown = asyncio.create_task(outbound._drop(endpoint))
    else:
        teardown = asyncio.create_task(outbound.stop())
    await asyncio.wait_for(cleanup_started.wait(), timeout=HANG_GUARD_S)

    try:
        assert not teardown.done()
        assert worker_is_schedulable
    finally:
        release_cleanup.set()
        await asyncio.gather(teardown, connector_task, return_exceptions=True)

    assert not worker_is_schedulable
    assert endpoint not in outbound._connections
    assert endpoint not in outbound._tasks


@pytest.mark.asyncio
async def test_failed_connection_removal_still_fences_the_live_session(
    tmp_path, monkeypatch
):
    from worker.inbound import service as inbound_service

    endpoint = "gpu-node:7444"
    secret = "ovnode_" + "s" * 40
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, secret, "a" * 64)
    outbound = inbound_service.OutboundNodes(store)
    outbound._servicer = object()
    saved = [endpoint]
    stopped = asyncio.Event()
    redialled = []

    class Connection:
        async def stop(self):
            stopped.set()

    async def connector():
        await asyncio.Event().wait()

    task = asyncio.create_task(connector())
    outbound._connections[endpoint] = Connection()
    outbound._tasks[endpoint] = task
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )
    monkeypatch.setattr(
        store,
        "forget_connection_secret",
        lambda _endpoint: (_ for _ in ()).throw(OSError("disk full")),
    )

    async def capture_redial(connection, servicer, **_kwargs):
        redialled.append((connection, servicer))

    monkeypatch.setattr(outbound, "_dial", capture_redial)

    with pytest.raises(OSError, match="disk full"):
        await outbound.remove(endpoint)

    assert stopped.is_set()
    assert task.done()
    assert saved == [endpoint]
    assert store.connection_secret(endpoint) == secret
    assert len(redialled) == 1
    assert redialled[0][0].endpoint == endpoint
    assert redialled[0][0].secret == secret
    assert redialled[0][1] is outbound._servicer
    assert endpoint not in outbound._connections
    assert endpoint not in outbound._tasks


@pytest.mark.asyncio
async def test_cancelled_replacement_restores_live_and_durable_generation(
    tmp_path, monkeypatch
):
    from worker.inbound import connector as connector_module
    from worker.inbound import service as inbound_service
    from worker.inbound.connection_string import format_connection

    endpoint = "gpu-node:7444"
    old_secret = "ovnode_" + "o" * 40
    new_secret = "ovnode_" + "n" * 40
    fingerprint = "a" * 64
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, old_secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    saved = [endpoint]
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )

    class OldConnection:
        async def stop(self):
            pass

    async def old_connector():
        await asyncio.Event().wait()

    old_task = asyncio.create_task(old_connector())
    outbound._connections[endpoint] = OldConnection()
    outbound._tasks[endpoint] = old_task

    candidate_waiting = asyncio.Event()
    made = []

    class Candidate:
        def __init__(self, _servicer, connection):
            self._connection = connection
            self.closed = False
            made.append(self)

        async def probe(self):
            pass

        async def run_forever(self):
            await asyncio.Event().wait()

        async def wait_until_registered(self, _task):
            candidate_waiting.set()
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(connector_module, "NodeConnection", Candidate)
    replacement = format_connection(
        host="gpu-node",
        port=7444,
        secret=new_secret,
        fingerprint=fingerprint,
    )
    adding = asyncio.create_task(outbound.add(replacement, object()))
    await asyncio.wait_for(candidate_waiting.wait(), timeout=HANG_GUARD_S)
    adding.cancel()

    with pytest.raises(asyncio.CancelledError):
        await adding

    assert old_task.done()
    assert made[1].closed is True
    assert outbound._connections[endpoint]._connection.secret == old_secret
    assert not outbound._tasks[endpoint].done()
    assert store.connection_secret(endpoint) == old_secret
    assert store.connection_fingerprint(endpoint) == fingerprint
    assert saved == [endpoint]
    await outbound.stop()


@pytest.mark.asyncio
async def test_cancelled_removal_drains_rollback_and_redials_previous_generation(
    tmp_path, monkeypatch
):
    from worker.inbound import service as inbound_service

    endpoint = "gpu-node:7444"
    secret = "ovnode_" + "s" * 40
    fingerprint = "a" * 64
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    outbound._servicer = object()
    saved = [endpoint]
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )

    class Connection:
        async def stop(self):
            pass

    async def connector():
        await asyncio.Event().wait()

    connector_task = asyncio.create_task(connector())
    outbound._connections[endpoint] = Connection()
    outbound._tasks[endpoint] = connector_task
    monkeypatch.setattr(
        store,
        "forget_connection_secret",
        lambda _endpoint: (_ for _ in ()).throw(asyncio.CancelledError()),
    )
    rollback_started = asyncio.Event()
    release_rollback = asyncio.Event()
    redialled = []

    async def blocked_redial(connection, servicer, **_kwargs):
        redialled.append((connection, servicer))
        rollback_started.set()
        await release_rollback.wait()
        outbound._connections[endpoint] = Connection()

    monkeypatch.setattr(outbound, "_dial", blocked_redial)
    removing = asyncio.create_task(outbound.remove(endpoint))
    await asyncio.wait_for(rollback_started.wait(), timeout=HANG_GUARD_S)
    removing.cancel()
    await asyncio.sleep(0)
    assert not removing.done(), "a second cancellation abandoned rollback"
    release_rollback.set()

    with pytest.raises(asyncio.CancelledError):
        await removing

    assert connector_task.done()
    assert len(redialled) == 1
    assert redialled[0][0].secret == secret
    assert redialled[0][1] is outbound._servicer
    assert endpoint in outbound._connections
    assert store.connection_secret(endpoint) == secret
    assert store.connection_fingerprint(endpoint) == fingerprint
    assert saved == [endpoint]


@pytest.mark.asyncio
async def test_failed_replacement_surfaces_credential_rollback_failure(
    tmp_path, monkeypatch
):
    from worker.inbound import connector as connector_module
    from worker.inbound import service as inbound_service
    from worker.inbound.connection_string import format_connection
    from worker.inbound.connector import (
        InboundConnectionError,
        InboundConnectionRollbackError,
    )

    endpoint = "gpu-node:7444"
    old_secret = "ovnode_" + "o" * 40
    new_secret = "ovnode_" + "n" * 40
    fingerprint = "a" * 64
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, old_secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    saved = [endpoint]
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    monkeypatch.setattr(
        outbound, "_save", lambda entries: saved.clear() or saved.extend(entries)
    )

    class Existing:
        async def stop(self):
            pass

    async def connector():
        await asyncio.Event().wait()

    old_task = asyncio.create_task(connector())
    outbound._connections[endpoint] = Existing()
    outbound._tasks[endpoint] = old_task

    class Probe:
        def __init__(self, *_args):
            pass

        async def probe(self):
            pass

    monkeypatch.setattr(connector_module, "NodeConnection", Probe)

    async def fail_candidate(connection, _servicer, **_kwargs):
        raise InboundConnectionError(
            f"candidate {connection.secret[-1]} failed readiness"
        )

    monkeypatch.setattr(outbound, "_dial", fail_candidate)
    real_remember = store.remember_connection_secret

    def fail_old_restore(target, secret, stored_fingerprint):
        if secret == old_secret:
            raise OSError("credential fsync failed")
        return real_remember(target, secret, stored_fingerprint)

    monkeypatch.setattr(store, "remember_connection_secret", fail_old_restore)
    replacement = format_connection(
        host="gpu-node",
        port=7444,
        secret=new_secret,
        fingerprint=fingerprint,
    )

    with pytest.raises(InboundConnectionRollbackError, match="remains stopped") as caught:
        await outbound.add(replacement, object())

    assert isinstance(caught.value.__cause__, InboundConnectionError)
    assert store.connection_secret(endpoint) == new_secret
    assert endpoint not in outbound._connections
    assert endpoint not in outbound._tasks
    assert old_task.done()
    assert saved == [endpoint]


@pytest.mark.asyncio
async def test_failed_removal_surfaces_durable_rollback_failure(
    tmp_path, monkeypatch
):
    from worker.inbound import service as inbound_service
    from worker.inbound.connector import InboundConnectionRollbackError

    endpoint = "gpu-node:7444"
    secret = "ovnode_" + "s" * 40
    fingerprint = "a" * 64
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    outbound._servicer = object()
    saved = [endpoint]
    monkeypatch.setattr(outbound, "saved", lambda: list(saved))
    save_calls = 0

    def fail_removal_save(entries):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("settings commit failed")
        saved.clear()
        saved.extend(entries)

    monkeypatch.setattr(outbound, "_save", fail_removal_save)

    class Existing:
        async def stop(self):
            pass

    async def connector():
        await asyncio.Event().wait()

    old_task = asyncio.create_task(connector())
    outbound._connections[endpoint] = Existing()
    outbound._tasks[endpoint] = old_task
    monkeypatch.setattr(
        store,
        "remember_connection_secret",
        lambda *_args: (_ for _ in ()).throw(OSError("credential restore failed")),
    )

    with pytest.raises(InboundConnectionRollbackError, match="remains stopped") as caught:
        await outbound.remove(endpoint)

    assert isinstance(caught.value.__cause__, OSError)
    assert store.connection_secret(endpoint) == ""
    assert endpoint not in outbound._connections
    assert endpoint not in outbound._tasks
    assert old_task.done()
    assert saved == [endpoint]


@pytest.mark.asyncio
async def test_saved_endpoint_reloads_its_key_from_protected_storage(tmp_path, monkeypatch):
    from worker.inbound import service as inbound_service

    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    endpoint = "10.0.0.2:7444"
    secret = "ovnode_" + "s" * 40
    fingerprint = "a" * 64
    store.remember_connection_secret(endpoint, secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    monkeypatch.setattr(outbound, "saved", lambda: [endpoint])
    dialled = []

    async def capture(connection, _servicer):
        dialled.append(connection)

    monkeypatch.setattr(outbound, "_dial", capture)
    await outbound.start_all(object())

    assert len(dialled) == 1
    assert dialled[0].endpoint == endpoint
    assert dialled[0].secret == secret
    assert dialled[0].fingerprint == fingerprint


@pytest.mark.asyncio
async def test_repasting_an_identical_terminal_connection_really_redials(
    tmp_path, monkeypatch
):
    from worker.inbound import service as inbound_service
    from worker.inbound.connection_string import format_connection, parse_connection

    endpoint = "10.0.0.2:7444"
    secret = "ovnode_" + "s" * 40
    fingerprint = "a" * 64
    text = format_connection(
        host="10.0.0.2", port=7444, secret=secret, fingerprint=fingerprint
    )
    parsed = parse_connection(text)
    store = _worker_modules().KeyStore(str(tmp_path / "keys.json"))
    store.remember_connection_secret(endpoint, secret, fingerprint)
    outbound = inbound_service.OutboundNodes(store)
    monkeypatch.setattr(outbound, "saved", lambda: [endpoint])

    class DeadConnection:
        _connection = parsed

    async def finished():
        return None

    dead_task = asyncio.create_task(finished())
    await dead_task
    outbound._connections[endpoint] = DeadConnection()
    outbound._tasks[endpoint] = dead_task
    dialled = []

    async def capture(connection, servicer, *, wait_until_ready=False):
        dialled.append((connection, servicer, wait_until_ready))

    monkeypatch.setattr(outbound, "_dial", capture)
    servicer = object()

    await outbound.add(text, servicer)

    assert dialled == [(parsed, servicer, True)]
    assert endpoint not in outbound._connections
    assert endpoint not in outbound._tasks


@pytest.mark.asyncio
async def test_a_stale_frame_cannot_poison_the_next_attach(inbound):
    """The outbox must not survive a dead session.

    Found on hardware. The queue was built once per NodeConnection and reused
    across reconnects, so a frame left behind by a dying session became the
    FIRST frame of the next attach. The node requires a registration there,
    aborted the call, and the two span at full speed — session epoch 2445
    inside one second, with the node logging "Locally aborted" on repeat and
    the panel reporting the worker offline while it was visibly connected.
    """
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    connection = inbound.connection

    # Exactly what a torn-down session leaves behind: an unsent frame, still
    # queued, that would be handed to the next attach as its opening word.
    stale = pb.ServerMessage(ping=pb.Ping(nonce=7))
    connection._outbox.put_nowait(stale)
    poisoned = connection._outbox

    task = asyncio.create_task(connection._connect_once())
    try:
        await _until(lambda: connection._outbox is not poisoned)
        assert connection._outbox is not poisoned, "the dead session's queue was reused"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_nested_input_id_is_accepted_the_way_staging_really_writes_it(
    inbound, tmp_path
):
    """Staged inputs are nested, and the node must take them as they come.

    `task_store.stage_input` mints `inputs/<digest><ext>` — a path, not a bare
    name. The node ran `safe_filename` on it, which rejects anything nested, so
    every real clone input was refused, the dispatch failed, and the scheduler
    retried about eighteen times a second while the GPU sat idle and the user
    watched a spinner. Every earlier test used a flat id like "ref-1" and so
    never touched the shape production actually produces.
    """
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    source = tmp_path / "reference.wav"
    source.write_bytes(b"reference audio bytes")

    nested = pb.ArtifactRef(
        artifact_id="inputs/0f1e2d3c4b5a69788796a5b4c3d2e1f0.wav",
        filename="reference.wav",
    )
    declared = await inbound.connection.push_input(nested, str(source))

    destination = tmp_path / "staged.wav"
    await inbound.artifacts.stage_in(
        declared, str(destination), key_id=inbound.panel_key_id
    )
    assert destination.read_bytes() == b"reference audio bytes"


@pytest.mark.asyncio
async def test_a_pushed_input_cannot_escape_the_staging_directory(inbound, tmp_path):
    """Accepting nested ids must not mean accepting traversal.

    The id is now hashed rather than used as a path, so it cannot steer
    placement at all. The declared FILENAME still can, and is still required to
    be a bare name — that is the containment the old check was really buying,
    and it must not have been traded away to fix the rejection above.
    """
    from worker.protocol.gen import worker_v1_pb2 as pb

    await inbound.connect_panel()
    source = tmp_path / "evil.wav"
    source.write_bytes(b"payload")

    hostile = pb.ArtifactRef(
        artifact_id="../../../../../../tmp/escaped.wav", filename="../../escaped.wav"
    )
    with pytest.raises(RuntimeError, match="bare filename|did not accept"):
        await inbound.connection.push_input(hostile, str(source))

    # And nothing was left behind by the refusal.
    root = inbound.artifacts._root
    assert not any("escaped" in name for _, _, files in os.walk(root) for name in files)


@pytest.mark.asyncio
async def test_an_inbound_only_node_still_unloads_idle_models(monkeypatch, tmp_path):
    """Requirement: free models nothing has used for ten minutes.

    The sweep used to live inside the dial-out agent, which an inbound-only
    node never starts — so a machine lending its GPU to panels that dial IN
    held several GB of weights forever. That is exactly the cost the sweep
    exists to avoid, and it was absent in the mode most likely to be a shared
    box: on hardware, that node's VRAM never came back.
    """
    from worker import agent as agent_module

    released = {"count": 0}
    refreshed = {"count": 0}

    def fake_release():
        released["count"] += 1
        return ["indextts"]

    async def fake_refresh():
        refreshed["count"] += 1

    monkeypatch.setattr(agent_module, "IDLE_SWEEP_INTERVAL_SECONDS", 0.05)
    import services.model_manager as model_manager
    import services.tts_backend as tts_backend

    monkeypatch.setattr(tts_backend, "release_idle_engines", fake_release)
    monkeypatch.setattr(
        model_manager, "gpu_pool_stats", lambda: {"running": 0, "queued": 0}
    )

    task = asyncio.create_task(agent_module.idle_unload_loop(fake_refresh))
    try:
        await _until(
            lambda: released["count"] >= 1 and refreshed["count"] >= 1, timeout=5.0
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert released["count"] >= 1, "nothing swept idle engines"
    assert refreshed["count"] >= 1, "freed VRAM was never re-advertised"


@pytest.mark.asyncio
async def test_enabling_inbound_starts_the_idle_sweep(monkeypatch, tmp_path):
    """The wiring, not just the loop: the gap was a missing caller."""
    from worker import agent as agent_module
    from worker.inbound import service as inbound_service

    started = asyncio.Event()
    received = []

    async def idle_unload_sentinel(refresh):
        received.append(refresh)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_module, "idle_unload_loop", idle_unload_sentinel)
    node = inbound_service.InboundNode()
    monkeypatch.setattr(inbound_service, "bind_host", lambda: "127.0.0.1")
    monkeypatch.setattr(inbound_service, "bind_port", lambda: 0)
    monkeypatch.setattr(
        inbound_service,
        "paths",
        lambda: {
            "keys": str(tmp_path / "k.json"),
            "staged": str(tmp_path / "s"),
            "certificate": str(tmp_path / "inbound.crt"),
            "private_key": str(tmp_path / "inbound.key"),
        },
    )
    monkeypatch.setattr(node, "_client_factory", lambda artifacts, key_id: None)

    await node.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_S)
        assert received == [node._listener.refresh_all]
    finally:
        await node.stop()
    assert node._idle_sweep is None


@pytest.mark.asyncio
async def test_concurrent_inbound_starts_publish_only_one_listener(monkeypatch):
    from worker.inbound import service as inbound_service

    node = inbound_service.InboundNode()
    entered = asyncio.Event()
    release = asyncio.Event()
    listener = object()
    calls = 0

    async def staged_start():
        nonlocal calls
        if node._listener is not None:
            return
        calls += 1
        entered.set()
        await release.wait()
        node._listener = listener

    monkeypatch.setattr(node, "_start", staged_start)
    first = asyncio.create_task(node.start())
    await asyncio.wait_for(entered.wait(), timeout=HANG_GUARD_S)
    second = asyncio.create_task(node.start())
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    await asyncio.gather(first, second)
    assert calls == 1
    assert node._listener is listener


@pytest.mark.asyncio
async def test_cancelled_listener_bind_closes_server_before_losing_its_handle(
    tmp_path, monkeypatch
):
    from worker.inbound import listener as listener_module

    worker = _worker_modules()
    start_entered = asyncio.Event()
    release_start = asyncio.Event()
    stopped = asyncio.Event()

    class Server:
        def add_secure_port(self, _bind, _credentials):
            return 7444

        async def start(self):
            start_entered.set()
            await release_start.wait()

        async def stop(self, grace):
            assert grace == 0
            stopped.set()

    server = Server()
    monkeypatch.setattr(listener_module.grpc.aio, "server", lambda **_kwargs: server)
    monkeypatch.setattr(
        listener_module.pb_grpc,
        "add_NodeServiceServicer_to_server",
        lambda *_args: None,
    )
    listener = worker.NodeListener(
        keys=worker.KeyStore(str(tmp_path / "keys.json")),
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )
    starting = asyncio.create_task(
        listener.start(host="127.0.0.1", port=7444)
    )
    await asyncio.wait_for(start_entered.wait(), timeout=HANG_GUARD_S)
    starting.cancel()
    await asyncio.sleep(0)
    assert not starting.done(), "bind cancellation abandoned a starting server"
    release_start.set()

    with pytest.raises(asyncio.CancelledError):
        await starting

    assert stopped.is_set()
    assert listener.running is False
    assert listener.port == 0


@pytest.mark.asyncio
async def test_listener_stop_failure_retains_handle_for_retry(tmp_path, monkeypatch):
    worker = _worker_modules()
    listener = worker.NodeListener(
        keys=worker.KeyStore(str(tmp_path / "keys.json")),
        log=worker.ConnectionLog(),
        artifacts=worker.ArtifactStore(str(tmp_path / "staged")),
        client_factory=lambda *_args: None,
        credentials=worker.tls.generate_self_signed(hostnames=["127.0.0.1"]),
    )
    attempts = 0

    class Server:
        async def stop(self, *, grace):
            nonlocal attempts
            assert grace == 1.0
            attempts += 1
            if attempts == 1:
                raise OSError("listener stop failed")

    server = Server()
    listener._server = server
    listener._bound_port = 7444
    monkeypatch.setattr(listener._servicer, "stop", lambda: asyncio.sleep(0))

    with pytest.raises(OSError, match="listener stop failed"):
        await listener.stop()

    assert listener._server is server
    assert listener.port == 7444
    await listener.stop()
    assert listener.running is False
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancelled_node_stop_drains_listener_before_clearing_handle():
    from worker.inbound import service as inbound_service

    node = inbound_service.InboundNode()
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()

    class Listener:
        async def stop(self):
            stop_entered.set()
            await release_stop.wait()

    listener = Listener()
    node._listener = listener
    stopping = asyncio.create_task(node.stop())
    await asyncio.wait_for(stop_entered.wait(), timeout=HANG_GUARD_S)
    stopping.cancel()
    await asyncio.sleep(0)

    assert not stopping.done(), "node cancellation abandoned a live listener"
    assert node._listener is listener
    release_stop.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert node._listener is None
