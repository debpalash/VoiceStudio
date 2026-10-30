"""#1256: a synth failed with a raw `FileNotFoundError: … 'ffprobe'`.

The reporter's toast, on a Mac with the app's own ffprobe sitting on disk:

    Couldn't synthesize audio. … Underlying error: RuntimeError: TTS engine
    stopped mid-generation with an error OmniVoice doesn't recognize. Retry
    once; if it keeps failing, please report it with the full trace.
    Underlying error: FileNotFoundError: [Errno 2] No such file or directory:
    'ffprobe'

Two separate defects, fixed here as two halves:

1. **It happened at all.** Every OmniVoice call site resolves ffmpeg/ffprobe
   explicitly (`find_ffprobe()`), which is why a bundled sidecar that was never
   on ``PATH`` works for us. Our dependencies get no such courtesy — one that
   shells out to ``ffprobe`` by bare name finds nothing. Publishing the
   resolved directories on ``PATH`` fixes every such dependency at once.

2. **It was illegible.** "an error OmniVoice doesn't recognize" is what the
   generic engine wrapper says when `classify()` returns "" — and it did,
   because nothing matched a missing media binary. It now names the class and
   points at Settings → Audio tools.
"""
from __future__ import annotations

import os

import pytest

from core.failure import build_failure, classify
from services import ffmpeg_utils


def _tool_filename(tool: str) -> str:
    return f"{tool}.exe" if os.name == "nt" else tool


# ── the failure is now classified ────────────────────────────────────────


def test_the_reporters_error_is_classified():
    assert classify("[Errno 2] No such file or directory: 'ffprobe'") == "MEDIA_TOOL_MISSING"


@pytest.mark.parametrize(
    "reason",
    [
        # As it arrives wrapped by the engine's generic handler.
        "FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'",
        "[Errno 2] No such file or directory: 'ffmpeg'",
        # subprocess on Windows words it differently.
        "[WinError 2] The system cannot find the file specified: 'ffmpeg'",
        '[Errno 2] No such file or directory: "ffprobe"',
    ],
)
def test_every_wording_of_the_missing_binary_is_classified(reason):
    assert classify(reason) == "MEDIA_TOOL_MISSING"


def test_the_hint_names_the_repair_and_is_actionable():
    failure = build_failure(
        FileNotFoundError(2, "No such file or directory", "ffprobe"),
        stage="generate",
    )
    assert failure["error_class"] == "FileNotFoundError"
    hint = failure.get("hint") or ""
    assert "Audio tools" in hint, "the user needs the panel that fixes it"
    assert "ffmpeg" in hint.lower()


def test_a_missing_INPUT_file_is_not_handed_the_media_engine_remedy():
    """The narrow half of the match. ffmpeg reporting that its *input* is
    missing is an entirely different problem — telling that user to reinstall
    the media engine sends them the wrong way."""
    assert classify(
        "ffmpeg failed: [Errno 2] No such file or directory: '/tmp/chunk_7.wav'"
    ) != "MEDIA_TOOL_MISSING"
    assert classify(
        "No such file or directory: '/Users/x/Movies/my-ffmpeg-export.mp4'"
    ) != "MEDIA_TOOL_MISSING"


def test_unrelated_errno_2_failures_keep_their_own_class():
    """The rule runs before the generic errno-2 handling, so it must not
    swallow the classes that were already correct."""
    assert classify("No module named 'omnivoice'") == "BROKEN_VENV"
    assert classify(
        "does not appear to have a file named model.safetensors"
    ) == "MODEL_CACHE_CORRUPT"


# ── and it is prevented in the first place ───────────────────────────────


def test_the_resolved_binaries_are_published_on_PATH(monkeypatch, tmp_path):
    """The half that stops the failure happening: a dependency that shells out
    by bare name must find what `find_ffprobe()` already resolved."""
    bin_dir = tmp_path / "media"
    bin_dir.mkdir()
    ffmpeg = bin_dir / _tool_filename("ffmpeg")
    ffprobe = bin_dir / _tool_filename("ffprobe")
    ffmpeg.write_text("")
    ffprobe.write_text("")

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(ffmpeg))
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: str(ffprobe))
    monkeypatch.setenv("PATH", "/usr/bin")

    added = ffmpeg_utils.ensure_media_tools_on_path()

    assert added == [str(bin_dir)]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir), (
        "must be prepended — the copy we validated should win over a broken "
        "system one"
    )


def test_publishing_is_idempotent(monkeypatch, tmp_path):
    """It runs at import time; a reload or a second call must not grow PATH
    without bound."""
    bin_dir = tmp_path / "media"
    bin_dir.mkdir()
    ffmpeg = bin_dir / _tool_filename("ffmpeg")
    ffmpeg.write_text("")
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(ffmpeg))
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    monkeypatch.setenv("PATH", "/usr/bin")

    ffmpeg_utils.ensure_media_tools_on_path()
    first = os.environ["PATH"]
    assert ffmpeg_utils.ensure_media_tools_on_path() == []
    assert os.environ["PATH"] == first


def test_separate_directories_are_both_added(monkeypatch, tmp_path):
    """A system ffmpeg plus a bundled ffprobe is a real configuration — the
    resolver already supports it, so PATH must reflect both."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ffmpeg = a / _tool_filename("ffmpeg")
    ffprobe = b / _tool_filename("ffprobe")
    ffmpeg.write_text("")
    ffprobe.write_text("")
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(ffmpeg))
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: str(ffprobe))
    monkeypatch.setenv("PATH", "/usr/bin")

    assert sorted(ffmpeg_utils.ensure_media_tools_on_path()) == sorted([str(a), str(b)])


def test_nothing_resolvable_is_a_no_op(monkeypatch):
    """A machine with no media engine at all must still boot — the classified
    error above is what that user gets, not a startup crash."""
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    monkeypatch.setenv("PATH", "/usr/bin")

    assert ffmpeg_utils.ensure_media_tools_on_path() == []
    assert os.environ["PATH"] == "/usr/bin"


def test_a_raising_resolver_never_breaks_startup(monkeypatch):
    def boom():
        raise RuntimeError("media_tools registry unreadable")

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", boom)
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", boom)
    monkeypatch.setenv("PATH", "/usr/bin")

    assert ffmpeg_utils.ensure_media_tools_on_path() == []


def test_a_path_that_merely_ends_in_the_tool_name_is_not_a_missing_tool(tmp_path):
    """Review finding (#1256): the match accepted any message ending in
    'ffmpeg'/'ffprobe', so a missing FILE whose name happens to be the tool's
    was handed the "repair your media engine" remedy.

    Paths are built from `tmp_path` rather than written as literals — they are
    only error-message data here, but a hardcoded /tmp trips Ruff S108."""
    paths = [
        str(tmp_path / "ffmpeg"),
        str(tmp_path / "sub" / "ffprobe"),
        "C:\\work\\ffmpeg",
    ]
    for path in paths:
        assert classify(f"[Errno 2] No such file or directory: {path}") != (
            "MEDIA_TOOL_MISSING"
        ), path


def test_the_published_paths_are_not_logged(monkeypatch, tmp_path, caplog):
    """Review finding (#1256): a user-set FFMPEG_PATH resolves under their home
    directory, and absolute home paths must not reach the log."""
    import logging

    bin_dir = tmp_path / "Users" / "alice" / "media"
    bin_dir.mkdir(parents=True)
    ffmpeg = bin_dir / _tool_filename("ffmpeg")
    ffmpeg.write_text("")
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(ffmpeg))
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    monkeypatch.setenv("PATH", "/usr/bin")

    with caplog.at_level(logging.INFO, logger="omnivoice.api"):
        ffmpeg_utils.ensure_media_tools_on_path()

    assert str(bin_dir) not in caplog.text
    assert "alice" not in caplog.text
