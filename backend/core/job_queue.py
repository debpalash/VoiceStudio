"""Serial async job queue for GPU-bound work.

Why: TTS inference, diarization, demucs, Whisper, ffmpeg muxing all contend
for the same GPU/CPU/fd budget. Without a gate, a second dub request can
OOM VRAM or trigger posix_spawn EAGAIN on macOS. This module provides a
single serialized worker with cancel semantics and queue-position
introspection so the UI can tell users "2 jobs ahead of you".

Complements `core.tasks.task_manager`:
  * `task_manager` — SSE event streaming (per-task history + listeners).
  * `job_queue`    — resource gating, cancellation, position tracking.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("omnivoice.jobs")


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    label: str
    fn: Callable[["Job"], Awaitable[Any]]
    state: JobState = JobState.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    result: Any = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class JobQueue:
    """Single-lane serial async worker with cancellation and introspection."""

    def __init__(self, name: str = "jobs", concurrency: int = 1) -> None:
        self.name = name
        self._concurrency = max(1, concurrency)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._workers: List[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._run_worker(i), name=f"{self.name}-worker-{i}")
            for i in range(self._concurrency)
        ]

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def submit(
        self,
        label: str,
        fn: Callable[[Job], Awaitable[Any]],
        job_id: Optional[str] = None,
    ) -> Job:
        """Enqueue a coroutine-returning callable. Idempotent on job_id."""
        jid = job_id or uuid.uuid4().hex[:12]
        async with self._lock:
            existing = self._jobs.get(jid)
            if existing is not None:
                return existing
            job = Job(id=jid, label=label, fn=fn)
            self._jobs[jid] = job
            self._order.append(jid)
        await self._queue.put(jid)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def position(self, job_id: str) -> int:
        """Return 0-indexed position among queued jobs, or -1 if not queued."""
        job = self._jobs.get(job_id)
        if not job or job.state != JobState.QUEUED:
            return -1
        idx = 0
        for jid in self._order:
            other = self._jobs.get(jid)
            if not other or other.state != JobState.QUEUED:
                continue
            if jid == job_id:
                return idx
            idx += 1
        return -1

    def list_jobs(self, include_finished: bool = False) -> List[Job]:
        if include_finished:
            return [self._jobs[j] for j in self._order if j in self._jobs]
        live = {JobState.QUEUED, JobState.RUNNING}
        return [self._jobs[j] for j in self._order if j in self._jobs and self._jobs[j].state in live]

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.state == JobState.QUEUED:
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
            job.cancel_event.set()
            job.done_event.set()
            return True
        if job.state == JobState.RUNNING:
            # Cooperative: worker polls `job.is_cancelled` between steps.
            job.cancel_event.set()
            return True
        return False

    async def wait(self, job_id: str, timeout: Optional[float] = None) -> Job:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if timeout is None:
            await job.done_event.wait()
        else:
            await asyncio.wait_for(job.done_event.wait(), timeout=timeout)
        return job

    def purge_finished(self, older_than_seconds: float = 3600.0) -> int:
        """Drop rows for jobs finished more than `older_than_seconds` ago."""
        cutoff = time.time() - older_than_seconds
        drop = [
            jid for jid in self._order
            if (j := self._jobs.get(jid))
            and j.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
            and (j.finished_at or 0.0) < cutoff
        ]
        for jid in drop:
            self._jobs.pop(jid, None)
        self._order = [j for j in self._order if j in self._jobs]
        return len(drop)

    async def _run_worker(self, idx: int) -> None:
        while True:
            jid = await self._queue.get()
            try:
                job = self._jobs.get(jid)
                if job is None:
                    continue
                if job.state == JobState.CANCELLED:
                    # Cancelled while queued — skip execution.
                    continue
                job.state = JobState.RUNNING
                job.started_at = time.time()
                try:
                    result = await job.fn(job)
                    if job.is_cancelled:
                        job.state = JobState.CANCELLED
                    else:
                        job.state = JobState.DONE
                        job.result = result
                except asyncio.CancelledError:
                    job.state = JobState.CANCELLED
                    job.cancel_event.set()
                    # Awaiting a cancelled child raises here without cancelling
                    # this worker. Only stop the lane when the worker itself
                    # received cancellation (e.g. during queue shutdown).
                    worker = asyncio.current_task()
                    if worker is None or worker.cancelling():
                        raise
                except Exception as e:
                    logger.exception("Job %s (%s) failed", job.id, job.label)
                    job.state = JobState.FAILED
                    job.error = str(e)
                finally:
                    job.finished_at = time.time()
                    job.done_event.set()
            finally:
                self._queue.task_done()


# Shared GPU-bound queue — single lane, protects TTS/ASR/demucs from VRAM contention.
gpu_queue = JobQueue(name="gpu", concurrency=1)
