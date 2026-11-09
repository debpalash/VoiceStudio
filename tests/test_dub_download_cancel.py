"""Cancelling a URL import stops yt-dlp's download thread before cleanup.

The task runner closes the ingest generator on any cancel (app, CLI or API),
but the download runs in a worker thread that only setting the cancel flag
never reached: it kept downloading and reading the cookie export while the
pipeline deleted that file and dropped the job's process records.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from services import dub_pipeline as dp


class _LongYDL:
    """yt-dlp stand-in: a download that keeps writing until a hook stops it."""

    def __init__(self, opts):
        self.opts = opts
        self.job_dir = os.path.dirname(opts["outtmpl"])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_post_processor(self, *_a, **_k):
        pass

    def extract_info(self, url, download=True):
        part = os.path.join(self.job_dir, "original.mp4.part")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with open(part, "ab") as f:
                f.write(b"x" * 64)
            for hook in self.opts.get("progress_hooks") or []:
                hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            time.sleep(0.01)
        raise AssertionError("the download was never told to stop")

    def prepare_filename(self, info):
        return os.path.join(self.job_dir, "original.mp4")


@pytest.fixture
def long_download(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _LongYDL)
    monkeypatch.setattr(dp, "_ensure_browser_playable_mp4", lambda p: p)


def test_cancel_event_stops_the_download_and_removes_partial_files(long_download, tmp_path):
    from yt_dlp.utils import DownloadCancelled

    stop = threading.Event()
    outcome = {}

    def run():
        try:
            dp.yt_download_sync("https://example.com/v", str(tmp_path), cancel_event=stop)
        except BaseException as exc:  # noqa: BLE001 — recorded for the assertion
            outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    while not list(tmp_path.glob("original.*")) and worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert list(tmp_path.glob("original.*")), outcome
    stop.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), DownloadCancelled)
    assert not list(tmp_path.glob("original.*"))


def test_closing_the_ingest_stops_the_download_before_removing_the_cookie(monkeypatch, tmp_path):
    running = threading.Event()
    stopped = threading.Event()
    observed = {}

    def fake_download(url, job_dir, *, cancel_event=None, progress_hook=None, **_kwargs):
        os.makedirs(job_dir, exist_ok=True)
        part = os.path.join(job_dir, "original.mp4.part")
        running.set()
        deadline = time.monotonic() + 5
        written = 0
        while time.monotonic() < deadline and not (cancel_event and cancel_event.is_set()):
            with open(part, "ab") as f:
                f.write(b"x")
            written += 1
            progress_hook({"status": "downloading", "downloaded_bytes": written % 100, "total_bytes": 100})
            time.sleep(0.01)
        stopped.set()
        raise RuntimeError("Download cancelled")

    real_delete = dp._delete_cookie_export

    def delete_cookie(path):
        observed.setdefault("download_stopped_first", stopped.is_set())
        return real_delete(path)

    monkeypatch.setattr(dp, "yt_download_sync", fake_download)
    monkeypatch.setattr(dp, "_delete_cookie_export", delete_cookie)
    cookie = tmp_path / "session.cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    job_dir = tmp_path / "job"

    async def start_then_cancel():
        pipeline = dp.ingest_pipeline("cancel-download", str(job_dir), {
            "kind": "url", "url": "https://example.com/v", "cookie_file": str(cookie)})
        assert '"download_start"' in await anext(pipeline)
        # The task runner checks the cancel flag when the next update arrives,
        # then closes the generator: the same for the app, the CLI and the API.
        assert '"download_progress"' in await asyncio.wait_for(anext(pipeline), 5)
        assert running.is_set() and not stopped.is_set()
        await pipeline.aclose()

    asyncio.run(start_then_cancel())
    assert observed["download_stopped_first"] is True
    assert not cookie.exists()
    assert not list(job_dir.glob("original.*"))
