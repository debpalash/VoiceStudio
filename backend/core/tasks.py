import asyncio
import contextlib
import time
import json
import logging

from core import job_store
from core import failure
from core import run_sentinel

logger = logging.getLogger("omnivoice.tasks")
_EXPECTED_STREAM_ERRORS = ("dub_source_changed", "dub_timing_overflow", "model_licence_required")


def _stream_payload(update):
    """The JSON payload and raw lines of one SSE update, or ``(None, [])``."""
    if isinstance(update, bytes):
        update = update.decode("utf-8", errors="replace")
    if not isinstance(update, str):
        return None, []
    lines = update.splitlines()
    try:
        payload = json.loads("\n".join(line[5:].strip() for line in lines if line.startswith("data:")))
    except (ValueError, TypeError):
        return None, lines
    return (payload if isinstance(payload, dict) else None), lines


def _stream_completed(update) -> bool:
    """Whether an update reports work that has already been committed."""
    payload, _lines = _stream_payload(update)
    return payload is not None and payload.get("type") == "done"


def _stream_failure(update):
    """Recognize terminal SSE failures, including generators that do not raise."""
    payload, lines = _stream_payload(update)
    if payload is None:
        return None
    if payload.get("type") != "error" and not any(line.strip() == "event: error" for line in lines):
        return None
    detail = payload.get("reason") or payload.get("error") or payload.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("reason")
    return detail if isinstance(detail, str) and detail else "Task failed"


_TERMINAL_EVENTS = frozenset({"done", "error", "cancelled"})


def _stream_event_type(update):
    """An SSE update's event name, else its JSON ``type``, else None."""
    payload, lines = _stream_payload(update)
    for line in lines:
        if line.startswith("event:"):
            return line[6:].strip()
    return payload.get("type") if payload is not None else None


def closing_event(job: "dict | None") -> "str | None":
    """The terminal frame a replay of a job that is no longer running must end with.

    A process that dies mid-task (an OOM kill, a crash, an update restart)
    leaves the job's last progress event as its tail. The startup sweep marks
    the job ``failed`` but cannot write the terminal event its worker never
    reached, so a reconnecting client's stream ended without one and the
    client kept waiting on a task that no longer exists (#4463). Returns None
    when the retained tail already ends with a terminal event or the job
    completed; a ``done`` event carries results that cannot be rebuilt here.
    """
    if not job or job.get("status") == "done":
        return None
    try:
        last = job_store.last_event(job["id"])
    except Exception:
        logger.exception("job_store.last_event failed; ending the replay from the job status")
        last = None
    if last is not None and _stream_event_type(last) in _TERMINAL_EVENTS:
        return None
    if job.get("status") == "cancelled":
        return f"data: {json.dumps({'type': 'cancelled'})}\n\n"
    # ``failed``, or still ``pending``/``running`` with no live task behind it.
    event = failure.build_failure_event(job.get("error") or job_store.INTERRUPTED_BY_RESTART, stage="task")
    return f"data: {json.dumps(event)}\n\n"


class TaskManager:
    """In-memory task dispatcher with SQLite-backed metadata.

    The dispatcher itself (queue + worker + listeners) stays in-memory for
    speed, but every state transition and every SSE event is mirrored to
    `jobs` / `job_events`. That means:

      - clients can reconnect via `/tasks/stream/{id}?after_seq=N` and catch up
      - restart recovers: orphaned `running` jobs are flipped to `failed`
      - `GET /jobs` works across restarts
    """

    def __init__(self):
        self.queue = None
        self.active_tasks = {}

    def _init_queue(self):
        if self.queue is None:
            self.queue = asyncio.Queue()

    async def add_task(self, task_id, task_type, func, *args, project_id=None, meta=None, **kwargs):
        self._init_queue()
        from core import diagnostic_reports
        task_obj = {
            "diagnostic_epoch": diagnostic_reports.inherited_grant(),
            "status": "pending",
            "type": task_type,
            "created_at": time.time(),
            "history": [],
            "listeners": [],
            "listeners_lock": asyncio.Lock(),
            "error": None,
            "cancelled": False,
        }
        self.active_tasks[task_id] = task_obj
        try:
            job_store.create(task_id, type=task_type, project_id=project_id, meta=meta)
        except Exception:
            logger.exception("job_store.create failed (non-fatal); in-memory task still runs")
        await self.queue.put((task_id, func, args, kwargs))

    def cancel_task(self, task_id):
        if task_id in self.active_tasks:
            self.active_tasks[task_id]["cancelled"] = True
            return True
        return False

    def is_cancelled(self, task_id):
        t = self.active_tasks.get(task_id)
        return t["cancelled"] if t else False

    async def add_listener(self, task_id, q):
        t = self.active_tasks.get(task_id)
        if not t:
            return False
        async with t["listeners_lock"]:
            t["listeners"].append(q)
        return True

    async def remove_listener(self, task_id, q):
        t = self.active_tasks.get(task_id)
        if not t:
            return
        async with t["listeners_lock"]:
            if q in t["listeners"]:
                t["listeners"].remove(q)

    async def _push_event(self, task_id, event_str):
        t = self.active_tasks.get(task_id)
        if t is None:
            return
        if event_str is not None:
            t["history"].append(event_str)
            try:
                seq = job_store.append_event(task_id, event_str)
                # Stash the seq on the in-memory copy too, mainly for tests.
                t.setdefault("event_seqs", []).append(seq)
            except Exception:
                # Never let disk writes break the live stream.
                logger.exception("job_store.append_event failed; event delivered to listeners only")
        # Snapshot listeners under lock so concurrent add/remove can't mutate mid-iteration.
        async with t["listeners_lock"]:
            listeners = list(t["listeners"])
        for q in listeners:
            await q.put(event_str)

    async def worker(self):
        self._init_queue()
        while True:
            task_id, func, args, kwargs = await self.queue.get()
            t = self.active_tasks.get(task_id)
            if not t:
                self.queue.task_done()
                continue

            t["status"] = "running"
            try:
                job_store.mark_running(task_id)
            except Exception:
                logger.exception("job_store.mark_running failed (non-fatal)")
            # Crash forensics (#1164): note what kind of work just started so
            # an unclean process death (OOM kill mid-dub, …) can be attributed
            # by the next run. Task TYPE only — never user content. The touch
            # is throttled + exception-safe by contract (core.run_sentinel).
            run_sentinel.touch_activity("task", t.get("type"))
            from core import diagnostic_reports
            diagnostic_epoch = t.get("diagnostic_epoch")
            try:
                import inspect
                with diagnostic_reports.grant_scope(diagnostic_epoch):
                    res = func(*args, **kwargs)
                    if inspect.isasyncgen(res):
                        # Close the stream however the task ends (done, cancelled,
                        # failed or raised) so a render releases what it holds, such
                        # as voice-file leases, instead of staying suspended (#2535).
                        async with contextlib.aclosing(res):
                            while True:
                                try:
                                    update = await diagnostic_reports.await_owned(anext(res), diagnostic_epoch)
                                except StopAsyncIteration:
                                    break
                                # A `done` update means the work is already
                                # committed; a cancel that arrived meanwhile must
                                # not report it as cancelled, or the client would
                                # redo work that exists.
                                if t.get("cancelled") and not _stream_completed(update):
                                    await self._push_event(task_id, f"data: {json.dumps({'type': 'cancelled'})}\n\n")
                                    t["status"] = "cancelled"
                                    try: job_store.mark_cancelled(task_id)
                                    except Exception: logger.exception("job_store.mark_cancelled failed")
                                    break
                                await self._push_event(task_id, update)
                                stream_error = _stream_failure(update)
                                if stream_error is not None:
                                    frame, _ = _stream_payload(update)
                                    code = frame.get("error_code") if frame is not None else None
                                    expected_refusal = isinstance(code, str) and code in _EXPECTED_STREAM_ERRORS
                                    if (
                                        diagnostic_epoch is not None
                                        and not expected_refusal
                                        and not diagnostic_reports.failure_already_captured(update, diagnostic_epoch)
                                    ):
                                        diagnostic_reports.capture_failure(diagnostic_reports.route_stage(t.get("type", "")), expected_epoch=diagnostic_epoch)
                                    t["status"] = "failed"
                                    t["error"] = stream_error
                                    try:
                                        job_store.mark_failed(task_id, stream_error)
                                    except Exception:
                                        logger.exception("job_store.mark_failed failed")
                                    break
                    elif inspect.iscoroutine(res):
                        await diagnostic_reports.await_owned(res, diagnostic_epoch)
                    if t["status"] not in {"cancelled", "failed"}:
                        t["status"] = "done"
                        try: job_store.mark_done(task_id)
                        except Exception: logger.exception("job_store.mark_done failed")
            except Exception as e:
                if diagnostic_epoch is not None and not t.get("cancelled"):
                    diagnostic_reports.capture_failure(diagnostic_reports.route_stage(t.get("type", "")), error=e, expected_epoch=diagnostic_epoch)
                logger.exception("Task %s failed", task_id)
                t["status"] = "failed"
                # plan-04 (#131): structured, non-empty failure event instead of
                # a bare str(e) (which is empty/cryptic for many exception types).
                evt = failure.build_failure_event(e, stage="task", context={"task_id": task_id})
                t["error"] = evt["reason"]
                try:
                    job_store.mark_failed(task_id, evt["reason"])
                except Exception:
                    logger.exception("job_store.mark_failed failed")
                try:
                    await self._push_event(task_id, f"data: {json.dumps(evt)}\n\n")
                except Exception as push_err:
                    logger.warning("Failed to push error event for %s: %s", task_id, push_err)
            finally:
                await self._push_event(task_id, None)  # EOF
                self.queue.task_done()

task_manager = TaskManager()
