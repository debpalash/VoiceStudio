"""Download-retry on transient broken-pipe failures (#579 / #598).

`download: Unable to download video: [Errno 32] Broken pipe` was a hard failure:
yt-dlp's own per-fragment retries don't cover a broken pipe raised while the
write side of a pipe closes mid-mux (a killed ffmpeg child / a CDN reset during
muxing), so a single transient blip aborted the whole ingest with a raw
"Broken pipe" string.

These assert the fix:
  * `_is_transient_download_error` classifies broken-pipe / network-drop
    failures as retryable, reusing the single `VIDEO_DOWNLOAD_NETWORK` taxonomy.
  * `yt_download_sync` retries a transient failure and recovers on a later
    attempt, cleaning up the partial download in between.
  * a non-transient failure (unsupported URL) is NOT retried — the existing
    unsupported-link vs network-drop hinting must not regress.

RED before the fix (no retry loop existed → first transient error propagated).
"""
from __future__ import annotations

import os

import pytest

from services import dub_pipeline as dp
from core import failure


# ── unit: retryability classification ───────────────────────────────────────

def test_broken_pipe_is_transient():
    # The exact message from #579/#598.
    exc = OSError("[Errno 32] Broken pipe")
    assert dp._is_transient_download_error(exc) is True
    # A yt-dlp DownloadError-style wrap with the same suffix.
    assert dp._is_transient_download_error(
        RuntimeError("Unable to download video: [Errno 32] Broken pipe")
    ) is True
    # BrokenPipeError matched by class even with a stripped message.
    assert dp._is_transient_download_error(BrokenPipeError()) is True
    assert dp._is_transient_download_error(ConnectionResetError()) is True


def test_youtube_reload_request_is_transient():
    reported = RuntimeError(
        "ERROR: [youtube] qQjZNdURxzg: The page needs to be reloaded."
    )
    assert failure.classify(str(reported)) == "VIDEO_DOWNLOAD_NETWORK"
    assert dp._is_transient_download_error(reported) is True


def test_unsupported_url_is_not_transient():
    # Must classify as UNSUPPORTED (more specific) and therefore NOT retry —
    # otherwise we'd waste 3 attempts on a link that can never download.
    exc = RuntimeError("Unsupported URL: https://www.douyin.com/discover")
    assert failure.classify(str(exc)) == "UNSUPPORTED_VIDEO_URL"
    assert dp._is_transient_download_error(exc) is False


def test_generic_error_is_not_transient():
    assert dp._is_transient_download_error(RuntimeError("totally unrelated")) is False


# ── integration: yt_download_sync retry/recovery ────────────────────────────

class _FakeYDL:
    """Minimal yt_dlp.YoutubeDL stand-in driven by a shared call counter."""

    # class-level state so each constructed instance shares the script
    behaviors: list = []
    calls: list = []
    job_dir: str = ""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_post_processor(self, *_args, **_kwargs):
        pass  # the media-URL guard; not exercised by this fake

    def extract_info(self, url, download=True):
        idx = len(_FakeYDL.calls)
        _FakeYDL.calls.append(url)
        behavior = _FakeYDL.behaviors[idx]
        # Simulate a partial download being written before the failure, so the
        # cleanup path has something to remove.
        with open(os.path.join(_FakeYDL.job_dir, "original.part"), "w") as f:
            f.write("partial")
        if isinstance(behavior, BaseException):
            raise behavior
        # Success: yt-dlp renames its `.part` to the final file. Mirror that so
        # the test asserts on real post-success state.
        out = os.path.join(_FakeYDL.job_dir, "original.mp4")
        os.replace(os.path.join(_FakeYDL.job_dir, "original.part"), out)
        return {"title": "Clip", "_filename": out}

    def prepare_filename(self, info):
        return os.path.join(_FakeYDL.job_dir, "original.mp4")


@pytest.fixture
def _patched_ytdlp(monkeypatch, tmp_path):
    import yt_dlp

    _FakeYDL.calls = []
    _FakeYDL.job_dir = str(tmp_path)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    # No real codec transcode / browser-playability probe in tests.
    monkeypatch.setattr(dp, "_ensure_browser_playable_mp4", lambda p: p)
    # Skip the real backoff sleep so the test is instant.
    monkeypatch.setattr(dp.time, "sleep", lambda *_a, **_k: None)
    return tmp_path


def test_retries_then_recovers_on_broken_pipe(_patched_ytdlp):
    job_dir = str(_patched_ytdlp)
    # Fail twice with a broken pipe, then succeed.
    _FakeYDL.behaviors = [
        OSError("[Errno 32] Broken pipe"),
        OSError("[Errno 32] Broken pipe"),
        None,
    ]
    video_path, title, subs = dp.yt_download_sync("https://example.com/v", job_dir)
    assert len(_FakeYDL.calls) == 3, "should retry twice then succeed"
    assert os.path.basename(video_path) == "original.mp4"
    assert title == "Clip"
    # The partial file from the failed attempts must have been cleaned up.
    assert not os.path.exists(os.path.join(job_dir, "original.part"))


def test_gives_up_after_bounded_retries(_patched_ytdlp):
    job_dir = str(_patched_ytdlp)
    # Always broken pipe — exhaust the bounded retries, then surface the error.
    _FakeYDL.behaviors = [OSError("[Errno 32] Broken pipe")] * 10
    with pytest.raises(OSError) as ei:
        dp.yt_download_sync("https://example.com/v", job_dir)
    assert "broken pipe" in str(ei.value).lower()
    # Bounded: exactly 1 + _YT_DOWNLOAD_RETRIES attempts, not infinite.
    assert len(_FakeYDL.calls) == dp._YT_DOWNLOAD_RETRIES + 1
    # The classified failure still carries the actionable network hint.
    evt = failure.build_failure(ei.value, stage="download", include_diagnostic=False)
    assert evt["docs_topic"] == "VIDEO_DOWNLOAD_NETWORK"
    assert evt["hint"]


def test_reload_request_exhaustion_keeps_network_guidance(_patched_ytdlp):
    job_dir = str(_patched_ytdlp)
    message = "ERROR: [youtube] qQjZNdURxzg: The page needs to be reloaded."
    _FakeYDL.behaviors = [RuntimeError(message)] * 10

    with pytest.raises(RuntimeError) as ei:
        dp.yt_download_sync("https://youtube.com/watch?v=qQjZNdURxzg", job_dir)

    assert len(_FakeYDL.calls) == dp._YT_DOWNLOAD_RETRIES + 1
    evt = failure.build_failure(ei.value, stage="download", include_diagnostic=False)
    assert evt["docs_topic"] == "VIDEO_DOWNLOAD_NETWORK"
    assert evt["hint"]


def test_does_not_retry_unsupported_url(_patched_ytdlp):
    job_dir = str(_patched_ytdlp)
    # A non-downloadable link must fail fast (no wasted retries).
    _FakeYDL.behaviors = [RuntimeError("Unsupported URL: https://x/feed")] * 10
    with pytest.raises(RuntimeError):
        dp.yt_download_sync("https://x/feed", job_dir)
    assert len(_FakeYDL.calls) == 1, "unsupported URL must not be retried"
