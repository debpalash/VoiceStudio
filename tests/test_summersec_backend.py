from __future__ import annotations

import asyncio
import os
import stat

import pytest


def test_prefs_delete_announces_and_secures_file(monkeypatch, tmp_path):
    from core import change_hooks, prefs

    path = tmp_path / "prefs.json"
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(path))
    seen = []
    monkeypatch.setattr(change_hooks, "notify", lambda *args: seen.append(args))

    prefs.set_("remove-me", "value")
    seen.clear()
    prefs.delete("remove-me")
    prefs.delete("missing")

    assert prefs.get("remove-me") is None
    assert seen == [("prefs", "remove-me"), ("prefs", "missing")]
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_proxy_setting_persists_and_clears(monkeypatch):
    from api.routers import system
    from core import prefs

    saved = []
    removed = []
    monkeypatch.setattr(system, "prefs_set", lambda *args: saved.append(args))
    monkeypatch.setattr(system, "prefs_delete", lambda key: removed.append(key))
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda _key: False)

    await system.set_env_var({"key": "HTTP_PROXY", "value": "http://proxy.test:8080"})
    await system.set_env_var({"key": "HTTP_PROXY", "value": ""})

    assert saved == [("env.HTTP_PROXY", "http://proxy.test:8080")]
    assert removed == ["env.HTTP_PROXY"]


@pytest.mark.asyncio
async def test_ffmpeg_thread_process_wrapper(monkeypatch):
    from services import ffmpeg_utils

    class FakePopen:
        returncode = None
        stdin = "stdin"
        stdout = "stdout"
        stderr = "stderr"
        pid = 42

        def communicate(self, data=None):
            self.returncode = 0
            return data or b"out", b"err"

        def wait(self):
            self.returncode = 0
            return 0

        def kill(self):
            self.killed = True

        def terminate(self):
            self.terminated = True

    seen = {}

    class InlineLoop:
        async def run_in_executor(self, _executor, fn, *args):
            return fn(*args)

    async def unsupported(*_args, **_kwargs):
        raise NotImplementedError

    def fake_popen(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return FakePopen()

    monkeypatch.setattr(ffmpeg_utils.asyncio, "create_subprocess_exec", unsupported)
    monkeypatch.setattr(ffmpeg_utils.asyncio, "get_running_loop", lambda: InlineLoop())
    monkeypatch.setattr(ffmpeg_utils.subprocess, "Popen", fake_popen)
    proc = await ffmpeg_utils._create_subprocess(
        ["tool", "arg"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="work",
    )

    assert proc.uses_sync_pipes is True
    assert seen["stdin"] == ffmpeg_utils.subprocess.PIPE
    assert seen["stdout"] == ffmpeg_utils.subprocess.PIPE
    assert seen["stderr"] == ffmpeg_utils.subprocess.PIPE
    assert seen["cwd"] == "work"
    assert await proc.communicate(b"input") == (b"input", b"err")
    assert await proc.wait() == 0
    proc.kill()
    proc.terminate()
    assert proc._popen.killed and proc._popen.terminated


@pytest.mark.asyncio
async def test_installer_thread_fallback_and_timeout(monkeypatch):
    from services import translation_engines

    class FakePopen:
        returncode = 0

        def __init__(self, times_out=False):
            self.times_out = times_out
            self.killed = False

        def communicate(self, timeout=None):
            if timeout is not None and self.times_out:
                raise translation_engines.subprocess.TimeoutExpired("pip", timeout)
            return b"installed", None

        def kill(self):
            self.killed = True

    class InlineLoop:
        async def run_in_executor(self, _executor, fn, *args):
            return fn(*args)

    monkeypatch.setattr(
        translation_engines.asyncio,
        "get_running_loop",
        lambda: InlineLoop(),
    )

    successful = FakePopen()
    monkeypatch.setattr(
        translation_engines.subprocess,
        "Popen",
        lambda *_args, **_kwargs: successful,
    )
    assert await translation_engines._run_installer_in_thread(["pip"], 2) == (0, "installed")

    timed_out = FakePopen(times_out=True)
    monkeypatch.setattr(
        translation_engines.subprocess,
        "Popen",
        lambda *_args, **_kwargs: timed_out,
    )
    assert await translation_engines._run_installer_in_thread(["pip"], 3) == (
        1,
        "pip timed out after 3s",
    )
    assert timed_out.killed


def test_simplified_chinese_language_aliases():
    from api.routers.dub_translate import FLORES_CODES, LANG_NAMES, TRANSLATE_CODES

    assert TRANSLATE_CODES["zh"] == TRANSLATE_CODES["cmn-Hans"] == "zh-CN"
    assert FLORES_CODES["zh"] == FLORES_CODES["zh-CN"] == FLORES_CODES["cmn-Hans"] == "zho_Hans"
    assert LANG_NAMES["zh"] == LANG_NAMES["zh-CN"] == LANG_NAMES["cmn-Hans"] == "Chinese (Simplified)"
