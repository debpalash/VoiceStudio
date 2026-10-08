"""A missing ffmpeg / closed stdio pipe must reach the user as its own fix (#2404, #2405).

Per-chunk ASR failures are deliberately reduced to fixed public messages (no
raw exception text may leave the process). That floor used to be the generic
"Check the selected ASR engine" reply for *every* class, so a Mac without a
resolvable ffmpeg (``[Errno 2] ... 'ffmpeg'``) or an orphaned backend whose
stdio pipe closed (``[Errno 32] Broken pipe``) got no next step. The exception
is now classified privately and mapped to a VoiceStudio-owned, actionable
message per class.
"""
from __future__ import annotations

import errno
import struct
import wave

import pytest

pytestmark = pytest.mark.usefixtures("asr_model_installed")


def _cases():
    from services.ffmpeg_utils import MediaToolUnavailableError

    return [
        (FileNotFoundError(errno.ENOENT, "No such file or directory", "ffmpeg"),
         "transcription_media_tool"),
        (RuntimeError("[Errno 2] No such file or directory: 'ffmpeg'"),
         "transcription_media_tool"),
        (MediaToolUnavailableError("Cannot transcribe: ffmpeg is missing"),
         "transcription_media_tool"),
        (BrokenPipeError(errno.EPIPE, "Broken pipe"), "transcription_pipe_lost"),
        (RuntimeError("TOKEN=chunk-secret /home/alice/a.wav"), "transcription_failed"),
        # A missing INPUT file is not a missing media engine.
        (FileNotFoundError(errno.ENOENT, "No such file or directory", "/tmp/in.wav"),
         "transcription_failed"),
    ]


def test_failure_code_follows_the_exception_chain():
    from core.public_errors import transcription_failure_code

    try:
        try:
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")
        except OSError as inner:
            raise RuntimeError("decode failed") from inner
    except RuntimeError as outer:
        assert transcription_failure_code(outer) == "transcription_pipe_lost"
    for exc, code in _cases():
        assert transcription_failure_code(exc) == code, repr(exc)


@pytest.mark.parametrize("index", range(6))
def test_chunk_failure_class_reaches_the_user(tmp_path, monkeypatch, index):
    import asyncio

    from api.routers import dub_core as dc
    from core.public_errors import stream_failure

    exc, code = _cases()[index]
    job_id = f"t_chunk_class_{index}"
    audio = tmp_path / "a.wav"
    with wave.open(str(audio), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack("<16000h", *([0] * 16000)))
    dc._dub_jobs[job_id] = {
        "audio_path": str(audio), "vocals_path": None, "scene_cuts": [],
    }

    class _ASR:
        id = "fake"

        def ensure_loaded(self):
            pass

        def transcribe(self, path, *, word_timestamps=True):
            raise exc

        def unload(self):
            pass

    monkeypatch.setattr(dc, "_CHUNK_TRANSCRIBE_ATTEMPTS", 1)
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.asr_backend.get_active_asr_backend", lambda *a, **k: _ASR()
    )

    async def _collect():
        resp = await dc.dub_transcribe_stream(job_id)
        parts = []
        async for chunk in resp.body_iterator:
            parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(parts)

    try:
        body = asyncio.run(_collect())
    finally:
        dc._dub_jobs.pop(job_id, None)

    assert f'"code": "{code}"' in body, body
    assert stream_failure(code)["detail"] in body
    assert "chunk-secret" not in body and "/home/alice" not in body


def test_imageio_style_binary_is_reachable_by_bare_name(tmp_path, monkeypatch):
    """Dependencies that exec a literal ``ffmpeg`` must find a renamed binary.

    imageio-ffmpeg names its build ``ffmpeg-<platform>-vN``; publishing only
    that directory on PATH never satisfied ``subprocess.run(["ffmpeg", ...])``
    (parakeet-mlx, openai-whisper, pydub), the ``[Errno 2] ... 'ffmpeg'`` of
    #2404.
    """
    import os
    import shutil
    import stat
    import sys

    import core.config as cfg
    from services import ffmpeg_utils

    if sys.platform == "win32":
        pytest.skip("POSIX shell stub; the shim logic is identical on Windows")
    real_dir = tmp_path / "imageio"
    real_dir.mkdir()
    real = real_dir / "ffmpeg-linux-x86_64-v7.1"
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(real.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: str(real))
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    monkeypatch.setenv("PATH", "/nonexistent")

    added = ffmpeg_utils.ensure_media_tools_on_path()

    assert added
    found = shutil.which("ffmpeg")
    assert found and os.path.samefile(found, real)
    # Idempotent: a second call must not break or duplicate the shim.
    monkeypatch.setenv("PATH", "/nonexistent")
    ffmpeg_utils.ensure_media_tools_on_path()
    assert os.path.samefile(shutil.which("ffmpeg"), real)


def _stub(path):
    import stat

    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_shim_survives_relative_paths_and_beats_a_competing_ffmpeg(tmp_path, monkeypatch):
    """Relative FFMPEG_PATH, and a system ffmpeg sitting beside the resolved ffprobe."""
    import os
    import shutil
    import sys

    import core.config as cfg
    from services import ffmpeg_utils

    if sys.platform == "win32":
        pytest.skip("POSIX shell stub")
    tools = tmp_path / "tools"
    tools.mkdir()
    _stub(tools / "ffmpeg-custom")
    system = tmp_path / "usr_bin"
    system.mkdir()
    _stub(system / "ffmpeg")
    _stub(system / "ffprobe")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "tools/ffmpeg-custom")
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: str(system / "ffprobe"))
    monkeypatch.setenv("PATH", str(system))

    ffmpeg_utils.ensure_media_tools_on_path()

    found = shutil.which("ffmpeg")
    assert os.path.samefile(found, tools / "ffmpeg-custom"), found


def test_copied_shim_is_refreshed_when_same_size_contents_differ(tmp_path, monkeypatch):
    import os
    import sys

    import core.config as cfg
    from services import ffmpeg_utils

    if sys.platform == "win32":
        pytest.skip("POSIX shell stub")
    real = tmp_path / "ffmpeg-custom"
    _stub(real)
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path / "data"))

    def _no_links(*_a, **_k):
        raise OSError(1, "links refused")

    monkeypatch.setattr(os, "symlink", _no_links)
    monkeypatch.setattr(os, "link", _no_links)

    shim_dir = ffmpeg_utils._bare_name_shim(str(real), "ffmpeg")
    copy = os.path.join(shim_dir, "ffmpeg")
    assert open(copy, "rb").read() == real.read_bytes()
    # Same length, different bytes: a corrupt copy must not keep shadowing it.
    data = bytearray(real.read_bytes())
    data[-3] = ord("X")
    with open(copy, "wb") as handle:
        handle.write(data)
    ffmpeg_utils._bare_name_shim(str(real), "ffmpeg")
    assert open(copy, "rb").read() == real.read_bytes()
