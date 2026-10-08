"""ffmpeg/ffprobe only ever read local inputs.

A media file can *contain* URLs: an upload named ``clip.mp4`` that is really
an HLS playlist, a concat list or a DASH manifest makes ffmpeg fetch every
URL inside it, so a dub upload could make the backend request internal
hosts. Two layers stop that:

1. every ffmpeg/ffprobe input carries ``-protocol_whitelist file,pipe``
   (``services.ffmpeg_utils.local_inputs_only``, applied centrally by the
   async spawn helpers and explicitly at each synchronous call site);
2. uploads whose content is a playlist/manifest are refused before any
   ffmpeg sees them (the same prefix check URL imports use).
"""
import ast
import asyncio
import http.server
import os
import pathlib
import shutil
import subprocess
import threading

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import ffmpeg_utils
from services.ffmpeg_utils import LOCAL_INPUT_PROTOCOLS, local_inputs_only

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
WL = "-protocol_whitelist"


# ── The shared wrapper ────────────────────────────────────────────────────

def _whitelists_before_each_input(cmd):
    inputs = [i for i, arg in enumerate(cmd) if arg == "-i"]
    assert inputs, cmd
    start = 0
    for idx in inputs:
        group = cmd[start:idx]
        assert WL in group, cmd
        assert group[group.index(WL) + 1] == LOCAL_INPUT_PROTOCOLS, cmd
        start = idx + 2


def test_every_ffmpeg_input_gets_the_whitelist():
    cmd = local_inputs_only([
        "/usr/bin/ffmpeg", "-y", "-i", "a.mp4",
        "-f", "concat", "-safe", "0", "-i", "list.txt",
        "-f", "lavfi", "-i", "anullsrc", "out.wav",
    ])
    _whitelists_before_each_input(cmd)
    assert cmd[-1] == "out.wav"
    # Input-specific options stay attached to their own input.
    assert cmd.index("-f") < cmd.index("list.txt")


def test_whitelist_is_idempotent_and_keeps_an_existing_one():
    once = local_inputs_only(["ffmpeg", "-i", "a.wav", "-i", "b.wav", "o.wav"])
    assert local_inputs_only(once) == once
    preset = ["ffmpeg", WL, "file", "-i", "a.wav", "o.wav"]
    assert local_inputs_only(preset) == preset


def test_ffprobe_gets_the_whitelist_once():
    cmd = local_inputs_only(["C:\\tools\\ffprobe.exe", "-v", "error", "x.mp4"])
    assert cmd[1:3] == [WL, LOCAL_INPUT_PROTOCOLS]
    assert local_inputs_only(cmd) == cmd


def test_other_commands_are_untouched(monkeypatch):
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    cmd = ["python", "-m", "demucs", "-i", "x"]
    assert local_inputs_only(cmd) == cmd


def test_a_renamed_override_binary_is_still_recognised(monkeypatch, tmp_path):
    exe = tmp_path / "custom-encoder"
    exe.write_text("")
    monkeypatch.setenv("FFMPEG_PATH", str(exe))
    cmd = local_inputs_only([str(exe), "-i", "a.wav", "o.wav"])
    _whitelists_before_each_input(cmd)


def _capture_spawns(monkeypatch):
    seen = []

    class _Proc:
        returncode = 0

        async def communicate(self, input=None):
            return b"", b""

        async def wait(self):
            return 0

    async def fake_spawn(cmd, **kwargs):
        seen.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(ffmpeg_utils, "_spawn_async", fake_spawn)
    return seen


def test_run_ffmpeg_adds_the_whitelist_even_for_a_renamed_binary(monkeypatch):
    seen = _capture_spawns(monkeypatch)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    asyncio.run(ffmpeg_utils.run_ffmpeg(["/opt/enc", "-y", "-i", "in.mp4", "out.wav"]))
    _whitelists_before_each_input(seen[0])


def test_spawn_subprocess_adds_the_whitelist(monkeypatch):
    seen = _capture_spawns(monkeypatch)
    asyncio.run(ffmpeg_utils.spawn_subprocess("/usr/bin/ffmpeg", "-i", "in.mp4", "o.wav"))
    asyncio.run(ffmpeg_utils.spawn_subprocess("/usr/bin/ffprobe", "-v", "error", "in.mp4"))
    _whitelists_before_each_input(seen[0])
    assert seen[1][1:3] == [WL, LOCAL_INPUT_PROTOCOLS]


# ── Every call site goes through the wrapper ─────────────────────────────

_ROUTED = {
    "local_inputs_only", "run_ffmpeg", "spawn_subprocess", "_spawn_with_retry",
    "run_proc_factory", "run_proc", "run_proc_streaming_stderr",
}
_PROBE_FLAGS = {"-i", "-show_entries", "-show_streams", "-show_format"}


def _is_media_tool(node) -> bool:
    if isinstance(node, ast.Name):
        name = node.id.lower()
    elif isinstance(node, ast.Attribute):
        name = node.attr.lower()
    elif isinstance(node, ast.Call):
        return _is_media_tool(node.func)
    else:
        return False
    return "ffmpeg" in name or "ffprobe" in name


def _names(node):
    return {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(node)
        if isinstance(n, (ast.Name, ast.Attribute))
    }


def _unrouted_media_commands(tree):
    """Yield (lineno, function) for argv literals that bypass the wrapper."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    # A module-local helper that itself hands its argv to a routed spawner
    # (e.g. ``_checked(cmd)`` -> ``run_ffmpeg(cmd)``) counts as routed.
    defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    routed = set(_ROUTED)
    while True:
        more = {d.name for d in defs if d.name not in routed and _names(d) & routed}
        if not more:
            break
        routed |= more
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        if not _is_media_tool(node.elts[0]):
            continue
        flags = {e.value for e in node.elts if isinstance(e, ast.Constant)}
        if not flags & _PROBE_FLAGS:
            continue
        scope = parents.get(node)
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        if not _names(scope or tree) & routed:
            yield node.lineno, getattr(scope, "name", "<module>")


def test_no_ffmpeg_or_ffprobe_call_bypasses_the_local_input_wrapper():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] == "tests" or ".venv" in rel.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{rel}:{line} ({func})" for line, func in _unrouted_media_commands(tree)]
    assert not offenders, (
        "ffmpeg/ffprobe argv built without local_inputs_only()/run_ffmpeg()/"
        "spawn_subprocess(); wrap it so inputs stay local-only:\n" + "\n".join(offenders)
    )


# ── End to end: a playlist named like a video is never followed ──────────

class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):  # noqa: N802 — http.server API
        type(self).hits.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def recorder():
    _Recorder.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Recorder.hits
    finally:
        server.shutdown()
        server.server_close()


def _playlist(base):
    return (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        f"#EXTINF:2.0,\n{base}/segment.ts\n#EXT-X-ENDLIST\n"
    ).encode()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs a system ffmpeg")
def test_ffmpeg_with_the_wrapper_never_fetches_playlist_urls(recorder, tmp_path):
    base, hits = recorder
    playlist = tmp_path / "upload.m3u8"
    playlist.write_bytes(_playlist(base))
    # The default whitelist may already stop this on new ffmpeg builds; the
    # explicit one is what makes it hold for every ffmpeg the app may run.
    cmd = local_inputs_only(
        [shutil.which("ffmpeg"), "-v", "error", "-y", "-i", str(playlist),
         str(tmp_path / "out.wav")]
    )
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    assert proc.returncode != 0
    assert hits == []


@pytest.mark.parametrize("route", ["/dub/upload", "/preview/upload"])
def test_dub_upload_of_a_playlist_is_refused_and_never_fetched(route, recorder, monkeypatch):
    from fastapi.testclient import TestClient

    from main import app
    from services import dub_pipeline

    base, hits = recorder
    queued = []

    async def fake_add_task(*args, **kwargs):
        queued.append(args)

    monkeypatch.setattr("core.tasks.task_manager.add_task", fake_add_task)
    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: shutil.which("ffmpeg") or "ffmpeg")

    with TestClient(app) as client:
        response = client.post(
            route,
            files={"video": ("clip.mp4", _playlist(base), "video/mp4")},
            headers={"Host": "127.0.0.1"},
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_media_file"
    assert queued == []
    assert hits == []


def test_pipeline_refuses_a_manifest_source(tmp_path):
    from core.failure import InvalidMediaFileError

    for head in (b"#EXTM3U\n", b"\xef\xbb\xbf  ffconcat version 1.0\n", b"<?xml version='1.0'?><MPD/>"):
        path = tmp_path / "source.mp4"
        path.write_bytes(head + b"x" * 64)
        with pytest.raises(InvalidMediaFileError):
            ffmpeg_utils.validate_media_source(str(path))
        with pytest.raises(InvalidMediaFileError):
            ffmpeg_utils.require_audio_stream(str(path))
