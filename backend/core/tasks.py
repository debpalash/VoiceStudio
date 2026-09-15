import asyncio
import time
import json
import logging

from core import job_store
from core import failure
from core import run_sentinel

logger = logging.getLogger("omnivoice.tasks")


def _stream_failure(update):
    """Recognize terminal SSE failures, including generators that do not raise."""
    if isinstance(update, bytes):
        update = update.decode("utf-8", errors="replace")
    if not isinstance(update, str):
        return None
    lines = update.splitlines()
    try:
        payload = json.loads("\n".join(line[5:].strip() for line in lines if line.startswith("data:")))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "error" and not any(line.strip() == "event: error" for line in lines):
        return None
    detail = payload.get("reason") or payload.get("error") or payload.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("reason")
    return detail if isinstance(detail, str) and detail else "Task failed"


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
        task_obj = {
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
            try:
                import inspect
                res = func(*args, **kwargs)
                if inspect.isasyncgen(res):
                    async for update in res:
                        if t.get("cancelled"):
                            await self._push_event(task_id, f"data: {json.dumps({'type': 'cancelled'})}\n\n")
                            t["status"] = "cancelled"
                            try: job_store.mark_cancelled(task_id)
                            except Exception: logger.exception("job_store.mark_cancelled failed")
                            break
                        await self._push_event(task_id, update)
                        stream_error = _stream_failure(update)
                        if stream_error is not None:
                            t["status"] = "failed"
                            t["error"] = stream_error
                            try:
                                job_store.mark_failed(task_id, stream_error)
                            except Exception:
                                logger.exception("job_store.mark_failed failed")
                            await res.aclose()
                            break
                elif inspect.iscoroutine(res):
                    await res
                if t["status"] not in {"cancelled", "failed"}:
                    t["status"] = "done"
                    try: job_store.mark_done(task_id)
                    except Exception: logger.exception("job_store.mark_done failed")
            except Exception as e:
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
