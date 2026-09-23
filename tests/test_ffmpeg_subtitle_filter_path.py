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

os.environ.setdefault("OMNIVOICE_MODEL", "test")

from api.routers.dub_export import _ffmpeg_filter_escape


WIN_CAPTION = r"C:\Users\Ada\omnivoice_data\dub_jobs\j1\exports\burn_subs_x.srt"
WIN_ESCAPED = r"C\:/Users/Ada/omnivoice_data/dub_jobs/j1/exports/burn_subs_x.srt"


def test_windows_drive_path_uses_forward_slashes_and_escaped_colon():
    """The form ffmpeg's subtitles/ass filters actually open on Windows."""
    assert _ffmpeg_filter_escape(WIN_CAPTION) == WIN_ESCAPED


def test_windows_path_inside_quoted_filter_has_no_doubled_backslashes():
    esc = _ffmpeg_filter_escape(WIN_CAPTION)
    graph = f"[0:v]subtitles='{esc}'[vsub]"
    assert "\\\\" not in graph
    assert graph == f"[0:v]subtitles='{WIN_ESCAPED}'[vsub]"


def test_posix_caption_path_is_unchanged():
    path = "/tmp/exports/burn_subs_x.srt"
    assert _ffmpeg_filter_escape(path) == path


def test_apostrophe_in_caption_path_is_escaped():
    assert _ffmpeg_filter_escape("/tmp/O'Brien.srt") == r"/tmp/O\'Brien.srt"


def test_already_forward_slashed_windows_path_only_escapes_the_colon():
    assert _ffmpeg_filter_escape("C:/Users/Ada/burn_subs_x.ass") == r"C\:/Users/Ada/burn_subs_x.ass"
