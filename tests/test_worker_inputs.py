"""Getting the user's own files onto the machine that renders them (B5).

Remote cloning could not work, and did not fail either. ``assignment_to_pb``
never populated ``inputs``, so the assignment carried ``ref_audio`` as a path
on the *control plane* — ``~/…/omnivoice_data/voices/x.wav``, which names
nothing on the worker. ``DownloadArtifact`` had no caller, and would have
404'd if it had one: it serves only the artifact directory, while reference
audio lives in ``VOICES_DIR`` or a tempfile. Meanwhile ``clone`` was
advertised as supported.

The failure mode is the quiet one. An engine handed a dead reference path does
not raise — it renders in its default voice, and the user gets audio that is
simply not their clone.

So the path has three halves, and this file covers all three:

* the control plane **stages** every file-valued parameter into the artifact
  store under its content hash (one copy per voice, however many clones),
* the assignment **declares** them and carries ids instead of paths,
* the worker **fetches** them and points the parameters at its own copies.

Plus the disk: ``purge_finished`` deleted rows and left every rendered result
and every staged reference clip behind, forever.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import threading

import pytest

from core.path_security import resolve_within
from services import tts_backend
from worker import deadlines, executor as executor_module, task_store
from worker.errors import ErrorClass
from worker.executor import TaskExecutor, TaskFailure
from worker.lifecycle import Attempt, Task, TaskState
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.transport import codec
from hang_guard import BARRIER_WATCHDOG_S, HANG_GUARD_S


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path, monkeypatch):
    """See test_worker_registry.py: patch the globals the store actually reads,
    because tests/backend/conftest.py purges core.* between tests."""
    from worker import task_store as ts

    db_globals = ts.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """The control plane's artifact directory, isolated per test."""
    root = tmp_path / "artifacts"
    (root / task_store.INPUTS_DIRNAME).mkdir(parents=True)
    monkeypatch.setattr(task_store, "artifact_root", lambda **_kw: str(root))
    return str(root)


@pytest.fixture
def voice(tmp_path):
    """A reference clip that exists ONLY on the control plane."""
    path = tmp_path / "voices" / "my-voice.wav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"RIFF" + b"reference audio" * 64)
    return str(path)


def _task(task_id="t1", **params) -> Task:
    return Task(
        task_id=task_id,
        operation="clone",
        engine="fake-engine",
        model_id="fake-model",
        params={"text": "hello", **params},
    )


def _attempt(task_id="t1", attempt_id="a1") -> Attempt:
    return Attempt(
        attempt_id=attempt_id, task_id=task_id, worker_id="w1", session_epoch=1, attempt_number=1
    )


def _budget() -> deadlines.Deadlines:
    return deadlines.Deadlines(
        accept_seconds=30,
        model_load_seconds=600,
        execution_seconds=300,
        progress_lease_seconds=120,
        result_delivery_seconds=900,
        grace_seconds=60,
    )


def _assignment(task: Task, *, artifact_root: str) -> pb.TaskAssignment:
    return codec.assignment_to_pb(task, _attempt(task.task_id), _budget(), artifact_root=artifact_root)


def _download_from(artifact_root: str, *, corrupt: bool = False):
    """A stand-in for ``DownloadArtifact``, resolved exactly as the server does.

    ``server._resolve_input`` is ``resolve_within(artifact_dir, artifact_id)``
    plus an ``isfile`` check, so a ref this cannot resolve is one the real RPC
    would answer with NOT_FOUND.
    """
    calls: list[str] = []

    async def fetch(ref, destination):
        calls.append(ref.artifact_id)
        source = resolve_within(artifact_root, ref.artifact_id)
        if not os.path.isfile(source):
            raise FileNotFoundError(ref.artifact_id)
        if corrupt:
            with open(destination, "wb") as handle:
                handle.write(b"truncated")
            return
        shutil.copyfile(source, destination)

    fetch.calls = calls
    return fetch


# ── The worker's engine stack ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _live_tts_backend():
    """Re-resolve the module alias post-purge — see test_worker_executor_residency."""
    global tts_backend

    import services.tts_backend  # noqa: PLC0415

    tts_backend = services.tts_backend


class _FakeBackend:
    """Records the kwargs the engine was actually called with."""

    id = "fake-engine"
    display_name = "Fake Engine (test)"
    sample_rate = 24_000

    last_kwargs: dict = {}

    def ensure_ready(self) -> None:
        pass

    def generate(self, text, **kwargs):
        import torch

        type(self).last_kwargs = dict(kwargs)
        return torch.zeros(240)


@pytest.fixture
def engine(monkeypatch, _live_tts_backend):
    _FakeBackend.last_kwargs = {}
    monkeypatch.setitem(tts_backend._REGISTRY, "fake-engine", _FakeBackend)
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    from services import watermark

    monkeypatch.setattr(watermark, "mark_synthetic", lambda audio, sr, **kw: audio)
    return _FakeBackend


# ── Staging, on the control plane ──────────────────────────────────────────


def test_reference_audio_is_copied_into_the_artifact_store(artifacts, voice):
    task = _task(ref_audio=voice)

    entries = task_store.ensure_staged(task, now=1000.0)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["key"] == "ref_audio"
    staged = resolve_within(artifacts, entry["artifact_id"])
    assert staged.read_bytes() == open(voice, "rb").read()
    assert entry["size_bytes"] == os.path.getsize(voice)
    assert entry["sha256"] in entry["artifact_id"]
    # The original stays where it is: a local fallback still needs it.
    assert task.params["ref_audio"] == voice
    assert os.path.isfile(voice)


def test_repeated_clones_of_one_voice_share_a_single_copy(artifacts, voice, tmp_path):
    copy = tmp_path / "a-different-name.wav"
    shutil.copyfile(voice, copy)

    first = task_store.ensure_staged(_task("t1", ref_audio=voice), now=1000.0)
    second = task_store.ensure_staged(_task("t2", ref_audio=str(copy)), now=1001.0)

    assert first[0]["artifact_id"] == second[0]["artifact_id"], "content hash, not filename"
    inputs = os.listdir(os.path.join(artifacts, task_store.INPUTS_DIRNAME))
    assert len(inputs) == 1


def test_staging_twice_stages_once(artifacts, voice, monkeypatch):
    task = _task(ref_audio=voice)
    task_store.ensure_staged(task, now=1000.0)

    def _explode(*_a, **_kw):
        raise AssertionError("re-staged an input that was already staged")

    monkeypatch.setattr(task_store, "stage_input", _explode)
    assert len(task_store.ensure_staged(task, now=1001.0)) == 1


def test_staged_input_is_durable_before_the_task_row_can_commit(
    db, artifacts, voice, monkeypatch
):
    real_fsync = task_store._fsync_file
    fsynced = []

    def fail_first_file_barrier(path):
        fsynced.append(os.fspath(path))
        raise OSError("disk barrier failed")

    monkeypatch.setattr(task_store, "_fsync_file", fail_first_file_barrier)
    with pytest.raises(task_store.InputStagingError, match="barrier failed"):
        task_store.create(_task(ref_audio=voice), now=1000.0)

    assert fsynced and fsynced[0].endswith(".part")
    assert task_store.get("t1") is None
    assert not list(
        (resolve_within(artifacts, task_store.INPUTS_DIRNAME)).glob("*.part")
    )
    monkeypatch.setattr(task_store, "_fsync_file", real_fsync)


@pytest.mark.parametrize("damage", [b"", b"x" * 1028])
def test_existing_staged_input_is_verified_before_reuse(
    artifacts, voice, damage
):
    task = _task(ref_audio=voice)
    entry = task_store.ensure_staged(task, now=1000.0)[0]
    staged = resolve_within(artifacts, entry["artifact_id"])
    if damage:
        damage = damage[: entry["size_bytes"]]
        if len(damage) < entry["size_bytes"]:
            damage += b"x" * (entry["size_bytes"] - len(damage))
    staged.write_bytes(damage)

    refreshed = task_store.ensure_staged(task, root=artifacts, now=1001.0)

    assert refreshed[0]["sha256"] == entry["sha256"]
    assert staged.read_bytes() == open(voice, "rb").read()


def test_corrupt_durable_input_without_its_source_is_never_dispatched(
    db, artifacts, voice
):
    task_store.create(_task(ref_audio=voice), now=1000.0)
    recovered = task_store.get("t1")
    entry = recovered.params[task_store.INPUTS_PARAM_KEY][0]
    staged = resolve_within(artifacts, entry["artifact_id"])
    staged.write_bytes(b"x" * entry["size_bytes"])

    with pytest.raises(task_store.InputStagingError, match="unavailable"):
        task_store.ensure_staged(recovered, root=artifacts, now=1001.0)


def test_a_parameter_that_is_not_a_file_is_left_alone(artifacts):
    task = _task(ref_audio="voice-profile-id")

    assert task_store.ensure_staged(task, now=1000.0) == []
    assert task_store.INPUTS_PARAM_KEY not in task.params


def test_an_unreadable_reference_is_reported_not_swallowed(artifacts, voice, monkeypatch):
    def _denied(path, *_a, **_kw):
        raise PermissionError("nope")

    monkeypatch.setattr(task_store, "_digest", _denied)
    with pytest.raises(task_store.InputStagingError):
        task_store.ensure_staged(_task(ref_audio=voice), now=1000.0)


def test_submitting_a_task_stages_and_records_its_inputs(db, artifacts, voice):
    task_store.create(_task(ref_audio=voice), now=1000.0)

    stored = task_store.get("t1")
    entries = stored.params[task_store.INPUTS_PARAM_KEY]
    assert len(entries) == 1, "the durable row must name what the task owns"
    assert resolve_within(artifacts, entries[0]["artifact_id"]).is_file()


# ── The assignment ─────────────────────────────────────────────────────────


def test_the_assignment_declares_its_inputs(artifacts, voice):
    """The regression: ``inputs`` was never populated, by anyone, ever."""
    task = _task(ref_audio=voice)

    assignment = _assignment(task, artifact_root=artifacts)

    assert len(assignment.inputs) == 1
    ref = assignment.inputs[0]
    assert ref.artifact_id.startswith(task_store.INPUTS_DIRNAME)
    assert ref.size_bytes == os.path.getsize(voice)
    assert len(ref.sha256) == 64
    assert ref.task_id == "t1" and ref.attempt_id == "a1"


def test_no_control_plane_path_reaches_the_worker(artifacts, voice):
    task = _task(ref_audio=voice)

    assignment = _assignment(task, artifact_root=artifacts)

    assert voice not in assignment.params_json, "sent a path that means nothing remotely"
    params = json.loads(assignment.params_json)
    assert params["ref_audio"] == assignment.inputs[0].artifact_id
    assert params["text"] == "hello"
    # Staging bookkeeping holds control-plane paths; it stays home.
    assert task_store.INPUTS_PARAM_KEY not in params


def test_an_unstageable_input_fails_the_task_instead_of_shipping_a_path(
    artifacts, voice, monkeypatch
):
    def _boom(*_a, **_kw):
        raise task_store.InputStagingError("the reference clip vanished")

    monkeypatch.setattr(task_store, "stage_input", _boom)
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)
    params = json.loads(assignment.params_json)

    assert list(assignment.inputs) == []
    assert "ref_audio" not in params, "a dead path renders the wrong voice, silently"
    assert params[executor_module.INPUT_ERRORS_PARAM]


def test_the_error_key_is_the_one_the_worker_reads():
    """Two modules, one contract, no import between them."""
    assert codec._INPUT_ERRORS_KEY == executor_module.INPUT_ERRORS_PARAM


# ── The worker side ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_clone_whose_reference_only_exists_on_the_control_plane_succeeds(
    db, artifacts, voice, engine, tmp_path
):
    """The whole point of the phase, end to end.

    The reference clip exists on the control plane and nowhere else. Submit,
    build the assignment, run it on a worker whose only route to the file is
    ``DownloadArtifact`` — and the engine must be called with a readable local
    copy of the user's actual voice.
    """
    task = _task(ref_audio=voice)
    task_store.create(task, now=1000.0)
    assignment = _assignment(task, artifact_root=artifacts)
    fetch = _download_from(artifacts)
    worker = TaskExecutor(fetch_input=fetch, input_dir=str(tmp_path / "worker-inputs"))

    await worker.execute(assignment, fetch_input=fetch)

    used = engine.last_kwargs["ref_audio"]
    assert used != voice, "the worker cannot open a control-plane path"
    assert os.path.isfile(used)
    assert open(used, "rb").read() == open(voice, "rb").read()
    assert fetch.calls == [assignment.inputs[0].artifact_id]


@pytest.mark.asyncio
async def test_the_second_clone_of_a_voice_transfers_nothing(
    artifacts, voice, engine, tmp_path
):
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)
    fetch = _download_from(artifacts)
    worker = TaskExecutor(fetch_input=fetch, input_dir=str(tmp_path / "worker-inputs"))

    await worker.execute(assignment)
    await worker.execute(assignment)

    assert len(fetch.calls) == 1, "content-addressed cache re-downloaded a clip it held"


@pytest.mark.asyncio
async def test_a_same_size_corrupt_cache_entry_is_hashed_off_loop_and_refetched(
    tmp_path, monkeypatch
):
    payload = b"correct reference bytes"
    ref = pb.ArtifactRef(
        artifact_id="inputs/reference.wav",
        filename="reference.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    cache = tmp_path / "inputs"
    cache.mkdir()
    destination = cache / executor_module._cache_name(ref)
    destination.write_bytes(b"x" * len(payload))
    calls = []
    verifier_threads = []
    event_loop_thread = threading.get_ident()
    real_already_held = executor_module._already_held

    def observed_already_held(path, advertised):
        verifier_threads.append(threading.get_ident())
        return real_already_held(path, advertised)

    async def fetch(_ref, partial):
        calls.append(partial)
        with open(partial, "wb") as handle:
            handle.write(payload)

    monkeypatch.setattr(executor_module, "_already_held", observed_already_held)
    result = await TaskExecutor(input_dir=str(cache))._fetch_one(ref, fetch)

    assert calls
    assert open(result, "rb").read() == payload
    assert verifier_threads and verifier_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_a_corrupt_shared_cache_generation_is_never_deleted_or_replaced(
    tmp_path
):
    payload = b"correct reference bytes"
    corrupt = b"x" * len(payload)
    ref = pb.ArtifactRef(
        artifact_id="inputs/reference.wav",
        filename="reference.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    cache = tmp_path / "inputs"
    cache.mkdir()
    destination = cache / executor_module._cache_name(ref)
    destination.write_bytes(corrupt)
    assert executor_module._lease_input_cache_path(str(destination))

    async def fetch(_ref, partial):
        # The first execution's active lease still names these bytes. A cache
        # repair must not unlink them before or replace them afterwards.
        assert destination.read_bytes() == corrupt
        with open(partial, "wb") as handle:
            handle.write(payload)

    try:
        result = await TaskExecutor(input_dir=str(cache))._fetch_one(ref, fetch)

        assert result != str(destination)
        assert result.endswith(".wav")
        assert destination.read_bytes() == corrupt
        assert open(result, "rb").read() == payload
        key = executor_module._cache_path_key(str(destination))
        assert executor_module._INPUT_CACHE_LEASES[key] == 1
        assert executor_module._INPUT_CACHE_MUTATIONS == set()
    finally:
        executor_module._release_input_cache_path(str(destination))


@pytest.mark.asyncio
async def test_fetched_input_is_fsynced_and_renamed_before_reuse(
    tmp_path, monkeypatch
):
    payload = b"durable reference audio"
    ref = pb.ArtifactRef(
        artifact_id="inputs/reference.wav",
        filename="reference.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    cache = tmp_path / "inputs"
    events = []
    real_replace = executor_module.os.replace

    def fsync_file(path):
        assert open(path, "rb").read() == payload
        events.append(("fsync-file", path))

    def replace(source, destination):
        events.append(("replace", source, destination))
        real_replace(source, destination)

    def fsync_directory(directory):
        events.append(("fsync-directory", directory))

    async def fetch(_ref, partial):
        with open(partial, "wb") as handle:
            handle.write(payload)
        events.clear()

    monkeypatch.setattr(executor_module, "_fsync_file", fsync_file)
    monkeypatch.setattr(executor_module.os, "replace", replace)
    monkeypatch.setattr(
        executor_module, "_fsync_parent_directory", fsync_directory
    )
    result = await TaskExecutor(input_dir=str(cache))._fetch_one(ref, fetch)
    events.append(("returned", result))

    partial = events[0][1]
    assert events == [
        ("fsync-file", partial),
        ("replace", partial, result),
        ("fsync-directory", str(cache)),
        ("returned", result),
    ]


@pytest.mark.asyncio
async def test_concurrent_fetches_use_distinct_partial_files(tmp_path):
    payload = b"same artifact" * 64
    ref = pb.ArtifactRef(
        artifact_id="inputs/same.wav",
        filename="same.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    destinations: list[str] = []

    async def fetch(_ref, destination):
        destinations.append(destination)
        await asyncio.sleep(0.01)
        with open(destination, "wb") as handle:
            handle.write(payload)

    worker = TaskExecutor(input_dir=str(tmp_path / "inputs"))
    first, second = await asyncio.gather(
        worker._fetch_one(ref, fetch),
        worker._fetch_one(ref, fetch),
    )

    assert first == second
    assert len(set(destinations)) == 2
    assert all(path.endswith(".part") for path in destinations)
    assert open(first, "rb").read() == payload
    assert not list((tmp_path / "inputs").glob("*.part"))


@pytest.mark.asyncio
async def test_cancelled_fetch_discards_its_partial_file(tmp_path):
    payload = b"partial input"
    ref = pb.ArtifactRef(
        artifact_id="inputs/cancelled.wav",
        filename="cancelled.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    download_started = asyncio.Event()

    async def fetch(_ref, destination):
        with open(destination, "wb") as handle:
            handle.write(payload)
        download_started.set()
        await asyncio.Event().wait()

    cache = tmp_path / "inputs"
    task = asyncio.create_task(TaskExecutor(input_dir=str(cache))._fetch_one(ref, fetch))
    await download_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not list(cache.glob("*.part"))


@pytest.mark.asyncio
async def test_cancelled_hash_waits_for_verifier_before_discarding_partial_file(
    tmp_path, monkeypatch
):
    payload = b"input waiting for verification"
    ref = pb.ArtifactRef(
        artifact_id="inputs/verifying.wav",
        filename="verifying.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    verify_started = threading.Event()
    release_verify = threading.Event()
    verifying_path: list[str] = []

    def blocked_verify(path, _ref):
        with open(path, "rb"):
            verifying_path.append(path)
            verify_started.set()
            release_verify.wait(timeout=BARRIER_WATCHDOG_S)

    async def fetch(_ref, destination):
        with open(destination, "wb") as handle:
            handle.write(payload)

    monkeypatch.setattr(executor_module, "_verify", blocked_verify)
    cache = tmp_path / "inputs"
    task = asyncio.create_task(TaskExecutor(input_dir=str(cache))._fetch_one(ref, fetch))
    assert await asyncio.to_thread(verify_started.wait, HANG_GUARD_S)

    task.cancel()
    await asyncio.sleep(0)
    try:
        assert not task.done(), "cancellation returned while the hash thread still held the file"
        assert os.path.exists(verifying_path[0])
    finally:
        release_verify.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(cache.glob("*.part"))


@pytest.mark.asyncio
async def test_a_damaged_transfer_is_refused(artifacts, voice, engine, tmp_path):
    """A truncated clip does not fail — it clones silence."""
    cache = tmp_path / "worker-inputs"
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)
    worker = TaskExecutor(fetch_input=_download_from(artifacts, corrupt=True), input_dir=str(cache))

    with pytest.raises(TaskFailure) as raised:
        await worker.execute(assignment)

    assert raised.value.error.code == "INPUT_CORRUPT"
    assert list(cache.iterdir()) == [], "a damaged transfer must not be committed"


@pytest.mark.asyncio
async def test_an_unreachable_input_is_retryable_not_terminal(artifacts, voice, engine, tmp_path):
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)
    shutil.rmtree(os.path.join(artifacts, task_store.INPUTS_DIRNAME))
    worker = TaskExecutor(fetch_input=_download_from(artifacts), input_dir=str(tmp_path / "in"))

    with pytest.raises(TaskFailure) as raised:
        await worker.execute(assignment)

    assert raised.value.error.error_class is ErrorClass.TRANSIENT


@pytest.mark.asyncio
async def test_a_worker_that_cannot_fetch_says_so(artifacts, voice, engine):
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)

    with pytest.raises(TaskFailure) as raised:
        await TaskExecutor().execute(assignment)

    assert raised.value.error.error_class is ErrorClass.CAPABILITY
    assert raised.value.error.code == "INPUT_TRANSFER_UNSUPPORTED"


@pytest.mark.asyncio
async def test_a_staging_error_is_terminal_on_the_worker(engine, tmp_path):
    assignment = pb.TaskAssignment(
        operation="clone",
        engine="fake-engine",
        params_json='{"text": "hi", "input_errors": ["the reference clip vanished"]}',
    )

    with pytest.raises(TaskFailure) as raised:
        await TaskExecutor(input_dir=str(tmp_path)).execute(assignment)

    assert raised.value.error.error_class is ErrorClass.TERMINAL
    assert raised.value.error.code == "INPUT_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_hostile_filename_cannot_escape_the_cache(artifacts, voice, engine, tmp_path):
    """``filename`` is remote input; only the hash names the local copy."""
    cache = tmp_path / "worker-inputs"
    assignment = _assignment(_task(ref_audio=voice), artifact_root=artifacts)
    assignment.inputs[0].filename = "../../../../pwned.wav"
    worker = TaskExecutor(fetch_input=_download_from(artifacts), input_dir=str(cache))

    await worker.execute(assignment)

    used = engine.last_kwargs["ref_audio"]
    assert os.path.dirname(os.path.realpath(used)) == os.path.realpath(str(cache))
    assert not (tmp_path.parent / "pwned.wav").exists()


def test_the_worker_input_cache_has_a_ceiling(tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    for index in range(5):
        path = directory / f"{index}.bin"
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + index, 1000 + index))

    executor_module._prune_input_cache(str(directory), limit_bytes=250)

    survivors = sorted(p.name for p in directory.iterdir())
    assert survivors == ["3.bin", "4.bin"], "the cache must evict oldest-first"


def test_cache_pruner_never_removes_in_flight_partial_files(tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    partial = directory / "artifact.unique.part"
    partial.write_bytes(b"x" * 500)
    (directory / "complete.bin").write_bytes(b"x" * 100)

    executor_module._lease_input_cache_path(str(partial))
    try:
        executor_module._prune_input_cache(
            str(directory),
            limit_bytes=0,
            now=partial.stat().st_mtime
            + executor_module._STALE_INPUT_PARTIAL_SECONDS
            + 1,
        )
    finally:
        executor_module._release_input_cache_path(str(partial))

    assert partial.read_bytes() == b"x" * 500
    assert not (directory / "complete.bin").exists()


def test_cache_pruner_counts_young_partials_and_sweeps_stale_ones(tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    now = 10_000.0
    stale = directory / "stale.unique.part"
    young = directory / "young.unique.part"
    complete = directory / "complete.bin"
    stale.write_bytes(b"s" * 500)
    young.write_bytes(b"y" * 500)
    complete.write_bytes(b"c" * 100)
    os.utime(
        stale,
        (
            now - executor_module._STALE_INPUT_PARTIAL_SECONDS - 1,
            now - executor_module._STALE_INPUT_PARTIAL_SECONDS - 1,
        ),
    )
    os.utime(young, (now, now))
    os.utime(complete, (now, now))

    executor_module._prune_input_cache(
        str(directory), limit_bytes=500, now=now
    )

    assert not stale.exists()
    assert young.exists()
    assert not complete.exists(), "young partial bytes must count toward the ceiling"


@pytest.mark.asyncio
async def test_execute_leases_materialized_inputs_against_concurrent_pruning(
    tmp_path, monkeypatch
):
    payload = b"active reference audio"
    ref = pb.ArtifactRef(
        artifact_id="inputs/active.wav",
        filename="active.wav",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assignment = pb.TaskAssignment(
        operation="clone",
        params_json=json.dumps({"text": "hello", "ref_audio": ref.artifact_id}),
        inputs=[ref],
    )
    cache = tmp_path / "cache"
    executor = TaskExecutor(input_dir=str(cache))
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    active_path = []

    async def fetch(_ref, partial):
        with open(partial, "wb") as handle:
            handle.write(payload)

    async def blocked_handler(_assignment, params, _report):
        active_path.append(params["ref_audio"])
        handler_started.set()
        await release_handler.wait()
        assert os.path.isfile(active_path[0])
        return {"payload": b"", "meta": {}}

    monkeypatch.setattr(executor, "_run_tts", blocked_handler)
    execution = asyncio.create_task(executor.execute(assignment, fetch_input=fetch))
    await asyncio.wait_for(handler_started.wait(), timeout=HANG_GUARD_S)
    evictable = cache / "older.bin"
    evictable.write_bytes(b"old")

    executor_module._prune_input_cache(str(cache), limit_bytes=0)

    assert os.path.isfile(active_path[0])
    assert not evictable.exists()
    release_handler.set()
    await execution

    executor_module._prune_input_cache(str(cache), limit_bytes=0)
    assert not os.path.exists(active_path[0])


# ── The disk ───────────────────────────────────────────────────────────────


def _finish(task: Task, *, at: float) -> None:
    task.state = TaskState.COMPLETED
    task.finished_at = at


def _result_artifact(artifacts: str, task_id: str) -> str:
    path = os.path.join(artifacts, task_id, "a1.bin")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"rendered audio")
    return path


WEEK = 7 * 24 * 3600


def test_purging_a_task_takes_its_artifacts_with_it(db, artifacts, voice):
    """The regression: rows were purged, bytes were kept — forever."""
    task = _task(ref_audio=voice)
    task_store.create(task, now=1000.0)
    entry = task.params[task_store.INPUTS_PARAM_KEY][0]
    staged = str(resolve_within(artifacts, entry["artifact_id"]))
    result = _result_artifact(artifacts, "t1")
    _finish(task, at=1000.0)
    task_store.save(task, now=1000.0)

    removed = task_store.purge_finished(now=1000.0 + WEEK + 1)

    assert removed == 1
    assert task_store.get("t1") is None
    assert not os.path.exists(result), "every remote render leaked its output"
    assert not os.path.exists(staged), "every remote clone leaked a copy of the voice"


def test_a_voice_another_task_still_uses_survives_the_purge(db, artifacts, voice):
    old = _task("t1", ref_audio=voice)
    task_store.create(old, now=1000.0)
    _finish(old, at=1000.0)
    task_store.save(old, now=1000.0)
    live = _task("t2", ref_audio=voice)
    task_store.create(live, now=1000.0)
    staged = str(resolve_within(artifacts, live.params[task_store.INPUTS_PARAM_KEY][0]["artifact_id"]))

    task_store.purge_finished(now=1000.0 + WEEK + 1)

    assert task_store.get("t1") is None
    assert task_store.get("t2") is not None
    assert os.path.isfile(staged), "one copy is shared by every clone of that voice"


def test_a_recently_staged_input_is_never_swept(db, artifacts, voice):
    task = _task(ref_audio=voice)
    task_store.create(task, now=1000.0 + WEEK)
    staged = str(resolve_within(artifacts, task.params[task_store.INPUTS_PARAM_KEY][0]["artifact_id"]))
    _finish(task, at=1000.0)
    task_store.save(task, now=1000.0)

    task_store.purge_finished(now=1000.0 + WEEK + 1)

    assert os.path.isfile(staged), "swept an input younger than the cutoff"


def test_purge_survives_a_missing_artifact_directory(db, tmp_path, monkeypatch):
    monkeypatch.setattr(task_store, "artifact_root", lambda **_kw: str(tmp_path / "gone"))
    task = _task()
    task_store.create(task, now=1000.0)
    _finish(task, at=1000.0)
    task_store.save(task, now=1000.0)

    assert task_store.purge_finished(now=1000.0 + WEEK + 1) == 1
