"""Hardsub burn-in must pass a Windows caption path ffmpeg can open.

ffmpeg's ``subtitles=`` / ``ass=`` filters treat ``:`` as an option
separator and do not open backslash paths reliably. The helper used to
double every backslash and then escape the drive colon, so a quoted
filter value became ``C\\:\\Users\\...\\file.srt`` and libass looked for
a file that is not on disk. Burned-in line and karaoke captions then
failed the export (or shipped with no on-screen text) on Windows, while
POSIX paths happened to work because they have neither ``\\`` nor ``:``.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")


WIN_CAPTION = r"C:\Users\Ada\omnivoice_data\dub_jobs\j1\exports\burn_subs_x.srt"
WIN_ESCAPED = r"C\:/Users/Ada/omnivoice_data/dub_jobs/j1/exports/burn_subs_x.srt"


def test_windows_drive_path_uses_forward_slashes_and_escaped_colon():
    """The form ffmpeg's subtitles/ass filters actually open on Windows."""
    from api.routers.dub_export import _ffmpeg_filter_escape

    assert _ffmpeg_filter_escape(WIN_CAPTION) == WIN_ESCAPED


def test_windows_path_inside_quoted_filter_has_no_doubled_backslashes():
    from api.routers.dub_export import _ffmpeg_filter_escape

    esc = _ffmpeg_filter_escape(WIN_CAPTION)
    graph = f"[0:v]subtitles='{esc}'[vsub]"
    assert "\\\\" not in graph
    assert graph == f"[0:v]subtitles='{WIN_ESCAPED}'[vsub]"


def test_posix_caption_path_is_unchanged():
    from api.routers.dub_export import _ffmpeg_filter_escape

    path = "/tmp/exports/burn_subs_x.srt"
    assert _ffmpeg_filter_escape(path) == path


def test_apostrophe_in_caption_path_is_escaped():
    from api.routers.dub_export import _ffmpeg_filter_escape

    assert _ffmpeg_filter_escape("/tmp/O'Brien.srt") == r"/tmp/O'\\\''Brien.srt"


def test_already_forward_slashed_windows_path_only_escapes_the_colon():
    from api.routers.dub_export import _ffmpeg_filter_escape

    assert _ffmpeg_filter_escape("C:/Users/Ada/burn_subs_x.ass") == r"C\:/Users/Ada/burn_subs_x.ass"


@pytest.mark.skipif(os.name == "nt", reason="literal backslashes are POSIX filenames")
def test_posix_literal_backslash_is_not_a_path_separator():
    from api.routers.dub_export import _ffmpeg_filter_escape

    assert _ffmpeg_filter_escape(r"/tmp/exports/O\Brien.srt") == r"/tmp/exports/O\\Brien.srt"


@pytest.mark.parametrize("filename", ["O'Brien.srt", r"O\Brien.srt", "O:Brien.srt"])
def test_ffmpeg_opens_quoted_caption_filename(tmp_path, filename):
    """Verify both filter parsers resolve the filename, not merely its escaped text."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    if os.name == "nt" and ("\\" in filename or ":" in filename):
        pytest.skip("Windows filenames cannot contain backslashes or colons")
    from api.routers.dub_export import _ffmpeg_filter_escape

    caption = tmp_path / filename
    caption.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    graph = f"subtitles='{_ffmpeg_filter_escape(str(caption))}'"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=s=64x64:r=1:d=1",
            "-vf", graph, "-frames:v", "1", "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
