"""Artifact transfer at the control-plane boundary.

The upload receiver is the one place where a remote peer writes bytes into the
user's filesystem and the app afterwards calls those bytes a finished render.
Every case here is a way that could go wrong without anybody noticing: a
transfer that stops early and is committed anyway, bytes that arrive out of
order and are appended regardless, a digest nobody checks, an artifact with no
ceiling, and — in the other direction — one worker reading the reference audio
staged for another's task.

The RPCs are driven directly rather than over a real stream: what is under test
is the integrity rule, not gRPC.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import threading
from dataclasses import replace

import pytest
import pytest_asyncio

from worker import deadlines as deadline_policy
from worker import identity, registry
from worker.clock import resolve
from worker.errors import ErrorClass, WorkerError
from worker.identity import WorkerKeypair
from worker.lifecycle import AttemptState, TaskState
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.scheduler import Scheduler
from worker.transport import codec, server as server_module
from worker.transport.server import REQUIRED_FEATURES, SESSION_METADATA_KEY, WorkerServicer
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

ENGINE, MODEL, OP = "indextts", "IndexTTS-2", "tts"

# ── Loop-responsiveness budgets (#1990 class) ─────────────────────────────
#
# Three tests below prove that a blocking filesystem call does NOT stall the
# gRPC event loop: they park the call on a barrier, then check that the loop
# still ran their own coroutine promptly.
#
# The measurement is a wall clock on a shared CI runner, so the two numbers
# have to be chosen against each other rather than picked to look tight.
# BARRIER_HOLD_S is what "stalled" costs: if the loop really were blocked, the
# waiter could not run until the watchdog released the barrier, so elapsed
# would be at least that. LOOP_RESPONSIVE_S is the budget, and the gap between
# them is the headroom.
#
# The original budget was 0.2 s against a 0.5 s hold. A loaded Windows runner
# measured 0.203 and turned main red for three milliseconds of scheduling
# noise — a red build that said nothing about the product. These values keep
# the same claim with a gap wide enough to mean something: responsive lands
# near 0.2, stalled lands at 1.5, and the line sits at 0.75 between them.
BARRIER_HOLD_S = 1.5
LOOP_RESPONSIVE_S = 0.75
# Long enough that the watchdog, not this, is what releases a barrier.
BARRIER_ABANDON_S = 5.0
# Hang-only awaits use hang_guard.HANG_GUARD_S (see tests/hang_guard.py).
# The waiter's own cap. Above BARRIER_HOLD_S so a genuinely stalled loop is
# reported by the budget assertion, which names the problem, rather than by a
# bare TimeoutError that does not.
BARRIER_REACHED_S = 3.0



async def _await_event(event, what, timeout=15.0):
    """Block on a threading.Event without spinning the event loop.

    The obvious `while not event.is_set(): await asyncio.sleep(0)` yields to
    the loop but never sleeps, so it runs the loop flat out on the same thread
    the awaited work needs to make progress. On a loaded Windows runner that
    starves the task setting the event and the wait times out with nothing
    actually wrong — a flake with no signal in it. `to_thread` parks the wait
    on a worker thread and leaves the loop free, which is both faster and
    deterministic.
    """
    assert await asyncio.to_thread(event.wait, timeout), (
        f"{what} did not happen within {timeout}s"
    )


def test_artifact_fsync_uses_a_windows_compatible_descriptor(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact.wav"
    artifact.write_bytes(b"audio")
    real_open = open
    modes = []

    def observed_open(path, mode="r", *args, **kwargs):
        modes.append(mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(server_module, "open", observed_open, raising=False)

    server_module._fsync_file(str(artifact))

    assert modes == ["r+b"]


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


class _Aborted(Exception):
    """What a real gRPC ``context.abort`` does: it raises."""

    def __init__(self, code, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _Context:
    def __init__(self, token: str = "") -> None:
        self.token = token

    def peer(self) -> str:
        return "ipv4:127.0.0.1:5555"

    def invocation_metadata(self):
        return ((SESSION_METADATA_KEY, self.token),) if self.token else ()

    async def abort(self, code, detail):
        raise _Aborted(code, detail)


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
    """A servicer with one enrolled worker, driven RPC by RPC."""

    def __init__(self, tmp_path) -> None:
        self.artifact_dir = str(tmp_path / "artifacts")
        self.pool = WorkerPool()
        self.scheduler = Scheduler(self.pool)
        self.servicer = WorkerServicer(
            self.scheduler, self.pool, artifact_dir=self.artifact_dir
        )
        self.keypair = WorkerKeypair.generate()
        self.worker_id = ""
        self.epoch = 0

    async def register(self) -> None:
        token = registry.create_enrollment(endpoint="localhost:1", cert_fingerprint="fp").encode()
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
                protocol_version_min=server_module.PROTOCOL_VERSION,
                protocol_version_max=server_module.PROTOCOL_VERSION,
                enrollment_token=token,
                public_key=self.keypair.public_bytes(),
                challenge=challenge,
                challenge_signature=signature,
                nonce=nonce,
                key_id=self.keypair.key_id,
                host=codec.host_to_pb({"hostname": "gpu2", "os": "linux", "arch": "x86_64"}),
                capabilities=[codec.capability_to_pb(c) for c in _capabilities()],
                max_concurrent_tasks=2,
            ),
            _Context(),
        )
        assert not response.error.code, response.error.code
        self.worker_id = response.worker_id
        self.epoch = response.session_epoch
        pending = self.servicer.session_for(
            self.worker_id, session_token=response.session_token
        )
        assert pending is not None
        assert self.servicer._activate_session(pending) is not None

    @property
    def token(self) -> str:
        return self.servicer._sessions[self.worker_id].session.token

    def running(self):
        """One task assigned to this worker and rendering."""
        task = self.scheduler.submit(operation=OP, engine=ENGINE, model_id=MODEL)
        assignment = self.scheduler.next_assignment()
        assert assignment is not None
        attempt = assignment.attempt
        self.scheduler.on_accepted(task.task_id, attempt.attempt_id, epoch=attempt.session_epoch)
        self.scheduler.on_started(task.task_id, attempt.attempt_id, epoch=attempt.session_epoch)
        return task, attempt

    def final_path(self, task, attempt) -> str:
        return os.path.join(self.artifact_dir, task.task_id, f"{attempt.attempt_id}.bin")

    async def upload(self, chunks) -> pb.ResultAck:
        return await self.servicer.UploadResult(_aiter(chunks), _Context(self.token))

    async def download(self, ref, *, context=None):
        collected = []
        async for chunk in self.servicer.DownloadArtifact(ref, context or _Context(self.token)):
            collected.append(chunk)
        return collected


@pytest_asyncio.fixture
async def plane(tmp_path, db):
    p = _Plane(tmp_path)
    await p.register()
    return p


async def _aiter(items):
    for item in items:
        yield item


def test_servicer_startup_sweeps_crash_surviving_result_partials(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    task_dir = artifact_dir / "task"
    task_dir.mkdir(parents=True)
    orphan = task_dir / "attempt.bin.part"
    fetched_orphan = task_dir / "attempt.bin.unique.part"
    final = task_dir / "attempt.bin"
    similarly_named = task_dir / "keep.partially"
    orphan.write_bytes(b"partial result")
    fetched_orphan.write_bytes(b"partial fetched result")
    final.write_bytes(b"committed result")
    similarly_named.write_bytes(b"not a resumable generation")

    servicer = WorkerServicer(
        Scheduler(WorkerPool()), WorkerPool(), artifact_dir=str(artifact_dir)
    )

    assert not orphan.exists()
    assert not fetched_orphan.exists()
    assert final.read_bytes() == b"committed result"
    assert similarly_named.exists()
    assert servicer._orphaned_upload_parts == set()


def test_servicer_retries_a_startup_partial_locked_by_windows(
    tmp_path, monkeypatch
):
    artifact_dir = tmp_path / "artifacts"
    task_dir = artifact_dir / "task"
    task_dir.mkdir(parents=True)
    orphan = task_dir / "attempt.bin.part"
    orphan.write_bytes(b"partial result")
    real_remove = server_module.os.remove
    attempts = 0

    def transient_remove(path):
        nonlocal attempts
        if os.fspath(path) == str(orphan) and attempts == 0:
            attempts += 1
            raise PermissionError("file is still locked")
        return real_remove(path)

    monkeypatch.setattr(server_module.os, "remove", transient_remove)
    servicer = WorkerServicer(
        Scheduler(WorkerPool()), WorkerPool(), artifact_dir=str(artifact_dir)
    )

    assert orphan.exists()
    assert str(orphan) in servicer._orphaned_upload_parts
    assert servicer.sweep_orphaned_upload_parts() == 1
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_revocation_aborts_an_upload_that_was_already_authorized(plane):
    task, attempt = plane.running()
    token = plane.token
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)

    async def chunks():
        yield pb.ResultChunk(ref=ref, offset=0, data=payload[:4], last=False)
        assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
        yield pb.ResultChunk(ref=ref, offset=4, data=payload[4:], last=True)

    with pytest.raises(_Aborted) as exc:
        await plane.servicer.UploadResult(chunks(), _Context(token))

    assert exc.value.code == server_module.grpc.StatusCode.UNAUTHENTICATED
    assert not os.path.exists(plane.final_path(task, attempt))
    assert not os.path.exists(plane.final_path(task, attempt) + ".part")


@pytest.mark.asyncio
async def test_revocation_during_upload_open_cannot_commit_the_first_chunk(
    plane, monkeypatch
):
    task, attempt = plane.running()
    token = plane.token
    session = plane.servicer._sessions[plane.worker_id]
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)
    entered = asyncio.Event()
    release = asyncio.Event()
    drop_control = asyncio.Event()
    original_start = server_module._Upload.start

    async def stalled_start(upload, offset):
        entered.set()
        await release.wait()
        return await original_start(upload, offset)

    monkeypatch.setattr(server_module._Upload, "start", stalled_start)
    class ControlContext(_Context):
        async def write(self, _message):
            pass

    async def control_frames():
        await drop_control.wait()
        if False:
            yield None

    control = asyncio.create_task(
        plane.servicer.Control(control_frames(), ControlContext(token))
    )
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    upload = asyncio.create_task(
        plane.servicer.UploadResult(
            _aiter([pb.ResultChunk(ref=ref, offset=0, data=payload, last=True)]),
            _Context(),
        )
    )
    await entered.wait()

    drop_control.set()
    await asyncio.wait_for(control, timeout=HANG_GUARD_S)
    assert token not in plane.servicer._by_token
    assert plane.worker_id not in plane.servicer._sessions
    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release.set()

    with pytest.raises(_Aborted) as exc:
        await upload

    assert exc.value.code == server_module.grpc.StatusCode.UNAUTHENTICATED
    assert not os.path.exists(plane.final_path(task, attempt))
    assert not os.path.exists(plane.final_path(task, attempt) + ".part")


@pytest.mark.asyncio
async def test_control_drop_cannot_hide_a_stalled_upload_from_later_revoke(plane):
    task, attempt = plane.running()
    token = plane.token
    session = plane.servicer._sessions[plane.worker_id]
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)
    upload_waiting = asyncio.Event()
    drop_control = asyncio.Event()

    class ControlContext(_Context):
        async def write(self, _message):
            pass

    async def control_frames():
        await drop_control.wait()
        if False:
            yield None

    async def stalled_upload():
        yield pb.ResultChunk(ref=ref, offset=0, data=payload[:4], last=False)
        upload_waiting.set()
        await asyncio.Event().wait()

    control = asyncio.create_task(
        plane.servicer.Control(control_frames(), ControlContext(token))
    )
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    upload = asyncio.create_task(
        plane.servicer.UploadResult(stalled_upload(), _Context(token))
    )
    await asyncio.wait_for(upload_waiting.wait(), timeout=HANG_GUARD_S)

    drop_control.set()
    await asyncio.wait_for(control, timeout=HANG_GUARD_S)
    assert token not in plane.servicer._by_token
    assert plane.worker_id not in plane.servicer._sessions

    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    with pytest.raises(_Aborted) as exc:
        await asyncio.wait_for(upload, timeout=HANG_GUARD_S)

    assert exc.value.code == server_module.grpc.StatusCode.UNAUTHENTICATED
    assert not os.path.exists(plane.final_path(task, attempt))
    assert not os.path.exists(plane.final_path(task, attempt) + ".part")
    assert plane.servicer._transfer_sessions == {}


@pytest.mark.asyncio
async def test_revoke_discards_a_resumable_partial_after_its_rpc_and_control_end(plane):
    task, attempt = plane.running()
    token = plane.token
    session = plane.servicer._sessions[plane.worker_id]
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)
    drop_control = asyncio.Event()

    class ControlContext(_Context):
        async def write(self, _message):
            pass

    async def control_frames():
        await drop_control.wait()
        if False:
            yield None

    control = asyncio.create_task(
        plane.servicer.Control(control_frames(), ControlContext(token))
    )
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)

    ack = await plane.servicer.UploadResult(
        _aiter([pb.ResultChunk(ref=ref, offset=0, data=payload[:4], last=False)]),
        _Context(token),
    )
    partial = plane.final_path(task, attempt) + ".part"
    assert ack.error.code == "UPLOAD_INCOMPLETE"
    assert os.path.exists(partial)
    assert plane.servicer._transfer_sessions == {}

    drop_control.set()
    await asyncio.wait_for(control, timeout=HANG_GUARD_S)
    assert token not in plane.servicer._by_token
    assert plane.worker_id not in plane.servicer._sessions

    plane.servicer.revoke_worker_sessions(plane.worker_id)

    assert not os.path.exists(partial)
    assert plane.servicer._partial_uploads == {}
    assert plane.servicer._partial_upload_expiries == {}


@pytest.mark.asyncio
async def test_terminal_attempt_discards_its_resumable_partial(plane):
    task, attempt = plane.running()
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)
    ack = await plane.upload(_chunks(ref, payload[:4], last=False))
    partial = plane.final_path(task, attempt) + ".part"
    assert ack.error.code == "UPLOAD_INCOMPLETE"
    assert os.path.exists(partial)

    plane.scheduler.cancel(task.task_id)
    await plane.servicer._handle(
        plane.servicer._sessions[plane.worker_id],
        pb.WorkerMessage(cancel_ack=pb.TaskCancelAck(ref=codec.task_ref(
            task.task_id, attempt.attempt_id, attempt.session_epoch
        ))),
    )

    assert not os.path.exists(partial)
    assert plane.servicer._partial_uploads == {}
    assert plane.servicer._partial_upload_expiries == {}


@pytest.mark.asyncio
async def test_incomplete_upload_expires_when_never_resumed(plane, monkeypatch):
    monkeypatch.setattr(server_module, "_PARTIAL_UPLOAD_TTL_SECONDS", 0.0)
    task, attempt = plane.running()
    payload = b"finished audio"
    ref = _ref(plane, task, attempt, payload=payload)

    ack = await plane.upload(_chunks(ref, payload[:4], last=False))
    partial = plane.final_path(task, attempt) + ".part"
    assert ack.error.code == "UPLOAD_INCOMPLETE"

    async def partial_is_expired():
        while (
            os.path.exists(partial)
            or plane.servicer._partial_uploads
            or plane.servicer._partial_upload_expiries
        ):
            await asyncio.sleep(0)

    await asyncio.wait_for(partial_is_expired(), timeout=HANG_GUARD_S)
    assert not os.path.exists(partial)
    assert plane.servicer._partial_uploads == {}
    assert plane.servicer._partial_upload_expiries == {}


@pytest.mark.asyncio
async def test_cancelled_resume_drains_rehash_and_releases_logical_lease(
    plane, monkeypatch
):
    task, attempt = plane.running()
    payload = b"the first half | and the second half"
    ref = _ref(plane, task, attempt, payload=payload)
    held = 15
    await plane.upload(_chunks(ref, payload[:held], last=False))
    partial = plane.final_path(task, attempt) + ".part"
    started = threading.Event()
    release = threading.Event()
    original_rehash = server_module._Upload._rehash_held

    def blocked_rehash(upload):
        started.set()
        if not release.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release resume rehash")
        original_rehash(upload)

    real_remove = server_module.os.remove

    def locked_remove(path):
        if path == partial:
            raise PermissionError("file is still locked")
        return real_remove(path)

    monkeypatch.setattr(server_module._Upload, "_rehash_held", blocked_rehash)
    monkeypatch.setattr(server_module.os, "remove", locked_remove)
    resumed = asyncio.create_task(
        plane.upload(
            [pb.ResultChunk(ref=ref, offset=held, data=payload[held:], last=True)]
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, HANG_GUARD_S)
        resumed.cancel()
        await asyncio.sleep(0)
        assert not resumed.done(), "cancellation returned while resume rehash still ran"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await resumed

    assert plane.servicer._active_uploads == {}
    assert plane.servicer._partial_uploads == {}
    assert plane.servicer._partial_upload_expiries == {}


def _ref(plane, task, attempt, *, payload=b"", sha256=None, size=None) -> pb.ArtifactRef:
    return pb.ArtifactRef(
        artifact_id="",
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        filename="result.wav",
        size_bytes=len(payload) if size is None else size,
        sha256=hashlib.sha256(payload).hexdigest() if sha256 is None else sha256,
        session_token=plane.token,
    )


def _chunks(ref, payload: bytes, *, size: int = 4, last: bool = True):
    """Split ``payload`` into offset-correct chunks."""
    out = []
    for start in range(0, len(payload), size):
        out.append(pb.ResultChunk(ref=ref, offset=start, data=payload[start : start + size]))
    if out and last:
        out[-1].last = True
    return out


# ── Commit only against a verified, complete transfer ──────────────────────


@pytest.mark.asyncio
async def test_a_verified_upload_commits_under_its_own_attempt(plane):
    task, attempt = plane.running()
    payload = b"rendered audio bytes"
    ref = _ref(plane, task, attempt, payload=payload)

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is True
    assert ack.bytes_received == len(payload)
    final = plane.final_path(task, attempt)
    assert open(final, "rb").read() == payload
    assert not os.path.exists(f"{final}.part")
    # The id handed back is store-relative, and re-resolves to what was
    # written — the worker never learns our filesystem layout.
    assert not os.path.isabs(ack.artifact_id)
    assert plane.servicer._contained_artifact(ack.artifact_id) == final


@pytest.mark.asyncio
async def test_verified_upload_is_durable_before_commit_ack(plane, monkeypatch):
    task, attempt = plane.running()
    payload = b"durable rendered audio"
    final = plane.final_path(task, attempt)
    events = []
    replace = server_module.os.replace

    def fsync_file(path):
        events.append(("fsync-file", path))

    def observed_replace(source, destination):
        events.append(("replace", source, destination))
        replace(source, destination)

    def fsync_directory(directory):
        events.append(("fsync-directory", directory))

    monkeypatch.setattr(server_module, "_fsync_file", fsync_file)
    monkeypatch.setattr(server_module.os, "replace", observed_replace)
    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", fsync_directory
    )

    ack = await plane.upload(
        _chunks(_ref(plane, task, attempt, payload=payload), payload)
    )
    events.append(("ack", ack.committed))

    assert events == [
        ("fsync-directory", plane.artifact_dir),
        ("fsync-file", f"{final}.part"),
        ("replace", f"{final}.part", final),
        ("fsync-directory", os.path.dirname(final)),
        ("ack", True),
    ]


@pytest.mark.asyncio
async def test_upload_durability_barrier_does_not_block_the_grpc_loop(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.running()
    payload = b"durable rendered audio"
    barrier_started = Event()
    release_barrier = Event()
    real_replace = server_module._durable_replace

    def blocked_replace(source, destination):
        barrier_started.set()
        if not release_barrier.wait(timeout=BARRIER_ABANDON_S):
            raise TimeoutError("test did not release result durability")
        real_replace(source, destination)

    monkeypatch.setattr(server_module, "_durable_replace", blocked_replace)
    watchdog = Timer(BARRIER_HOLD_S, release_barrier.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    uploading = asyncio.create_task(
        plane.upload(_chunks(_ref(plane, task, attempt, payload=payload), payload))
    )

    async def wait_for_barrier():
        while not barrier_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_barrier(), timeout=BARRIER_REACHED_S)
        elapsed = asyncio.get_running_loop().time() - started_at
        assert elapsed < LOOP_RESPONSIVE_S, "result fsync stalled the gRPC event loop"
    finally:
        release_barrier.set()
        watchdog.cancel()
    assert (await uploading).committed is True


@pytest.mark.asyncio
async def test_upload_directory_barrier_does_not_block_the_grpc_loop(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.running()
    payload = b"durable rendered audio"
    barrier_started = Event()
    release_barrier = Event()

    def blocked_parent_fsync(directory):
        if os.path.abspath(directory) != os.path.abspath(plane.artifact_dir):
            return
        barrier_started.set()
        if not release_barrier.wait(timeout=BARRIER_ABANDON_S):
            raise TimeoutError("test did not release result-directory durability")

    monkeypatch.setattr(
        server_module, "_fsync_parent_directory", blocked_parent_fsync
    )
    watchdog = Timer(BARRIER_HOLD_S, release_barrier.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    uploading = asyncio.create_task(
        plane.upload(_chunks(_ref(plane, task, attempt, payload=payload), payload))
    )

    async def wait_for_barrier():
        while not barrier_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_barrier(), timeout=BARRIER_REACHED_S)
        elapsed = asyncio.get_running_loop().time() - started_at
        assert (
            elapsed < LOOP_RESPONSIVE_S
        ), "result-directory fsync stalled the gRPC event loop"
    finally:
        release_barrier.set()
        watchdog.cancel()
    assert (await uploading).committed is True


@pytest.mark.asyncio
async def test_revocation_during_result_barrier_cannot_ack_published_bytes(
    plane, monkeypatch
):
    from threading import Event

    task, attempt = plane.running()
    payload = b"durable rendered audio"
    final = plane.final_path(task, attempt)
    barrier_finished = Event()
    release_barrier = Event()
    real_replace = server_module._durable_replace

    def paused_after_replace(source, destination):
        real_replace(source, destination)
        barrier_finished.set()
        if not release_barrier.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release result durability")

    monkeypatch.setattr(server_module, "_durable_replace", paused_after_replace)
    uploading = asyncio.create_task(
        plane.upload(_chunks(_ref(plane, task, attempt, payload=payload), payload))
    )

    await _await_event(barrier_finished, "the durability barrier")
    assert os.path.isfile(final)
    assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
    release_barrier.set()

    refused = await uploading
    assert refused.committed is False
    assert refused.error.code == "ATTEMPT_NOT_LIVE"
    assert not os.path.exists(final)
    assert plane.servicer._artifact_bytes == {}


@pytest.mark.asyncio
async def test_upload_commit_serializes_with_an_inline_result(plane, monkeypatch):
    """A stale upload rollback cannot unlink a newer retained-stream winner."""
    task, attempt = plane.running()
    upload_payload = b"uploaded generation"
    inline_payload = b"inline winner"
    final = plane.final_path(task, attempt)
    upload_published = threading.Event()
    release_upload = threading.Event()
    real_replace = server_module._durable_replace

    def pause_after_upload_publish(source, destination):
        real_replace(source, destination)
        upload_published.set()
        if not release_upload.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release upload publication")

    monkeypatch.setattr(
        server_module, "_durable_replace", pause_after_upload_publish
    )
    uploading = asyncio.create_task(
        plane.upload(
            _chunks(
                _ref(plane, task, attempt, payload=upload_payload), upload_payload
            )
        )
    )
    assert await asyncio.to_thread(upload_published.wait, HANG_GUARD_S)

    inline = asyncio.create_task(
        plane.servicer._on_result(
            plane.servicer._sessions[plane.worker_id],
            pb.TaskResult(
                ref=codec.ref_for(attempt), inline_payload=inline_payload
            ),
        )
    )
    key = (task.task_id, attempt.attempt_id)

    async def wait_until_inline_queues_behind_upload():
        while True:
            gate = plane.servicer._result_publications.get(key)
            if gate is not None and gate.users == 2:
                return
            await asyncio.sleep(0)

    queued = True
    try:
        await asyncio.wait_for(
            wait_until_inline_queues_behind_upload(), timeout=HANG_GUARD_S
        )
    except asyncio.TimeoutError:
        queued = False
    finally:
        release_upload.set()
    committed = await asyncio.wait_for(uploading, timeout=HANG_GUARD_S)
    await asyncio.wait_for(inline, timeout=HANG_GUARD_S)

    assert queued, "inline publication bypassed the active upload gate"
    assert committed.committed is True
    assert task.state is TaskState.COMPLETED
    assert task.result_ref == final
    assert open(final, "rb").read() == inline_payload
    assert plane.servicer._result_publications == {}


@pytest.mark.asyncio
async def test_upload_write_does_not_block_revocation_or_publish_after_it(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.running()
    payload = b"rendered audio"
    write_started = Event()
    release_write = Event()
    real_write_all = server_module._write_all

    def blocked_write(handle, data):
        watchdog.start()
        write_started.set()
        if not release_write.wait(timeout=BARRIER_WATCHDOG_S):
            raise TimeoutError("test did not release the result upload write")
        real_write_all(handle, data)

    monkeypatch.setattr(server_module, "_write_all", blocked_write)
    watchdog_fired = Event()

    def unblock_stalled_loop():
        watchdog_fired.set()
        release_write.set()

    watchdog = Timer(5, unblock_stalled_loop)
    uploading = asyncio.create_task(
        plane.upload(_chunks(_ref(plane, task, attempt, payload=payload), payload))
    )

    async def wait_for_write():
        while not write_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_write(), timeout=HANG_GUARD_S)
        assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
        assert not watchdog_fired.is_set(), "upload write stalled the event loop"
        assert not release_write.is_set(), "revocation waited for the upload write"
    finally:
        release_write.set()
        watchdog.cancel()
    refused = await uploading
    assert refused.committed is False
    assert refused.error.code == "ATTEMPT_NOT_LIVE"
    assert plane.servicer._active_uploads == {}
    assert not os.path.exists(plane.final_path(task, attempt))


@pytest.mark.asyncio
async def test_upload_close_error_releases_live_lease_and_session(
    plane, monkeypatch
):
    task, attempt = plane.running()
    payload = b"rendered audio"
    ref = _ref(plane, task, attempt, payload=payload)
    real_open = open

    class CloseErrorHandle:
        def __init__(self, handle):
            self._handle = handle

        def close(self):
            self._handle.close()
            raise OSError("close failed")

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def close_error_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if str(path).endswith(".part") and mode in ("wb", "ab"):
            return CloseErrorHandle(handle)
        return handle

    async def incomplete():
        yield pb.ResultChunk(ref=ref, offset=0, data=payload, last=False)

    monkeypatch.setattr(server_module, "open", close_error_open, raising=False)
    with pytest.raises(OSError, match="close failed"):
        await plane.servicer.UploadResult(incomplete(), _Context(plane.token))

    assert plane.servicer._active_uploads == {}
    assert plane.servicer._transfer_sessions == {}
    monkeypatch.setattr(server_module, "open", real_open, raising=False)
    retried = await plane.upload(_chunks(ref, payload))
    assert retried.committed is True


@pytest.mark.asyncio
async def test_verified_upload_atomically_replaces_an_existing_attempt_file(plane):
    task, attempt = plane.running()
    final = plane.final_path(task, attempt)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    with open(final, "wb") as fh:
        fh.write(b"stale result")
    payload = b"new verified result"

    ack = await plane.upload(_chunks(_ref(plane, task, attempt, payload=payload), payload))

    assert ack.committed is True
    assert open(final, "rb").read() == payload


@pytest.mark.parametrize("task_id", ["CON", "NUL.txt", "name.", "x" * 241])
@pytest.mark.asyncio
async def test_windows_hostile_artifact_components_are_refused(plane, task_id):
    assert plane.servicer._artifact_path(task_id, "attempt") is None


@pytest.mark.asyncio
async def test_a_stream_that_ends_without_a_last_chunk_commits_nothing(plane):
    """The iterator simply stopping is a truncated transfer, not a result.

    This committed whatever had arrived, renamed it into place, and returned
    committed=True — so a dropped connection two thirds of the way through a
    render delivered two thirds of a render as the finished article.
    """
    task, attempt = plane.running()
    payload = b"half a render, and then the link died"
    ref = _ref(plane, task, attempt, payload=payload)

    ack = await plane.upload(_chunks(ref, payload[:12], last=False))

    assert ack.committed is False
    assert ack.error.code == "UPLOAD_INCOMPLETE"
    assert ack.bytes_received == 12
    final = plane.final_path(task, attempt)
    assert not os.path.exists(final)
    # Kept, so the resume has something to resume onto.
    assert os.path.getsize(f"{final}.part") == 12


@pytest.mark.asyncio
async def test_a_digest_mismatch_is_never_renamed_into_place(plane):
    task, attempt = plane.running()
    payload = b"corrupted on the wire"
    # Same length, different bytes: only the digest can tell these apart.
    ref = _ref(plane, task, attempt, payload=b"what the worker sent!")

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is False
    assert ack.error.code == "DIGEST_MISMATCH"
    final = plane.final_path(task, attempt)
    assert not os.path.exists(final)
    # And the bad bytes are gone: a resume must not append onto them.
    assert not os.path.exists(f"{final}.part")


@pytest.mark.asyncio
async def test_an_upload_with_no_declared_digest_is_refused_before_any_bytes(plane):
    task, attempt = plane.running()
    payload = b"unverifiable"
    ref = _ref(plane, task, attempt, payload=payload, sha256="")

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is False
    assert ack.error.code == "DIGEST_REQUIRED"
    assert not os.path.exists(f"{plane.final_path(task, attempt)}.part")


@pytest.mark.asyncio
async def test_a_size_that_disagrees_with_the_bytes_delivered_is_refused(plane):
    task, attempt = plane.running()
    payload = b"eight..."
    ref = _ref(plane, task, attempt, payload=payload, size=len(payload) + 4)

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is False
    assert ack.error.code == "SIZE_MISMATCH"
    assert not os.path.exists(plane.final_path(task, attempt))


# ── Offsets are checked, and the ack is the bytes-held probe ───────────────


@pytest.mark.asyncio
async def test_a_chunk_at_the_wrong_offset_is_refused_with_the_bytes_held(plane):
    """``chunk.offset`` was read as a truthiness flag and then ignored, so a
    gap or an overlap was appended as though it were the next byte."""
    task, attempt = plane.running()
    payload = b"0123456789abcdef"
    ref = _ref(plane, task, attempt, payload=payload)
    stream = [
        pb.ResultChunk(ref=ref, offset=0, data=payload[:4]),
        pb.ResultChunk(ref=ref, offset=999, data=payload[4:], last=True),
    ]

    ack = await plane.upload(stream)

    assert ack.committed is False
    assert ack.error.code == "OFFSET_MISMATCH"
    # The only report of "bytes already held" this RPC can make: one terminal
    # ack, carrying the offset to resume from.
    assert ack.bytes_received == 4
    assert not os.path.exists(plane.final_path(task, attempt))


@pytest.mark.asyncio
async def test_a_resume_hashes_the_bytes_already_on_disk(plane):
    """Otherwise the digest would attest only to the resumed tail — verifying
    the half of the file that was never in doubt."""
    task, attempt = plane.running()
    payload = b"the first half of it | and the second half of it"
    ref = _ref(plane, task, attempt, payload=payload)

    dropped = await plane.upload(_chunks(ref, payload[:20], last=False))
    assert dropped.bytes_received == 20

    resumed = await plane.upload(
        [pb.ResultChunk(ref=ref, offset=20, data=payload[20:], last=True)]
    )

    assert resumed.committed is True
    assert resumed.bytes_received == len(payload)
    assert open(plane.final_path(task, attempt), "rb").read() == payload


@pytest.mark.asyncio
async def test_a_resume_onto_corrupted_held_bytes_still_fails_verification(plane):
    task, attempt = plane.running()
    payload = b"the first half of it | and the second half of it"
    ref = _ref(plane, task, attempt, payload=payload)
    await plane.upload(_chunks(ref, b"tampered with here!!", last=False))

    resumed = await plane.upload(
        [pb.ResultChunk(ref=ref, offset=20, data=payload[20:], last=True)]
    )

    assert resumed.committed is False
    assert resumed.error.code == "DIGEST_MISMATCH"
    assert not os.path.exists(plane.final_path(task, attempt))


@pytest.mark.asyncio
async def test_a_refused_resume_restarts_the_partial_expiry_lease(plane):
    task, attempt = plane.running()
    payload = b"the first half | and the second half"
    ref = _ref(plane, task, attempt, payload=payload)
    incomplete = await plane.upload(_chunks(ref, payload[:10], last=False))
    assert incomplete.error.code == "UPLOAD_INCOMPLETE"
    part = f"{plane.final_path(task, attempt)}.part"
    original_expiry = plane.servicer._partial_upload_expiries[part]

    refused = await plane.upload(
        [pb.ResultChunk(ref=ref, offset=999, data=payload[10:], last=True)]
    )

    assert refused.error.code == "OFFSET_MISMATCH"
    replacement_expiry = plane.servicer._partial_upload_expiries[part]
    assert replacement_expiry is not original_expiry
    assert original_expiry.cancelled()
    assert not replacement_expiry.cancelled()
    retained = plane.servicer._partial_uploads[plane.worker_id][part]
    retained.discard()
    assert part not in plane.servicer._partial_upload_expiries


@pytest.mark.asyncio
@pytest.mark.parametrize("fsync_delay_s", [0.0, 0.55], ids=["fast-disk", "slow-disk"])
async def test_concurrent_uploads_cannot_share_one_attempt_partial(
    plane, monkeypatch, fsync_delay_s
):
    # slow-disk: every fsync past 0.5 s, as on a disk shared with other busy
    # suites. The commit alone then takes over a second, which is what the old
    # one-second hang guards turned into an intermittent TimeoutError.
    if fsync_delay_s:
        real_fsync = os.fsync

        def slow_fsync(fd):
            threading.Event().wait(fsync_delay_s)
            return real_fsync(fd)

        monkeypatch.setattr(server_module.os, "fsync", slow_fsync)
    task, attempt = plane.running()
    first_payload = b"A" * 20_000
    second_payload = b"B" * 20_000
    first_ref = _ref(plane, task, attempt, payload=first_payload)
    second_ref = _ref(plane, task, attempt, payload=second_payload)
    first_owns_path = asyncio.Event()
    release_first = asyncio.Event()

    async def first_chunks():
        yield pb.ResultChunk(
            ref=first_ref, offset=0, data=first_payload[:10_000], last=False
        )
        first_owns_path.set()
        await release_first.wait()
        yield pb.ResultChunk(
            ref=first_ref,
            offset=10_000,
            data=first_payload[10_000:],
            last=True,
        )

    first = asyncio.create_task(
        plane.servicer.UploadResult(first_chunks(), _Context(plane.token))
    )
    await asyncio.wait_for(first_owns_path.wait(), timeout=HANG_GUARD_S)

    refused = await plane.upload(_chunks(second_ref, second_payload))

    assert refused.committed is False
    assert refused.error.code == "UPLOAD_IN_PROGRESS"
    release_first.set()
    committed = await asyncio.wait_for(first, timeout=HANG_GUARD_S)
    assert committed.committed is True
    assert open(plane.final_path(task, attempt), "rb").read() == first_payload
    assert plane.servicer._active_uploads == {}


# ── Ceilings ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_upload_past_its_declared_size_is_cut_off(plane):
    """A declared size narrows the cap; it cannot be exceeded by streaming."""
    task, attempt = plane.running()
    ref = _ref(plane, task, attempt, payload=b"tiny", size=4)

    ack = await plane.upload(_chunks(ref, b"very much larger than four bytes"))

    assert ack.committed is False
    assert ack.error.code == "ARTIFACT_TOO_LARGE"
    final = plane.final_path(task, attempt)
    assert not os.path.exists(final)
    assert not os.path.exists(f"{final}.part")


@pytest.mark.asyncio
async def test_an_undeclared_upload_is_bounded_by_the_artifact_ceiling(plane, monkeypatch):
    monkeypatch.setattr(server_module, "MAX_ARTIFACT_BYTES", 8)
    task, attempt = plane.running()
    ref = _ref(plane, task, attempt, payload=b"sixteen bytes!!!", size=0)

    ack = await plane.upload(_chunks(ref, b"sixteen bytes!!!"))

    assert ack.committed is False
    assert ack.error.code == "ARTIFACT_TOO_LARGE"


@pytest.mark.asyncio
async def test_the_per_task_artifact_budget_is_enforced_across_attempts(plane, monkeypatch):
    """One artifact under the cap, twice, must not add up to more than a task
    is allowed to deliver."""
    monkeypatch.setattr(server_module, "MAX_TASK_ARTIFACT_BYTES", 24)
    task, attempt = plane.running()
    payload = b"sixteen bytes!!!"
    first = await plane.upload(_chunks(ref := _ref(plane, task, attempt, payload=payload), payload))
    assert first.committed is True
    assert ref.size_bytes == 16

    # A second attempt of the same task, delivering another 16 bytes. CAPACITY
    # so the retry can land on the same worker — anything else excludes it.
    plane.scheduler.on_failed(
        task.task_id,
        attempt.attempt_id,
        WorkerError(error_class=ErrorClass.CAPACITY, code="RETRY", message="again"),
        epoch=attempt.session_epoch,
    )
    retry = plane.scheduler.next_assignment()
    assert retry is not None
    plane.scheduler.on_accepted(task.task_id, retry.attempt.attempt_id, epoch=retry.attempt.session_epoch)
    plane.scheduler.on_started(task.task_id, retry.attempt.attempt_id, epoch=retry.attempt.session_epoch)

    ack = await plane.upload(
        _chunks(_ref(plane, task, retry.attempt, payload=payload), payload)
    )

    assert ack.committed is False
    assert ack.error.code == "TASK_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_unacknowledged_results_fill_one_workers_retained_disk_quota(plane):
    plane.servicer._max_stored_artifact_bytes_per_worker = 10
    plane.servicer._max_stored_artifact_bytes_total = 100
    payload = b"123456"
    first_task, first_attempt = plane.running()

    first = await plane.upload(
        _chunks(_ref(plane, first_task, first_attempt, payload=payload), payload)
    )
    assert first.committed is True

    second_task, second_attempt = plane.running()
    refused = await plane.upload(
        _chunks(_ref(plane, second_task, second_attempt, payload=payload), payload)
    )

    assert refused.committed is False
    assert refused.error.code == "STORAGE_QUOTA_EXCEEDED"
    assert os.path.exists(plane.final_path(first_task, first_attempt))
    assert not os.path.exists(plane.final_path(second_task, second_attempt))
    assert sum(
        artifact.size_bytes
        for artifact in plane.servicer._stored_artifacts.values()
    ) == len(payload)
    assert plane.servicer._artifact_reservations == {}


@pytest.mark.asyncio
async def test_concurrent_workers_reserve_against_one_retained_disk_quota(
    tmp_path, db
):
    from worker.lifecycle import Attempt

    servicer = WorkerServicer(
        Scheduler(WorkerPool()),
        WorkerPool(),
        artifact_dir=str(tmp_path / "artifacts"),
        max_stored_artifact_bytes_per_worker=100,
        max_stored_artifact_bytes_total=6,
    )
    attempts = [
        Attempt(
            attempt_id=f"attempt-{worker}",
            task_id=f"task-{worker}",
            worker_id=worker,
            session_epoch=1,
            attempt_number=1,
        )
        for worker in ("alice", "bob")
    ]

    admitted = await asyncio.gather(
        *(
            servicer._reserve_artifact_capacity(
                attempt,
                str(tmp_path / "artifacts" / attempt.task_id / "result.bin"),
                6,
            )
            for attempt in attempts
        )
    )

    assert sorted(admitted) == [False, True]
    assert sum(
        reservation.size_bytes
        for reservation in servicer._artifact_reservations.values()
    ) == 6


@pytest.mark.asyncio
async def test_cancelled_piggyback_releases_only_its_upload_reservation(
    plane, monkeypatch
):
    task, attempt = plane.running()
    payload = b"reserved result"
    chunk = pb.ResultChunk(
        ref=_ref(plane, task, attempt, payload=payload), offset=0
    )
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0
    real_makedirs = server_module._durable_makedirs

    def block_first_makedirs(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            if not release_first.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release upload admission")
        real_makedirs(path)

    monkeypatch.setattr(server_module, "_durable_makedirs", block_first_makedirs)
    context = _Context(plane.token)
    first = asyncio.create_task(
        plane.servicer._open_upload(
            context, chunk, retained_session=plane.servicer._sessions[plane.worker_id]
        )
    )
    assert await asyncio.to_thread(first_started.wait, HANG_GUARD_S)

    second, refusal, _session = await plane.servicer._open_upload(
        context, chunk, retained_session=plane.servicer._sessions[plane.worker_id]
    )
    assert second is not None and refusal is None
    reservation = plane.servicer._artifact_reservations[second.final]
    assert len(reservation.owners) == 2

    first.cancel()
    await asyncio.sleep(0)
    assert not first.done()
    release_first.set()
    with pytest.raises(asyncio.CancelledError):
        await first

    reservation = plane.servicer._artifact_reservations[second.final]
    assert set(reservation.owners) == {second.reservation_owner}
    await second.discard_async()
    assert second.final not in plane.servicer._artifact_reservations


@pytest.mark.asyncio
async def test_committed_piggyback_keeps_waiting_owner_reserved(
    plane, monkeypatch
):
    """A smaller winner cannot free a larger contender's quota claim."""
    plane.servicer._max_stored_artifact_bytes_per_worker = 14
    plane.servicer._max_stored_artifact_bytes_total = 14
    task, attempt = plane.running()
    larger = b"0123456789"
    smaller = b"abcd"
    larger_chunk = pb.ResultChunk(
        ref=_ref(plane, task, attempt, payload=larger), offset=0
    )
    smaller_chunk = pb.ResultChunk(
        ref=_ref(plane, task, attempt, payload=smaller), offset=0
    )
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0
    real_makedirs = server_module._durable_makedirs

    def block_first_makedirs(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            if not release_first.wait(timeout=BARRIER_WATCHDOG_S):
                raise TimeoutError("test did not release upload admission")
        real_makedirs(path)

    monkeypatch.setattr(server_module, "_durable_makedirs", block_first_makedirs)
    context = _Context(plane.token)
    first = asyncio.create_task(
        plane.servicer._open_upload(
            context,
            larger_chunk,
            retained_session=plane.servicer._sessions[plane.worker_id],
        )
    )
    assert await asyncio.to_thread(first_started.wait, HANG_GUARD_S)

    second, refusal, _session = await plane.servicer._open_upload(
        context,
        smaller_chunk,
        retained_session=plane.servicer._sessions[plane.worker_id],
    )
    assert second is not None and refusal is None
    assert await second.write(
        pb.ResultChunk(
            ref=smaller_chunk.ref, offset=0, data=smaller, last=True
        )
    ) is None
    assert (await second.commit()).committed is True

    reservation = plane.servicer._artifact_reservations[second.final]
    assert reservation.size_bytes == len(larger)
    assert len(reservation.owners) == 1

    third_task, third_attempt = plane.running()
    third_payload = b"x"
    third, third_refusal, _session = await plane.servicer._open_upload(
        context,
        pb.ResultChunk(
            ref=_ref(
                plane, third_task, third_attempt, payload=third_payload
            ),
            offset=0,
        ),
        retained_session=plane.servicer._sessions[plane.worker_id],
    )
    assert third is None
    assert third_refusal.error.code == "STORAGE_QUOTA_EXCEEDED"

    release_first.set()
    first_upload, refusal, _session = await first
    assert first_upload is not None and refusal is None
    await first_upload.discard_async()
    assert first_upload.final not in plane.servicer._artifact_reservations


# ── The delivery phase actually exists ─────────────────────────────────────


@pytest.mark.asyncio
async def test_an_upload_moves_the_task_into_result_uploading(plane):
    """``Task.uploading`` had zero callers, so RESULT_UPLOADING was
    unreachable and every byte of delivery ran under the execution phase."""
    task, attempt = plane.running()
    payload = b"0123456789abcdef"
    ref = _ref(plane, task, attempt, payload=payload)
    seen: list[TaskState] = []

    async def observed():
        for chunk in _chunks(ref, payload):
            yield chunk
            seen.append(task.state)

    ack = await plane.servicer.UploadResult(observed(), _Context(plane.token))

    assert ack.committed is True
    assert seen[0] is TaskState.RESULT_UPLOADING
    assert attempt.state is AttemptState.UPLOADING


@pytest.mark.asyncio
async def test_the_upload_lease_runs_on_the_result_delivery_budget(plane):
    """A slow delivery is bounded by ``result_delivery_seconds`` (900s), not by
    the execution budget it used to inherit — which is what made a large
    upload die mid-transfer under the 120s progress lease."""
    task, attempt = plane.running()
    budget = deadline_policy.for_task(OP)
    payload = b"0123456789abcdef"
    ref = _ref(plane, task, attempt, payload=payload)

    async def slow():
        chunks = _chunks(ref, payload)
        yield chunks[0]
        # Age the delivery phase past the execution budget but well inside the
        # delivery one. Under the old code there was no delivery phase, so the
        # keepalive ceiling clamped the lease into the past and the next sweep
        # would have failed a task that was uploading fine.
        attempt.phase_started_at = resolve(None) - (budget.execution_seconds + 60)
        for chunk in chunks[1:]:
            yield chunk

    ack = await plane.servicer.UploadResult(slow(), _Context(plane.token))

    assert ack.committed is True
    assert budget.result_delivery_seconds > budget.execution_seconds + 60
    assert not attempt.lease_expired()


def test_upload_keepalive_does_not_move_completed_progress_backwards(plane):
    task, attempt = plane.running()
    plane.scheduler.on_progress(
        task.task_id, attempt.attempt_id, progress=1.0, epoch=attempt.session_epoch
    )

    plane.servicer._renew_upload_lease(attempt)

    assert attempt.progress == 1.0


@pytest.mark.asyncio
async def test_an_upload_onto_a_cancelled_task_is_refused(plane):
    task, attempt = plane.running()
    payload = b"too late"
    ref = _ref(plane, task, attempt, payload=payload)
    plane.scheduler.cancel(task.task_id)

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is False
    assert ack.error.code == "ATTEMPT_NOT_LIVE"
    assert not os.path.exists(plane.final_path(task, attempt))


@pytest.mark.asyncio
async def test_upload_that_commits_before_cancel_is_removed_on_terminal_result(plane):
    task, attempt = plane.running()
    payload = b"too late after upload"
    ref = _ref(plane, task, attempt, payload=payload)
    ack = await plane.upload(_chunks(ref, payload))
    final = plane.final_path(task, attempt)
    assert ack.committed is True
    assert os.path.exists(final)

    plane.scheduler.cancel(task.task_id)
    await plane.servicer._on_result(
        plane.servicer._sessions[plane.worker_id],
        pb.TaskResult(
            ref=codec.ref_for(attempt),
            artifacts=[pb.ArtifactRef(artifact_id=ack.artifact_id)],
        ),
    )

    assert not os.path.exists(final)
    assert plane.servicer._artifact_bytes == {}


@pytest.mark.asyncio
async def test_an_upload_for_another_workers_attempt_is_refused(plane):
    task, attempt = plane.running()
    payload = b"not yours"
    ref = _ref(plane, task, attempt, payload=payload)
    plane.servicer._sessions[plane.worker_id].worker_id = "someone-else"

    ack = await plane.upload(_chunks(ref, payload))

    assert ack.committed is False
    assert ack.error.code == "UNKNOWN_ATTEMPT"
    assert not os.path.exists(plane.final_path(task, attempt))


# ── Serving staged inputs ──────────────────────────────────────────────────


def _stage(plane, task, name: str, data: bytes) -> str:
    """Stand in for the input-staging step: a file inside the artifact store."""
    path = os.path.join(plane.artifact_dir, "inputs", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    artifact_id = os.path.join("inputs", name)
    task.params["inputs"] = [{"artifact_id": artifact_id}]
    return artifact_id


@pytest.mark.asyncio
async def test_a_worker_can_read_the_input_staged_for_its_own_task(plane):
    task, attempt = plane.running()
    artifact_id = _stage(plane, task, "voice.wav", b"reference audio" * 10)

    chunks = await plane.download(
        pb.ArtifactRef(
            artifact_id=artifact_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            session_token=plane.token,
        )
    )

    assert b"".join(c.data for c in chunks) == b"reference audio" * 10
    assert chunks[-1].last is True
    assert chunks[0].ref.size_bytes == len(b"reference audio" * 10)
    # Nothing hands a session token back out that did not have to go out.
    assert all(not c.ref.session_token for c in chunks)


@pytest.mark.asyncio
async def test_expired_transfer_token_remains_bound_to_its_live_control_stream(
    plane,
):
    task, attempt = plane.running()
    artifact_id = _stage(plane, task, "voice.wav", b"reference audio")
    session = plane.servicer._sessions[plane.worker_id]
    session.session = replace(session.session, expires_at=0)
    session.stream_open = True
    ref = pb.ArtifactRef(
        artifact_id=artifact_id,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        session_token=plane.token,
    )

    chunks = await plane.download(ref, context=_Context(plane.token))
    assert b"".join(chunk.data for chunk in chunks) == b"reference audio"

    session.stream_open = False
    with pytest.raises(_Aborted) as exc:
        await plane.download(ref, context=_Context(plane.token))
    assert exc.value.code == server_module.grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_revoke_cancels_a_backpressured_download_without_another_poll(
    plane, monkeypatch
):
    task, attempt = plane.running()
    token = plane.token
    session = plane.servicer._sessions[plane.worker_id]
    artifact_id = _stage(plane, task, "large.wav", b"abcdefghijklmnopqrst")
    monkeypatch.setattr(server_module, "_DOWNLOAD_CHUNK_BYTES", 4)
    drop_control = asyncio.Event()

    class ControlContext(_Context):
        async def write(self, _message):
            pass

    async def control_frames():
        await drop_control.wait()
        if False:
            yield None

    control = asyncio.create_task(
        plane.servicer.Control(control_frames(), ControlContext(token))
    )
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    tracked_before = set(session.egress_tasks)
    stream = plane.servicer.DownloadArtifact(
        pb.ArtifactRef(
            artifact_id=artifact_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            session_token=token,
        ),
        _Context(token),
    )
    first_received = asyncio.Event()
    hold_consumer = asyncio.Event()
    received = []

    async def consume():
        async for chunk in stream:
            received.append(bytes(chunk.data))
            first_received.set()
            await hold_consumer.wait()

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(first_received.wait(), timeout=HANG_GUARD_S)
    assert received == [b"abcd"]
    await asyncio.sleep(0)
    tracked = session.egress_tasks - tracked_before
    assert consumer in tracked
    producers = [task for task in tracked if task is not consumer]
    assert len(producers) == 1 and not producers[0].done()

    drop_control.set()
    await asyncio.wait_for(control, timeout=HANG_GUARD_S)
    assert plane.worker_id in plane.servicer._transfer_sessions
    plane.servicer.revoke_worker_sessions(plane.worker_id)
    await asyncio.gather(consumer, *producers, return_exceptions=True)

    assert consumer.done()
    assert all(task.done() for task in producers)
    assert plane.servicer._transfer_sessions == {}


@pytest.mark.asyncio
async def test_blocked_download_read_does_not_stall_revocation(
    plane, monkeypatch
):
    from threading import Event, Timer

    task, attempt = plane.running()
    token = plane.token
    artifact_id = _stage(plane, task, "large.wav", b"reference audio")
    read_started = Event()
    release_read = Event()
    real_open = open

    class BlockedReader:
        def __init__(self, handle):
            self._handle = handle

        def read(self, size):
            read_started.set()
            if not release_read.wait(timeout=BARRIER_ABANDON_S):
                raise TimeoutError("test did not release the input-artifact read")
            return self._handle.read(size)

        def close(self):
            self._handle.close()

    def blocked_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if mode == "rb" and str(path).endswith("large.wav"):
            return BlockedReader(handle)
        return handle

    monkeypatch.setattr(server_module, "open", blocked_open, raising=False)
    stream = plane.servicer.DownloadArtifact(
        pb.ArtifactRef(
            artifact_id=artifact_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            session_token=token,
        ),
        _Context(token),
    )
    watchdog = Timer(BARRIER_HOLD_S, release_read.set)
    watchdog.start()
    started_at = asyncio.get_running_loop().time()
    fetching = asyncio.create_task(anext(stream))

    async def wait_for_read():
        while not read_started.is_set():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_read(), timeout=BARRIER_REACHED_S)
        assert plane.servicer.revoke_worker_sessions(plane.worker_id) == 1
        assert asyncio.get_running_loop().time() - started_at < LOOP_RESPONSIVE_S
    finally:
        release_read.set()
        watchdog.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fetching
    assert plane.servicer._transfer_sessions == {}


@pytest.mark.asyncio
async def test_revoke_fences_a_final_download_chunk_queued_after_control_drop(
    plane, monkeypatch
):
    task, attempt = plane.running()
    token = plane.token
    session = plane.servicer._sessions[plane.worker_id]
    artifact_id = _stage(plane, task, "one-chunk.wav", b"audio")
    monkeypatch.setattr(server_module, "_DOWNLOAD_CHUNK_BYTES", 64)
    drop_control = asyncio.Event()

    class ControlContext(_Context):
        async def write(self, _message):
            pass

    async def control_frames():
        await drop_control.wait()
        if False:
            yield None

    control = asyncio.create_task(
        plane.servicer.Control(control_frames(), ControlContext(token))
    )
    for _ in range(20):
        if session.stream_open:
            break
        await asyncio.sleep(0)
    stream = plane.servicer.DownloadArtifact(
        pb.ArtifactRef(
            artifact_id=artifact_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            session_token=token,
        ),
        _Context(token),
    )
    assert (await anext(stream)).data == b"audio"
    await asyncio.sleep(0)
    assert plane.worker_id in plane.servicer._transfer_sessions

    drop_control.set()
    await asyncio.wait_for(control, timeout=HANG_GUARD_S)
    plane.servicer.revoke_worker_sessions(plane.worker_id)

    with pytest.raises(_Aborted) as exc:
        await anext(stream)
    assert exc.value.code == server_module.grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_dispatch_stages_and_serves_from_the_servicers_artifact_root(plane, tmp_path):
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"reference audio")
    task = plane.scheduler.submit(
        operation=OP, engine=ENGINE, model_id=MODEL, params={"ref_audio": str(voice)}
    )
    assignment = plane.scheduler.next_assignment()
    assert assignment is not None

    assert await plane.servicer.dispatch(assignment)
    message = await plane.servicer._sessions[plane.worker_id].outbox.get()
    wire = message.assignment
    assert wire.inputs
    staged = os.path.join(plane.artifact_dir, wire.inputs[0].artifact_id)
    assert os.path.isfile(staged)

    chunks = await plane.download(
        pb.ArtifactRef(
            artifact_id=wire.inputs[0].artifact_id,
            task_id=task.task_id,
            attempt_id=assignment.attempt.attempt_id,
            session_token=plane.token,
        )
    )
    assert b"".join(chunk.data for chunk in chunks) == voice.read_bytes()


@pytest.mark.asyncio
async def test_a_worker_cannot_read_an_input_for_a_task_it_is_not_running(plane):
    """Authentication is not authorisation: from this phase on, staged inputs
    are the user's own reference audio."""
    task, attempt = plane.running()
    artifact_id = _stage(plane, task, "voice.wav", b"reference audio")
    plane.servicer._sessions[plane.worker_id].worker_id = "someone-else"

    with pytest.raises(_Aborted) as caught:
        await plane.download(
            pb.ArtifactRef(
                artifact_id=artifact_id,
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                session_token=plane.token,
            )
        )

    assert "PERMISSION_DENIED" in str(caught.value.code)


@pytest.mark.asyncio
async def test_a_live_task_cannot_authorize_another_tasks_staged_input(plane):
    first_task, _first_attempt = plane.running()
    first_input = _stage(
        plane, first_task, "first-voice.wav", b"private reference audio"
    )
    second_task, second_attempt = plane.running()

    with pytest.raises(_Aborted) as caught:
        await plane.download(
            pb.ArtifactRef(
                artifact_id=first_input,
                task_id=second_task.task_id,
                attempt_id=second_attempt.attempt_id,
                session_token=plane.token,
            )
        )

    assert "PERMISSION_DENIED" in str(caught.value.code)


@pytest.mark.asyncio
async def test_an_input_request_naming_no_task_is_refused(plane):
    task, _attempt = plane.running()
    artifact_id = _stage(plane, task, "voice.wav", b"reference audio")

    with pytest.raises(_Aborted) as caught:
        await plane.download(
            pb.ArtifactRef(artifact_id=artifact_id, session_token=plane.token)
        )

    assert "PERMISSION_DENIED" in str(caught.value.code)


@pytest.mark.asyncio
async def test_an_input_outside_the_artifact_store_is_not_served(plane, tmp_path):
    task, attempt = plane.running()
    secret = tmp_path / "secret.txt"
    secret.write_text("private")

    for artifact_id in ("../secret.txt", str(secret), "/etc/passwd"):
        task.params["inputs"] = [{"artifact_id": artifact_id}]
        with pytest.raises(_Aborted) as caught:
            await plane.download(
                pb.ArtifactRef(
                    artifact_id=artifact_id,
                    task_id=task.task_id,
                    attempt_id=attempt.attempt_id,
                    session_token=plane.token,
                )
            )
        assert "NOT_FOUND" in str(caught.value.code)
