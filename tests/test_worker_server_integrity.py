"""Control-plane boundary integrity.

Every case here is a way a finished render could be lost, misplaced, or written
somewhere it was never meant to go — at the one layer where the peer is remote
and everything it says is untrusted input. The frames are driven straight into
the servicer rather than through a real stream: what is under test is the
translation from wire to scheduler, not gRPC.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from worker import identity, registry, task_store
from worker.errors import ErrorClass, WorkerError
from worker.identity import WorkerKeypair
from worker.lifecycle import AttemptState, TaskState
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.scheduler import Scheduler
from worker.transport import codec, server as server_module
from worker.transport.server import PROTOCOL_VERSION, REQUIRED_FEATURES, WorkerServicer
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

# How long a test waits for a Control stream to finish ending. These waits
# only prove the stream ends; the latency assertions stay at 0.2s. Ending
# runs the real disconnect path, which persists every queued task, and on a
# slow Windows runner the 64-task case took longer than the 2s it had.
_CONTROL_EXIT_TIMEOUT_S = 10.0

ENGINE, MODEL, OP = "indextts", "IndexTTS-2", "tts"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Throwaway DB, patched where the stores actually read it."""
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


class _Context:
    """Just enough of a gRPC servicer context for Register."""

    def peer(self) -> str:
        return "ipv4:127.0.0.1:5555"

    def invocation_metadata(self):
        return ()


def _capabilities() -> list[dict]:
    return [
        {
            "engine": ENGINE,
            "model_id": MODEL,
            "operations": [OP],
            "supported": True,
            "installed": True,
            "downloaded": True,
            "resident": False,
            "backend": "cuda",
            "free_memory_bytes": 24 * 1024**3,
        }
    ]


class _Plane:
    """A servicer with one enrolled worker, driven frame by frame."""

    def __init__(self, tmp_path):
        self.artifact_dir = str(tmp_path / "artifacts")
        self.pool = WorkerPool()
        self.scheduler = Scheduler(self.pool)
        self.servicer = WorkerServicer(
            self.scheduler, self.pool, artifact_dir=self.artifact_dir
        )
        self.keypair = WorkerKeypair.generate()
        self.worker_id = ""
        self.epoch = 0

    async def register(
        self,
        *,
        in_flight=(),
        completed_unacked=(),
        activate=True,
        capabilities=None,
        host=None,
        max_concurrent_tasks=2,
    ) -> pb.RegisterResponse:
        """Join on first call, prove key possession on every later one."""
        token = ""
        if not self.worker_id:
            token = registry.create_enrollment(
                endpoint="localhost:1", cert_fingerprint="fp"
            ).encode()
        challenge, nonce = identity.new_challenge(), identity.new_challenge()
        signature = self.keypair.sign(
            identity.challenge_message(
                challenge=challenge,
                worker_id=self.worker_id,
                session_epoch=self.epoch,
                nonce=nonce,
            )
        )
        response = await self.servicer.Register(
            pb.RegisterRequest(
                features=sorted(REQUIRED_FEATURES),
                envelope=pb.Envelope(sequence=self.epoch),
                protocol_version_min=PROTOCOL_VERSION,
                protocol_version_max=PROTOCOL_VERSION,
                enrollment_token=token,
                worker_id=self.worker_id,
                public_key=self.keypair.public_bytes(),
                challenge=challenge,
                challenge_signature=signature,
                nonce=nonce,
                key_id=self.keypair.key_id,
                host=codec.host_to_pb(
                    host
                    or {"hostname": "gpu2", "os": "linux", "arch": "x86_64"}
                ),
                capabilities=[
                    codec.capability_to_pb(c)
                    for c in (capabilities or _capabilities())
                ],
                max_concurrent_tasks=max_concurrent_tasks,
                in_flight=list(in_flight),
                completed_unacked=list(completed_unacked),
            ),
            _Context(),
        )
        assert not response.error.code, response.error.code
        self.worker_id = response.worker_id
        self.epoch = response.session_epoch
        if activate:
            pending = self.servicer.session_for(
                self.worker_id, session_token=response.session_token
            )
            assert pending is not None
            assert self.servicer._activate_session(pending) is not None
        return response

    @property
    def session(self):
        return self.servicer._sessions[self.worker_id]

    @property
    def outbox(self) -> list[pb.ServerMessage]:
        queue = self.session.outbox
        return list(queue._queue)

    def assign(self):
        """Submit one task and bind it to the connected worker."""
        task = self.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
        assignment = self.scheduler.next_assignment()
        assert assignment is not None
        return task, assignment.attempt

    async def send(self, message: pb.WorkerMessage) -> None:
        await self.servicer._handle(self.session, message)


@pytest_asyncio.fixture
async def plane(tmp_path, db):
    p = _Plane(tmp_path)
    await p.register()
    return p


def _result(ref, *, payload=b"", artifact_id="") -> pb.WorkerMessage:
    artifacts = [pb.ArtifactRef(artifact_id=artifact_id)] if artifact_id else []
    return pb.WorkerMessage(
        result=pb.TaskResult(
            ref=ref,
            inline_payload=payload,
            artifacts=artifacts,
            result_json='{"ok": true}',
        )
    )


def _token_registration_request(token, keypair, *, signer=None):
    challenge = identity.new_challenge()
    nonce = identity.new_challenge()
    signature = (signer or keypair).sign(
        identity.challenge_message(
            challenge=challenge,
            worker_id="",
            session_epoch=0,
            nonce=nonce,
        )
    )
    return pb.RegisterRequest(
        envelope=pb.Envelope(sequence=0),
        enrollment_token=token.encode(),
        public_key=keypair.public_bytes(),
        challenge=challenge,
        challenge_signature=signature,
        nonce=nonce,
        host=codec.host_to_pb({"hostname": "gpu2"}),
    )


def test_a_spent_token_recovers_a_dropped_registration_response(plane):
    token = registry.create_enrollment(endpoint="localhost:1", cert_fingerprint="fp")
    keypair = WorkerKeypair.generate()
    request = _token_registration_request(token, keypair)

    first = plane.servicer._authenticate(request)
    retried = plane.servicer._authenticate(request)

    assert first is not None
    assert retried is not None and retried.id == first.id


def test_spent_token_recovery_requires_the_original_private_key(plane):
    token = registry.create_enrollment(endpoint="localhost:1", cert_fingerprint="fp")
    keypair = WorkerKeypair.generate()
    assert plane.servicer._authenticate(
        _token_registration_request(token, keypair)
    ) is not None

    stolen = _token_registration_request(
        token, keypair, signer=WorkerKeypair.generate()
    )

    assert plane.servicer._authenticate(stolen) is None


# ── B13: artifact paths are minted, never assembled from the wire ──────────


@pytest.mark.asyncio
async def test_inline_result_never_writes_outside_the_artifact_directory(plane, tmp_path):
    """os.path.join drops its prefix on an absolute component, so a worker that
    names its own task could write anywhere the app can."""
    escape = tmp_path / "escape"
    for task_id in ("../../../..", str(escape), "/tmp"):
        await plane.send(
            _result(
                codec.task_ref(task_id, "../../pwned", plane.epoch),
                payload=b"owned",
            )
        )

    assert not escape.exists()
    assert not (tmp_path / "pwned.bin").exists()
    assert os.listdir(plane.artifact_dir) == []


@pytest.mark.asyncio
async def test_an_inline_result_lands_under_its_own_attempt(plane):
    task, attempt = plane.assign()

    await plane.send(_result(codec.ref_for(attempt), payload=b"audio"))

    expected = os.path.join(plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin")
    assert task.result_ref == expected
    assert open(expected, "rb").read() == b"audio"


@pytest.mark.asyncio
async def test_inline_result_is_durable_before_result_ack(plane, monkeypatch):
    task, attempt = plane.assign()
    payload = b"durable inline audio"
    final = os.path.join(
        plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin"
    )
    events = []
    send = plane.session.send

    def fsync_file(path):
        assert open(path, "rb").read() == payload
        events.append(("fsync-file", path))

    def fsync_directory(directory):
        events.append(("fsync-directory", directory))

    async def observed_send(message):
        if message.WhichOneof("payload") == "result_ack":
            events.append(("ack", message.result_ack.ref.attempt_id))
        await send(message)

    monkeypatch.setattr(server_module, "_fsync_file", fsync_file)
    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", fsync_directory
    )
    monkeypatch.setattr(plane.session, "send", observed_send)

    await plane.send(_result(codec.ref_for(attempt), payload=payload))

    assert events == [
        ("fsync-directory", plane.artifact_dir),
        ("fsync-file", final),
        ("fsync-directory", os.path.dirname(final)),
        ("ack", attempt.attempt_id),
    ]


@pytest.mark.asyncio
async def test_result_persistence_failure_reconnects_for_redelivery(
    plane, monkeypatch
):
    task, attempt = plane.assign()
    plane.scheduler.on_accepted(
        task.task_id, attempt.attempt_id, epoch=plane.epoch
    )
    plane.scheduler.on_started(
        task.task_id, attempt.attempt_id, epoch=plane.epoch
    )
    worker = plane.pool.get(plane.worker_id)
    assert worker is not None
    model_key = f"{ENGINE}:{MODEL}"
    plane.pool.breakers.record_failure(
        plane.worker_id,
        model_key,
        WorkerError(
            error_class=ErrorClass.TRANSIENT,
            code="GPU_FAULT",
            message="one earlier worker fault",
        ),
    )
    breaker = plane.pool.breakers.get(plane.worker_id, model_key)
    assert breaker.consecutive_failures == 1

    real_commit = task_store.commit_result
    calls = 0

    def fail_once(candidate, *, result_json=None, now=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("result transaction failed")
        return real_commit(candidate, result_json=result_json, now=now)

    monkeypatch.setattr(task_store, "commit_result", fail_once)
    result = _result(codec.ref_for(attempt), payload=b"redelivered audio")
    keep_stream_open = asyncio.Event()

    async def first_stream():
        yield result
        await keep_stream_open.wait()

    with pytest.raises(OSError, match="result transaction failed"):
        await asyncio.wait_for(
            plane.servicer._read_loop(plane.session, first_stream()), timeout=HANG_GUARD_S
        )

    durable = task_store.get(task.task_id)
    assert durable is not None
    durable_attempt = durable.get_attempt(attempt.attempt_id)
    assert durable_attempt is not None
    assert task.state is TaskState.RUNNING
    assert attempt.state is AttemptState.RUNNING
    assert task.result_ref is None
    assert durable.state is TaskState.RUNNING
    assert durable_attempt.state is AttemptState.RUNNING
    assert worker.capacity.active_tasks == 1
    assert attempt.attempt_id in worker.in_flight
    assert breaker.consecutive_failures == 1
    assert breaker.last_error is not None
    assert [m for m in plane.outbox if m.HasField("result_ack")] == []

    async def replacement_stream():
        yield result

    await plane.servicer._read_loop(plane.session, replacement_stream())

    assert calls == 2
    assert task.state is TaskState.COMPLETED
    assert attempt.state is AttemptState.COMMITTED
    assert task_store.is_committed(task.task_id)
    assert worker.capacity.active_tasks == 0
    assert attempt.attempt_id not in worker.in_flight
    assert breaker.consecutive_failures == 0
    assert breaker.last_error is None
    assert len([m for m in plane.outbox if m.HasField("result_ack")]) == 1


@pytest.mark.asyncio
async def test_inline_result_barrier_does_not_block_control_frames(
    plane, monkeypatch
):
    from threading import Event, Timer

    _task, attempt = plane.assign()
    barrier_started = Event()
    release_barrier = Event()
    real_write = server_module._write_inline_artifact

    def blocked_write(path, payload):
        barrier_started.set()
        if not release_barrier.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release inline durability")
        real_write(path, payload)

    monkeypatch.setattr(server_module, "_write_inline_artifact", blocked_write)
    watchdog = Timer(0.5, release_barrier.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    delivery = asyncio.create_task(
        plane.send(_result(codec.ref_for(attempt), payload=b"inline audio"))
    )

    async def wait_for_barrier():
        while not barrier_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_barrier(), timeout=HANG_GUARD_S)
        assert asyncio.get_running_loop().time() - started_at < 0.2
    finally:
        release_barrier.set()
        watchdog.cancel()
    await delivery


@pytest.mark.asyncio
async def test_superseded_sessions_serialize_one_attempt_result_publication(
    plane, monkeypatch
):
    """A retained old stream and its replacement cannot share a result path."""
    task, attempt = plane.assign()
    old_session = plane.session
    response = await plane.register(
        activate=False, in_flight=[codec.ref_for(attempt)]
    )
    replacement = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert replacement is not None
    assert plane.servicer._activate_session(replacement) is not None

    first_started = threading.Event()
    release_first = threading.Event()
    writes: list[bytes] = []
    real_write = server_module._write_inline_artifact

    def blocked_first_write(path, payload):
        writes.append(payload)
        if len(writes) == 1:
            first_started.set()
            if not release_first.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release the first publication")
        real_write(path, payload)

    monkeypatch.setattr(
        server_module, "_write_inline_artifact", blocked_first_write
    )
    first_payload = b"old generation result"
    second_payload = b"replacement generation result"
    first = asyncio.create_task(
        plane.servicer._on_result(
            old_session,
            _result(codec.ref_for(attempt), payload=first_payload).result,
        )
    )
    assert await asyncio.to_thread(first_started.wait, HANG_GUARD_S)
    second = asyncio.create_task(
        plane.servicer._on_result(
            replacement,
            _result(codec.ref_for(attempt), payload=second_payload).result,
        )
    )
    await asyncio.sleep(0.05)

    assert writes == [first_payload]
    release_first.set()
    await asyncio.gather(first, second)

    assert task.state is TaskState.COMPLETED
    assert task.result_ref is not None
    assert Path(task.result_ref).read_bytes() == first_payload
    assert writes == [first_payload]
    assert plane.servicer._result_publications == {}


@pytest.mark.asyncio
async def test_new_result_directory_barrier_failure_prevents_inline_ack(
    plane, monkeypatch
):
    task, attempt = plane.assign()
    final = os.path.join(
        plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin"
    )

    def fail_task_entry(directory):
        if os.path.abspath(directory) == os.path.abspath(plane.artifact_dir):
            raise OSError("task directory barrier failed")

    monkeypatch.setattr(server_module, "_fsync_parent_directory", fail_task_entry)
    with pytest.raises(OSError, match="task directory barrier failed"):
        await plane.send(_result(codec.ref_for(attempt), payload=b"audio"))

    assert task.state is not TaskState.COMPLETED
    assert plane.outbox == []
    assert not os.path.exists(final)

    # mkdir already happened before its barrier failed. A retry must fsync the
    # existing entry instead of treating existence as a durable commit.
    retried = []
    monkeypatch.setattr(
        server_module,
        "_fsync_parent_directory",
        lambda directory: retried.append(os.path.abspath(directory)),
    )
    await plane.send(_result(codec.ref_for(attempt), payload=b"audio"))

    assert os.path.abspath(plane.artifact_dir) in retried
    assert task.state is TaskState.COMPLETED
    assert len(plane.outbox) == 1


@pytest.mark.asyncio
async def test_fetched_result_persists_every_directory_before_ack(plane, monkeypatch):
    task, attempt = plane.assign()
    payload = b"durable fetched audio"
    final = os.path.join(
        plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin"
    )
    events = []
    real_replace = server_module.os.replace
    send = plane.session.send

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            events.append(("fetch", destination))
            with open(destination, "wb") as handle:
                handle.write(payload)

    def fsync_file(path):
        events.append(("fsync-file", path))

    def replace(source, destination):
        events.append(("replace", source, destination))
        real_replace(source, destination)

    def fsync_directory(directory):
        events.append(("fsync-directory", directory))

    async def observed_send(message):
        if message.WhichOneof("payload") == "result_ack":
            events.append(("ack", message.result_ack.ref.attempt_id))
        await send(message)

    plane.session.connection = Connection()
    session = plane.session
    monkeypatch.setattr(server_module, "_fsync_file", fsync_file)
    monkeypatch.setattr(server_module.os, "replace", replace)
    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", fsync_directory
    )
    monkeypatch.setattr(plane.session, "send", observed_send)
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    session = plane.session

    await plane.servicer._on_result(
        plane.session,
        pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
    )

    assert events[0] == ("fsync-directory", plane.artifact_dir)
    assert events[1][0] == "fetch"
    partial = events[1][1]
    assert events[2] == ("fsync-file", partial)
    assert events[3] == ("replace", partial, final)
    assert events[4] == ("fsync-directory", os.path.dirname(final))
    assert events[5] == ("ack", attempt.attempt_id)


@pytest.mark.asyncio
async def test_an_absolute_artifact_reference_is_refused(plane):
    """The uploaded-artifact path is a reference into our store, not a path."""
    task, attempt = plane.assign()

    await plane.send(_result(codec.ref_for(attempt), artifact_id="/etc/passwd"))

    assert task.state is not TaskState.COMPLETED
    assert task.result_ref is None
    assert plane.outbox == []


@pytest.mark.asyncio
async def test_a_declared_artifact_fetch_failure_is_not_committed_or_acknowledged(plane):
    task, attempt = plane.assign()

    class Connection:
        async def fetch_result(self, _ref, _destination, *, max_bytes=None):
            raise RuntimeError("the staged stream ended early")

    plane.session.connection = Connection()
    session = plane.session
    payload = b"rendered audio still staged on the node"
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    await plane.servicer._on_result(
        plane.session,
        pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
    )

    assert task.state is not TaskState.COMPLETED
    assert task.result_ref is None
    assert plane.outbox == []


@pytest.mark.asyncio
async def test_inbound_result_retries_directory_barrier_before_ack(plane, monkeypatch):
    task, attempt = plane.assign()
    payload = b"rendered audio still staged on the node"
    fetches = 0

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            nonlocal fetches
            fetches += 1
            with open(destination, "wb") as handle:
                handle.write(payload)

    plane.session.connection = Connection()
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    result = pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact])

    final = os.path.join(
        plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin"
    )

    def failed_directory_barrier(directory):
        if os.path.abspath(directory) == os.path.dirname(final):
            raise OSError("directory barrier failed")

    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", failed_directory_barrier
    )

    await plane.servicer._on_result(plane.session, result)
    await plane.servicer._on_result(plane.session, result)

    assert fetches == 1
    assert task.state is not TaskState.COMPLETED
    assert plane.outbox == []

    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", lambda _directory: None
    )
    await plane.servicer._on_result(plane.session, result)

    assert fetches == 1
    assert task.state is TaskState.COMPLETED
    assert len(plane.outbox) == 1


@pytest.mark.asyncio
async def test_inbound_result_barrier_does_not_block_control_frames(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.assign()
    payload = b"rendered audio still staged on the node"

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)

    plane.session.connection = Connection()
    session = plane.session
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    barrier_started = Event()
    release_barrier = Event()
    real_replace = server_module._durable_replace

    def blocked_replace(source, destination):
        barrier_started.set()
        if not release_barrier.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release fetched-result durability")
        real_replace(source, destination)

    monkeypatch.setattr(server_module, "_durable_replace", blocked_replace)
    watchdog = Timer(0.5, release_barrier.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    delivery = asyncio.create_task(
        plane.servicer._on_result(
            session,
            pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
        )
    )

    async def wait_for_barrier():
        while not barrier_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_barrier(), timeout=HANG_GUARD_S)
        assert asyncio.get_running_loop().time() - started_at < 0.2
    finally:
        release_barrier.set()
        watchdog.cancel()
    await delivery
    assert task.state is TaskState.COMPLETED


@pytest.mark.asyncio
async def test_inbound_result_directory_barrier_does_not_block_control_frames(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.assign()
    payload = b"rendered audio staged on the node"

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)

    plane.session.connection = Connection()
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    barrier_started = Event()
    release_barrier = Event()

    def blocked_parent_fsync(directory):
        if os.path.abspath(directory) != os.path.abspath(plane.artifact_dir):
            return
        barrier_started.set()
        if not release_barrier.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release result-directory durability")

    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", blocked_parent_fsync
    )
    watchdog = Timer(0.5, release_barrier.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    fetching = asyncio.create_task(
        plane.servicer._fetch_inbound_artifact(plane.session, attempt, artifact)
    )

    async def wait_for_barrier():
        while not barrier_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_barrier(), timeout=HANG_GUARD_S)
        assert asyncio.get_running_loop().time() - started_at < 0.2
    finally:
        release_barrier.set()
        watchdog.cancel()
    path = await fetching
    assert path is not None and open(path, "rb").read() == payload


@pytest.mark.asyncio
async def test_revocation_during_inbound_result_barrier_cannot_ack(
    plane, monkeypatch
):
    from threading import Event

    task, attempt = plane.assign()
    payload = b"rendered audio still staged on the node"

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)

    plane.session.connection = Connection()
    session = plane.session
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    final = os.path.join(
        plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin"
    )
    barrier_finished = Event()
    release_barrier = Event()
    real_replace = server_module._durable_replace

    def paused_after_replace(source, destination):
        real_replace(source, destination)
        barrier_finished.set()
        if not release_barrier.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release fetched-result durability")

    monkeypatch.setattr(server_module, "_durable_replace", paused_after_replace)
    delivery = asyncio.create_task(
        plane.servicer._on_result(
            session,
            pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
        )
    )

    async def wait_for_barrier():
        while not barrier_finished.is_set():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_barrier(), timeout=HANG_GUARD_S)
    assert os.path.isfile(final)
    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release_barrier.set()
    await delivery

    assert task.state is not TaskState.COMPLETED
    assert not os.path.exists(final)
    assert list(session.outbox._queue) == []


@pytest.mark.asyncio
async def test_a_late_artifact_can_complete_a_timed_out_attempt(plane):
    task, attempt = plane.assign()
    task.max_attempts = 1
    plane.scheduler.on_failed(
        task.task_id,
        attempt.attempt_id,
        WorkerError(
            error_class=ErrorClass.TIMEOUT,
            code="EXECUTION_TIMEOUT",
            message="the result was late",
        ),
        epoch=attempt.session_epoch,
    )
    assert task.state is TaskState.TIMEOUT
    payload = b"late but valid rendered audio"

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)

    plane.session.connection = Connection()
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    await plane.servicer._on_result(
        plane.session,
        pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
    )

    assert task.state is TaskState.COMPLETED
    assert task.result_ref is not None
    assert open(task.result_ref, "rb").read() == payload
    assert len(plane.outbox) == 1


@pytest.mark.asyncio
async def test_a_result_for_another_workers_attempt_is_not_stored(plane, tmp_path):
    """Attempt ownership gates the write, so a second worker cannot overwrite
    the attempt that is about to win."""
    task, attempt = plane.assign()
    plane.session.worker_id = "someone-else"

    await plane.send(_result(codec.ref_for(attempt), payload=b"theirs"))

    assert not os.path.exists(
        os.path.join(plane.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin")
    )
    # Withholding the write is not enough on its own. The commit ran anyway,
    # marking the task done with no artifact — so the owning worker's real
    # delivery arrived as a duplicate and its audio was thrown away. Asserting
    # only the absent file let that through.
    assert task.state is not TaskState.COMPLETED, "a foreign frame committed the task"
    assert plane.outbox == [], "acking licences the wrong worker to forget"


@pytest.mark.asyncio
async def test_liveness_survives_the_reconnect_that_interrupts_it(plane):
    """A worker that drops mid-render and resumes must still be able to say so.

    The regression: task frames were fenced against the *live* session epoch,
    which ``begin_session`` bumps on every reconnect — while the worker keeps
    echoing the ref stamped at dispatch. So every keepalive after a resume was
    silently discarded and the control plane expired a task whose GPU was
    still rendering it, reporting it as silence.
    """
    task, attempt = plane.assign()
    ref = codec.ref_for(attempt)
    plane.scheduler.on_accepted(task.task_id, attempt.attempt_id, epoch=ref.session_epoch)
    plane.scheduler.on_started(task.task_id, attempt.attempt_id, epoch=ref.session_epoch)

    before = attempt.lease_expires_at
    # A resuming worker declares what it is still holding; that is what keeps
    # the attempt alive across the gap instead of reconciling it away as LOST.
    await plane.register(in_flight=[ref])  # same worker, new session epoch
    assert plane.epoch != ref.session_epoch, "the reconnect must move the session on"

    await plane.send(
        pb.WorkerMessage(progress=pb.TaskProgress(ref=ref, keepalive=True, progress=0.0))
    )

    assert attempt.lease_expires_at > before, "the keepalive was fenced away"


@pytest.mark.asyncio
async def test_a_failure_after_a_reconnect_is_not_swallowed(plane):
    """Same fence, worse consequence: the worker's own error report vanished
    and the task died of silence instead of the reason it actually had."""
    task, attempt = plane.assign()
    ref = codec.ref_for(attempt)
    plane.scheduler.on_accepted(task.task_id, attempt.attempt_id, epoch=ref.session_epoch)
    plane.scheduler.on_started(task.task_id, attempt.attempt_id, epoch=ref.session_epoch)
    await plane.register(in_flight=[ref])

    await plane.send(
        pb.WorkerMessage(
            failed=pb.TaskFailed(
                ref=ref,
                error=pb.Error(code="CUDA_OOM", message="out of memory"),
            )
        )
    )

    assert attempt.state is not AttemptState.RUNNING, "the failure never landed"
    assert attempt.error is not None and attempt.error.code == "CUDA_OOM", (
        "the task would have died of PROGRESS_LEASE_EXPIRED instead of the "
        "reason the worker actually reported"
    )


@pytest.mark.asyncio
async def test_reads_and_writes_share_one_containment_rule(plane):
    """The asymmetry that made this bug possible was two implementations of
    the same rule, one of which was missing."""
    for artifact_id in ("", "../../../../etc/passwd", "..\\..\\windows\\win.ini"):
        assert plane.servicer._resolve_input(artifact_id) is None
        assert plane.servicer._contained_artifact(artifact_id) is None


# ── B10: redelivery survives the reconnect that carries it ────────────────


@pytest.mark.asyncio
async def test_register_keeps_an_unacknowledged_result_alive(plane):
    """The worker holds the only copy. Reconciling it away as LOST while it is
    redelivering is the largest silent-loss path in the system."""
    task, attempt = plane.assign()

    await plane.register(completed_unacked=[codec.ref_for(attempt)])

    assert task.get_attempt(attempt.attempt_id).state is not AttemptState.LOST
    assert task.state is not TaskState.QUEUED


@pytest.mark.asyncio
async def test_a_result_from_a_replaced_epoch_still_commits(plane):
    """A result is a statement about a past epoch by construction: it was
    assigned in the session the reconnect just replaced."""
    task, attempt = plane.assign()
    stale_ref = codec.ref_for(attempt)

    await plane.register(completed_unacked=[stale_ref])
    assert stale_ref.session_epoch != plane.epoch, "the reconnect must move the session on"
    await plane.send(_result(stale_ref, payload=b"audio"))

    assert task.state is TaskState.COMPLETED
    assert [m.result_ack.ref.task_id for m in plane.outbox] == [task.task_id]


@pytest.mark.asyncio
async def test_replaced_connection_cannot_mutate_the_live_session(plane):
    """Late connection-state frames belong to the connection that sent them."""
    old_session = plane.session
    await plane.register()
    live = plane.pool.get(plane.worker_id)
    assert live is not None
    expected_capabilities = list(live.record.capabilities)
    expected_heartbeat = live.last_heartbeat_at
    expected_active = live.capacity.active_tasks
    expected_available = live.capacity.available_slots
    expected_latency = live.latency_ms
    old_session.pending_pings[77] = time.monotonic() - 10

    stale_capability = codec.capability_to_pb(
        {
            "engine": "stale-engine",
            "model_id": "stale-model",
            "operations": ["tts"],
            "supported": True,
            "installed": True,
        }
    )
    frames = [
        pb.WorkerMessage(
            heartbeat=pb.Heartbeat(active_tasks=99, available_slots=0)
        ),
        pb.WorkerMessage(
            capabilities=pb.CapabilityUpdate(capabilities=[stale_capability])
        ),
        pb.WorkerMessage(goodbye=pb.WorkerGoodbye(reason="stale stream")),
        pb.WorkerMessage(pong=pb.Pong(nonce=77)),
    ]

    for frame in frames:
        await plane.servicer._handle(old_session, frame)

    assert live.last_heartbeat_at == expected_heartbeat
    assert live.capacity.active_tasks == expected_active
    assert live.capacity.available_slots == expected_available
    assert live.record.capabilities == expected_capabilities
    assert registry.get(plane.worker_id).capabilities == expected_capabilities
    assert live.draining is False
    assert live.latency_ms == expected_latency
    assert 77 in old_session.pending_pings


@pytest.mark.asyncio
async def test_heartbeat_flood_updates_live_state_without_blocking_or_flooding_sqlite(
    plane, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_touch(worker_id: str, **_kwargs) -> None:
        calls.append(worker_id)
        started.set()
        assert release.wait(BARRIER_WATCHDOG_S)

    monkeypatch.setattr(registry, "touch", slow_touch)
    safety_release = threading.Timer(0.5, release.set)
    safety_release.start()
    before = time.monotonic()
    await plane.send(
        pb.WorkerMessage(
            heartbeat=pb.Heartbeat(active_tasks=1, available_slots=1)
        )
    )
    assert time.monotonic() - before < 0.2
    assert await asyncio.to_thread(started.wait, HANG_GUARD_S)

    for _ in range(50):
        await plane.send(
            pb.WorkerMessage(
                heartbeat=pb.Heartbeat(active_tasks=2, available_slots=2)
            )
        )

    live = plane.pool.get(plane.worker_id)
    assert live.capacity.active_tasks == 2
    assert live.capacity.available_slots == 2
    assert calls == [plane.worker_id]
    release.set()
    safety_release.cancel()
    await asyncio.sleep(0.02)
    assert calls == [plane.worker_id]
    await plane.servicer._cancel_session_maintenance(plane.session)


@pytest.mark.asyncio
async def test_capability_flood_coalesces_off_loop_to_the_latest_snapshot(
    plane, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_update(_worker_id: str, *, capabilities: list[dict], **_kwargs) -> None:
        calls.append(capabilities[0]["engine"])
        if len(calls) == 1:
            started.set()
            assert release.wait(BARRIER_WATCHDOG_S)

    monkeypatch.setattr(registry, "update_capabilities", slow_update)
    monkeypatch.setattr(server_module, "_CAPABILITY_UPDATE_INTERVAL_SECONDS", 0.01)

    def update(engine: str) -> pb.WorkerMessage:
        return pb.WorkerMessage(
            capabilities=pb.CapabilityUpdate(
                capabilities=[
                    pb.ModelCapability(
                        engine=engine,
                        model_id=MODEL,
                        operations=[OP],
                        supported=True,
                        installed=True,
                        derived_concurrency=1,
                    )
                ]
            )
        )

    safety_release = threading.Timer(0.5, release.set)
    safety_release.start()
    await plane.send(update("first"))
    assert await asyncio.to_thread(started.wait, HANG_GUARD_S)
    before = time.monotonic()
    for index in range(50):
        await plane.send(update(f"burst-{index}"))
    assert time.monotonic() - before < 0.2
    assert calls == ["first"]

    task = plane.session.capability_update_task
    release.set()
    safety_release.cancel()
    await asyncio.wait_for(task, HANG_GUARD_S)

    assert calls == ["first", "burst-49"]
    assert plane.pool.get(plane.worker_id).record.capabilities[0]["engine"] == "burst-49"


@pytest.mark.asyncio
async def test_queued_capability_update_from_superseded_session_never_lands(
    plane, monkeypatch
):
    monkeypatch.setattr(server_module, "_CAPABILITY_UPDATE_INTERVAL_SECONDS", 0.05)
    old_session = plane.session
    old_session.last_capability_apply_at = time.monotonic()
    await plane.servicer._handle(
        old_session,
        pb.WorkerMessage(
            capabilities=pb.CapabilityUpdate(
                capabilities=[pb.ModelCapability(engine="stale", derived_concurrency=1)]
            )
        ),
    )
    await asyncio.sleep(0)

    fresh = {**_capabilities()[0], "engine": "fresh"}
    await plane.register(capabilities=[fresh])
    await asyncio.sleep(0.1)

    assert plane.pool.get(plane.worker_id).record.capabilities[0]["engine"] == "fresh"
    assert registry.get(plane.worker_id).capabilities[0]["engine"] == "fresh"
    assert old_session.capability_update_task is None


@pytest.mark.asyncio
async def test_oversized_capability_updates_are_dropped_before_conversion(plane):
    before_live = copy.deepcopy(plane.pool.get(plane.worker_id).record.capabilities)
    before_durable = registry.get(plane.worker_id).capabilities
    updates = [
        pb.CapabilityUpdate(
            capabilities=[
                pb.ModelCapability(engine=f"engine-{index}")
                for index in range(server_module._MAX_CAPABILITY_ENTRIES + 1)
            ]
        ),
        pb.CapabilityUpdate(
            capabilities=[
                pb.ModelCapability(
                    engine="oversized",
                    display_name="x"
                    * (server_module._MAX_CAPABILITY_UPDATE_BYTES + 1),
                )
            ]
        ),
    ]

    for update in updates:
        await plane.send(pb.WorkerMessage(capabilities=update))

    assert plane.session.capability_update_task is None
    assert plane.pool.get(plane.worker_id).record.capabilities == before_live
    assert registry.get(plane.worker_id).capabilities == before_durable


@pytest.mark.asyncio
async def test_hostile_wire_concurrency_claims_are_clamped_to_server_limit(plane):
    hostile = 2**32 - 1
    await plane.register(max_concurrent_tasks=hostile)
    live = plane.pool.get(plane.worker_id)
    assert live.capacity.max_concurrent_tasks == server_module.MAX_CONCURRENT_TASKS
    assert registry.get(plane.worker_id).max_concurrent_tasks == server_module.MAX_CONCURRENT_TASKS

    await plane.send(
        pb.WorkerMessage(
            capabilities=pb.CapabilityUpdate(
                capabilities=[
                    pb.ModelCapability(
                        engine=ENGINE,
                        model_id=MODEL,
                        derived_concurrency=hostile,
                    )
                ]
            )
        )
    )
    await asyncio.wait_for(plane.session.capability_update_task, HANG_GUARD_S)
    slot = live.capacity.slot_for(ENGINE, MODEL)
    assert slot.derived_concurrency == server_module.MAX_CONCURRENT_TASKS
    assert registry.get(plane.worker_id).capabilities[0]["derived_concurrency"] == server_module.MAX_CONCURRENT_TASKS

    await plane.send(
        pb.WorkerMessage(
            heartbeat=pb.Heartbeat(
                active_tasks=hostile, available_slots=hostile
            )
        )
    )
    await asyncio.wait_for(plane.session.heartbeat_touch_task, HANG_GUARD_S)
    assert live.capacity.active_tasks == server_module.MAX_CONCURRENT_TASKS
    assert live.capacity.available_slots == 0


@pytest.mark.asyncio
async def test_unconfirmed_replacement_quiesces_then_restores_live_session(
    plane, monkeypatch
):
    from worker.transport import server as server_module

    monkeypatch.setattr(server_module, "_REGISTRATION_OPEN_TIMEOUT_SECONDS", 0.01)
    old_session = plane.session
    old_worker = plane.pool.get(plane.worker_id)
    response = await plane.register(activate=False)

    assert plane.servicer._sessions[plane.worker_id] is old_session
    assert plane.pool.get(plane.worker_id) is old_worker
    assert old_worker.registration_pending is True
    assert old_worker.draining is False
    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None

    await asyncio.sleep(0.05)

    assert plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    ) is None
    assert plane.servicer._sessions[plane.worker_id] is old_session
    assert plane.pool.get(plane.worker_id) is old_worker
    assert old_worker.registration_pending is False
    assert old_worker.draining is False


@pytest.mark.asyncio
async def test_discarded_registration_never_publishes_staged_worker_metadata(plane):
    before = registry.get(plane.worker_id)
    staged_capability = {
        "engine": "future-engine",
        "model_id": "future-model",
        "operations": ["tts"],
        "supported": True,
        "installed": True,
    }
    response = await plane.register(
        activate=False,
        capabilities=[staged_capability],
        host={"hostname": "replacement", "os": "linux", "arch": "arm64"},
        max_concurrent_tasks=7,
    )

    pending = registry.get(plane.worker_id)
    assert pending.capabilities == before.capabilities
    assert pending.host == before.host
    assert pending.max_concurrent_tasks == before.max_concurrent_tasks

    assert plane.servicer.discard_unopened_session(
        plane.worker_id, session_token=response.session_token
    )
    after = registry.get(plane.worker_id)
    assert after.capabilities == before.capabilities
    assert after.host == before.host
    assert after.max_concurrent_tasks == before.max_concurrent_tasks


@pytest.mark.asyncio
async def test_real_drain_survives_pending_registration_discard_and_activation(plane):
    old_worker = plane.pool.get(plane.worker_id)
    discarded = await plane.register(activate=False)
    assert await plane.servicer.drain(plane.worker_id)
    assert old_worker.draining is True

    assert plane.servicer.discard_unopened_session(
        plane.worker_id, session_token=discarded.session_token
    )
    assert old_worker.registration_pending is False
    assert old_worker.draining is True

    replacement = await plane.register(activate=False)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=replacement.session_token
    )
    assert pending is not None
    assert plane.servicer._activate_session(pending) is not None
    assert plane.pool.get(plane.worker_id).draining is True


@pytest.mark.asyncio
async def test_failed_replacement_activation_restores_live_pool_snapshot(
    plane, monkeypatch
):
    task, attempt = plane.assign()
    before = copy.deepcopy(task)
    old_session = plane.session
    old_worker = plane.pool.get(plane.worker_id)
    response = await plane.register(activate=False)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert pending is not None

    def fail_reconciliation(*_args, **_kwargs):
        raise OSError("task store unavailable")

    monkeypatch.setattr(task_store, "save_many", fail_reconciliation)

    with pytest.raises(OSError, match="task store unavailable"):
        await plane.servicer._activate_session_async(pending)

    assert plane.servicer._sessions[plane.worker_id] is old_session
    assert plane.pool.get(plane.worker_id) is old_worker
    assert old_worker.registration_pending is True
    assert task == before
    assert attempt == before.attempts[0]


@pytest.mark.asyncio
async def test_blocked_reconnect_persistence_does_not_stall_another_worker(
    plane, monkeypatch
):
    """A large recovery generation cannot hold the shared gRPC event loop."""
    for index in range(64):
        plane.scheduler.submit(
            operation=OP,
            engine=ENGINE,
            model_id=f"queued-{index}",
        )

    reconnecting = SimpleNamespace(
        servicer=plane.servicer,
        keypair=WorkerKeypair.generate(),
        worker_id="",
        epoch=0,
    )
    response = await _Plane.register(reconnecting, activate=False)
    pending = plane.servicer.session_for(
        reconnecting.worker_id, session_token=response.session_token
    )
    assert pending is not None

    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    main_thread = threading.current_thread()
    real_save_many = task_store.save_many
    save_calls = 0

    def blocked_save_many(*args, **kwargs):
        nonlocal save_calls
        assert threading.current_thread() is not main_thread
        call = save_calls
        save_calls += 1
        if call >= len(started):
            return real_save_many(*args, **kwargs)
        started[call].set()
        if not release[call].wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release reconciliation")
        return real_save_many(*args, **kwargs)

    monkeypatch.setattr(task_store, "save_many", blocked_save_many)
    monkeypatch.setattr(
        plane.servicer, "_queue_heartbeat_touch", lambda _session: None
    )
    close_stream = asyncio.Event()

    async def frames():
        await close_stream.wait()
        if False:
            yield pb.WorkerMessage()

    class Context:
        def invocation_metadata(self):
            return (("x-omnivoice-session", response.session_token),)

        async def write(self, _message):
            return None

        async def abort(self, _code, message):
            raise RuntimeError(message)

    control = asyncio.create_task(plane.servicer.Control(frames(), Context()))
    assert await asyncio.to_thread(started[0].wait, HANG_GUARD_S)

    await asyncio.wait_for(
        plane.servicer._handle(
            plane.session,
            pb.WorkerMessage(
                heartbeat=pb.Heartbeat(active_tasks=0, available_slots=2)
            ),
        ),
        timeout=HANG_GUARD_S,
    )
    assert await asyncio.wait_for(
        plane.servicer.prewarm(
            plane.worker_id, engine=ENGINE, model_id=MODEL
        ),
        timeout=HANG_GUARD_S,
    )
    assert not control.done()

    release[0].set()
    while not pending.activated:
        await asyncio.sleep(0)
    live = plane.pool.get(reconnecting.worker_id)
    task = plane.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=MODEL
    )
    plane.scheduler._bind(task, live, now=time.time())

    close_stream.set()
    assert await asyncio.to_thread(started[1].wait, HANG_GUARD_S)
    await asyncio.wait_for(
        plane.servicer._handle(
            plane.session,
            pb.WorkerMessage(
                heartbeat=pb.Heartbeat(active_tasks=0, available_slots=2)
            ),
        ),
        timeout=HANG_GUARD_S,
    )
    assert await asyncio.wait_for(
        plane.servicer.prewarm(
            plane.worker_id, engine=ENGINE, model_id=MODEL
        ),
        timeout=HANG_GUARD_S,
    )
    assert not control.done()

    release[1].set()
    await asyncio.wait_for(control, timeout=_CONTROL_EXIT_TIMEOUT_S)


@pytest.mark.asyncio
async def test_terminal_attempt_claimed_during_handshake_is_cancelled_on_activation(plane):
    task, attempt = plane.assign()
    ref = codec.ref_for(attempt)
    response = await plane.register(
        in_flight=[ref], activate=False, max_concurrent_tasks=1
    )
    assert plane.scheduler.cancel(task.task_id)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert pending is not None

    assert plane.servicer._activate_session(pending) is not None

    cancels = [message.cancel.ref for message in list(pending.outbox._queue)]
    assert [(item.task_id, item.attempt_id) for item in cancels] == [
        (task.task_id, attempt.attempt_id)
    ]
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 1
    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_unknown_claim_consumes_capacity_until_its_cancel_lands(plane):
    unknown = pb.TaskRef(
        task_id="unknown-task", attempt_id="unknown-attempt", session_epoch=1
    )
    response = await plane.register(
        in_flight=[unknown], activate=False, max_concurrent_tasks=1
    )
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )

    assert pending is not None
    assert plane.servicer._activate_session(pending) is not None
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 1
    cancel = next(
        message.cancel for message in pending.outbox._queue if message.HasField("cancel")
    )

    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None

    await plane.servicer._handle(
        pending,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=cancel.ref)),
    )
    await plane.servicer._handle(
        pending,
        pb.WorkerMessage(
            heartbeat=pb.Heartbeat(active_tasks=0, available_slots=1)
        ),
    )

    assert pending.pending_claim_cancels == set()
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 0
    assert plane.scheduler.next_assignment() is not None


@pytest.mark.asyncio
async def test_unknown_claim_ack_requires_the_exact_current_session(plane):
    unknown = pb.TaskRef(
        task_id="unknown-task", attempt_id="unknown-attempt", session_epoch=1
    )
    first_response = await plane.register(
        in_flight=[unknown], activate=False, max_concurrent_tasks=1
    )
    first = plane.servicer.session_for(
        plane.worker_id, session_token=first_response.session_token
    )
    assert first is not None
    assert plane.servicer._activate_session(first) is not None
    first_cancel = next(
        message.cancel for message in first.outbox._queue if message.HasField("cancel")
    )

    second_response = await plane.register(
        in_flight=[unknown], activate=False, max_concurrent_tasks=1
    )
    second = plane.servicer.session_for(
        plane.worker_id, session_token=second_response.session_token
    )
    assert second is not None
    assert plane.servicer._activate_session(second) is not None
    second_cancel = next(
        message.cancel for message in second.outbox._queue if message.HasField("cancel")
    )

    await plane.servicer._handle(
        first,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=first_cancel.ref)),
    )
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 1

    lookalike = pb.TaskRef(
        task_id="different-task",
        attempt_id=second_cancel.ref.attempt_id,
        session_epoch=second_cancel.ref.session_epoch,
    )
    await plane.servicer._handle(
        second,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=lookalike)),
    )
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 1

    await plane.servicer._handle(
        second,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=second_cancel.ref)),
    )
    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 0


@pytest.mark.asyncio
async def test_reconnect_seeds_capacity_from_claimed_live_attempts(plane):
    task, attempt = plane.assign()
    response = await plane.register(
        in_flight=[codec.ref_for(attempt)], activate=False, max_concurrent_tasks=1
    )
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )

    assert pending is not None
    assert plane.servicer._activate_session(pending) is not None
    live = plane.pool.get(plane.worker_id)
    assert live.capacity.active_tasks == 1
    assert live.capacity.slot_for(task.engine, task.model_id).active == 1

    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_terminal_claim_cancel_ack_cannot_release_a_live_models_slot(plane):
    live_task, live_attempt = plane.assign()
    terminal_task, terminal_attempt = plane.assign()
    assert plane.scheduler.cancel(terminal_task.task_id)
    capabilities = _capabilities()
    capabilities[0]["derived_concurrency"] = 1
    response = await plane.register(
        in_flight=[codec.ref_for(live_attempt), codec.ref_for(terminal_attempt)],
        activate=False,
        capabilities=capabilities,
        max_concurrent_tasks=2,
    )
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )

    assert pending is not None
    assert plane.servicer._activate_session(pending) is not None
    capacity = plane.pool.get(plane.worker_id).capacity
    assert capacity.slot_for(ENGINE, MODEL).active == 2

    # The node has already cancelled terminal B locally before its mandatory
    # registration-confirmation heartbeat, but the control plane must retain
    # B's reservation until the queued CancelAck identifies which model freed.
    await plane.servicer._handle(
        pending,
        pb.WorkerMessage(
            heartbeat=pb.Heartbeat(active_tasks=1, available_slots=1)
        ),
    )
    assert capacity.active_tasks == 2

    await plane.servicer._handle(
        pending,
        pb.WorkerMessage(
            cancel_ack=pb.TaskCancelAck(ref=codec.ref_for(terminal_attempt))
        ),
    )

    assert capacity.active_tasks == 1
    assert capacity.slot_for(ENGINE, MODEL).active == 1
    assert live_attempt.attempt_id in plane.pool.get(plane.worker_id).in_flight
    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_unseeded_terminal_claim_ack_cannot_release_a_live_model_slot(
    plane,
):
    other_model = "Other-TTS"
    capabilities = _capabilities()
    capabilities[0]["derived_concurrency"] = 1
    capabilities.append(
        {
            **capabilities[0],
            "model_id": other_model,
            "derived_concurrency": 1,
        }
    )

    live_a = plane.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=MODEL
    )
    attempt_a = live_a.assign(
        worker_id=plane.worker_id, session_epoch=plane.epoch
    )
    live_b = plane.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=other_model
    )
    attempt_b = live_b.assign(
        worker_id=plane.worker_id, session_epoch=plane.epoch
    )
    terminal_c = plane.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=MODEL
    )
    attempt_c = terminal_c.assign(
        worker_id=plane.worker_id, session_epoch=plane.epoch
    )
    assert plane.scheduler.cancel(terminal_c.task_id)

    response = await plane.register(
        in_flight=[
            codec.ref_for(attempt_a),
            codec.ref_for(attempt_b),
            codec.ref_for(attempt_c),
        ],
        activate=False,
        capabilities=capabilities,
        max_concurrent_tasks=2,
    )
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert pending is not None
    assert plane.servicer._activate_session(pending) is not None
    assert attempt_c.attempt_id not in pending.pending_claim_reservations
    cancel = next(
        message.cancel
        for message in pending.outbox._queue
        if message.HasField("cancel")
        and message.cancel.ref.attempt_id == attempt_c.attempt_id
    )

    capacity = plane.pool.get(plane.worker_id).capacity
    assert capacity.active_tasks == 2
    assert capacity.slot_for(ENGINE, MODEL).active == 1
    assert capacity.slot_for(ENGINE, other_model).active == 1

    await plane.servicer._handle(
        pending,
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=cancel.ref)),
    )

    live = plane.pool.get(plane.worker_id)
    assert live.capacity.active_tasks == 2
    assert live.capacity.slot_for(ENGINE, MODEL).active == 1
    assert live.capacity.slot_for(ENGINE, other_model).active == 1
    assert attempt_c.attempt_id not in live.in_flight
    assert attempt_a.attempt_id in live.in_flight
    assert attempt_b.attempt_id in live.in_flight
    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_stream_teardown_cleans_authority_when_disconnect_persistence_fails(
    plane, monkeypatch
):
    tasks = []
    for _ in range(2):
        task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
        assert plane.scheduler.next_assignment() is not None
        tasks.append(task)
    before = [copy.deepcopy(task) for task in tasks]
    durable_before = [task_store.get(task.task_id) for task in tasks]
    session = plane.session
    token = session.session.token
    real_save = task_store._save_with_conn
    saves = 0

    def fail_second_disconnect_write(conn, task, *, stamp):
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("task store unavailable")
        real_save(conn, task, stamp=stamp)

    monkeypatch.setattr(task_store, "_save_with_conn", fail_second_disconnect_write)

    async def no_frames():
        if False:
            yield None

    with pytest.raises(OSError, match="task store unavailable"):
        await plane.servicer.run_inbound_stream(session, no_frames(), object())

    assert plane.pool.get(plane.worker_id) is None
    assert plane.worker_id not in plane.servicer._sessions
    assert token not in plane.servicer._by_token
    assert tasks == before
    assert [task_store.get(task.task_id) for task in tasks] == durable_before


@pytest.mark.asyncio
async def test_reentrant_revoke_cannot_activate_a_cached_authority_row(
    plane, monkeypatch
):
    response = await plane.register(activate=False)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert pending is not None
    cached = registry.get(plane.worker_id)

    def revoke_then_return_stale(_worker_id):
        assert registry.revoke(plane.worker_id)
        plane.scheduler.on_disconnected(plane.worker_id)
        return cached

    monkeypatch.setattr(registry, "get", revoke_then_return_stale)

    assert await plane.servicer._activate_session_async(pending) is None
    assert plane.pool.get(plane.worker_id) is None
    assert response.session_token not in plane.servicer._by_token


@pytest.mark.asyncio
async def test_revoked_live_session_cannot_extend_or_finish_its_attempt(plane):
    task, attempt = plane.assign()
    plane.scheduler.on_accepted(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    plane.scheduler.on_started(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    before = copy.deepcopy(task)
    session = plane.session
    ref = codec.ref_for(attempt)

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    await plane.servicer._handle(
        session,
        pb.WorkerMessage(
            progress=pb.TaskProgress(ref=ref, progress=0.9, stage="revoked")
        ),
    )
    await plane.servicer._handle(session, _result(ref, payload=b"revoked"))

    assert task == before
    assert plane.worker_id not in plane.servicer._sessions
    assert session.session.token not in plane.servicer._by_token


@pytest.mark.asyncio
async def test_revocation_wakes_and_ends_an_open_control_stream(plane):
    from worker.transport.server import SESSION_METADATA_KEY

    session = plane.session

    class Context:
        def invocation_metadata(self):
            return ((SESSION_METADATA_KEY, session.session.token),)

        async def write(self, _message):
            pass

        async def abort(self, _code, message):
            raise RuntimeError(message)

    async def frames():
        await asyncio.Event().wait()
        if False:
            yield None

    control = asyncio.create_task(plane.servicer.Control(frames(), Context()))
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    assert session.stream_open is True

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    plane.scheduler.on_disconnected(plane.worker_id)
    await asyncio.wait_for(control, timeout=_CONTROL_EXIT_TIMEOUT_S)

    assert session.stream_open is False
    assert plane.pool.get(plane.worker_id) is None


@pytest.mark.asyncio
async def test_revocation_cancels_an_assignment_already_blocked_in_control_write(plane):
    from worker.transport.server import SESSION_METADATA_KEY

    session = plane.session
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    written = []

    class Context:
        def invocation_metadata(self):
            return ((SESSION_METADATA_KEY, session.session.token),)

        async def write(self, message):
            if message.WhichOneof("payload") == "assignment":
                write_started.set()
                await release_write.wait()
            written.append(message.WhichOneof("payload"))

        async def abort(self, _code, message):
            raise RuntimeError(message)

    async def frames():
        await asyncio.Event().wait()
        if False:
            yield None

    control = asyncio.create_task(plane.servicer.Control(frames(), Context()))
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    assert await plane.servicer.dispatch(assignment)
    await asyncio.wait_for(write_started.wait(), timeout=HANG_GUARD_S)

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    plane.scheduler.on_disconnected(plane.worker_id)
    release_write.set()
    await asyncio.wait_for(control, timeout=_CONTROL_EXIT_TIMEOUT_S)

    assert "assignment" not in written
    assert task.active_attempt is not None


@pytest.mark.asyncio
async def test_replacement_cancels_an_old_assignment_blocked_in_control_write(plane):
    from worker.transport.server import SESSION_METADATA_KEY

    old_session = plane.session
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    written = []

    class Context:
        def invocation_metadata(self):
            return ((SESSION_METADATA_KEY, old_session.session.token),)

        async def write(self, message):
            if message.WhichOneof("payload") == "assignment":
                write_started.set()
                await release_write.wait()
            written.append(message.WhichOneof("payload"))

        async def abort(self, _code, message):
            raise RuntimeError(message)

    async def frames():
        await asyncio.Event().wait()
        if False:
            yield None

    control = asyncio.create_task(plane.servicer.Control(frames(), Context()))
    for _ in range(20):
        if old_session.stream_open:
            break
        await asyncio.sleep(0)
    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    assert await plane.servicer.dispatch(assignment)
    await asyncio.wait_for(write_started.wait(), timeout=HANG_GUARD_S)

    response = await plane.register(activate=False)
    replacement = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert replacement is not None
    assert plane.servicer._activate_session(replacement) is not None
    assert old_session.egress_fenced is True
    release_write.set()
    await asyncio.sleep(0)

    assert "assignment" not in written
    assert not any(message.HasField("assignment") for message in old_session.outbox._queue)
    control.cancel()
    await asyncio.gather(control, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_cancels_an_assignment_blocked_in_inbound_egress(plane):
    from worker.inbound.connector import NodeConnection

    session = plane.session
    transfer_started = asyncio.Event()
    release_transfer = asyncio.Event()
    delivered = []

    class BlockingOutbox:
        async def put(self, message):
            transfer_started.set()
            await release_transfer.wait()
            delivered.append(message.WhichOneof("payload"))

    connector = object.__new__(NodeConnection)
    connector._outbox = BlockingOutbox()
    pump = asyncio.create_task(connector._pump_outbound(session))
    session.outbox.put_nowait(pb.ServerMessage(assignment=pb.TaskAssignment()))
    await asyncio.wait_for(transfer_started.wait(), timeout=HANG_GUARD_S)

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release_transfer.set()
    await asyncio.gather(pump, return_exceptions=True)

    assert delivered == []


@pytest.mark.asyncio
async def test_replacement_cancels_an_assignment_blocked_in_inbound_egress(plane):
    from worker.inbound.connector import NodeConnection

    old_session = plane.session
    transfer_started = asyncio.Event()
    release_transfer = asyncio.Event()
    delivered = []

    class BlockingOutbox:
        async def put(self, message):
            transfer_started.set()
            await release_transfer.wait()
            delivered.append(message.WhichOneof("payload"))

    connector = object.__new__(NodeConnection)
    connector._outbox = BlockingOutbox()
    pump = asyncio.create_task(connector._pump_outbound(old_session))
    old_session.outbox.put_nowait(pb.ServerMessage(assignment=pb.TaskAssignment()))
    await asyncio.wait_for(transfer_started.wait(), timeout=HANG_GUARD_S)

    response = await plane.register(activate=False)
    replacement = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert replacement is not None
    assert plane.servicer._activate_session(replacement) is not None
    release_transfer.set()
    await asyncio.gather(pump, return_exceptions=True)

    assert old_session.egress_fenced is True
    assert delivered == []


@pytest.mark.asyncio
async def test_revocation_discards_an_assignment_already_in_inbound_request_queue(plane):
    from worker.inbound.connector import NodeConnection

    session = plane.session
    connector = object.__new__(NodeConnection)
    connector._outbox = asyncio.Queue()
    connector._active_session = session
    session.connection = connector
    connector._outbox.put_nowait(
        pb.ServerMessage(assignment=pb.TaskAssignment())
    )

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    outbound = connector._outbound()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(outbound), timeout=HANG_GUARD_S)


@pytest.mark.asyncio
async def test_revocation_during_inbound_result_fetch_cannot_commit(plane, monkeypatch):
    task, attempt = plane.assign()
    plane.scheduler.on_accepted(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    plane.scheduler.on_started(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    session = plane.session
    session.connection = object()
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    before = copy.deepcopy(task)

    async def blocked_fetch(_session, _attempt, _artifact):
        fetch_started.set()
        await release_fetch.wait()
        return "fetched.wav"

    monkeypatch.setattr(plane.servicer, "_fetch_inbound_artifact", blocked_fetch)
    handling = asyncio.create_task(
        plane.servicer._handle(
            session,
            _result(codec.ref_for(attempt), artifact_id="remote-result"),
        )
    )
    await asyncio.wait_for(fetch_started.wait(), timeout=HANG_GUARD_S)

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release_fetch.set()
    await handling

    assert task == before
    assert not any(
        message.WhichOneof("payload") == "result_ack"
        for message in list(session.outbox._queue)
    )


@pytest.mark.asyncio
async def test_revocation_after_inbound_fetch_wakes_removes_bytes_and_budget(plane):
    task, attempt = plane.assign()
    session = plane.session
    payload = b"fetched before revocation publishes"
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)
            fetch_started.set()
            await release_fetch.wait()

    session.connection = Connection()
    artifact = pb.ArtifactRef(
        artifact_id="remote-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    handling = asyncio.create_task(
        plane.servicer._on_result(
            session,
            pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
        )
    )
    await asyncio.wait_for(fetch_started.wait(), timeout=HANG_GUARD_S)

    release_fetch.set()
    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    await handling

    final = plane.servicer._artifact_path(task.task_id, attempt.attempt_id)
    assert final is not None and not os.path.exists(final)
    assert not list(Path(final).parent.glob("*.part"))
    assert plane.servicer._artifact_bytes == {}
    assert task.state is not TaskState.COMPLETED


@pytest.mark.asyncio
async def test_cancel_during_inbound_fetch_discards_the_nonwinning_artifact(plane):
    task, attempt = plane.assign()
    plane.scheduler.on_accepted(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    plane.scheduler.on_started(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    session = plane.session
    payload = b"late cancelled result"
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            with open(destination, "wb") as handle:
                handle.write(payload)
            fetch_started.set()
            await release_fetch.wait()

    session.connection = Connection()
    artifact = pb.ArtifactRef(
        artifact_id="remote-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    handling = asyncio.create_task(
        plane.servicer._on_result(
            session,
            pb.TaskResult(ref=codec.ref_for(attempt), artifacts=[artifact]),
        )
    )
    await asyncio.wait_for(fetch_started.wait(), timeout=HANG_GUARD_S)

    plane.scheduler.cancel(task.task_id)
    release_fetch.set()
    await handling

    final = plane.servicer._artifact_path(task.task_id, attempt.attempt_id)
    assert final is not None and not os.path.exists(final)
    assert plane.servicer._artifact_bytes == {}
    assert any(
        frame.WhichOneof("payload") == "result_ack"
        for frame in list(session.outbox._queue)
    )


@pytest.mark.asyncio
async def test_revoke_cleans_partial_result_when_inbound_stream_cancels_fetch(plane):
    task, attempt = plane.assign()
    plane.scheduler.on_accepted(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    plane.scheduler.on_started(
        task.task_id, attempt.attempt_id, epoch=attempt.session_epoch
    )
    before = copy.deepcopy(task)
    session = plane.session
    fetch_started = asyncio.Event()
    destinations = []
    path = plane.servicer._artifact_path(task.task_id, attempt.attempt_id)
    assert path is not None

    class Connection:
        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            destinations.append(destination)
            with open(destination, "wb") as handle:
                handle.write(b"partial result")
            fetch_started.set()
            await asyncio.Event().wait()

    async def frames():
        yield _result(codec.ref_for(attempt), artifact_id="remote-result")
        await asyncio.Event().wait()

    stream = asyncio.create_task(
        plane.servicer.run_inbound_stream(session, frames(), Connection())
    )
    await asyncio.wait_for(fetch_started.wait(), timeout=HANG_GUARD_S)
    assert len(destinations) == 1 and os.path.exists(destinations[0])

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    await asyncio.wait_for(stream, timeout=HANG_GUARD_S)

    assert not os.path.exists(path)
    assert not os.path.exists(destinations[0])
    assert task == before
    assert session.connection is None


@pytest.mark.asyncio
async def test_reentrant_disable_cannot_publish_a_cached_enabled_row(
    plane, monkeypatch
):
    response = await plane.register(activate=False)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert pending is not None
    cached = registry.get(plane.worker_id)

    def disable_then_return_stale(_worker_id):
        registry.set_enabled(plane.worker_id, False)
        return cached

    monkeypatch.setattr(registry, "get", disable_then_return_stale)

    assert plane.servicer._activate_session(pending) is not None
    live = plane.pool.get(plane.worker_id)
    assert live is not None and live.record.enabled is False
    plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assert plane.scheduler.next_assignment() is None


@pytest.mark.asyncio
async def test_staged_input_verification_does_not_block_heartbeats(
    plane, monkeypatch
):
    from threading import Event, Timer

    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    payload = b"large staged reference"
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = os.path.join(task_store.INPUTS_DIRNAME, f"{digest}.wav")
    staged = Path(plane.artifact_dir, artifact_id)
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(payload)
    task.params = {
        "text": "hello",
        "ref_audio": artifact_id,
        task_store.INPUTS_PARAM_KEY: [
            {
                "artifact_id": artifact_id,
                "filename": "reference.wav",
                "sha256": digest,
                "size_bytes": len(payload),
                "key": "ref_audio",
                "index": None,
            }
        ],
    }
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    verification_started = Event()
    release_verification = Event()
    real_digest = task_store._digest

    def blocked_digest(path):
        verification_started.set()
        if not release_verification.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release staged-input verification")
        return real_digest(path)

    monkeypatch.setattr(task_store, "_digest", blocked_digest)
    watchdog = Timer(0.5, release_verification.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    dispatch = asyncio.create_task(plane.servicer.dispatch(assignment))

    async def wait_for_verification():
        while not verification_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_verification(), timeout=HANG_GUARD_S)
        await plane.send(
            pb.WorkerMessage(
                heartbeat=pb.Heartbeat(active_tasks=1, available_slots=1)
            )
        )
        assert asyncio.get_running_loop().time() - started_at < 0.2
        assert plane.pool.get(plane.worker_id).capacity.active_tasks == 1
    finally:
        release_verification.set()
        watchdog.cancel()
    assert await dispatch is True


@pytest.mark.asyncio
async def test_inbound_upload_cannot_send_to_a_replaced_session(plane, monkeypatch):
    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    old_session = plane.session
    old_session.connection = object()
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()

    real_assignment = codec.assignment_to_pb

    def assignment_with_input(*args, **kwargs):
        message = real_assignment(*args, **kwargs)
        message.inputs.add(artifact_id="staged-input")
        return message

    async def blocked_upload(_session, _message):
        upload_started.set()
        await release_upload.wait()

    monkeypatch.setattr(codec, "assignment_to_pb", assignment_with_input)
    monkeypatch.setattr(plane.servicer, "_push_inbound_inputs", blocked_upload)
    sending = asyncio.create_task(plane.servicer.dispatch(assignment))
    await asyncio.wait_for(upload_started.wait(), timeout=HANG_GUARD_S)

    response = await plane.register(activate=False)
    replacement = plane.servicer.session_for(
        plane.worker_id, session_token=response.session_token
    )
    assert replacement is not None
    assert plane.servicer._activate_session(replacement) is not None
    release_upload.set()

    assert await sending is False
    assert assignment.task.active_attempt is not assignment.attempt
    assert not any(message.HasField("assignment") for message in old_session.outbox._queue)
    assert not any(message.HasField("assignment") for message in replacement.outbox._queue)


@pytest.mark.asyncio
async def test_inbound_upload_cannot_send_an_attempt_cancelled_while_awaiting(
    plane, monkeypatch
):
    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    session = plane.session
    session.connection = object()
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()

    real_assignment = codec.assignment_to_pb

    def assignment_with_input(*args, **kwargs):
        message = real_assignment(*args, **kwargs)
        message.inputs.add(artifact_id="staged-input")
        return message

    async def blocked_upload(_session, _message):
        upload_started.set()
        await release_upload.wait()

    monkeypatch.setattr(codec, "assignment_to_pb", assignment_with_input)
    monkeypatch.setattr(plane.servicer, "_push_inbound_inputs", blocked_upload)
    sending = asyncio.create_task(plane.servicer.dispatch(assignment))
    await asyncio.wait_for(upload_started.wait(), timeout=HANG_GUARD_S)

    assert plane.scheduler.cancel(task.task_id)
    release_upload.set()

    assert await sending is False
    assert not any(message.HasField("assignment") for message in session.outbox._queue)


@pytest.mark.asyncio
async def test_revoke_stops_an_inbound_input_upload_before_more_bytes_leave(
    plane, monkeypatch
):
    task = plane.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None
    session = plane.session
    session.connection = object()
    first_chunk_sent = asyncio.Event()
    release_upload = asyncio.Event()
    sent = []

    real_assignment = codec.assignment_to_pb

    def assignment_with_input(*args, **kwargs):
        message = real_assignment(*args, **kwargs)
        message.inputs.add(artifact_id="user-reference-audio")
        return message

    async def upload_in_chunks(_session, _message):
        sent.append("first")
        first_chunk_sent.set()
        await release_upload.wait()
        sent.append("second")

    monkeypatch.setattr(codec, "assignment_to_pb", assignment_with_input)
    monkeypatch.setattr(plane.servicer, "_push_inbound_inputs", upload_in_chunks)
    dispatch = asyncio.create_task(plane.servicer.dispatch(assignment))
    await asyncio.wait_for(first_chunk_sent.wait(), timeout=HANG_GUARD_S)

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release_upload.set()

    assert await asyncio.wait_for(dispatch, timeout=HANG_GUARD_S) is False
    assert sent == ["first"]
    assert not any(message.HasField("assignment") for message in session.outbox._queue)


@pytest.mark.asyncio
async def test_cancelled_confirmation_immediately_releases_pending_handoff(
    plane, monkeypatch
):
    from worker.inbound import connector as connector_module

    old_worker = plane.pool.get(plane.worker_id)
    response = await plane.register(activate=False)
    waiting = asyncio.Event()

    class Stream:
        def __init__(self):
            self.reads = 0

        async def read(self):
            self.reads += 1
            if self.reads == 1:
                return pb.WorkerMessage(register=pb.RegisterRequest())
            waiting.set()
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

    connection = connector_module.NodeConnection(
        plane.servicer,
        SimpleNamespace(
            host="gpu-node", endpoint="gpu-node:7444", secret="ovnode_test"
        ),
    )
    monkeypatch.setattr(connector_module, "_fetch_pinned_certificate", lambda _c: b"cert")
    monkeypatch.setattr(connector_module.pb_grpc, "NodeServiceStub", lambda _c: Stub())
    monkeypatch.setattr(connection, "_channel", lambda _certificate: Channel())
    async def register(_request):
        return response

    monkeypatch.setattr(connection, "_register", register)

    task = asyncio.create_task(connection._connect_once())
    await asyncio.wait_for(waiting.wait(), timeout=HANG_GUARD_S)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert plane.servicer._pending_sessions.get(plane.worker_id) is None
    assert response.session_token not in plane.servicer._by_token
    assert old_worker.registration_pending is False


@pytest.mark.asyncio
async def test_registration_retry_reuses_the_single_pending_epoch_and_token(plane):
    old_session = plane.session
    first = await plane.register(activate=False)
    pending = plane.servicer.session_for(
        plane.worker_id, session_token=first.session_token
    )
    assert pending is not None

    retried = await plane.register(activate=False)

    assert retried.session_token == first.session_token
    assert retried.session_epoch == first.session_epoch
    assert plane.servicer.session_for(
        plane.worker_id, session_token=retried.session_token
    ) is pending
    assert plane.servicer._sessions[plane.worker_id] is old_session
    assert plane.servicer._activate_session(pending) is not None
    assert plane.servicer._sessions[plane.worker_id] is pending


@pytest.mark.asyncio
async def test_concurrent_register_flood_persists_one_pending_epoch_off_loop(
    plane, monkeypatch
):
    real_begin = registry.begin_session
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_begin(worker_id: str, **kwargs) -> int:
        calls.append(worker_id)
        started.set()
        assert release.wait(BARRIER_WATCHDOG_S)
        return real_begin(worker_id, **kwargs)

    monkeypatch.setattr(registry, "begin_session", slow_begin)
    before_epoch = registry.get(plane.worker_id).session_epoch
    safety_release = threading.Timer(0.5, release.set)
    safety_release.start()
    registrations = [
        asyncio.create_task(plane.register(activate=False)) for _ in range(25)
    ]
    assert await asyncio.to_thread(started.wait, HANG_GUARD_S)

    # The durable call is blocked, but the gRPC loop remains runnable and no
    # sibling Register starts a second SQLite epoch transaction.
    before = time.monotonic()
    await asyncio.sleep(0)
    assert time.monotonic() - before < 0.2
    assert calls == [plane.worker_id]

    release.set()
    safety_release.cancel()
    responses = await asyncio.wait_for(asyncio.gather(*registrations), HANG_GUARD_S)

    assert len({response.session_token for response in responses}) == 1
    assert len({response.session_epoch for response in responses}) == 1
    assert calls == [plane.worker_id]
    assert registry.get(plane.worker_id).session_epoch == before_epoch + 1


@pytest.mark.asyncio
async def test_a_result_this_plane_cannot_place_is_not_acknowledged(plane):
    """An ack is the worker's licence to forget. Granting it for a frame we
    dropped destroys the render."""
    await plane.send(_result(codec.task_ref("no-such-task", "no-such-attempt", plane.epoch)))

    assert plane.outbox == []


@pytest.mark.asyncio
async def test_a_duplicate_result_is_acknowledged(plane):
    """Redelivery of work that already committed is not wrong, it just lost —
    without an ack the worker redelivers forever."""
    task, attempt = plane.assign()
    ref = codec.ref_for(attempt)
    await plane.send(_result(ref, payload=b"audio"))

    await plane.send(_result(ref, payload=b"audio"))

    assert task.state is TaskState.COMPLETED
    assert len(plane.outbox) == 2


@pytest.mark.asyncio
async def test_inbound_redelivery_reuses_bytes_fetched_before_commit_failed(
    plane, monkeypatch
):
    task, attempt = plane.assign()
    payload = b"durable rendered audio"
    artifact = pb.ArtifactRef(
        artifact_id="node-result",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        filename="result.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    class Connection:
        calls = 0

        async def fetch_result(self, _ref, destination, *, max_bytes=None):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("the node already served this result")
            with open(destination, "wb") as handle:
                handle.write(payload)

    connection = Connection()
    plane.session.connection = connection
    real_commit = plane.scheduler.on_result

    def fail_commit_once(*_args, **_kwargs):
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(plane.scheduler, "on_result", fail_commit_once)
    result = pb.TaskResult(
        ref=codec.ref_for(attempt), artifacts=[artifact], result_json='{"ok": true}'
    )
    with pytest.raises(RuntimeError, match="database commit failed"):
        await plane.servicer._on_result(plane.session, result)

    path = plane.servicer._artifact_path(task.task_id, attempt.attempt_id)
    assert path is not None and open(path, "rb").read() == payload
    assert task.state is not TaskState.COMPLETED
    monkeypatch.setattr(plane.scheduler, "on_result", real_commit)

    await plane.servicer._on_result(plane.session, result)

    assert connection.calls == 1
    assert task.state is TaskState.COMPLETED
    assert task.result_ref == path
    assert open(path, "rb").read() == payload


@pytest.mark.asyncio
async def test_a_result_for_a_task_this_plane_forgot_is_acknowledged(plane):
    """After a restart the task graph is gone but the commit is on disk, and
    that fact is a durable verdict."""
    task, attempt = plane.assign()
    ref = codec.ref_for(attempt)
    await plane.send(_result(ref, payload=b"audio"))
    assert task_store.is_committed(task.task_id) is True
    plane.scheduler._tasks.clear()
    plane.session.outbox._queue.clear()

    await plane.send(_result(ref, payload=b"audio"))

    assert len(plane.outbox) == 1


# ── B12: one bad frame is not a broken session ────────────────────────────


@pytest.mark.asyncio
async def test_an_illegal_frame_does_not_end_the_read_loop(plane):
    """A late or out-of-order frame raises from the domain. Letting that end
    the reader disconnects a worker that is mid-render."""
    task, attempt = plane.assign()
    await plane.send(_result(codec.ref_for(attempt), payload=b"audio"))
    late = pb.WorkerMessage(accepted=pb.TaskAccepted(ref=codec.ref_for(attempt)))
    beat = pb.WorkerMessage(heartbeat=pb.Heartbeat(active_tasks=3, available_slots=1))

    async def frames():
        yield late
        yield beat

    await plane.servicer._read_loop(plane.session, frames())

    assert plane.pool.get(plane.worker_id).capacity.active_tasks == 3
    assert task.state is TaskState.COMPLETED


# ── B1: the lease is the scheduler's arithmetic, not the transport's ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("keepalive", [True, False])
async def test_progress_frames_carry_their_keepalive_flag(plane, keepalive):
    """A timer-driven frame renews the lease but proves no work was done, so
    the distinction has to survive the boundary."""
    _, attempt = plane.assign()
    seen: list[dict] = []
    plane.scheduler.on_progress = lambda *a, **kw: seen.append(kw)

    await plane.send(
        pb.WorkerMessage(
            progress=pb.TaskProgress(
                ref=codec.ref_for(attempt),
                progress=0.4,
                stage="generating",
                keepalive=keepalive,
            )
        )
    )

    assert seen[0]["keepalive"] is keepalive
    assert seen[0]["stage"] == "generating"
