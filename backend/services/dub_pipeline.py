"""
Dub-pipeline service — Phase 2.4 (ROADMAP.md).

Extracts the non-HTTP business logic out of the 889-line `dub_core.py` router
so the router can stay thin (HTTP concerns only) and this module can be
imported cleanly by other routers (dub_generate, dub_translate, dub_export)
and future callers (the Tools page, the headless CLI, tests).

What's here
-----------
* **Job state** — `_dub_jobs` in-memory dict + `get_job` / `save_job` that
  hydrate from `dub_history.job_data` on cache miss.
* **Content-hash cache lookup** — `compute_file_hash`, `find_cached_job`.
* **Safe path resolution** — `safe_job_dir`.
* **Process lifecycle** — ffmpeg/demucs subprocess tracking + `kill_job_procs`
  so `POST /dub/abort/{id}` can tear down in-flight work (implemented in
  `services.proc_registry`, re-exported here for compatibility).
* **SSE helpers** — `sse_event`, `prep_event`.

What stays in the router
------------------------
The route decorators + request-body validation + response shaping. The big
ingest/transcribe generators still live there for now — they're tightly
coupled to FastAPI's `StreamingResponse`/async-generator contract, and
moving them is a follow-up.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from typing import AsyncIterator, Optional

import soundfile as sf

from core.config import DUB_DIR
from fastapi import HTTPException
from services.ffmpeg_utils import (
    _get_semaphore,
    _spawn_with_retry,
    find_ffmpeg,
    find_ffprobe,
    raise_for_audio_extract_failure,
    require_audio_stream,
)
from services.srt_parser import spoken_cue_text
from services.model_manager import get_best_device
# Process lifecycle moved to its own leaf module so ffmpeg_utils can import
# it at module top (no dub_pipeline ↔ ffmpeg_utils cycle). Re-exported here —
# dub_core and tests still alias these names through this module.
from services.proc_registry import (  # noqa: F401 — re-exports
    _active_procs,
    _active_procs_lock,
    has_active_procs,
    kill_job_procs,
    register_proc,
    unregister_proc,
)
from core.db import db_conn
from core import event_bus
from core import failure
from core.logging_utils import log_safe

logger = logging.getLogger("omnivoice.dub_pipeline")


def _media_process_error(tool: str, returncode: int, stderr: bytes, *, paths=()) -> str:
    """Keep the actionable end of native diagnostics without leaking paths."""
    from core.scrub import scrub_text
    detail = stderr.decode(errors="replace")
    # Input/output may live outside home directories (mounted media, Windows
    # drive roots). Remove the exact command paths before generic scrubbing.
    for path in sorted((str(p) for p in paths if p), key=len, reverse=True):
        for variant in {path, path.replace("\\", "/"), path.replace("/", "\\")}:
            detail = detail.replace(variant, "[redacted path]")
    detail = scrub_text(detail).strip()
    tail = detail[-2000:]
    if len(detail) > 2000:
        tail = "…" + tail
    return f"{tool} exited with code {returncode}" + (f": {tail}" if tail else ". No diagnostic output.")


# ── Module-level state ──────────────────────────────────────────────────────
# These used to live in dub_core.py. The router now re-exports them for
# backward compat during the transition.

_dub_jobs: dict[str, dict] = {}
# Re-entrant: `save_job` takes this lock itself (see below), and the atomic
# helpers call it while already holding it.
_dub_jobs_lock = threading.RLock()

#: Ingests currently running. Used only so "clear history" can also sweep a job
#: that has no row yet — it would appear in no id list otherwise.
_inflight_jobs: set[str] = set()

#: Recently-deleted job ids, most-recent last. Guarded by ``_dub_jobs_lock``.
#:
#: Dict membership alone cannot express "the user withdrew this job" (#1252
#: review): a job's FIRST persistence creates the entry, so an absent key means
#: "not written yet" for a new job and "deleted" for an established one — two
#: opposite instructions from one signal.
#:
#: Scoped to DELETED, not to in-flight ingests. Scoping it to ingests looked
#: right and closed nothing that mattered: a dub is imported once and rendered
#: many times, so the realistic delete lands during a RENDER, long after its
#: ingest ended — and a render's save would then write the row straight back.
#:
#: Retained rather than cleared on completion, because there is no moment at
#: which a delete stops mattering: any operation still holding that job can
#: persist it. ``begin_ingest`` drops an id explicitly, since re-importing is a
#: deliberate revival.
#:
#: Expired by AGE, not by count. A count-bounded LRU is evictable by ordinary
#: use: ``DELETE /dub/history`` purges every row with no limit, so a user
#: clearing a large history mid-render would push the rendering job's own
#: marker out and the render would then write it back (#1252 review). Age
#: cannot be gamed that way — what matters is how long ago the delete happened,
#: not how many others followed it.
#:
#: The count cap is a memory backstop only, set far above any real history:
#: ~4096 short ids is a few hundred KB. Reaching it needs 4096 deletions inside
#: one TTL window, at which point the oldest markers are the least likely to
#: still be held.
_WITHDRAWN_TTL_S = 6 * 3600   # outlives any realistic render or transcribe
_WITHDRAWN_MAX = 4096         # memory backstop, not the eviction policy
_withdrawn_jobs: "OrderedDict[str, float]" = OrderedDict()

_DUB_DIR_REAL = os.path.realpath(DUB_DIR)
_HASH_BUF_SIZE = 1 << 18  # 256 KB chunks for hashing


# ── Pure helpers ────────────────────────────────────────────────────────────


def compute_file_hash(path: str) -> str:
    """SHA-256 digest of a file, streamed in 256 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def safe_job_dir(job_id: str) -> Optional[str]:
    """Resolve a job directory under DUB_DIR, rejecting traversal."""
    if not job_id or "/" in job_id or "\\" in job_id or job_id in (".", ".."):
        return None
    candidate = os.path.realpath(os.path.join(DUB_DIR, job_id))
    if not candidate.startswith(_DUB_DIR_REAL + os.sep):
        return None
    return candidate


def job_dir_referenced_by_others(job_id: str) -> "list[str]":
    """History ids of OTHER jobs whose persisted paths point into ``job_id``'s
    directory (#1331, the deletion half).

    The content-hash cache legitimately points a newer job's ``vocals_path``
    (and, for jobs created before the job-scoped-clones fix, its clone
    reference paths) into an older job's directory. Deleting that older entry
    used to ``rmtree`` the dir regardless, silently breaking the newer job:
    single-segment regens fell back to the default voice, stems exports lost
    their sources. The caller uses this to keep the DIRECTORY while still
    deleting the history row — disk is the cheap thing here; another job's
    voice is not.

    Scans persisted ``job_data`` as text for the dir prefix rather than
    enumerating every path-bearing key: keys have grown before (vocals,
    no_vocals, thumb, clone refs, segment refs) and a scan cannot fall behind
    the schema.
    """
    target = safe_job_dir(job_id)
    if not target:
        return []
    needle = target.rstrip(os.sep) + os.sep
    # JSON-encoded job_data escapes backslashes, so match the Windows form too.
    needle_json = needle.replace("\\", "\\\\")
    holders: list[str] = []
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, job_data FROM dub_history WHERE id != ?", (job_id,)
            ).fetchall()
    except Exception:
        return []  # no DB, nothing persisted can reference us
    for row in rows:
        data = row["job_data"] or ""
        if needle in data or needle_json in data:
            holders.append(row["id"])
    return holders


def sse_event(event: str, payload) -> bytes:
    """Encode one Server-Sent Event frame. UTF-8 bytes, ready to yield."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def prep_event(event_type: str, **fields) -> str:
    """Build a `data:` SSE line for the ingest pipeline (plain `data: {...}`)."""
    return f"data: {json.dumps({'type': event_type, **fields})}\n\n"


# ── Content-hash cache lookup ───────────────────────────────────────────────


def find_cached_job(content_hash: str, exclude_job_id: str) -> Optional[dict]:
    """Find a previous job with the same content hash that has the heavy
    artifacts (vocals/no-vocals/scene cuts) still on disk. Returns a dict
    of paths + metadata the caller can shallow-copy into the new job, or
    None if no usable cache exists.
    """
    if not content_hash:
        return None
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, job_data FROM dub_history WHERE content_hash=? AND id!=? ORDER BY created_at DESC LIMIT 5",
            (content_hash, exclude_job_id),
        ).fetchall()
    for row in rows:
        try:
            job = json.loads(row["job_data"])
        except (json.JSONDecodeError, TypeError):
            continue
        cached_dir = safe_job_dir(row["id"])
        if not cached_dir or not os.path.isdir(cached_dir):
            continue
        vocals = job.get("vocals_path") or os.path.join(cached_dir, "vocals.wav")
        if not os.path.isfile(vocals):
            continue
        # Separation-quality gate: stems produced before the HQ-extraction
        # change were separated from the 16 kHz MONO ASR file — a mono,
        # 8 kHz-ceiling music bed. audio_hq.wav in the cached job dir is the
        # marker that its stems came from the full-quality stereo extraction;
        # without it, reusing the cache would silently keep serving the
        # narrow-band mono bed forever for that video. Re-separating once is
        # the better deal.
        if not os.path.isfile(os.path.join(cached_dir, "audio_hq.wav")):
            logger.info(
                "cache candidate %s has pre-HQ (mono/16k-derived) stems — "
                "skipping reuse so separation reruns at full quality", row["id"],
            )
            continue
        return {
            "job_dir": cached_dir,
            "job_id": row["id"],
            "vocals_path": vocals,
            "no_vocals_path": job.get("no_vocals_path"),
            "scene_cuts": job.get("scene_cuts") or [],
            "thumb_path": job.get("thumb_path"),
            "duration": job.get("duration", 0.0),
        }
    return None


# ── Process lifecycle ───────────────────────────────────────────────────────
# register_proc / unregister_proc / kill_job_procs / has_active_procs live in
# services.proc_registry (imported + re-exported above).


# ── Job state (in-memory + SQLite fallback) ────────────────────────────────


def get_job(job_id: str) -> Optional[dict]:
    """Look up a job. Checks the in-memory cache first, then falls back to
    `dub_history.job_data` so saved projects still resolve after restart.
    """
    with _dub_jobs_lock:
        if job_id in _dub_jobs:
            return _dub_jobs[job_id]
    with db_conn() as conn:
        row = conn.execute("SELECT job_data FROM dub_history WHERE id=?", (job_id,)).fetchone()
    if row and row["job_data"]:
        try:
            job = json.loads(row["job_data"])
            with _dub_jobs_lock:
                _dub_jobs[job_id] = job
            return job
        except json.JSONDecodeError:
            # job_id arrives from request paths — strip newlines so a crafted
            # id can't forge extra log lines (py/log-injection).
            safe_id = str(job_id).replace("\r", "").replace("\n", "")
            logger.exception("Failed to decode dub_history.job_data for %s", safe_id)
    return None


def put_job(job_id: str, job: dict) -> None:
    """Insert / replace the in-memory job record. Does NOT persist."""
    with _dub_jobs_lock:
        _dub_jobs[job_id] = job


def merge_job(job_id: str, updates: dict) -> bool:
    """Merge *updates* into an existing in-memory job. Does NOT persist.

    Returns ``False`` when the job is gone — which is a real, reachable state,
    not a defensive nicety: ingest runs for minutes (demucs, scene detection,
    thumbnailing) and ``DELETE /dub/history/{id}`` pops the entry out from
    under it. The pipeline used to finish with a bare
    ``_dub_jobs[job_id].update(...)``, so deleting an in-flight dub surfaced as
    the toast ``ingest: 'mgw39lx3'`` — ``str(KeyError)`` is the repr of the
    key, nothing more (#1252/#1253). Callers treat ``False`` as "the user
    withdrew this job" and stop, rather than resurrecting a record that was
    deliberately deleted.
    """
    with _dub_jobs_lock:
        job = _dub_jobs.get(job_id)
        if job is None:
            return False
        job.update(updates)
        return True


def _expire_withdrawn(now: float, protected: int = 0) -> None:
    """Drop withdrawal markers that are too old to still matter.

    Caller must hold ``_dub_jobs_lock``. Age first — that is the policy — then
    a size cap purely so the mapping cannot grow without bound.

    ``protected`` is how many markers the current purge just recorded. Those sit
    at the end (newest) and are never evicted by the size cap: they are the most
    likely to still be held by a running job, and dropping one is exactly the
    resurrection this whole mechanism exists to prevent. A single "clear
    history" larger than the cap would otherwise force us to discard live
    markers — which is what broke CI. The bound therefore is
    ``cap + one purge``, not ``cap``.
    """
    cutoff = now - _WITHDRAWN_TTL_S
    while _withdrawn_jobs:
        _, deleted_at = next(iter(_withdrawn_jobs.items()))
        if deleted_at >= cutoff:
            break
        _withdrawn_jobs.popitem(last=False)
    floor = max(_WITHDRAWN_MAX, protected)
    while len(_withdrawn_jobs) > floor:
        _withdrawn_jobs.popitem(last=False)


def begin_ingest(job_id: str) -> None:
    """Mark an ingest as running.

    Re-importing an id is a deliberate revival, so this clears any tombstone —
    the only thing that legitimately un-deletes a job.
    """
    with _dub_jobs_lock:
        _inflight_jobs.add(job_id)
        _withdrawn_jobs.pop(job_id, None)


def end_ingest(job_id: str) -> None:
    """Mark an ingest as finished, however it ended.

    Deliberately does NOT clear the tombstone: the ingest ending is not the
    user un-deleting anything, and a render started before the delete can still
    be holding that job.
    """
    with _dub_jobs_lock:
        _inflight_jobs.discard(job_id)


def put_and_save_job(
    job_id: str,
    job: dict,
    *,
    filename: str = "",
    duration: float = 0.0,
    content_hash: str = "",
) -> bool:
    """:func:`put_job` and :func:`save_job` as ONE atomic step.

    Returns ``False`` when the job was withdrawn — the user deleted or cleared
    its history while this ingest was running — in which case nothing is
    written. Gating on the tombstone rather than on dict membership is what
    makes this correct for a job's FIRST write, where an absent key is normal
    (#1252 review).
    """
    with _dub_jobs_lock:
        if job_id in _withdrawn_jobs:
            return False
        _dub_jobs[job_id] = job
        save_job(job_id, job, filename, duration, content_hash)
        return True


def merge_and_save_job(
    job_id: str,
    updates: dict,
    *,
    filename: str = "",
    duration: float = 0.0,
    content_hash: str = "",
) -> bool:
    """:func:`merge_job` and :func:`save_job` as ONE atomic step.

    Splitting them leaves a window that resurrects deleted work (#1252 review):
    merge succeeds, the user deletes the dub — removing the row *and* the
    in-memory entry — and the pending ``save_job`` then UPSERTs the row straight
    back, so a dub the user deleted reappears in history. The delete endpoints
    take this same lock around their own row-delete + evict, so the two
    sequences cannot interleave at all.

    Returns ``False`` when the job is already gone; the caller stops there.

    The ``save_job`` write happens INSIDE the lock deliberately. That serialises
    dub job-state access against one SQLite UPSERT — normally microseconds under
    WAL, but up to sqlite3's 5 s default busy timeout if another writer is
    holding the write lock. The alternative — releasing the lock before the
    write — is the resurrection race this exists to close, so a rare latency
    blip is the better trade. No locked region here calls another locked
    function, so the plain (non-reentrant) ``_dub_jobs_lock`` cannot deadlock.
    """
    with _dub_jobs_lock:
        job = _dub_jobs.get(job_id)
        if job is None or job_id in _withdrawn_jobs:
            return False
        job.update(updates)
        save_job(job_id, job, filename, duration, content_hash)
        return True


def purge_jobs(job_ids, *, delete_rows, include_inflight: bool = False) -> None:
    """Delete history rows and evict the in-memory records as ONE atomic step.

    ``delete_rows`` is called with the lock held, so a concurrent
    :func:`merge_and_save_job` cannot slip between the row-delete and the
    evict and write the job straight back (#1252 review). Both delete
    endpoints go through here; ``DELETE /dub/history`` previously never evicted
    from memory at all, so an in-flight job survived "clear history" entirely
    and re-saved itself on completion.
    """
    with _dub_jobs_lock:
        delete_rows()
        # Deterministic order, de-duplicated. A `set` here made which markers
        # the size cap evicts depend on PYTHONHASHSEED — the test for this very
        # behaviour passed locally and failed in CI for that reason alone.
        targets = list(dict.fromkeys(job_ids))
        if include_inflight:
            # "Clear history" means everything, including a job whose first row
            # hasn't been written yet — it wouldn't appear in `job_ids` at all.
            targets += [j for j in sorted(_inflight_jobs) if j not in set(targets)]
        now = time.monotonic()
        for job_id in targets:
            _dub_jobs.pop(job_id, None)
            _withdrawn_jobs.pop(job_id, None)
            _withdrawn_jobs[job_id] = now  # most-recent last
        _expire_withdrawn(now, protected=len(targets))


def save_job(job_id: str, job: dict, filename: str = "", duration: float = 0.0, content_hash: str = "") -> None:
    """Persist dub job state to SQLite so it survives restarts. Uses UPSERT
    on `id` so repeated saves in a session keep the latest snapshot.

    language / language_code / content_hash only update when the incoming
    value is non-empty: the ingest-time insert runs before the target
    language is known (both columns ""), generation sets them on the job
    dict, and a later save from a job that lost them (e.g. hydrated from an
    old row) must not clobber the healed columns back to "". The frontend
    keys history restore off language_code, so a frozen "" hid finished
    tracks until the user re-picked a language.
    """
    with _dub_jobs_lock:
        # The withdrawal gate lives HERE, not in the callers (#1252 review).
        # Eight call sites across generate / translate / export / core persist
        # jobs directly, so gating only the ingest helpers left every
        # post-ingest save able to resurrect a dub the user deleted mid-render.
        # One choke point closes the class and the ninth caller inherits it.
        if job_id in _withdrawn_jobs:
            logger.info(
                "Dub job %s was deleted while it was still running — not persisting", log_safe(job_id),
            )
            return
        _persist_job(job_id, job, filename, duration, content_hash)


def _persist_job(job_id: str, job: dict, filename: str, duration: float, content_hash: str) -> None:
    """The actual write. Callers go through :func:`save_job`, which gates it."""
    try:
        segments = job.get("segments") or []
        tracks = list((job.get("dubbed_tracks") or {}).keys())
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO dub_history
                   (id, filename, duration, segments_count, language, language_code, tracks, job_data, content_hash, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     filename=excluded.filename,
                     duration=excluded.duration,
                     segments_count=excluded.segments_count,
                     language=CASE WHEN excluded.language != '' THEN excluded.language ELSE dub_history.language END,
                     language_code=CASE WHEN excluded.language_code != '' THEN excluded.language_code ELSE dub_history.language_code END,
                     tracks=excluded.tracks,
                     job_data=excluded.job_data,
                     content_hash=CASE WHEN excluded.content_hash != '' THEN excluded.content_hash ELSE dub_history.content_hash END""",
                (job_id, filename or job.get("filename", ""),
                 duration or job.get("duration", 0.0),
                 len(segments), job.get("language", ""), job.get("language_code", ""),
                 json.dumps(tracks), json.dumps(job, default=str), content_hash or "", time.time()),
            )
    except Exception as exc:
        logger.error("Failed to persist dub job %s: %s", log_safe(job_id), log_safe(exc))
        return
    event_bus.emit("dub_history", {"action": "saved", "id": job_id})


# ── Ingest pipeline (download → extract → demucs → scene → thumb) ──────────


def run_proc_factory(job_id: str):
    """Return an async `run_proc` helper bound to this job_id.

    Spawns subprocesses under the shared ffmpeg semaphore, tracks them so
    `kill_job_procs(job_id)` can terminate them, raises HTTP 504 on timeout.
    """
    async def run_proc(cmd, timeout: float = 900.0):
        async with _get_semaphore():
            p = await _spawn_with_retry(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            register_proc(job_id, p)
            try:
                try:
                    stdout, stderr = await asyncio.wait_for(p.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(p.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                    raise HTTPException(status_code=504, detail=f"subprocess timed out after {timeout}s")
                return p, stdout, stderr
            finally:
                unregister_proc(job_id, p)
                if p.returncode is None:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(p.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
    return run_proc


async def run_proc_streaming_stderr(
    job_id: str,
    cmd: list[str],
    *,
    timeout: float = 1800.0,
) -> AsyncIterator[tuple]:
    """Spawn `cmd` and stream its stderr line-by-line as it runs.

    Yields:
      ('stderr', line: str) — once per logical line (split on \\r or \\n,
        so tqdm-style progress bars that overwrite the same line via
        carriage return surface as separate lines)
      ('done', returncode: int, full_stderr: bytes) — exactly once when
        the subprocess exits; this is always the final value.

    Honors the same semaphore + register_proc/kill plumbing as run_proc.
    """
    async with _get_semaphore():
        p = await _spawn_with_retry(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        register_proc(job_id, p)
        stderr_parts: list[bytes] = []
        rc: int = -1
        try:
            if getattr(p, "uses_sync_pipes", False):
                # Fallback loops (the Windows SelectorEventLoop uvicorn forces
                # under --reload) hand back a thread-backed proc whose .stderr is
                # a plain SYNC pipe, not an asyncio StreamReader — `await
                # p.stderr.read()` there raises "a coroutine or an awaitable is
                # required" and crashed the demucs step. We can't stream that
                # pipe incrementally without leaking blocked executor threads on
                # every 1s poll, so run to completion via the wrapper's async
                # communicate() and replay stderr as the same line events. No
                # live progress on that degraded loop, but the subprocess still
                # runs and the emitted event sequence is identical. The native
                # async path (Proactor/posix — every release build) is the
                # unchanged `else` below.
                try:
                    _out, err_bytes = await asyncio.wait_for(
                        p.communicate(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
                    raise HTTPException(
                        status_code=504,
                        detail=f"subprocess timed out after {timeout}s",
                    )
                err_bytes = err_bytes or b""
                stderr_parts.append(err_bytes)
                for _line in re.split(rb"[\r\n]", err_bytes):
                    _text = _line.decode(errors="replace")
                    if _text.strip():
                        yield ("stderr", _text)
            else:
                buf = b""
                start = time.monotonic()
                while True:
                    if time.monotonic() - start > timeout:
                        try:
                            p.kill()
                        except ProcessLookupError:
                            pass
                        raise HTTPException(
                            status_code=504,
                            detail=f"subprocess timed out after {timeout}s",
                        )
                    try:
                        chunk = await asyncio.wait_for(p.stderr.read(256), timeout=1.0)
                    except asyncio.TimeoutError:
                        if p.returncode is not None:
                            break
                        continue
                    if not chunk:
                        break
                    stderr_parts.append(chunk)
                    buf += chunk
                    while True:
                        idx_r = buf.find(b"\r")
                        idx_n = buf.find(b"\n")
                        if idx_r < 0 and idx_n < 0:
                            break
                        if idx_r < 0:
                            idx = idx_n
                        elif idx_n < 0:
                            idx = idx_r
                        else:
                            idx = min(idx_r, idx_n)
                        line = buf[:idx].decode(errors="replace")
                        buf = buf[idx + 1:]
                        if line.strip():
                            yield ("stderr", line)
            rc = await p.wait()
        finally:
            unregister_proc(job_id, p)
            if p.returncode is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(p.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            try:
                p.stdout.close()
            except Exception:
                pass
    yield ("done", rc, b"".join(stderr_parts))


_BROWSER_VIDEO_CODECS = {"h264", "avc1"}
_BROWSER_AUDIO_CODECS = {"aac", "mp4a"}


def _probe_codecs(path: str) -> tuple[str, str]:
    """Return (video_codec, audio_codec) lowercased, or ('','') on probe error."""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return ("", "")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        vcodec = (out.stdout or "").strip().lower()
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        acodec = (out.stdout or "").strip().lower()
        return vcodec, acodec
    except Exception:
        return ("", "")


def _ensure_browser_playable_mp4(video_path: str) -> str:
    """Guarantee `video_path` is an mp4 with h264 video + aac audio.

    Three classes of fix the in-app `<video>` element needs:
      1. `.webm`/`.mkv` containers — WKWebView refuses them outright.
      2. `.mp4` with VP9 or AV1 video — WKWebView can't decode either
         inside an mp4 container (Safari ships VP9 only in WebM).
      3. `.mp4` with Opus audio — same story; mp4-with-opus is rare and
         poorly supported across webviews.

    Strategy: probe codecs; if extension is mp4 AND both codecs are in
    the browser-safe set, leave alone. Otherwise transcode to h264+aac.
    Returns the (possibly new) file path.
    """
    ffmpeg_bin = find_ffmpeg()
    is_mp4 = video_path.lower().endswith(".mp4")
    if is_mp4:
        vcodec, acodec = _probe_codecs(video_path)
        if vcodec in _BROWSER_VIDEO_CODECS and acodec in _BROWSER_AUDIO_CODECS:
            return video_path  # already safe — fast path, no rewrite
    # Need to rewrite. Try stream-copy first when the container is the
    # only problem; fall back to full transcode for codec mismatches.
    target = os.path.splitext(video_path)[0] + ".mp4"
    if target == video_path:
        target = os.path.splitext(video_path)[0] + ".browser.mp4"
    # Stream-copy attempt — works when codecs are already h264+aac but
    # the container is wrong (rare with our format selector but cheap to
    # try). Skip straight to transcode if we already know codecs are bad.
    rc = 1
    if is_mp4:
        # Codecs were probed and known-bad; skip the copy attempt.
        pass
    else:
        rc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", video_path,
             "-c:v", "copy", "-c:a", "copy",
             "-movflags", "+faststart", target],
            capture_output=True,
        ).returncode
        if rc != 0 or not os.path.exists(target):
            rc = 1
    if rc != 0:
        # Full transcode — h264 baseline-ish + aac is the safe combo.
        rc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", video_path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", target],
            capture_output=True,
        ).returncode
    if rc == 0 and os.path.exists(target) and target != video_path:
        try:
            os.remove(video_path)
        except OSError:
            pass
        return target
    if rc != 0:
        logger.warning(
            "Could not transcode %s to browser-playable mp4 — the in-app "
            "video player may render this file as a black box.",
            log_safe(video_path),
        )
    return video_path


async def _ensure_browser_playable_mp4_for_job(job_id: str, video_path: str) -> str:
    """Normalize an upload through the job's cancellable process registry."""
    is_mp4 = video_path.lower().endswith(".mp4")
    vcodec, acodec = await asyncio.to_thread(_probe_codecs, video_path)
    if is_mp4 and vcodec in _BROWSER_VIDEO_CODECS and acodec in _BROWSER_AUDIO_CODECS:
        return video_path

    target = os.path.splitext(video_path)[0] + ".mp4"
    if target == video_path:
        target = os.path.splitext(video_path)[0] + ".browser.mp4"
    run_proc = run_proc_factory(job_id)
    ffmpeg_bin = find_ffmpeg()

    async def attempt(cmd: list[str]) -> int:
        try:
            proc, _stdout, _stderr = await run_proc(cmd, timeout=1800.0)
            return proc.returncode
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Browser-media normalization process failed for %s: %s",
                log_safe(video_path),
                log_safe(exc),
            )
            return 1

    rc = 1
    if not is_mp4:
        rc = await attempt(
            [
                ffmpeg_bin, "-y", "-i", video_path,
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart", target,
            ]
        )
        if rc == 0 and os.path.exists(target):
            target_vcodec, target_acodec = await asyncio.to_thread(_probe_codecs, target)
            if (
                target_vcodec not in _BROWSER_VIDEO_CODECS
                or target_acodec not in _BROWSER_AUDIO_CODECS
            ):
                rc = 1
        else:
            rc = 1
    if rc != 0:
        rc = await attempt(
            [
                ffmpeg_bin, "-y", "-i", video_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", target,
            ]
        )
    if rc == 0 and os.path.exists(target) and target != video_path:
        try:
            os.remove(video_path)
        except OSError:
            pass  # Best effort: the normalized target is already complete.
        return target
    logger.warning(
        "Could not transcode %s to browser-playable mp4 — the in-app "
        "video player may render this file as a black box.",
        log_safe(video_path),
    )
    return video_path


# Bounded retry for transient download failures (#579/#598). yt-dlp's own
# `retries`/`fragment_retries` cover per-fragment HTTP flakes, but a broken
# pipe ([Errno 32]) raised while the write side of a pipe closes mid-stream
# (a killed ffmpeg merge child, a CDN reset during muxing) aborts the whole
# `extract_info` call and is NOT covered by them — so a single transient blip
# failed the entire ingest with a raw "Broken pipe". We add a small download-
# level retry on top, cleaning up the partial download between attempts so a
# half-written `original.*` can't poison the next try.
_YT_DOWNLOAD_RETRIES = 2  # total attempts = 1 + retries = 3


def _with_target_facts(exc: BaseException, job_dir: str) -> BaseException:
    """``exc`` with the download destination described, when the failure looks
    like the OS refusing a file operation (#1225).

    Returns ``exc`` untouched for network/format failures — their message is
    already about the remote side, and appending disk facts would just be
    noise. Never raises."""
    try:
        # Shared with failure.classify() so the "is this a disk problem?"
        # answer can't differ between the class we assign and whether we
        # bother naming the folder (#1225 review).
        if not failure.is_os_write_refusal(str(exc)):
            return exc
        facts = failure.describe_path_target(os.path.join(job_dir, "original.mp4"))
        if not facts:
            return exc
        msg = (
            f"{exc} — saving to {job_dir} ({facts}). The OS refused the write, "
            f"so retrying the same link won't help: check the drive isn't full, "
            f"the folder is writable, and antivirus or a cloud-sync client "
            f"(OneDrive, Dropbox) isn't locking it."
        )
        try:
            return type(exc)(msg)
        except Exception:
            # Not every exception class takes a plain message (soundfile's
            # LibsndfileError wants an int code). Keep the text, drop the type.
            return RuntimeError(msg)
    except Exception:
        return exc


def _is_transient_download_error(exc: BaseException) -> bool:
    """True when a download failure is worth retrying (broken pipe / net drop).

    Reuses the single failure taxonomy (`VIDEO_DOWNLOAD_NETWORK`) rather than a
    parallel keyword list, so "what counts as transient" stays single-sourced
    with the error-hint classification. ``BrokenPipeError``/``ConnectionError``
    are matched by class too, since a bare instance may be wrapped or re-raised
    with a stripped message that no longer contains "broken pipe".
    """
    if isinstance(exc, (BrokenPipeError, ConnectionError)):
        return True
    return failure.classify(str(exc)) == "VIDEO_DOWNLOAD_NETWORK"


# YouTube serves some videos' high-quality formats signature-protected to the
# default player client, so the media download 403s even though extraction
# worked. Forcing an alternate client commonly bypasses it; on a 403 we escalate
# through these (in order) before giving up (#625).
_YT_PLAYER_CLIENTS = ["tv", "android", "web_safari"]


def _is_forbidden_download_error(exc: BaseException) -> bool:
    """True for a failure the CURRENT player client can't get past, but another
    one commonly can.

    A 403 is the original case (#625): extraction worked, the media fetch was
    refused, and the same client keeps refusing. "This video is DRM protected"
    (#1254) behaves identically and belongs here for the same reason — YouTube
    serves a DRM-only format set to *some* player clients for videos that are
    not actually DRM'd. The reporter saw it fail and then succeed on a plain
    retry of the same URL, which is exactly what a per-client format set looks
    like from outside. Escalating the client is the fix; a bare retry only
    works when the next attempt happens to draw a different one.
    """
    s = str(exc)
    if "403" in s or "Forbidden" in s:
        return True
    low = s.lower()
    return "drm protected" in low or "drm-protected" in low


def _cleanup_partial_download(job_dir: str) -> None:
    """Remove any half-written `original.*` files before a retry.

    A partial download left on disk would otherwise be picked up as a "finished"
    file by the post-download codec probe, or collide with the next attempt's
    output. Raises a stable error instead of retrying against unsafe stale data.
    """
    import glob
    for stale in glob.glob(os.path.join(job_dir, "original.*")):
        try:
            os.remove(stale)
        except OSError as exc:
            logger.warning("Partial video download cleanup failed")
            raise RuntimeError(
                "Could not prepare the video download retry. Close any app using its temporary files and retry."
            ) from exc


def _delete_cookie_export(cookie_file: str | None) -> bool:
    """Best-effort removal of the per-import authentication export."""
    if not cookie_file:
        return True
    try:
        os.unlink(cookie_file)
    except OSError:
        # Best effort: cleanup must never replace the download result.
        return False
    return True


def yt_download_sync(
    url: str,
    job_dir: str,
    *,
    fetch_subs: bool = False,
    sub_langs: list[str] | None = None,
    progress_hook=None,
    cookie_file: str | None = None,
) -> tuple[str, str, list[str]]:
    """Blocking yt-dlp download into `job_dir`.

    Returns (video_path, title, downloaded_sub_files).

    Captions are downloaded in a separate, best-effort pass after the video
    so subtitle failures (rate-limits, missing tracks) can never derail the
    actual ingest. Defaults skip YouTube's auto-translated set — requesting
    "all" expands into ~100 per-language variants per video and reliably
    trips HTTP 429, and the downstream Translate step handles target
    languages more reliably than YouTube's machine translations anyway.
    Pass an explicit `sub_langs` list to override the default selection.
    """
    import glob
    import yt_dlp
    outtmpl = os.path.join(job_dir, "original.%(ext)s")
    # #1225: yt-dlp surfaces an OS write rejection as a bare
    # "Unable to download video: [Errno 22] Invalid argument" — no path, no
    # reason, and three manual retries all fail identically because nothing
    # about it is transient. Fail here instead, naming the directory, when we
    # can already see it won't work.
    _target_facts = failure.describe_path_target(outtmpl)
    if "not writable" in _target_facts or "does not exist" in _target_facts:
        # Worded so classify() places it in the download path: it must carry
        # both an OS-refusal signature and download context, or the user gets
        # no hint at all — the failure this PR exists to fix (#1225 review).
        raise OSError(
            f"Unable to download video: unable to open for writing in "
            f"{job_dir} ({_target_facts}). The video downloads into this job "
            f"folder under your VoiceStudio data directory — check it exists, is "
            f"writable, and isn't locked by antivirus or a cloud-sync client."
        )
    ydl_opts: dict = {
        "outtmpl": outtmpl,
        # Prefer h264+aac streams so the merged mp4 is natively decodable
        # by WKWebView/WebView2/Chromium. YouTube serves VP9+Opus as the
        # default high-quality combo, which yt-dlp will happily mux into
        # an mp4 container — but Safari/WKWebView refuses to decode VP9
        # inside mp4, leaving a black <video> in the dub editor. Fall
        # back to any combo only when no h264/aac variant exists, then
        # the post-download codec probe below will transcode it.
        "format": (
            "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/"
            "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/"
            "b[vcodec^=avc1][acodec^=mp4a]/"
            "bv*[vcodec^=avc1]+ba/"
            "b[vcodec^=avc1]/"
            "bv*+ba/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        # Don't stamp the downloaded file's mtime with the video's upload date
        # (#642): on Windows an out-of-range/invalid timestamp makes the os.utime
        # call raise `[Errno 22] Invalid argument`, failing the whole ingest. We
        # download to a throwaway `original.*` and never use its mtime, so skip
        # it entirely (equivalent to yt-dlp's --no-mtime).
        "updatetime": False,
        "socket_timeout": 30,
        # Resilience against YouTube CDN flakes: a single empty fragment
        # (commonly the very last one — "Did not get any data blocks")
        # used to fail the whole ingest at 99% complete. Retry each
        # fragment generously and tolerate one that never returns data;
        # missing the last <0.5% of audio is acceptable for transcription.
        "fragment_retries": 10,
        "retries": 10,
        "extractor_retries": 5,
        "skip_unavailable_fragments": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    # #712: the format selector above pulls separate video+audio streams, so
    # yt-dlp muxes them via ffmpeg (merge_output_format=mp4). yt-dlp only looks
    # for ffmpeg on PATH and aborts with "you have requested merging of multiple
    # formats but ffmpeg is not installed" — but VoiceStudio's ffmpeg is often a
    # bundled Tauri sidecar / imageio-ffmpeg binary that isn't on PATH (common on
    # Windows). Point yt-dlp at the exact ffmpeg we resolve so the merge works.
    _ffmpeg_bin = find_ffmpeg()
    if _ffmpeg_bin:
        ydl_opts["ffmpeg_location"] = _ffmpeg_bin
    if progress_hook is not None:
        ydl_opts["progress_hooks"] = [progress_hook]

    # Download with a bounded retry on transient/broken-pipe-class failures
    # (#579/#598). A broken pipe mid-mux isn't recoverable inside yt-dlp's own
    # fragment retries, but a fresh `extract_info` usually succeeds. Between
    # attempts we wipe the partial `original.*` so a half-written file can't be
    # mistaken for a finished download.
    info = None
    path = None
    transient_used = 0
    client_idx = 0
    while True:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = ydl.prepare_filename(info)
            break
        except Exception as exc:
            _cleanup_partial_download(job_dir)
            # 403 Forbidden: not transient — escalate the YouTube player client,
            # which commonly bypasses a signature-protected format set (#625).
            if _is_forbidden_download_error(exc) and client_idx < len(_YT_PLAYER_CLIENTS):
                client = _YT_PLAYER_CLIENTS[client_idx]
                client_idx += 1
                ydl_opts = {**ydl_opts, "extractor_args": {"youtube": {"player_client": [client]}}}
                logger.warning(
                    "Download 403 for %s — retrying with player_client=%s (#625)", log_safe(url), log_safe(client),
                )
                continue
            # Transient/broken-pipe: a fresh extract_info usually succeeds
            # (#579/#598). A 403 never counts here — it's escalated above.
            if (transient_used < _YT_DOWNLOAD_RETRIES
                    and _is_transient_download_error(exc)
                    and not _is_forbidden_download_error(exc)):
                transient_used += 1
                logger.warning(
                    "Transient download failure for %s (attempt %d/%d): %s — retrying",
                    log_safe(url), transient_used, _YT_DOWNLOAD_RETRIES, log_safe(exc),
                )
                time.sleep(2 * transient_used)  # brief, increasing backoff
                continue
            # #1225: an OS-level rejection (errno 22 / EACCES / ENOSPC) tells
            # the user nothing on its own. Attach what we can observe about
            # the destination so the message identifies a full drive, a
            # removed folder, or an antivirus/cloud-sync lock. Wording keeps
            # the yt-dlp text so classify() still sees the download context.
            described = _with_target_facts(exc, job_dir)
            if described is exc:
                raise
            raise described from exc
    root, _ = os.path.splitext(path)
    mp4 = root + ".mp4"
    if os.path.exists(mp4):
        video_path = mp4
    else:
        video_path = path
    # Browser-playability guard: WKWebView (Tauri on macOS) refuses to
    # decode VP9/AV1 video and Opus audio even when they're wrapped in an
    # mp4 container, and refuses .webm/.mkv outright. We probe the actual
    # codecs and transcode only when needed — `<video>` rendering is
    # what we care about, not what extension yt-dlp ended up writing.
    video_path = _ensure_browser_playable_mp4(video_path)
    title = info.get("title") or os.path.basename(video_path)

    sub_files: list[str] = []
    if fetch_subs:
        if sub_langs:
            langs = list(sub_langs)
        else:
            langs = _default_caption_languages(info)
        if not langs:
            logger.info("No captions available on %s (skipping subtitle pass)", log_safe(url))
        else:
            sub_opts = {
                **ydl_opts,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": langs,
                "subtitlesformat": "vtt",
                "extractor_args": {"youtube": {"skip": ["translated_subs"]}},
                "ignoreerrors": True,
                "extractor_retries": 5,
                "sleep_interval_subtitles": 1,
            }
            try:
                with yt_dlp.YoutubeDL(sub_opts) as ydl_sub:
                    ydl_sub.extract_info(url, download=True)
            except Exception as e:
                logger.warning(
                    "Subtitle download failed for %s (continuing with video): %s",
                    log_safe(url), log_safe(e),
                )
            base = os.path.splitext(video_path)[0]
            sub_files = sorted(glob.glob(base + ".*.vtt"))
    return video_path, title, sub_files


def _default_caption_languages(info: dict) -> list[str]:
    """Return original-language caption tracks without translated auto-captions.

    Some extractors omit ``language`` even though yt-dlp exposes an original
    automatic-caption track such as ``en-orig``.  Treat that explicit suffix as
    source metadata so caption-first ingest still works instead of needlessly
    loading ASR.  Manual tracks remain eligible because they are authored source
    material and yt-dlp's ``skip=translated_subs`` guard still applies.
    """
    original = str(info.get("language") or "").strip()
    manual = {
        str(language).strip()
        for language in (info.get("subtitles") or {})
        if str(language).strip()
    }
    automatic = {
        str(language).strip()
        for language in (info.get("automatic_captions") or {})
        if str(language).strip()
    }
    selected = set(manual)
    if original:
        primary = original.split("-", 1)[0]
        has_source_manual = any(
            language == original or language.split("-", 1)[0] == primary
            for language in manual
        )
        if not has_source_manual:
            for candidate in (
                f"{original}-orig",
                f"{primary}-orig",
                original,
                primary,
            ):
                if candidate in automatic:
                    selected.add(candidate)
                    break
    else:
        selected.update(language for language in automatic if language.endswith("-orig"))
    return sorted(selected)


def parse_vtt_segments(vtt_path: str) -> list[dict]:
    """Very small WEBVTT parser → list of {start, end, text}.

    Purpose: turn yt-dlp's downloaded caption track into the same segment
    shape the dub pipeline's transcript step produces, so the UI can seed
    its editor from YouTube captions instead of running Whisper. We don't
    care about positioning cues or styling — just the timed text.
    """
    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return []

    def _ts(s: str) -> float:
        # Format: HH:MM:SS.mmm or MM:SS.mmm
        parts = s.strip().split(":")
        try:
            parts = [float(p.replace(",", ".")) for p in parts]
        except ValueError:
            return 0.0
        if len(parts) == 3:
            h, m, sec = parts
        elif len(parts) == 2:
            h, m, sec = 0.0, parts[0], parts[1]
        else:
            return 0.0
        return h * 3600.0 + m * 60.0 + sec

    segments: list[dict] = []
    blocks = raw.replace("\r\n", "\n").split("\n\n")
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() and not ln.startswith("WEBVTT") and not ln.startswith("NOTE")]
        if not lines:
            continue
        # Skip numeric cue ID line if present
        if "-->" not in lines[0] and len(lines) > 1:
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        ts_line = lines[0]
        try:
            left, right = ts_line.split("-->")
            # Drop any settings after the right timestamp ("00:01.000 align:left line:0%")
            right = right.strip().split(" ")[0]
            start = _ts(left)
            end = _ts(right)
        except Exception:
            continue
        text = spoken_cue_text(" ".join(ln.strip() for ln in lines[1:]), webvtt=True)
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


async def ingest_pipeline(
    job_id: str,
    job_dir: str,
    source: dict,
    filename_hint: Optional[str] = None,
) -> AsyncIterator[str]:
    """Async generator: emit SSE events per processing stage.

    Stages: download_start, download_done, extract_start, extract_done,
    demucs_start, demucs_done, scene_start, scene_done, ready, error, cancelled.
    """
    youtube_subs_by_lang: dict[str, list[dict]] = {}
    # Audio-only jobs (#119) skip scene detection + thumbnailing below; the
    # transcribe → translate → TTS core is identical.
    input_type = (source.get("input_type") or "video").lower()
    # Declare the run so a "clear history" arriving before this job's first
    # persistence can still withdraw it (#1252 review).
    begin_ingest(job_id)
    try:
        if source.get("kind") == "url":
            url = source["url"]
            fetch_subs = bool(source.get("fetch_subs"))
            sub_langs = source.get("sub_langs") or None
            cookie_file = source.get("cookie_file") or None
            yield prep_event("download_start", url=url)
            # Bridge yt-dlp's per-fragment progress callback (fires inside
            # the worker thread) into the async generator via a threadsafe
            # queue so the UI can render a real download bar.
            loop = asyncio.get_running_loop()
            dl_queue: asyncio.Queue = asyncio.Queue()
            _last_pct = -1

            def _yt_progress(d: dict) -> None:
                nonlocal _last_pct
                status = d.get("status")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    cur = d.get("downloaded_bytes") or 0
                    if total > 0:
                        pct = max(0, min(100, int(cur * 100 / total)))
                        if pct != _last_pct:
                            _last_pct = pct
                            payload = {
                                "percent": pct,
                                "speed_bps": d.get("speed") or 0,
                                "eta_s": d.get("eta"),
                            }
                            loop.call_soon_threadsafe(dl_queue.put_nowait, payload)

            dl_task = asyncio.create_task(asyncio.to_thread(
                yt_download_sync, url, job_dir,
                fetch_subs=fetch_subs, sub_langs=sub_langs,
                progress_hook=_yt_progress,
                cookie_file=cookie_file,
            ))
            try:
                while not dl_task.done():
                    try:
                        payload = await asyncio.wait_for(dl_queue.get(), timeout=0.5)
                        yield prep_event("download_progress", **payload)
                    except asyncio.TimeoutError:
                        continue
                # Drain any final queued events.
                while not dl_queue.empty():
                    payload = dl_queue.get_nowait()
                    yield prep_event("download_progress", **payload)
                video_path, title, sub_files = await dl_task
            except Exception as e:
                logger.error("Download failed for job %s: %s", log_safe(job_id), log_safe(e))
                if not dl_task.done():
                    dl_task.cancel()
                yield prep_event("error", **failure.build_failure(e, stage="download"))
                shutil.rmtree(job_dir, ignore_errors=True)
                return
            # yt-dlp (including its optional subtitle pass) is finished. Drop
            # the login credential before the much longer audio-prep stages.
            if _delete_cookie_export(cookie_file):
                source["cookie_file"] = None
            filename = title or os.path.basename(video_path)
            try:
                size = os.path.getsize(video_path)
            except OSError:
                size = 0
            # Parse each downloaded .<lang>.vtt into a segment list we can
            # stash on the job. The router will merge this into the job dict
            # after ingest completes so /dub/transcribe-stream (or a future
            # "use-youtube-subs" toggle) can seed segments from it.
            for sf_path in sub_files:
                base = os.path.splitext(os.path.basename(sf_path))[0]
                # "original.en" → lang "en"; fallback to the trailing token.
                lang_tag = base.rsplit(".", 1)[-1] if "." in base else "und"
                segs = parse_vtt_segments(sf_path)
                if segs:
                    youtube_subs_by_lang[lang_tag] = segs
            yield prep_event(
                "download_done",
                title=title, size=size, filename=filename,
                youtube_subs=sorted(youtube_subs_by_lang.keys()),
            )
        else:
            video_path = source["path"]
            filename = filename_hint or os.path.basename(video_path)

        audio_path = os.path.join(job_dir, "audio.wav")
        ffmpeg = find_ffmpeg()
        run_proc = run_proc_factory(job_id)

        yield prep_event("extract_start")
        try:
            # A video with no audio stream has nothing to transcribe or dub.
            # Name that instead of letting ffmpeg fail with exit 234 and a
            # stream dump ending in "Invalid argument".
            await asyncio.to_thread(require_audio_stream, video_path)
            p, _, stderr = await run_proc([
                ffmpeg, "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1", audio_path, "-y",
            ])
            if p.returncode != 0:
                # The probe can be undetermined (no ffprobe); recognize the
                # same cause from ffmpeg's own wording.
                await asyncio.to_thread(raise_for_audio_extract_failure, stderr, video_path)
                msg = _media_process_error("FFmpeg", p.returncode, stderr, paths=(video_path, audio_path, job_dir))
                raise Exception(msg)
            # Second, FULL-QUALITY extraction for source separation. audio.wav
            # is deliberately 16 kHz mono — that's what ASR wants — but Demucs
            # used to separate that same file, so the music bed inherited mono
            # (stereo image destroyed: L/R correlation 1.000 vs the original's
            # 0.754, measured) and an 8 kHz ceiling (nothing real above half
            # the ASR rate — the bed's "muffled" sound at its source). Demucs
            # resamples to 44.1 kHz internally either way, so separating the
            # stereo original costs about the same and returns a true-stereo,
            # full-band bed. Best-effort: on failure Demucs falls back to the
            # ASR file, which is exactly the old behavior.
            audio_hq_path = os.path.join(job_dir, "audio_hq.wav")
            try:
                p_hq, _, stderr_hq = await run_proc([
                    ffmpeg, "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                    "-ar", "44100", "-ac", "2", audio_hq_path, "-y",
                ])
                if p_hq.returncode != 0 or not os.path.exists(audio_hq_path):
                    logger.warning(
                        "HQ audio extraction failed (rc=%s) — separation falls "
                        "back to the 16k mono ASR file", p_hq.returncode,
                    )
                    audio_hq_path = None
            except Exception as e_hq:  # noqa: BLE001 — quality upgrade, never fatal
                logger.warning("HQ audio extraction errored (%s) — falling back", log_safe(e_hq))
                audio_hq_path = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Extract failed for job %s: %s", log_safe(job_id), log_safe(e))
            yield prep_event("error", **failure.build_failure(e, stage="extract"))
            return

        try:
            dur = float(sf.info(audio_path).frames) / float(sf.info(audio_path).samplerate)
        except Exception:
            dur = 0.0

        # URL downloads already pass through this guard in yt_download_sync.
        # Uploaded videos did not, so a valid VP9/AV1/Opus upload could be
        # processed successfully but remain undecodable by the in-app WebView.
        # Codec probing/transcoding is blocking; keep it off the event loop.
        if source.get("kind") != "url" and input_type != "audio":
            video_path = await _ensure_browser_playable_mp4_for_job(job_id, video_path)

        # Content-hash cache: reuse artifacts from previous matching jobs.
        content_hash = await asyncio.to_thread(compute_file_hash, audio_path)
        cached = find_cached_job(content_hash, job_id)
        if cached:
            logger.info("Cache hit for job %s (hash %s) → reusing artifacts from %s",
                        log_safe(job_id), log_safe(content_hash[:12]), log_safe(cached["job_id"]))
            vocals_path = os.path.join(job_dir, "vocals.wav")
            no_vocals_path = os.path.join(job_dir, "no_vocals.wav")
            thumb_path = os.path.join(job_dir, "thumb.jpg")

            if cached["vocals_path"] and os.path.isfile(cached["vocals_path"]):
                shutil.copy2(cached["vocals_path"], vocals_path)
            else:
                vocals_path = audio_path
            if cached["no_vocals_path"] and os.path.isfile(cached["no_vocals_path"]):
                shutil.copy2(cached["no_vocals_path"], no_vocals_path)
            else:
                no_vocals_path = None
            if cached["thumb_path"] and os.path.isfile(cached["thumb_path"]):
                shutil.copy2(cached["thumb_path"], thumb_path)
            else:
                thumb_path = None

            scene_cuts = cached["scene_cuts"] or []

            full_job = {
                "video_path": video_path,
                "audio_path": audio_path,
                "vocals_path": vocals_path,
                "no_vocals_path": no_vocals_path,
                "thumb_path": thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                "duration": dur,
                "filename": filename,
                "segments": None,
                "dubbed_tracks": {},
                "scene_cuts": scene_cuts,
                "youtube_subs": youtube_subs_by_lang or None,
                "input_type": input_type,
                "source_lang_override": source.get("source_lang"),
            }
            if not put_and_save_job(
                job_id, full_job, filename=filename, duration=dur, content_hash=content_hash,
            ):
                logger.info("Dub job %s was deleted during ingest — discarding its result", log_safe(job_id))
                yield prep_event("cancelled")
                return
            yield prep_event("extract_done", job_id=job_id, duration=round(dur, 2), filename=filename)
            yield prep_event("cached",
                             has_bg=bool(no_vocals_path and os.path.exists(no_vocals_path)),
                             scene_count=len(scene_cuts))
            yield prep_event("ready", job_id=job_id, duration=round(dur, 2), filename=filename)

        else:
            # Full pipeline: demucs → scene → thumbnail.
            partial = {
                "video_path": video_path,
                "audio_path": audio_path,
                "vocals_path": audio_path,  # fallback until demucs completes
                "no_vocals_path": None,
                "thumb_path": None,
                "duration": dur,
                "filename": filename,
                "segments": None,
                "dubbed_tracks": {},
                "scene_cuts": [],
                "youtube_subs": youtube_subs_by_lang or None,
                "input_type": input_type,
                "source_lang_override": source.get("source_lang"),
            }
            if not put_and_save_job(
                job_id, partial, filename=filename, duration=dur, content_hash=content_hash,
            ):
                logger.info("Dub job %s was deleted during ingest — discarding its result", log_safe(job_id))
                yield prep_event("cancelled")
                return
            yield prep_event("extract_done", job_id=job_id, duration=round(dur, 2), filename=filename)

            vocals_path = os.path.join(job_dir, "vocals.wav")
            no_vocals_path = os.path.join(job_dir, "no_vocals.wav")
            scene_cuts: list = []

            yield prep_event("demucs_start")
            try:
                demucs_cmd = [sys.executable, "-m", "demucs.separate",
                              "--two-stems", "vocals", "-n", "htdemucs", "-d", get_best_device(),
                              audio_hq_path or audio_path, "-o", job_dir]
                rc = -1
                stderr_full = b""
                last_pct = -1
                # demucs writes a tqdm progress bar to stderr as
                # "  42%|████      | …" — surface each new integer percent
                # to the UI so the user sees the bar instead of a static
                # spinner during the multi-minute separation step.
                async for evt in run_proc_streaming_stderr(job_id, demucs_cmd, timeout=1800.0):
                    if evt[0] == "stderr":
                        m = re.search(r"(\d{1,3})%", evt[1])
                        if m:
                            pct = max(0, min(100, int(m.group(1))))
                            if pct != last_pct:
                                last_pct = pct
                                yield prep_event("demucs_progress", percent=pct)
                    elif evt[0] == "done":
                        rc, stderr_full = evt[1], evt[2]
                if rc != 0:
                    raise Exception(_media_process_error("Demucs", rc, stderr_full, paths=(audio_hq_path, audio_path, job_dir)))
                # Stems land under the INPUT's basename ("audio_hq" when the
                # full-quality extraction succeeded, "audio" on its fallback).
                demucs_out = os.path.join(
                    job_dir, "htdemucs",
                    os.path.splitext(os.path.basename(audio_hq_path or audio_path))[0],
                )
                if os.path.exists(os.path.join(demucs_out, "vocals.wav")):
                    shutil.move(os.path.join(demucs_out, "vocals.wav"), vocals_path)
                    shutil.move(os.path.join(demucs_out, "no_vocals.wav"), no_vocals_path)
                    shutil.rmtree(os.path.join(job_dir, "htdemucs"), ignore_errors=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Demucs failed for %s, falling back to mixed audio: %s", log_safe(job_id), log_safe(e))
                # plan-04: surface the degradation (job continues with mixed audio).
                yield prep_event("warning", **failure.build_failure(e, stage="demucs", include_diagnostic=False))
                vocals_path = audio_path
                no_vocals_path = None
            yield prep_event("demucs_done",
                             has_bg=bool(no_vocals_path and os.path.exists(no_vocals_path)))

            # Audio-only jobs (#119) have no video to scan or thumbnail. Skip
            # both ffmpeg passes but still emit scene_done (count=0) so the
            # prep SSE contract the frontend waits on is unchanged.
            thumb_path = os.path.join(job_dir, "thumb.jpg")
            if input_type == "audio":
                # No scenes/thumbnail in audio — emit the start/done pair anyway
                # so the prep SSE stage sequence stays symmetric with the video
                # path (the frontend's stage tracker expects both).
                yield prep_event("scene_start")
                yield prep_event("scene_done", count=0)
                thumb_path = None
            else:
                yield prep_event("scene_start")
                try:
                    p, _, stderr_scene = await run_proc([
                        ffmpeg, "-i", video_path, "-filter:v",
                        "select='gt(scene,0.3)',showinfo", "-f", "null", "-",
                    ], timeout=600.0)
                    matches = re.finditer(r"pts_time:([\d\.]+)", stderr_scene.decode(errors="replace"))
                    scene_cuts = [float(m.group(1)) for m in matches]
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("Scene detection failed for %s: %s", log_safe(job_id), log_safe(e))
                    yield prep_event("warning", **failure.build_failure(e, stage="scene", include_diagnostic=False))
                yield prep_event("scene_done", count=len(scene_cuts))

                offset = max(0.5, min(1.5, dur * 0.1)) if dur else 1.0
                try:
                    await run_proc([
                        ffmpeg, "-y", "-ss", f"{offset:.2f}", "-i", video_path,
                        "-vframes", "1", "-vf", "scale=320:-2",
                        "-q:v", "4", thumb_path,
                    ], timeout=30.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("Thumbnail extraction failed for %s: %s", log_safe(job_id), log_safe(e))
                    yield prep_event("warning", **failure.build_failure(e, stage="thumbnail", include_diagnostic=False))

            # The job can legitimately be gone by now — everything above takes
            # minutes and `DELETE /dub/history/{id}` pops the record. Deleting
            # an in-flight dub used to raise KeyError here and surface as the
            # toast `ingest: 'mgw39lx3'` (#1252/#1253). A withdrawn job is not
            # an error: stop quietly rather than re-persisting what the user
            # just deleted.
            # Merge and persist as one step: a delete landing BETWEEN them
            # would remove the row and then have it written straight back, so
            # the dub the user deleted reappears in history (#1252 review).
            if not merge_and_save_job(
                job_id,
                {
                    "vocals_path": vocals_path,
                    "no_vocals_path": no_vocals_path,
                    "thumb_path": thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                    "scene_cuts": scene_cuts,
                },
                filename=filename,
                duration=dur,
                content_hash=content_hash,
            ):
                logger.info("Dub job %s was deleted during ingest — discarding its result", log_safe(job_id))
                yield prep_event("cancelled")
                return
            yield prep_event("ready", job_id=job_id, duration=round(dur, 2), filename=filename)

    except asyncio.CancelledError:
        logger.info("Dub prep cancelled for job %s; killing subprocesses and cleaning up", log_safe(job_id))
        kill_job_procs(job_id)
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        finally:
            _dub_jobs.pop(job_id, None)
        yield prep_event("cancelled")
        raise
    except Exception as e:
        # plan-04 (#131): no unhandled ingest failure may be silent. Log the
        # real traceback and surface a structured, non-empty reason with stage
        # context instead of letting it bubble up as a bare task error.
        logger.error("Ingest pipeline failed for job %s: %s", log_safe(job_id), log_safe(e))
        yield prep_event("error", **failure.build_failure(e, stage="ingest"))
        return
    finally:
        # Cookie exports are login credentials. Keep an explicitly selected
        # export only for this download, then remove it on success, failure or
        # cancellation; never copy it into the project/job directory.
        cookie_file = source.get("cookie_file")
        _delete_cookie_export(cookie_file)
        end_ingest(job_id)
        with _active_procs_lock:
            _active_procs.pop(job_id, None)
