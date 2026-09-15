"""#2108 class: a `/tasks/stream` that is busy but quiet must not go byte-silent.

ffmpeg on a long video, a slow TTS segment or a job queued behind another one
all leave the task stream with nothing to say for minutes, and byte-silent SSE
gets severed by the desktop webview, Chrome's ~5 min cap or a proxy's idle
timeout (#1196). SSE comment frames keep it alive and are invisible to every
consumer: EventSource, the fetch-based generate reader (`data: ` lines only),
the CLI tailer and the bench script.
"""
import asyncio


def test_quiet_task_stream_emits_keepalive_comments(monkeypatch):
    from api.routers import dub_export as de
    from core.tasks import task_manager

    task_id = "prep_quiet"
    # raising=False: without the fix the constant does not exist, and the test
    # must then fail on the assertion below, not here.
    monkeypatch.setattr(de, "TASK_STREAM_KEEPALIVE_S", 0.02, raising=False)
    monkeypatch.setattr("core.job_store.get", lambda _id: None)
    monkeypatch.setattr("core.job_store.events_since", lambda *a, **k: [])

    async def _first_frames(n, budget_s=1.0):
        task_manager.active_tasks[task_id] = {
            "status": "running", "type": "prep", "created_at": 0.0, "history": [],
            "listeners": [], "listeners_lock": asyncio.Lock(),
            "error": None, "cancelled": False,
        }
        resp = await de.stream_task(task_id)
        frames = []

        async def _read():
            async for chunk in resp.body_iterator:
                frames.append(chunk)
                if len(frames) >= n:
                    break

        # Pre-fix the reader blocks on an empty queue forever; bound the wait
        # so the failure is a clean assertion rather than a hung test.
        try:
            await asyncio.wait_for(_read(), timeout=budget_s)
        except asyncio.TimeoutError:
            pass
        return frames

    try:
        frames = asyncio.run(_first_frames(2))
    finally:
        task_manager.active_tasks.pop(task_id, None)

    assert frames == [": keepalive\n\n"] * 2, frames
