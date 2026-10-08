"""Explicit, ephemeral YouTube authentication for URL ingest (#1429/#1432)."""
import asyncio
import importlib
import os
import stat
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

COOKIE_TEXT = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"


@pytest.fixture
def dub_core():
    """Import the application router only when a test needs it."""
    return importlib.import_module("api.routers.dub_core")


@pytest.fixture
def dub_pipeline():
    """Import the application pipeline only when a test needs it."""
    return importlib.import_module("services.dub_pipeline")


def test_cookie_export_requires_deliberate_netscape_file_and_is_private(dub_core):
    with pytest.raises(HTTPException) as exc:
        dub_core._stage_cookie_export('{"cookies": []}')
    assert exc.value.status_code == 400

    path = dub_core._stage_cookie_export(COOKIE_TEXT)
    try:
        with open(path, encoding="utf-8") as cookie_file:
            assert cookie_file.read() == COOKIE_TEXT
        if os.name != "nt":
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    finally:
        os.unlink(path)


def test_cookie_export_accepts_a_bom_and_rejects_empty_or_oversized_files(dub_core):
    path = dub_core._stage_cookie_export("\ufeff" + COOKIE_TEXT)
    try:
        assert os.path.exists(path)
    finally:
        os.unlink(path)

    for contents in ("", "# Netscape HTTP Cookie File\n" + "x" * (1024 * 1024)):
        with pytest.raises(HTTPException) as exc:
            dub_core._stage_cookie_export(contents)
        assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("scheme", "host", "origin", "allowed"),
    [
        ("http", "127.0.0.1", "http://localhost:3901", True),
        # The retired Tauri webview origin is no longer a trusted local UI.
        ("http", "127.0.0.1", "http://tauri.localhost", False),
        ("http", "::1", "http://localhost:3901", True),
        ("https", "192.0.2.20", "https://studio.example", True),
        ("http", "192.0.2.20", "http://localhost", False),
        ("http", "127.0.0.1", "http://studio.example", False),
        ("http", "127.0.0.1", None, False),
    ],
)
def test_cookie_credentials_only_cross_https_or_local_ui(
    dub_core, scheme, host, origin, allowed
):
    assert dub_core._cookie_transport_allowed(scheme, host, origin) is allowed


def test_cookie_export_is_forwarded_to_ytdlp(dub_pipeline, tmp_path, monkeypatch):
    import yt_dlp

    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def add_post_processor(self, *_args, **_kwargs):
            pass  # the media-URL guard; not exercised by this fake

        def extract_info(self, _url, download=True):
            raise RuntimeError("stop after capturing options")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    cookie_path = str(tmp_path / "cookies.txt")
    with pytest.raises(RuntimeError):
        dub_pipeline.yt_download_sync(
            "https://youtube.com/watch?v=abc",
            str(tmp_path),
            cookie_file=cookie_path,
        )

    assert captured["cookiefile"] == cookie_path


def test_pipeline_deletes_cookie_export_after_download_failure(
    dub_pipeline, tmp_path, monkeypatch
):
    cookie_path = tmp_path / "session.cookies.txt"
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")

    def fail_download(*_args, **_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(dub_pipeline, "yt_download_sync", fail_download)
    async def collect_events():
        events = []
        async for event in dub_pipeline.ingest_pipeline(
            "cookie-cleanup",
            str(tmp_path / "job"),
            {
                "kind": "url",
                "url": "https://youtube.com/watch?v=abc",
                "cookie_file": str(cookie_path),
            },
        ):
            events.append(event)
        return events

    events = asyncio.run(collect_events())

    assert any('"type": "error"' in event for event in events)
    assert "secret" not in "".join(events)
    assert not cookie_path.exists()


def test_cookie_cleanup_is_idempotent(dub_pipeline, tmp_path):
    cookie_path = tmp_path / "session.cookies.txt"
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")
    dub_pipeline._delete_cookie_export(str(cookie_path))
    dub_pipeline._delete_cookie_export(str(cookie_path))
    assert not cookie_path.exists()


def test_pipeline_cancellation_deletes_cookie_before_download(dub_pipeline, tmp_path):
    cookie_path = tmp_path / "cancel.cookies.txt"
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")

    async def start_then_cancel():
        pipeline = dub_pipeline.ingest_pipeline(
            "cookie-cancel",
            str(tmp_path / "job-cancel"),
            {
                "kind": "url",
                "url": "https://youtube.com/watch?v=abc",
                "cookie_file": str(cookie_path),
            },
        )
        await anext(pipeline)
        await pipeline.aclose()

    asyncio.run(start_then_cancel())
    assert not cookie_path.exists()


def test_enqueue_failure_deletes_staged_cookie(dub_core, tmp_path, monkeypatch):
    from schemas.requests import DubIngestUrlRequest
    from starlette.requests import Request

    cookie_path = tmp_path / "queued.cookies.txt"
    monkeypatch.setattr(dub_core, "_stage_cookie_export", lambda _text: str(cookie_path))
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")
    monkeypatch.setattr(dub_core, "_safe_job_dir", lambda _job_id: str(tmp_path / "job"))

    async def fail_add(*_args, **_kwargs):
        raise RuntimeError("queue closed")

    monkeypatch.setattr(dub_core.task_manager, "add_task", fail_add)
    request = Request(
        {"type": "http", "scheme": "http", "server": ("127.0.0.1", 80),
         "client": ("127.0.0.1", 1234), "path": "/dub/ingest-url",
         "headers": [(b"origin", b"http://localhost:3901")]}
    )
    with pytest.raises(RuntimeError, match="queue closed"):
        asyncio.run(
            dub_core.dub_ingest_url(
                DubIngestUrlRequest(
                    url="https://youtube.com/watch?v=abc", cookie_file=COOKIE_TEXT
                ),
                request,
            )
        )
    assert not cookie_path.exists()


def test_job_directory_failure_happens_before_cookie_staging(
    dub_core, tmp_path, monkeypatch
):
    from schemas.requests import DubIngestUrlRequest
    from starlette.requests import Request

    staged = False

    def stage_cookie(_text):
        nonlocal staged
        staged = True
        return str(tmp_path / "should-not-exist.cookies.txt")

    monkeypatch.setattr(dub_core, "_stage_cookie_export", stage_cookie)
    monkeypatch.setattr(
        dub_core, "_safe_job_dir", lambda _job_id: str(tmp_path / "job")
    )

    def fail_makedirs(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dub_core.os, "makedirs", fail_makedirs)
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("127.0.0.1", 80),
            "client": ("127.0.0.1", 1234),
            "path": "/dub/ingest-url",
            "headers": [(b"origin", b"http://localhost:3901")],
        }
    )

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            dub_core.dub_ingest_url(
                DubIngestUrlRequest(
                    url="https://youtube.com/watch?v=abc", cookie_file=COOKIE_TEXT
                ),
                request,
            )
        )
    assert staged is False


def test_failed_cookie_unlink_can_be_retried(dub_pipeline, tmp_path, monkeypatch):
    cookie_path = tmp_path / "retry.cookies.txt"
    cookie_path.write_text(COOKIE_TEXT, encoding="utf-8")
    real_unlink = os.unlink
    attempts = 0

    def flaky_unlink(path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("temporarily busy")
        real_unlink(path)

    monkeypatch.setattr(dub_pipeline.os, "unlink", flaky_unlink)
    assert dub_pipeline._delete_cookie_export(str(cookie_path)) is False
    assert cookie_path.exists()
    assert dub_pipeline._delete_cookie_export(str(cookie_path)) is True
    assert not cookie_path.exists()
