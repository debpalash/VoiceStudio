"""MP3 export must not retain the temporary uncompressed background mix."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def export_module(monkeypatch):
    core = types.ModuleType("api.routers.dub_core")
    core._get_job = lambda job_id: None
    core._safe_lang_or_400 = lambda lang: lang
    monkeypatch.setitem(sys.modules, "api.routers.dub_core", core)
    path = Path(__file__).parents[1] / "backend/api/routers/dub_export.py"
    spec = importlib.util.spec_from_file_location("mp3_export_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("outcome", ["success", "mix_failure", "encode_failure", "cancel_mix", "cancel_encode"])
def test_temporary_background_mix_is_removed_on_every_exit(export_module, monkeypatch, tmp_path, outcome):
    from fastapi import HTTPException
    module = export_module
    voice, bed = tmp_path / "voice.wav", tmp_path / "bed.wav"
    voice.write_bytes(b"voice")
    bed.write_bytes(b"bed")
    job = {"dubbed_tracks": {"de": {"path": str(voice)}}}
    monkeypatch.setattr(module, "_existing_job_dir_or_404", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: job)
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    monkeypatch.setattr(module, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(module, "bed_mix_filter", lambda *a, **kw: "fake-mix")
    monkeypatch.setattr(module, "_consume_native_save", lambda _: None)
    async def background(*args):
        return str(bed)
    monkeypatch.setattr(module, "_preserved_background", background)
    mixed_paths = []
    async def ffmpeg(cmd, **kwargs):
        target = Path(cmd[-1])
        target.write_bytes(b"mixed PCM" if target.suffix == ".wav" else b"encoded MP3")
        phase = "mix" if target.suffix == ".wav" else "encode"
        if phase == "mix":
            mixed_paths.append(target)
        if outcome == "cancel_" + phase:
            raise asyncio.CancelledError()
        return (1 if outcome == phase + "_failure" else 0), b"", b"encoder failure"
    monkeypatch.setattr(module, "run_ffmpeg", ffmpeg)
    async def invoke():
        return await module.dub_download_mp3("job", lang="de", preserve_bg=True,
                                             save_authorization="", bitrate="192k")
    if outcome == "success":
        response = asyncio.run(invoke())
        assert Path(response.path).read_bytes() == b"encoded MP3"
    else:
        with pytest.raises(asyncio.CancelledError if outcome.startswith("cancel_") else HTTPException):
            asyncio.run(invoke())
    assert mixed_paths and all(not path.exists() for path in mixed_paths)
    exports = list((tmp_path / "exports").iterdir())
    assert len(exports) == (1 if outcome == "success" else 0)
    assert voice.read_bytes() == b"voice" and bed.read_bytes() == b"bed"


def test_cancellation_reaps_real_encoder_before_removing_its_work_directory(export_module, monkeypatch, tmp_path):
    from services import ffmpeg_utils
    module = export_module
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"voice")
    monkeypatch.setattr(module, "_existing_job_dir_or_404", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: {"dubbed_tracks": {"de": {"path": str(voice)}}})
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    monkeypatch.setattr(module, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(module, "bed_mix_filter", lambda *a, **kw: "unused-mix")
    monkeypatch.setattr(ffmpeg_utils, "_FFMPEG_SEMAPHORE", None)
    async def background(*args):
        return str(voice)
    monkeypatch.setattr(module, "_preserved_background", background)
    children, targets = [], []
    async def spawn(cmd, **kwargs):
        target = Path(cmd[-1])
        targets.append(target)
        # A real child holds the encoder output open until the production
        # cancellation path kills and reaps it. No ffmpeg/model dependency.
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys,time; f=open(sys.argv[1],'wb'); f.write(b'partial'); f.flush(); time.sleep(30)",
            str(target), **kwargs,
        )
        children.append(child)
        return child
    monkeypatch.setattr(ffmpeg_utils, "_spawn_with_retry", spawn)
    async def exercise():
        task = asyncio.create_task(module.dub_download_mp3(
            "job", lang="de", preserve_bg=True, save_authorization="", bitrate="192k"))
        try:
            for _ in range(500):
                if targets and targets[0].exists() and targets[0].stat().st_size:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Encoder did not open its temporary output")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert children and all(child.returncode is not None for child in children)
            assert not list((tmp_path / "exports").iterdir())
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    asyncio.run(exercise())
