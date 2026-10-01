"""Audiobook render stops on client disconnect (#1216).

"The Create audiobook button has no stop option." The frontend fix aborts the
fetch on Stop; this pins the BACKEND half: ``_render_longform_sse`` polls
``is_disconnected()`` at each chapter boundary and, when the client is gone,
stops scheduling further chapters instead of rendering the whole book into a
stream nobody reads — while leaving the finished chapters cached and the resume
manifest in place so a later Create/resume finishes the rest cheaply.

No models / GPU: the synth boundary is stubbed (a CPU tone), exactly like the
longform e2e. ffmpeg is never reached on the stop path (we break before
assembling), so it's stubbed truthy only to clear the early gate.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import torch

from services.ffmpeg_utils import find_ffmpeg


def _resolve(_voice_id):
    return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}


def _stub_build_synth():
    def _factory(default_voice=None, language=None, opts=None, voice_map=None):
        def synth(text, voice_id, speed=None):
            return torch.zeros(2400)  # 0.1s @ 24k, 1-D float32
        return {"mode": "generic", "resolve": _resolve, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}
    return _factory


def _plan(*chapters):
    from services.audiobook import AudiobookPlan, Chapter, Span
    return AudiobookPlan(chapters=[
        Chapter(title=title, spans=[Span(voice_id=None, text=body)])
        for title, body in chapters
    ])


def _drive(plan, monkeypatch, outputs_dir, *, is_disconnected=None, **kw):
    from core.db import init_db
    init_db()
    from api.routers import audiobook
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(outputs_dir))
    # Clear the early ffmpeg gate; the stop path never reaches run_ffmpeg.
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "/usr/bin/true")

    async def _run():
        out = []
        async for frame in audiobook._render_longform_sse(
            plan, default_voice=None, is_disconnected=is_disconnected, **kw
        ):
            out.append(json.loads(frame[len("data:"):].strip()))
        return out

    return asyncio.run(_run())


def _disconnect_after(n_ok):
    """Async is_disconnected that returns False for the first ``n_ok`` polls
    (letting those chapters render), then True (client gone)."""
    state = {"i": 0}

    async def check():
        i = state["i"]
        state["i"] += 1
        return i >= n_ok

    return check


def test_disconnect_stops_early_and_preserves_resume(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _drive(
        _plan(("One", "a"), ("Two", "b"), ("Three", "c")),
        monkeypatch, out, is_disconnected=_disconnect_after(1),
    )
    types = [e["type"] for e in events]
    # Only the first chapter rendered; the render stopped before scheduling more.
    assert types.count("chapter") == 1
    assert "assembling" not in types and "done" not in types
    assert types[-1] == "stopped"
    assert events[-1]["rendered"] == 1 and events[-1]["total"] == 3
    from core import job_store
    assert job_store.get(events[0]["job_id"])["status"] == "cancelled"

    # The resume manifest is preserved (NOT cleared), so the finished chapter is
    # offered for resume — Create-again picks up the rest from the cache.
    from services import longform_resume
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(out))
    assert any(e["job_type"] == "audiobook" for e in longform_resume.scan_resumable())


@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg required for the full-render control")
def test_no_disconnect_renders_all_chapters(tmp_path, monkeypatch):
    # Control: a connected client (is_disconnected always False) is never
    # stopped — the normal completion path is untouched.
    out = tmp_path / "outputs"
    out.mkdir()
    from core.db import init_db
    init_db()

    async def _connected():
        return False

    from api.routers import audiobook
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(out))

    async def _run():
        return [json.loads(f[len("data:"):].strip())
                async for f in audiobook._render_longform_sse(
                    _plan(("One", "a"), ("Two", "b"), ("Three", "c")),
                    default_voice=None, fmt="m4b", is_disconnected=_connected)]

    events = asyncio.run(_run())
    types = [e["type"] for e in events]
    assert "stopped" not in types
    assert types.count("chapter") == 3
    assert types[-1] == "done"
    from core import job_store
    assert job_store.get(events[0]["job_id"])["status"] == "done"


@pytest.mark.parametrize("close_kind", ["close", "cancel"])
def test_cancelled_native_stream_leaves_terminal_job_and_checkpoint(close_kind):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg required to reach the native renderer lifecycle")
    from core import job_store
    from core.db import init_db
    from api.routers import audiobook
    from services import longform_resume

    init_db()

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def transport_wait():
            entered.set()
            await release.wait()
            return False

        stream = audiobook._render_longform_sse(
            _plan(("One", "First chapter.")), default_voice=None,
            is_disconnected=transport_wait,
        )
        event = json.loads((await anext(stream))[len("data:"):].strip())
        assert event["type"] == "started"
        job_id = event["job_id"]
        assert job_store.get(job_id)["status"] == "running"
        if close_kind == "cancel":
            task = asyncio.create_task(anext(stream))
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await stream.aclose()
        assert job_store.get(job_id)["status"] == "cancelled"
        assert longform_resume.has_manifest("audiobook", job_id)

    asyncio.run(run())


@pytest.mark.parametrize("endpoint", ["audiobook", "longform", "resume"])
@pytest.mark.parametrize("abort_kind", ["task_cancel", "send_error", "old_disconnect", "anyio_cancel"])
def test_public_http_transport_cancel_retires_running_job(endpoint, abort_kind):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg required to reach the native renderer lifecycle")
    from fastapi import FastAPI
    from api.routers.audiobook import router
    from core import job_store
    from core.db import init_db
    from services import longform_resume
    from starlette.requests import ClientDisconnect
    import anyio

    init_db()
    app = FastAPI()
    app.include_router(router)
    events = []
    path = "/audiobook"
    payload = {"text": "# One\nFirst chapter."}
    if endpoint == "longform":
        path = "/longform/render"
        payload = {"chapters": [{"title": "One", "spans": [{"text": "First chapter."}]}]}
    elif endpoint == "resume":
        longform_resume.write_manifest(longform_resume.build_manifest(
            job_id="previous", job_type="audiobook", title="One",
            plan_chapters=[{"title": "One", "spans": [{"voice_id": None, "text": "First chapter."}]}],
            params={"default_voice": None}))
        path = "/audiobook/resume/previous"
        payload = {}

    async def run():
        request_body = json.dumps(payload).encode()
        received_request = False
        started = asyncio.Event()

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                events.append(json.loads(message["body"].decode()[len("data:"):].strip()))
                started.set()
                if abort_kind == "task_cancel":
                    raise asyncio.CancelledError
                if abort_kind == "send_error":
                    raise OSError("Client connection closed")
                if abort_kind == "anyio_cancel":
                    cancel_scope.cancel()
                    await anyio.sleep(0)
                await asyncio.Future()  # Old ASGI disconnect listener cancels this task.

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3" if abort_kind == "old_disconnect" else "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": [(b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
        if abort_kind == "anyio_cancel":
            with anyio.CancelScope() as cancel_scope:
                await app(scope, receive, send)
        elif abort_kind == "old_disconnect":
            await app(scope, receive, send)
        else:
            with pytest.raises(asyncio.CancelledError if abort_kind == "task_cancel" else ClientDisconnect):
                await app(scope, receive, send)
        # Assert BEFORE event-loop shutdown can finalize any lazy iterators.
        assert [e["type"] for e in events] == ["started"]
        job_id = events[0]["job_id"]
        assert job_store.get(job_id)["status"] == "cancelled"
        assert longform_resume.has_manifest("story" if endpoint == "longform" else "audiobook", job_id)
        if endpoint == "resume":
            assert longform_resume.has_manifest("audiobook", "previous")

    asyncio.run(run())


@pytest.mark.parametrize("terminal", ["done", "failed"])
def test_closed_stream_preserves_existing_terminal_status(terminal):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg required to reach the native renderer lifecycle")
    from api.routers import audiobook
    from core import job_store
    from core.db import init_db

    init_db()

    async def run():
        stream = audiobook._render_longform_sse(
            _plan(("One", "First chapter.")), default_voice=None,
        )
        event = json.loads((await anext(stream))[len("data:"):].strip())
        assert event["type"] == "started"
        job_id = event["job_id"]
        # Finalization can run after another owner recorded the terminal state.
        if terminal == "done":
            job_store.mark_done(job_id)
        else:
            job_store.mark_failed(job_id, "Stopped by the owning job")
        await stream.aclose()
        assert job_store.get(job_id)["status"] == terminal

    asyncio.run(run())


@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg required to reach native render lifecycle")
def test_failed_render_keeps_failed_status(monkeypatch):
    from api.routers import audiobook
    from core import job_store
    from core.db import init_db
    from services import longform_resume

    init_db()

    def factory(*args, **kwargs):
        spec = _stub_build_synth()(*args, **kwargs)
        def unavailable_model(*_args, **_kwargs):
            raise RuntimeError("Local synthesis unavailable")
        spec["synth"] = unavailable_model
        return spec

    monkeypatch.setattr(audiobook, "_build_synth", factory)  # External synthesis boundary only.

    async def run():
        return [json.loads(frame[len("data:"):].strip())
                async for frame in audiobook._render_longform_sse(
                    _plan(("One", "Unique failed-render control.")), default_voice=None)]

    events = asyncio.run(run())
    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "error"
    job_id = events[0]["job_id"]
    assert job_store.get(job_id)["status"] == "failed"
    assert longform_resume.has_manifest("audiobook", job_id)


@pytest.mark.parametrize("terminal", [None, "done", "failed"])
def test_public_iterator_closure_retires_job_before_loop_shutdown(terminal):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg required to reach native render lifecycle")
    from api.routers import audiobook
    from core import job_store
    from core.db import init_db
    from services import longform_resume
    init_db()

    async def run():
        stream = audiobook._public_longform_stream(
            _plan(("One", "First chapter.")), default_voice=None)
        event = json.loads((await anext(stream))[len("data:"):].strip())
        assert event["type"] == "started"
        job_id = event["job_id"]
        assert job_store.get(job_id)["status"] == "running"
        if terminal == "done":
            job_store.mark_done(job_id)
        elif terminal == "failed":
            job_store.mark_failed(job_id, "Stopped by the owning job")
        await stream.aclose()
        # Check while this loop and the inner generator are still alive.
        assert job_store.get(job_id)["status"] == (terminal or "cancelled")
        assert longform_resume.has_manifest("audiobook", job_id)

    asyncio.run(run())


@pytest.mark.parametrize("case", ["empty_chapters", "empty_spans", "missing_ffmpeg"])
def test_finite_public_setup_error_retires_job_and_keeps_checkpoint(case, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.audiobook import router
    from core import job_store
    from core.db import init_db
    from services import longform_resume
    init_db()
    app = FastAPI()
    app.include_router(router)
    payload = {"chapters": []}
    if case == "empty_spans":
        payload = {"chapters": [{"title": "One", "spans": [{"text": ""}]}]}
    elif case == "missing_ffmpeg":
        payload = {"chapters": [{"title": "One", "spans": [{"text": "Hello."}]}]}
        # External executable availability only; routes, history and checkpoint
        # processing are native. No synthesis is reached on this setup path.
        monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)
    previous = {row["id"] for row in job_store.list_jobs(limit=100000)}
    client = TestClient(app, client=("127.0.0.1", 50000))
    try:
        response = client.post("/longform/render", json=payload)
    finally:
        client.close()
    assert response.status_code == 200
    frames = [json.loads(frame[len("data:"):].strip())
              for frame in response.text.strip().split("\n\n")]
    assert len(frames) == 1 and frames[0]["type"] == "error"
    expected = "ffmpeg not available" if case == "missing_ffmpeg" else "nothing to render"
    assert expected in frames[0]["error"]
    jobs = [row for row in job_store.list_jobs(limit=100000) if row["id"] not in previous]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["finished_at"] is not None
    assert jobs[0]["error"] == frames[0]["error"]
    assert longform_resume.has_manifest("story", jobs[0]["id"])


def test_actual_cache_directory_error_retires_public_job(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.audiobook import router
    from core import job_store
    from core.db import init_db
    from services.longform_render import LONGFORM_CACHE_SUBDIR
    from services import longform_resume
    init_db()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / LONGFORM_CACHE_SUBDIR).write_text("A file occupies the cache directory name")
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(outputs))
    previous = {row["id"] for row in job_store.list_jobs(limit=100000)}
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, client=("127.0.0.1", 50000))
    try:
        response = client.post("/longform/render", json={"chapters": [{"title": "One", "spans": [{"text": "Hello."}]}]})
    finally:
        client.close()
    assert response.status_code == 200
    assert '"type": "error"' in response.text
    assert "cache directory" not in response.text  # Setup exception details stay local.
    jobs = [row for row in job_store.list_jobs(limit=100000) if row["id"] not in previous]
    assert len(jobs) == 1 and jobs[0]["status"] == "failed"
    assert jobs[0]["finished_at"] is not None
    assert longform_resume.has_manifest("story", jobs[0]["id"])
