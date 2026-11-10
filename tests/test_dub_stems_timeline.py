"""Stems must share the exported track's fitted timeline."""
import asyncio
import importlib.util
import io
import random
from pathlib import Path
import sys
import types
import zipfile
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def export_module(monkeypatch):
    # Job-store boundary only: importing dub_core also loads unrelated engines.
    core = types.ModuleType("api.routers.dub_core")
    core._get_job = lambda job_id: None
    core._safe_lang_or_400 = lambda lang: lang
    monkeypatch.setitem(sys.modules, "api.routers.dub_core", core)
    path = Path(__file__).parents[1] / "backend/api/routers/dub_export.py"
    spec = importlib.util.spec_from_file_location("stems_export_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    module.test_app = app
    return module


def test_stems_zip_uses_the_same_retimed_background_as_playback(export_module, monkeypatch, tmp_path):
    module = export_module
    vocals = tmp_path / "voice.wav"
    original_bed = tmp_path / "original.wav"
    fitted_bed = tmp_path / "fitted.wav"
    for path in (vocals, original_bed, fitted_bed):
        path.write_bytes(path.stem.encode())
    job = {"dubbed_tracks": {"de": {"path": str(vocals), "timing_strategy": "smart_fit"}},
           "no_vocals_path": str(original_bed), "filename": "interview.mp4",
           "timing_strategy": "smart_fit",
           "fit_plans": {"de": {"plan": [{"orig_start": 0, "orig_end": 1, "stretch_ratio": 2}]}}}
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: job)
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    monkeypatch.setattr(module, "_optional_dub_artifact", lambda value, *a: value)
    prepare = AsyncMock(return_value=str(fitted_bed))
    monkeypatch.setattr(module, "_preserved_background", prepare)
    with TestClient(module.test_app) as client:
        response = client.get("/dub/export-stems/job", params={"lang": "de"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("background_original.wav") == b"fitted"
        assert archive.read("vocals_dubbed_de.wav") == b"voice"
    prepare.assert_awaited_once_with(job, "job", "de")


def test_stems_without_separated_background_still_export_vocals(export_module, monkeypatch, tmp_path):
    module = export_module
    vocals = tmp_path / "voice.wav"
    vocals.write_bytes(b"voice")
    job = {"dubbed_tracks": {"de": {"path": str(vocals)}}}
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: job)
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    prepare = AsyncMock()
    monkeypatch.setattr(module, "_preserved_background", prepare)
    with TestClient(module.test_app) as client:
        response = client.get("/dub/export-stems/job", params={"lang": "de"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["vocals_dubbed_de.wav"]
    prepare.assert_not_awaited()


@pytest.mark.parametrize("earlier_strategy,later_strategy,should_retime", [
    ("stretch_video", "smart_fit", True),
    ("strict_slot", "stretch_video", False),
    (None, "stretch_video", True),
])
def test_stems_use_selected_tracks_strategy_after_another_language_is_generated(
    export_module, monkeypatch, tmp_path, earlier_strategy, later_strategy, should_retime
):
    from services import dub_background
    module = export_module
    voice, bed, source, fitted = [tmp_path / name for name in ("voice.wav", "bed.wav", "source.wav", "fitted.wav")]
    for path in (voice, bed, source, fitted):
        path.write_bytes(path.stem.encode())
    plan = [{"orig_start": 0, "orig_end": 1, "stretch_ratio": 2}]
    job = {
        "dubbed_tracks": {
            "de": {"path": str(voice), "timing_strategy": earlier_strategy,
                   "source_segments": [{"start": 0, "end": 1}]},
            "es": {"path": "later.wav", "timing_strategy": later_strategy},
        },
        "timing_strategy": later_strategy,
        "duration": 1,
        "video_stretch_plans": {"de": {"plan": plan, "orig_duration": 1}},
        "no_vocals_path": str(bed), "audio_path": str(source),
    }
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_existing_job_dir_or_404", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: job)
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    monkeypatch.setattr(module, "_optional_dub_artifact", lambda value, *a: value)
    # Both export plan resolution AND _preserved_background stay real. Only
    # the expensive audio renderer is replaced at its output boundary.
    renderer = AsyncMock(return_value=str(fitted))
    monkeypatch.setattr(dub_background, "surgical_background", renderer)
    with TestClient(module.test_app) as client:
        response = client.get("/dub/export-stems/job", params={"lang": "de"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("background_original.wav") == (b"fitted" if should_retime else b"bed")
    if should_retime:
        assert renderer.await_args.args[4] == plan
        assert renderer.await_args.args[5] == 1
    else:
        renderer.assert_not_awaited()
    assert job["timing_strategy"] == later_strategy


@pytest.mark.parametrize("kind", ["segments", "stems"])
def test_dub_zip_sends_bounded_chunks(export_module, monkeypatch, tmp_path, kind):
    """Send a complete ZIP without holding the entire archive in one response chunk."""
    module = export_module
    audio = tmp_path / "voice.wav"
    payload = random.Random(3924).randbytes(512 * 1024)
    audio.write_bytes(payload)
    job = {
        "filename": "interview.mp4",
        "segments": [{"start": 0, "end": 1, "speaker_id": "Speaker 1"}],
        "dubbed_tracks": {"de": {"path": str(audio)}},
    }
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: job)
    monkeypatch.setattr(module, "_existing_segment_artifact", lambda *_: str(audio))
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    monkeypatch.setattr(module, "_optional_dub_artifact", lambda *a: None)

    async def download():
        route = module.dub_export_stems if kind == "stems" else module.dub_export_segments_zip
        response = await route("job", lang="de")
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            pytest.fail("ASGI 2.4 response does not need to poll for disconnects")

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        return messages

    start, *messages = asyncio.run(download())
    headers = dict(start["headers"])
    chunks = [message["body"] for message in messages if message.get("body")]
    assert start["status"] == 200
    assert headers[b"content-type"] == b"application/zip"
    assert f"{kind}_interview.zip".encode() in headers[b"content-disposition"]
    assert len(chunks) > 1
    assert max(map(len, chunks)) <= 64 * 1024
    assert int(headers[b"content-length"]) == sum(map(len, chunks))
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
        member = "vocals_dubbed_de.wav" if kind == "stems" else "001_0.00-1.00_Speaker1.wav"
        assert archive.namelist() == [member]
        assert archive.read(member) == payload


@pytest.mark.parametrize("failure", ["compression", "disconnect"])
def test_dub_zip_closes_temporary_archive_on_failure(export_module, monkeypatch, tmp_path, failure):
    """Neither a failed build nor an interrupted download leaves an archive open."""
    from starlette.requests import ClientDisconnect

    module = export_module
    audio = tmp_path / "voice.wav"
    if failure == "disconnect":
        audio.write_bytes(b"voice")
    original = module.TemporaryFile
    opened = []

    def track_archive():
        archive = original()
        opened.append(archive)
        return archive

    monkeypatch.setattr(module, "TemporaryFile", track_archive)

    async def download():
        response = module._DubZipResponse([(str(audio), "voice.wav")], "dub.zip")

        async def send(message):
            if failure == "compression":
                pytest.fail("HTTP headers were sent before compression finished")
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        async def receive():
            pytest.fail("ASGI 2.4 response does not need to poll for disconnects")

        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    expected = FileNotFoundError if failure == "compression" else ClientDisconnect
    with pytest.raises(expected):
        asyncio.run(download())
    assert len(opened) == 1
    assert opened[0].closed
