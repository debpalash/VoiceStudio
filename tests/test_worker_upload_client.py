"""Worker-side result delivery: upload, integrity, and outbox priority.

Three failures live here, all of them in the gap between "the worker finished
the work" and "the control plane has it":

* **B4** — every result rode the control stream inline against an 8 MiB frame
  ceiling, so anything past roughly three minutes of 24 kHz audio could not be
  delivered at all. The executor has always computed an inline/upload decision
  and the client has always discarded it; ``UploadResult`` was implemented on
  the server and called by nobody.
* **The lease during delivery** — a multi-minute upload sent nothing on the
  control stream, so a 120 s progress lease expired mid-transfer and killed an
  attempt whose audio was already rendered.
* **Head-of-line blocking** — one FIFO outbox put the heartbeat behind
  whatever bulk frame was being written, which is how a busy worker gets
  declared dead.

These drive a real ``WorkerClient`` against a fake upload stub and read its
outbox: the invariant is what this side puts on which wire, and in what order,
which a real server round trip would only obscure.
"""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import threading
import time

import pytest

from worker.executor import INLINE_LIMIT_BYTES
from worker.identity import WorkerKeypair
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.transport.client import (
    MAX_MESSAGE_BYTES,
    UPLOAD_STAGE,
    WorkerClient,
    WorkerConfig,
    _Outbox,
)
from worker.transport.server import SESSION_METADATA_KEY
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S

ENGINE, MODEL, OP = "indextts", "indextts:v2", "tts"
LEASE_SECONDS = 1
ARTIFACT_ID = "t-1/a-1.bin"

# Comfortably past the frame ceiling: before this change, exactly the result
# that came back as a terminal RESULT_TOO_LARGE.
OVERSIZED = b"\x7f" * (MAX_MESSAGE_BYTES + 1024)


class _FakeStub:
    """Records what UploadResult received, and can be told to refuse."""

    def __init__(self, *, error: Exception | None = None, chunk_delay: float = 0.0) -> None:
        self.chunks: list[pb.ResultChunk] = []
        self.metadata = []
        self.calls = 0
        self._error = error
        self._chunk_delay = chunk_delay

    async def UploadResult(self, request_iterator, metadata=()) -> pb.ResultAck:  # noqa: N802
        self.calls += 1
        self.metadata.append(tuple(metadata))
        if self._error is not None:
            raise self._error
        received = 0
        async for chunk in request_iterator:
            self.chunks.append(chunk)
            received += len(chunk.data)
            if self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)
            if chunk.last:
                break
        return pb.ResultAck(artifact_id=ARTIFACT_ID, bytes_received=received, committed=True)

    @property
    def uploaded(self) -> bytes:
        return b"".join(c.data for c in self.chunks)


class _ResumingStub(_FakeStub):
    async def UploadResult(self, request_iterator, metadata=()) -> pb.ResultAck:  # noqa: N802
        self.calls += 1
        self.metadata.append(tuple(metadata))
        first = None
        current = []
        async for chunk in request_iterator:
            first = first or chunk
            self.chunks.append(chunk)
            current.append(chunk)
        if self.calls == 1:
            return pb.ResultAck(bytes_received=2 * 1024 * 1024, committed=False)
        received = first.offset + sum(len(c.data) for c in current)
        return pb.ResultAck(artifact_id=ARTIFACT_ID, bytes_received=received, committed=True)


def _client(execute, *, stub: _FakeStub | None = None) -> WorkerClient:
    config = WorkerConfig(
        endpoint="127.0.0.1:1",
        cert_fingerprint="",
        certificate_pem=b"",
        keypair=WorkerKeypair.generate(),
        worker_id="w-1",
    )
    client = WorkerClient(config, execute=execute)
    client._session_token = "sess-1"
    client._stub = stub
    return client


def _returning(payload: bytes, meta: dict | None = None):
    async def execute(assignment, **_):
        return {"meta": dict(meta or {"ok": True}), "payload": payload}

    return execute


@pytest.mark.asyncio
async def test_declared_input_is_downloaded_with_the_session_and_written_locally(tmp_path):
    class Stub:
        def DownloadArtifact(self, request):  # noqa: N802
            assert request.session_token == "sess-1"

            async def chunks():
                yield pb.ArtifactChunk(offset=0, data=b"reference ", last=False)
                yield pb.ArtifactChunk(offset=10, data=b"audio", last=True)

            return chunks()

    client = _client(_returning(b""), stub=Stub())
    destination = tmp_path / "voice.part"

    await client._fetch_input(pb.ArtifactRef(artifact_id="inputs/voice.wav"), str(destination))

    assert destination.read_bytes() == b"reference audio"


@pytest.mark.asyncio
async def test_cancelled_input_download_removes_the_partial_destination(tmp_path):
    download_blocked = asyncio.Event()

    class Stub:
        def DownloadArtifact(self, _request):  # noqa: N802
            async def chunks():
                yield pb.ArtifactChunk(offset=0, data=b"partial", last=False)
                download_blocked.set()
                await asyncio.Event().wait()

            return chunks()

    client = _client(_returning(b""), stub=Stub())
    destination = tmp_path / "voice.part"
    task = asyncio.create_task(
        client._fetch_input(pb.ArtifactRef(artifact_id="inputs/voice.wav"), str(destination))
    )
    await download_blocked.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not destination.exists()


@pytest.mark.asyncio
async def test_input_file_writes_are_off_loop_and_complete_short_writes(tmp_path, monkeypatch):
    write_started = threading.Event()
    release_write = threading.Event()
    real_open = builtins.open
    write_calls = 0

    class ShortBlockingFile:
        def __init__(self, path):
            self._handle = real_open(path, "wb")

        def write(self, payload):
            nonlocal write_calls
            write_calls += 1
            write_started.set()
            release_write.wait(timeout=BARRIER_WATCHDOG_S)
            return self._handle.write(payload[:2])

        def close(self):
            self._handle.close()

    monkeypatch.setattr(builtins, "open", lambda path, _mode: ShortBlockingFile(path))

    class Stub:
        def DownloadArtifact(self, _request):  # noqa: N802
            async def chunks():
                yield pb.ArtifactChunk(offset=0, data=b"reference audio", last=True)

            return chunks()

    def delayed_release():
        assert write_started.wait(timeout=BARRIER_WATCHDOG_S)
        time.sleep(0.2)
        release_write.set()

    release_thread = threading.Thread(target=delayed_release)
    release_thread.start()
    client = _client(_returning(b""), stub=Stub())
    destination = tmp_path / "voice.part"
    task = asyncio.create_task(
        client._fetch_input(pb.ArtifactRef(artifact_id="inputs/voice.wav"), str(destination))
    )
    ticks = 0
    while not release_write.is_set():
        ticks += 1
        await asyncio.sleep(0.01)
    await task
    release_thread.join(timeout=HANG_GUARD_S)

    assert ticks >= 3, "a blocked file write stalled the worker event loop"
    assert write_calls > 1, "short writes must be retried until the chunk is complete"
    assert destination.read_bytes() == b"reference audio"


@pytest.mark.asyncio
async def test_cancel_during_input_write_drains_then_removes_partial_file(tmp_path, monkeypatch):
    write_started = threading.Event()
    release_write = threading.Event()
    real_open = builtins.open

    class BlockingFile:
        def __init__(self, path):
            self._handle = real_open(path, "wb")

        def write(self, payload):
            write_started.set()
            release_write.wait(timeout=BARRIER_WATCHDOG_S)
            return self._handle.write(payload)

        def close(self):
            self._handle.close()

    monkeypatch.setattr(builtins, "open", lambda path, _mode: BlockingFile(path))

    class Stub:
        def DownloadArtifact(self, _request):  # noqa: N802
            async def chunks():
                yield pb.ArtifactChunk(offset=0, data=b"partial", last=True)

            return chunks()

    client = _client(_returning(b""), stub=Stub())
    destination = tmp_path / "voice.part"
    task = asyncio.create_task(
        client._fetch_input(pb.ArtifactRef(artifact_id="inputs/voice.wav"), str(destination))
    )
    assert await asyncio.to_thread(write_started.wait, HANG_GUARD_S)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "cancellation detached an in-flight file write"
    release_write.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not destination.exists()


def _assignment(*, lease_seconds: int = LEASE_SECONDS) -> pb.TaskAssignment:
    return pb.TaskAssignment(
        ref=pb.TaskRef(task_id="t-1", attempt_id="a-1", session_epoch=1),
        operation=OP,
        engine=ENGINE,
        model_id=MODEL,
        params_json="{}",
        deadlines=pb.Deadlines(
            accept_seconds=20,
            model_load_seconds=600,
            execution_seconds=300,
            progress_lease_seconds=lease_seconds,
        ),
    )


class _Wire:
    """Drains the client's outbox, recording each frame's arrival time."""

    def __init__(self, client: WorkerClient) -> None:
        self.frames: list[tuple[float, pb.WorkerMessage]] = []
        self._task = asyncio.create_task(self._drain(client))

    async def _drain(self, client: WorkerClient) -> None:
        loop = asyncio.get_running_loop()
        while True:
            message = await client._outbox.get()
            self.frames.append((loop.time(), message))

    def kinds(self) -> list[str]:
        return [m.WhichOneof("payload") for _, m in self.frames]

    def of(self, kind: str) -> list[pb.WorkerMessage]:
        return [m for _, m in self.frames if m.WhichOneof("payload") == kind]

    async def until(self, *kinds: str, timeout: float = 20.0) -> pb.WorkerMessage:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for _, message in self.frames:
                if message.WhichOneof("payload") in kinds:
                    return message
            await asyncio.sleep(0.02)
        raise AssertionError(f"no {kinds} frame; saw {self.kinds()}")

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def _drained(wire: "_Wire", kind: str, count: int, timeout: float = 5.0) -> None:
    """Wait until *count* frames of *kind* have come off the outbox."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if wire.kinds().count(kind) >= count:
            assert wire.kinds().count(kind) == count
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"only {wire.kinds().count(kind)} {kind} frames, wanted {count}")


async def _settle(client: WorkerClient, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if not client._running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the task never finished")


# ── B4: over the threshold, the result is uploaded rather than refused ─────


@pytest.mark.asyncio
async def test_an_oversized_result_is_uploaded_not_failed():
    """The B4 regression, and the direct reversal of the Phase 0 stopgap.

    A result over the frame ceiling used to come back as a terminal
    RESULT_TOO_LARGE with the audio thrown away. It must now arrive.
    """
    stub = _FakeStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        message = await wire.until("result", "failed")
        await _settle(client)

        assert message.WhichOneof("payload") == "result", (
            f"delivery failed instead of uploading: {message.failed.error.code}"
        )
        assert stub.uploaded == OVERSIZED
        assert stub.metadata == [((SESSION_METADATA_KEY, "sess-1"),)]
        assert not message.result.inline_payload
        assert [a.artifact_id for a in message.result.artifacts] == [ARTIFACT_ID]
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_pending_holds_only_the_reference_never_the_payload():
    """#B9's other half: an over-cap frame re-sent on every reconnect tears the
    session down each time. The bytes are durable on the control plane once the
    upload commits, so redelivery must cost one small frame."""
    stub = _FakeStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        await wire.until("result")
        await _settle(client)

        pending = client._pending["t-1/a-1"]
        assert pending.inline_payload == b""
        assert [a.artifact_id for a in pending.artifacts] == [ARTIFACT_ID]

        await client._redeliver_pending()
        await _drained(wire, "result", 2)
        # The redelivered frame is the one the server would have rejected.
        assert wire.of("result")[-1].ByteSize() < MAX_MESSAGE_BYTES
        assert stub.calls == 1, "redelivery must not re-upload the bytes"
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_a_result_under_the_threshold_still_rides_inline():
    """The upload path must not cost small results their single-frame delivery."""
    stub = _FakeStub()
    payload = b"\0" * 1024
    client = _client(_returning(payload), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        message = await wire.until("result")
        await _settle(client)

        assert stub.calls == 0
        assert message.result.inline_payload == payload
        assert not message.result.artifacts
    finally:
        await wire.close()


# ── Integrity: the ref states what was sent ────────────────────────────────


@pytest.mark.asyncio
async def test_the_artifact_ref_carries_sha256_and_size():
    """Both fields exist in the proto and were populated by nobody, so the
    receiver had no way to tell a truncated transfer from a finished one."""
    stub = _FakeStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        message = await wire.until("result")
        await _settle(client)

        ref = message.result.artifacts[0]
        assert ref.sha256 == hashlib.sha256(OVERSIZED).hexdigest()
        assert ref.size_bytes == len(OVERSIZED)
        assert (ref.task_id, ref.attempt_id) == ("t-1", "a-1")
        # Every chunk announced the same digest and length up front.
        assert {c.ref.sha256 for c in stub.chunks} == {ref.sha256}
        # The control stream is already authenticated; don't widen where the
        # session token is written by echoing it back on it.
        assert ref.session_token == ""
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_chunk_offsets_are_bytes_already_sent_and_only_the_last_commits():
    """The receiver checks ``offset`` against the length it holds and commits
    only on ``last`` — both are contracts this side has to keep."""
    stub = _FakeStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        await wire.until("result")
        await _settle(client)

        assert len(stub.chunks) > 1, "an 8 MiB result must not be one chunk"
        sent = 0
        for chunk in stub.chunks:
            assert chunk.offset == sent, f"offset {chunk.offset} != {sent} bytes already sent"
            assert chunk.data, "an empty chunk carries no progress"
            sent += len(chunk.data)
            assert chunk.session_token == "sess-1"
        assert sent == len(OVERSIZED)
        assert [c.last for c in stub.chunks].count(True) == 1
        assert stub.chunks[-1].last
        # Every chunk has to fit in a frame with room for its own ref.
        assert max(len(c.data) for c in stub.chunks) < MAX_MESSAGE_BYTES
    finally:
        await wire.close()


# ── The negotiated threshold ───────────────────────────────────────────────


def test_the_default_threshold_is_the_executors_and_is_not_spelled_twice():
    client = _client(_returning(b""))
    assert client.inline_limit() == INLINE_LIMIT_BYTES


@pytest.mark.asyncio
async def test_config_update_lowers_the_threshold_and_is_honoured():
    """``inline_result_threshold_bytes`` is proto field 4, sent by nobody and
    read by nobody. A payload that inlined a moment ago must now upload."""
    stub = _FakeStub()
    payload = b"\0" * 4096
    client = _client(_returning(payload), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_server_message(
            pb.ServerMessage(config=pb.ConfigUpdate(inline_result_threshold_bytes=1024))
        )
        assert client.inline_limit() == 1024

        await client._on_assignment(_assignment())
        message = await wire.until("result")
        await _settle(client)

        assert stub.uploaded == payload
        assert not message.result.inline_payload
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_a_config_update_leaves_the_other_fields_alone():
    """One negotiated key must not silently reset another."""
    client = _client(_returning(b""))
    client.config.max_concurrent_tasks = 3
    await client._on_server_message(
        pb.ServerMessage(config=pb.ConfigUpdate(inline_result_threshold_bytes=2048))
    )
    assert client.config.max_concurrent_tasks == 3
    await client._on_server_message(
        pb.ServerMessage(config=pb.ConfigUpdate(max_concurrent_tasks=2))
    )
    assert client.inline_limit() == 2048
    assert client.config.max_concurrent_tasks == 2


def test_a_negotiated_threshold_cannot_exceed_what_a_frame_holds():
    """Otherwise a generous control plane turns every large result back into
    the RESULT_TOO_LARGE this phase exists to remove."""
    client = _client(_returning(b""))
    client._inline_threshold = 64 * 1024 * 1024
    assert client.inline_limit() < MAX_MESSAGE_BYTES
    assert client._should_upload(OVERSIZED)


@pytest.mark.asyncio
async def test_a_non_committed_ack_resumes_from_the_server_offset():
    stub = _ResumingStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        await wire.until("result")
        await _settle(client)

        assert stub.calls == 2
        starts = [chunk.offset for chunk in stub.chunks]
        assert 2 * 1024 * 1024 in starts
    finally:
        await wire.close()


# ── The lease survives a slow upload ───────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_progress_renews_the_lease_across_a_slow_transfer():
    """A transfer longer than the progress lease used to die mid-delivery.

    Asserted as a gap invariant, because the lease expires on the interval
    *between* frames — and as a stage, because the control plane keys the much
    longer delivery budget off it.
    """
    # Eight chunks at a third of a lease each: three leases' worth of transfer.
    stub = _FakeStub(chunk_delay=LEASE_SECONDS / 3.0)
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        await wire.until("result", timeout=LEASE_SECONDS * 20)
        await _settle(client)

        times = [t for t, _ in wire.frames]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert max(gaps) < LEASE_SECONDS, (
            f"went silent for {max(gaps):.2f}s under a {LEASE_SECONDS}s lease"
        )

        uploading = [
            m.progress for m in wire.of("progress") if m.progress.stage == UPLOAD_STAGE
        ]
        assert len(uploading) >= len(stub.chunks)
        # The first one lands before any bytes, so the attempt is on its
        # delivery budget before a slow uplink can burn the ordinary lease.
        assert uploading[0].progress == 0.0
        assert uploading[-1].progress == pytest.approx(1.0)
        # Upload progress is a bounded delivery keepalive, not synthesis
        # progress; otherwise these frames erase the delivery deadline and
        # overwrite the completed 100% synthesis value.
        assert all(p.keepalive for p in uploading)
        assert [p.progress for p in uploading] == sorted(p.progress for p in uploading)
    finally:
        await wire.close()


# ── When the upload cannot happen ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_upload_failure_falls_back_to_inline_when_it_still_fits():
    """An older control plane without UploadResult, or one stumble, must not
    destroy a render that already succeeded."""
    payload = b"\0" * (INLINE_LIMIT_BYTES * 2)
    stub = _FakeStub(error=RuntimeError("UNIMPLEMENTED"))
    client = _client(_returning(payload), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        message = await wire.until("result", "failed")
        await _settle(client)

        assert message.WhichOneof("payload") == "result"
        assert message.result.inline_payload == payload
        assert client._pending["t-1/a-1"].inline_payload == payload
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_an_upload_failure_above_the_frame_ceiling_is_transient():
    """There is no inline fallback here. TRANSIENT, not TERMINAL: the failure
    is the path, not the output, so another worker can succeed — and nothing
    undeliverable may enter the redelivery set."""
    stub = _FakeStub(error=RuntimeError("connection reset"))
    client = _client(_returning(OVERSIZED), stub=stub)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        failed = await wire.until("failed")
        await _settle(client)

        assert failed.failed.error.code == "RESULT_UPLOAD_FAILED"
        assert failed.failed.error.error_class == pb.ERROR_CLASS_TRANSIENT
        assert client._pending == {}
        assert "result" not in wire.kinds()
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_no_session_is_a_failure_not_a_crash():
    """``_stub`` is None between connections; an oversized result finishing in
    that window must be reported, not raised into the task's generic handler
    as an unclassified error."""
    client = _client(_returning(OVERSIZED), stub=None)
    wire = _Wire(client)
    try:
        await client._on_assignment(_assignment())
        failed = await wire.until("failed")
        await _settle(client)
        assert failed.failed.error.code == "RESULT_UPLOAD_FAILED"
        assert client._pending == {}
    finally:
        await wire.close()


# ── The outbox split ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_control_frames_overtake_a_queued_bulk_frame():
    """The heartbeat is the whole liveness model; it must not queue behind a
    payload that has no bounded size."""
    outbox = _Outbox()
    await outbox.put(pb.WorkerMessage(result=pb.TaskResult()), bulk=True)
    await outbox.put(pb.WorkerMessage(heartbeat=pb.Heartbeat(active_tasks=1)))
    await outbox.put(pb.WorkerMessage(pong=pb.Pong(nonce=7)))

    order = [(await outbox.get()).WhichOneof("payload") for _ in range(3)]
    assert order == ["heartbeat", "pong", "result"]


@pytest.mark.asyncio
async def test_the_outbox_blocks_rather_than_spinning_when_empty():
    outbox = _Outbox()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(outbox.get(), timeout=0.1)

    waiter = asyncio.create_task(outbox.get())
    await asyncio.sleep(0.05)
    await outbox.put(pb.WorkerMessage(pong=pb.Pong(nonce=7)))
    assert (await asyncio.wait_for(waiter, timeout=HANG_GUARD_S)).WhichOneof("payload") == "pong"


@pytest.mark.asyncio
async def test_a_result_is_the_only_frame_queued_as_bulk():
    """Everything the control plane uses to decide this worker is alive has to
    stay on the fast queue — including the upload's own progress."""
    stub = _FakeStub()
    client = _client(_returning(OVERSIZED), stub=stub)
    try:
        await client._on_assignment(_assignment())
        deadline = asyncio.get_running_loop().time() + 20
        while not client._outbox.bulk.qsize():
            assert asyncio.get_running_loop().time() < deadline, "no result was ever queued"
            await asyncio.sleep(0.02)

        bulk = [client._outbox.bulk.get_nowait() for _ in range(client._outbox.bulk.qsize())]
        control = [
            client._outbox.control.get_nowait()
            for _ in range(client._outbox.control.qsize())
        ]
        assert {m.WhichOneof("payload") for m in bulk} == {"result"}
        assert "result" not in {m.WhichOneof("payload") for m in control}
        assert {"accepted", "started", "progress"} <= {
            m.WhichOneof("payload") for m in control
        }
    finally:
        await _settle(client)
