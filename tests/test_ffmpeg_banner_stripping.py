"""#1309: an ffmpeg failure reported its build configuration, not the fault.

ffmpeg and ffprobe print a version + configuration banner to STDERR on every
invocation, before doing any work. When a command fails we capture that stderr
and it becomes the error message, so the reporter saw:

    extract: ffmpeg version N-125781-gacf6b520c1-20260727 Copyright (c) 2000-2026 …
      built with gcc 15.2.0 (crosstool-NG …)
      configuration: --prefix=/ffbuild/prefix --pkg-config-flags=--static …

— several hundred characters of build flags, and not one word about why the
extract failed. The diagnosis is always *after* the banner.
"""
from __future__ import annotations

import pytest

BANNER = """ffmpeg version N-125781-gacf6b520c1-20260727 Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 15.2.0 (crosstool-NG 1.28.0.23_185f348)
  configuration: --prefix=/ffbuild/prefix --pkg-config-flags=--static --enable-gpl
  libavutil      59. 39.100 / 59. 39.100
  libavcodec     61. 19.100 / 61. 19.100
"""


def _strip(text):
    from core.failure import strip_ffmpeg_banner

    return strip_ffmpeg_banner(text)


def test_real_error_survives_and_banner_does_not():
    real = "Output file #0 does not contain any stream"
    out = _strip(BANNER + real)
    assert out == real
    assert "configuration:" not in out
    assert "N-125781" not in out


def test_multi_line_error_is_kept_whole():
    real = "Input #0, mov,mp4:\n  Stream #0:0: Video: h264\nNo audio stream found"
    out = _strip(BANNER + real)
    assert out == real


def test_ffprobe_banner_too():
    out = _strip(BANNER.replace("ffmpeg version", "ffprobe version") + "boom")
    assert out == "boom"


def test_prefixed_homebrew_banner_keeps_wrapper_and_real_error():
    """Dub wraps stderr after decoding it, so the banner is not at byte zero."""
    homebrew = BANNER.replace(
        "N-125781-gacf6b520c1-20260727", "9.0.2"
    ).replace("built with gcc 15.2.0", "built with Apple clang version 21.0.0")
    out = _strip(
        "FFmpeg exited with code 1: "
        + homebrew
        + "Error opening input file sample.mp4"
    )
    assert out == "FFmpeg exited with code 1:\nError opening input file sample.mp4"
    assert "configuration:" not in out
    assert "Apple clang" not in out


def test_banner_only_message_is_left_alone():
    """A message that is *nothing but* banner is unhelpful — an empty one is
    worse. Fail loud with something rather than silently with nothing."""
    out = _strip(BANNER)
    assert out.strip()
    assert "ffmpeg version" in out


@pytest.mark.parametrize("value", ["", None, "plain failure, no ffmpeg here"])
def test_non_ffmpeg_text_is_untouched(value):
    assert _strip(value) == (value or "")


def test_build_failure_reports_the_error_not_the_build_flags():
    """End-to-end through the function the dub pipeline actually calls."""
    from core.failure import build_failure

    fields = build_failure(
        RuntimeError(BANNER + "Invalid data found when processing input"),
        stage="extract",
    )
    assert "Invalid data found" in fields["reason"]
    assert "configuration:" not in fields["reason"]
    assert "crosstool-NG" not in fields["reason"]


def test_classification_sees_the_error_not_the_banner():
    """classify() runs on the same text; matching against a build string is how
    a real topic gets missed.

    Asserts the BANNER IS GONE, not merely that the error is present. The first
    version of this test only checked that the post-banner text appeared in
    `reason` — which was true before the fix too, since the banner was simply
    prepended to it. A test that passes against the code it is meant to catch
    is worse than no test (CodeRabbit).
    """
    from core.failure import build_failure

    fields = build_failure(
        RuntimeError(BANNER + "No space left on device"), stage="extract"
    )
    assert "No space left on device" in fields["reason"]
    assert "ffmpeg version" not in fields["reason"]
    assert "libavcodec" not in fields["reason"]
    # And the reason should START with the real error, not bury it after 300
    # characters of build flags — that ordering is the whole user complaint.
    assert fields["reason"].strip().startswith("No space left on device")
