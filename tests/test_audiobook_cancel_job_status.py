"""A cancelled or closed audiobook response retires its still-active job (#2536)."""
import asyncio
import json
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from services.audiobook import parse_audiobook_script  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    from api.routers import audiobook
    from core import config, db, job_store
    from services import longform_resume

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(longform_resume, "work_dir",
                        lambda t, j: str(tmp_path / "work" / f"{t}-{j}"))
    monkeypatch.setattr(audiobook, "_resolve_default_language", lambda *_a: None)
    db.init_db()
    return audiobook, job_store


def _status(job_store, job_id):
    return job_store.get(job_id)["status"]


def _stream(audiobook, job_id, block):
    return audiobook._public_longform_stream(
        parse_audiobook_script("# One\n\nHello there.\n", default_voice=None),
        default_voice=None, job_id=job_id, job_type="audiobook",
        is_disconnected=block,
    )


def _blocker():
    gate = asyncio.Event()

    async def wait_forever():
        await gate.wait()
        return False

    return wait_forever


def test_closing_the_iterator_retires_the_job_as_cancelled(env):
    audiobook, job_store = env

    async def run():
        stream = _stream(audiobook, "closejob", _blocker())
        first = await stream.__anext__()
        assert json.loads(first[len("data: "):])["type"] == "started"
        assert _status(job_store, "closejob") == "running"
        await stream.aclose()

    asyncio.run(run())
    assert _status(job_store, "closejob") == "cancelled"


def test_cancelling_while_awaiting_disconnect_propagates_and_retires(env):
    audiobook, job_store = env

    async def run():
        stream = _stream(audiobook, "canceljob", _blocker())
        await stream.__anext__()
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)  # now parked inside is_disconnected()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(run())
    assert _status(job_store, "canceljob") == "cancelled"


@pytest.mark.parametrize("terminal", ["done", "failed", "cancelled"])
def test_terminal_jobs_keep_their_status(env, terminal):
    audiobook, job_store = env

    async def run():
        stream = _stream(audiobook, "termjob", _blocker())
        await stream.__anext__()
        if terminal == "failed":
            job_store.mark_failed("termjob", "boom")
        else:
            getattr(job_store, f"mark_{terminal}")("termjob")
        await stream.aclose()

    asyncio.run(run())
    assert _status(job_store, "termjob") == terminal


def test_asgi_transport_cancellation_retires_the_job(env, monkeypatch):
    """The reported path: the HTTP task is cancelled after `started` is sent."""
    audiobook, job_store = env
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(audiobook.router)
    seen: dict = {}
    parked = asyncio.Event  # a synthesis that never finishes: no model is loaded

    async def stuck_chapter(*_a, **_kw):
        await parked().wait()

    monkeypatch.setattr(audiobook, "_run_chapter", stuck_chapter)

    async def run():
        started = asyncio.Event()
        never = asyncio.Event()
        body = json.dumps({"text": "# One\n\nHello there.\n"}).encode()
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await never.wait()  # the transport never reports a disconnect itself

        async def send(message):
            if message["type"] == "http.response.body" and b"started" in message.get("body", b""):
                seen["job_id"] = json.loads(message["body"].decode()[len("data: "):])["job_id"]
                started.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": "/audiobook", "raw_path": b"/audiobook",
            "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
            "client": ("127.0.0.1", 1), "server": ("test", 80),
        }
        task = asyncio.ensure_future(app(scope, receive, send))
        await asyncio.wait_for(started.wait(), 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert _status(job_store, seen["job_id"]) == "cancelled"


def test_pre_render_refusal_does_not_leave_the_job_running(env, monkeypatch):
    audiobook, job_store = env
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: None)

    async def run():
        stream = _stream(audiobook, "nofmpeg", None)
        return [e async for e in stream]

    events = asyncio.run(run())
    assert "ffmpeg not available" in events[-1]
    assert _status(job_store, "nofmpeg") == "failed"
