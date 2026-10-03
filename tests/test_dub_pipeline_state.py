"""Phase 2.4/2.7 — `services/dub_pipeline` state helpers.

Covers the non-ingest, non-streaming surface: path safety, cache lookup,
process tracking, in-memory + DB job round-trip.
"""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import uuid
import pytest
from core.db import db_conn, init_db
from services import dub_pipeline as dp


@pytest.fixture(autouse=True)
def _init():
    init_db()
    yield


def _jid():
    return f"p_{uuid.uuid4().hex[:8]}"


# ── Path safety ─────────────────────────────────────────────────────────────


def test_safe_job_dir_rejects_traversal():
    assert dp.safe_job_dir("") is None
    assert dp.safe_job_dir("../etc") is None
    assert dp.safe_job_dir("..") is None
    assert dp.safe_job_dir("a/b") is None
    # Legit ids resolve under DUB_DIR.
    ok = dp.safe_job_dir("abc123")
    assert ok is not None
    assert ok.endswith("abc123")


# ── SSE event shape ─────────────────────────────────────────────────────────


def test_prep_event_contains_type_and_fields():
    out = dp.prep_event("extract_done", job_id="x", duration=1.5)
    assert out.startswith("data: ")
    assert '"type": "extract_done"' in out
    assert '"job_id": "x"' in out
    assert '"duration": 1.5' in out
    assert out.endswith("\n\n")


def test_sse_event_shape():
    out = dp.sse_event("segments", {"n": 3})
    assert out.startswith(b"event: segments\ndata: ")
    assert out.endswith(b"\n\n")


# ── Process tracking ────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_register_unregister_has_active():
    jid = _jid()
    proc = _FakeProc()
    assert not dp.has_active_procs(jid)
    dp.register_proc(jid, proc)
    assert dp.has_active_procs(jid)
    dp.unregister_proc(jid, proc)
    assert not dp.has_active_procs(jid)


def test_kill_job_procs_is_idempotent():
    jid = _jid()
    dp.register_proc(jid, _FakeProc())
    dp.register_proc(jid, _FakeProc())
    dp.kill_job_procs(jid)
    # Called twice — second call is a no-op.
    dp.kill_job_procs(jid)
    assert not dp.has_active_procs(jid)


# ── Job state round-trip ────────────────────────────────────────────────────


def test_put_get_job_in_memory():
    jid = _jid()
    assert dp.get_job(jid) is None
    dp.put_job(jid, {"filename": "x.mp4", "duration": 1.23})
    got = dp.get_job(jid)
    assert got["filename"] == "x.mp4"
    assert got["duration"] == 1.23


def test_save_job_persists_to_dub_history():
    """save_job writes to dub_history so a subsequent get_job on a cold cache
    can hydrate from disk."""
    jid = _jid()
    dp.put_job(jid, {"filename": "disk.mp4", "duration": 9.0, "dubbed_tracks": {}, "segments": []})
    dp.save_job(jid, dp.get_job(jid), filename="disk.mp4", duration=9.0)

    # Simulate fresh process: drop in-memory entry, force re-hydrate.
    dp._dub_jobs.pop(jid, None)
    rehydrated = dp.get_job(jid)
    assert rehydrated is not None
    assert rehydrated["filename"] == "disk.mp4"
    assert rehydrated["duration"] == 9.0


def _lang_row(jid):
    with db_conn() as conn:
        return conn.execute(
            "SELECT language, language_code FROM dub_history WHERE id=?", (jid,)
        ).fetchone()


def test_save_job_upsert_heals_language_columns():
    """Completed-tracks-hidden P0: the ingest-time insert writes language /
    language_code as "" (target language not chosen yet). Generation sets them
    on the job dict, so the next save_job UPSERT must update the columns —
    before the fix the update list skipped them and the row stayed "" forever,
    which made history restore hand the frontend 'und' and hide the finished
    tracks' tabs."""
    jid = _jid()
    job = {"filename": "v.mp4", "duration": 3.0, "segments": [], "dubbed_tracks": {}}
    dp.save_job(jid, job)  # ingest-time insert: both columns ""
    row = _lang_row(jid)
    assert row["language"] == "" and row["language_code"] == ""

    job["language"] = "Bengali"
    job["language_code"] = "bn"
    dp.save_job(jid, job)  # post-generation re-save heals the columns
    row = _lang_row(jid)
    assert row["language"] == "Bengali"
    assert row["language_code"] == "bn"


def test_save_job_upsert_does_not_clobber_language_with_empty():
    """A later save without language info (e.g. a segment edit on a job dict
    that predates the fields) must NOT reset the healed columns — same
    non-empty guard the UPSERT already applies to content_hash."""
    jid = _jid()
    job = {
        "filename": "v.mp4", "duration": 3.0, "segments": [], "dubbed_tracks": {},
        "language": "Bengali", "language_code": "bn",
    }
    dp.save_job(jid, job)
    job.pop("language")
    job.pop("language_code")
    dp.save_job(jid, job)  # empty values must lose to the stored ones
    row = _lang_row(jid)
    assert row["language"] == "Bengali"
    assert row["language_code"] == "bn"


def test_cancelled_ingest_waits_for_locked_worker_before_cleanup(monkeypatch, tmp_path):
    import asyncio
    import threading

    release = threading.Event()
    job_dir = tmp_path / 'queued-ingest'
    job_dir.mkdir()
    (job_dir / 'input.wav').write_bytes(b'pending input')
    events = []
    async def exercise():
        loop = asyncio.get_running_loop()
        held, contended = asyncio.Event(), asyncio.Event()
        original = dp._dub_jobs_lock
        class Lock:
            def __enter__(self):
                if not original.acquire(blocking=False):
                    loop.call_soon_threadsafe(contended.set)
                    original.acquire()
                return self
            def __exit__(self, *_):
                original.release()
        monkeypatch.setattr(dp, '_dub_jobs_lock', Lock())
        def publisher():
            with original:
                loop.call_soon_threadsafe(held.set)
                assert release.wait(2), 'ingest lock wait blocked the event loop'
        publishing = loop.run_in_executor(None, publisher)
        await held.wait()
        async def ingest():
            async for event in dp.ingest_pipeline('queued-ingest', str(job_dir), {'kind': 'upload'}):
                events.append(event)
        task = asyncio.create_task(ingest())
        try:
            await asyncio.wait_for(contended.wait(), 10)
            task.cancel()
            rendezvous = asyncio.Event()
            loop.call_soon(rendezvous.set)
            await rendezvous.wait()
            assert job_dir.exists(), 'cleanup raced the queued ingest worker'
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await publishing
        finally:
            release.set()
            await asyncio.gather(task, publishing, return_exceptions=True)
    asyncio.run(exercise())
    assert not job_dir.exists()
    assert 'queued-ingest' not in dp._inflight_jobs
    assert 'queued-ingest' not in dp._dub_jobs
    assert any('cancelled' in event for event in events)


def test_cancelled_ingest_with_inflight_save_cannot_reload_deleted_files(monkeypatch, tmp_path):
    import asyncio
    import threading
    from collections import OrderedDict
    from types import SimpleNamespace
    import soundfile as sf
    from core import db

    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'cancelled-save.sqlite'))
    monkeypatch.setattr(dp, '_dub_jobs', {})
    monkeypatch.setattr(dp, '_withdrawn_jobs', OrderedDict())
    monkeypatch.setattr(dp, '_inflight_jobs', set())
    with db.db_conn() as conn:
        conn.executescript(db._BASE_SCHEMA)
    job_dir = tmp_path / 'saving-ingest'
    job_dir.mkdir()
    source = job_dir / 'input.wav'
    source.write_bytes(b'input fixture')
    monkeypatch.setattr(dp, 'find_ffmpeg', lambda: 'test-ffmpeg')
    monkeypatch.setattr(dp, 'validate_media_source', lambda *_: None)
    monkeypatch.setattr(dp, 'require_audio_stream', lambda *_: None)
    monkeypatch.setattr(dp, 'find_cached_job', lambda *_: None)
    async def extract(cmd, **kwargs):
        sf.write(cmd[-2], [.1] * 160, 16000)
        return SimpleNamespace(returncode=0), b'', b''
    monkeypatch.setattr(dp, 'run_proc_factory', lambda _: extract)
    release = threading.Event()
    real_put = dp.put_and_save_job
    async def exercise():
        loop = asyncio.get_running_loop()
        persisted = asyncio.Event()
        def slow_put(*args, **kwargs):
            result = real_put(*args, **kwargs)
            loop.call_soon_threadsafe(persisted.set)
            assert release.wait(2), 'cancellation handling blocked the event loop'
            return result
        monkeypatch.setattr(dp, 'put_and_save_job', slow_put)
        async def ingest():
            async for _ in dp.ingest_pipeline('saving-ingest', str(job_dir), {
                'kind': 'upload', 'input_type': 'audio', 'path': str(source),
            }):
                pass
        task = asyncio.create_task(ingest())
        try:
            await asyncio.wait_for(persisted.wait(), 10)
            with db.db_conn() as conn:
                assert conn.execute('SELECT id FROM dub_history WHERE id=?', ('saving-ingest',)).fetchone()
            task.cancel()
            rendezvous = asyncio.Event()
            loop.call_soon(rendezvous.set)
            await rendezvous.wait()
            assert job_dir.exists()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())
    assert not job_dir.exists()
    assert 'saving-ingest' not in dp._dub_jobs
    assert dp.get_job('saving-ingest') is None, 'cancelled ingest reloaded a row pointing at deleted files'
    dp.save_job('saving-ingest', {'filename': 'late-worker.wav'})
    assert dp.get_job('saving-ingest') is None, 'withdrawal must block a late save too'


@pytest.mark.parametrize('change', ['delete', 'replace', 'revive'])
def test_cold_job_read_serializes_with_deletion_and_replacement(monkeypatch, tmp_path, change):
    import threading
    from contextlib import contextmanager
    from collections import OrderedDict
    from core import db

    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'cold-read.sqlite'))
    monkeypatch.setattr(dp, '_dub_jobs', {})
    monkeypatch.setattr(dp, '_withdrawn_jobs', OrderedDict())
    monkeypatch.setattr(dp, '_inflight_jobs', set())
    with db.db_conn() as conn:
        conn.executescript(db._BASE_SCHEMA)
    dp.save_job('cold-read', {'filename': 'old.wav'})
    read, release, contended = threading.Event(), threading.Event(), threading.Event()
    original_lock = dp._dub_jobs_lock
    class Lock:
        def __enter__(self):
            if not original_lock.acquire(blocking=False):
                contended.set()
                original_lock.acquire()
            return self
        def __exit__(self, *_):
            original_lock.release()
    monkeypatch.setattr(dp, '_dub_jobs_lock', Lock())
    @contextmanager
    def paused_read():
        with db.db_conn() as conn:
            class Conn:
                def execute(self, *args):
                    row = conn.execute(*args).fetchone()
                    class Result:
                        def fetchone(self):
                            read.set()
                            assert release.wait(3)
                            order.append("read")
                            return row
                    return Result()
            yield Conn()
    monkeypatch.setattr(dp, 'db_conn', paused_read)
    found, order = [], []
    replacement = {'filename': 'current.wav'}
    def lookup():
        found.append(dp.get_job('cold-read'))
    def mutate():
        if change in {'delete', 'revive'}:
            def delete():
                with db.db_conn() as conn:
                    conn.execute('DELETE FROM dub_history WHERE id=?', ('cold-read',))
            dp.purge_jobs(['cold-read'], delete_rows=delete)
            if change == 'revive':
                dp.begin_ingest('cold-read')
        else:
            dp.put_job('cold-read', replacement)
        order.append('mutate')
    worker = threading.Thread(target=lookup)
    mutation = threading.Thread(target=mutate)
    worker.start()
    try:
        assert read.wait(2)
        mutation.start()
        assert contended.wait(2), 'cold hydration did not serialize with the source mutation'
        release.set()
        worker.join(2)
        mutation.join(2)
        assert not worker.is_alive() and not mutation.is_alive()
        assert found == [{'filename': 'old.wav'}]
        assert order == ['read', 'mutate']
        if change == 'replace':
            assert dp._dub_jobs['cold-read'] is replacement
        else:
            assert 'cold-read' not in dp._dub_jobs
            assert dp.get_job('cold-read') is None
            assert ('cold-read' in dp._withdrawn_jobs) == (change == 'delete')
    finally:
        release.set()
        worker.join(2)
        if mutation.ident is not None:
            mutation.join(2)
