"""yt-dlp must not depend on a working stdout/stderr (Errno 22 on Windows, Errno 32 on macOS).

`quiet` alone still draws the progress bar on stdout and prints errors to
stderr. The packaged app's stdio can be a dead pipe, so the options must set
`noprogress` and a `logger`, and yt-dlp must then survive broken streams.
"""
import errno
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services import dub_pipeline  # noqa: E402


class _DeadStream:
    encoding = "utf-8"

    def write(self, *_):
        raise OSError(errno.EPIPE, "Broken pipe")

    def flush(self):
        raise OSError(errno.EPIPE, "Broken pipe")

    def isatty(self):
        return False


def _captured_opts(tmp_path, monkeypatch):
    import yt_dlp

    captured = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            raise RuntimeError("stop after capturing opts")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    with pytest.raises(Exception):
        dub_pipeline.yt_download_sync("https://youtu.be/abc", str(tmp_path))
    return captured


def test_download_opts_silence_stdio(tmp_path, monkeypatch):
    opts = _captured_opts(tmp_path, monkeypatch)
    assert opts["noprogress"] is True
    assert opts["logger"] is not None


def test_ytdlp_output_survives_broken_stdio(tmp_path, monkeypatch):
    opts = _captured_opts(tmp_path, monkeypatch)
    monkeypatch.undo()  # restore the real YoutubeDL
    import yt_dlp
    from yt_dlp.downloader import FileDownloader

    monkeypatch.setattr(sys, "stdout", _DeadStream())
    monkeypatch.setattr(sys, "stderr", _DeadStream())
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.to_screen("[download] Destination: x")
        ydl.to_stderr("ERROR: boom")
        ydl.report_error("boom", is_error=False)
        fd = FileDownloader(ydl, opts)
        fd._prepare_multiline_status()
        fd.report_progress({
            "status": "downloading", "downloaded_bytes": 5, "total_bytes": 10,
            "elapsed": 1.0, "speed": 5.0, "eta": 1, "filename": "x", "info_dict": {},
        })
        fd._finish_multiline_status()
