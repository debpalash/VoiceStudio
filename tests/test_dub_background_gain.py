"""Background volume reaches real media exports and immutable preview caches."""
from __future__ import annotations

import asyncio
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers import dub_core, dub_export


@pytest.fixture
def media_client(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg required for real media mix regression")
    monkeypatch.setenv("FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(dub_export, "DUB_DIR", str(tmp_path))
    job_id = "gain_fixture"
    folder = tmp_path / job_id
    folder.mkdir()
    def tone(name, frequency, amplitude):
        path = folder / name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(48000)
            out.writeframes(b"".join(struct.pack("<hh", *([round(32767 * amplitude * math.sin(
                2 * math.pi * frequency * i / 48000))] * 2)) for i in range(48000)))
        return str(path)
    bed = tone("background.wav", 400, .02)
    voice = tone("dubbed_es.wav", 1000, .03)
    video = str(folder / "source.mp4")
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=32x32:r=10:d=1",
                    "-i", bed, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", video],
                   check=True, timeout=15)
    job = {
        "video_path": video, "audio_path": bed, "no_vocals_path": bed,
        "duration": 1., "filename": "source.mp4", "segments": [{"start": .2, "end": .8}],
        "dubbed_tracks": {"es": {"path": voice, "language": "Spanish", "language_code": "es"}},
    }
    monkeypatch.setitem(dub_core._dub_jobs, job_id, job)
    app = FastAPI()
    app.include_router(dub_export.router)
    with TestClient(app) as client:
        yield client, job, folder, ffmpeg


def amplitude(data, folder, ffmpeg, frequency):
    path = folder / "measured-media"
    path.write_bytes(data)
    raw = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0",
                                   "-ar", "48000", "-ac", "2", "-f", "f32le", "pipe:1"], timeout=15)
    values = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)[4800:38400, 0]
    angles = np.arange(len(values)) * (2 * math.pi * frequency / 48000)
    return 2 * abs(np.sum(values * np.exp(-1j * angles))) / len(values)


@pytest.mark.parametrize("kind", ["video", "audio-only", "preview", "wav", "mp3"])
@pytest.mark.parametrize("gain", [0, 1, 2, 4])
def test_real_export_applies_background_gain(media_client, kind, gain):
    client, job, folder, ffmpeg = media_client
    endpoint = {"video": "download", "audio-only": "download", "preview": "preview-video",
                "wav": "download-audio", "mp3": "download-mp3"}[kind]
    if kind == "audio-only":
        job["input_type"] = "audio"
    response = client.get(f"/dub/{endpoint}/gain_fixture", params={
        "lang": "es", "default_track": "es", "include_tracks": "es",
        "out_format": "wav", "bed_gain": gain,
    })
    assert response.status_code == 200, response.text
    assert amplitude(response.content, folder, ffmpeg, 400) == pytest.approx(.02 * gain, rel=.04, abs=.0003)
    assert amplitude(response.content, folder, ffmpeg, 1000) == pytest.approx(.033, abs=.003)


@pytest.mark.parametrize("endpoint", ["download", "preview-video", "download-audio", "download-mp3"])
@pytest.mark.parametrize("gain", ["nan", "inf", "-inf", "-0.1", "4.1", "loud"])
def test_invalid_gain_is_rejected_before_job_lookup(endpoint, gain):
    app = FastAPI()
    app.include_router(dub_export.router)
    with TestClient(app) as client:
        response = client.get(f"/dub/{endpoint}/missing-job", params={"lang": "es", "bed_gain": gain})
    assert response.status_code == 422, response.text
    assert any(error["loc"][-1] == "bed_gain" for error in response.json()["detail"])


def test_default_and_background_off_preserve_existing_audio(media_client):
    client, _, folder, ffmpeg = media_client
    base = "/dub/download-audio/gain_fixture?lang=es"
    default = client.get(base)
    explicit = client.get(base + "&bed_gain=1")
    assert default.status_code == explicit.status_code == 200
    assert default.content == explicit.content
    dry = client.get(base + "&preserve_bg=false&bed_gain=4")
    assert dry.status_code == 200
    assert amplitude(dry.content, folder, ffmpeg, 400) < .0001
    assert amplitude(dry.content, folder, ffmpeg, 1000) == pytest.approx(.03, abs=.0002)


def test_preview_gain_has_independent_cache_and_head_does_not_mux(media_client):
    client, _, folder, _ = media_client
    url = "/dub/preview-video/gain_fixture?lang=es"
    assert client.head(url + "&bed_gain=2").status_code == 200
    assert not list((folder / "exports").glob("preview*.mp4"))
    assert client.get(url).status_code == 200
    assert client.get(url + "&bed_gain=2").status_code == 200
    before = {p.name: p.stat().st_mtime_ns for p in (folder / "exports").glob("preview*.mp4")}
    assert len(before) == 2
    assert client.get(url + "&bed_gain=2").status_code == 200
    assert before == {p.name: p.stat().st_mtime_ns for p in (folder / "exports").glob("preview*.mp4")}
    assert client.get(url + "&bed_gain=2.0000001").status_code == 200
    previews = list((folder / "exports").glob("preview*.mp4"))
    assert len(previews) == 2  # default plus the latest nondefault gain
    assert not any(p.name.endswith("_gain_2.mp4") for p in previews)


def test_wav_mix_gain_cache_retains_leased_other_gain(media_client):
    _, job, folder, _ = media_client
    exports = folder / "exports"
    exports.mkdir(exist_ok=True)
    async def run():
        args = (str(exports), "es", job["no_vocals_path"], job["dubbed_tracks"]["es"]["path"])
        normal = await dub_export._mixed_dub_audio(*args)
        louder = await dub_export._mixed_dub_audio(*args, bed_gain=2)
        assert normal != louder
        assert Path(normal).exists()  # still leased by a previous reader
        again = await dub_export._mixed_dub_audio(*args, bed_gain=2.)
        assert again == louder
        for path in [normal, louder, again]:
            dub_export._release_mix(path)
        assert not Path(normal).exists()
        assert Path(louder).is_file()
    asyncio.run(run())


def test_decoder_measures_known_stereo_tones_without_mono_downmix_gain(media_client):
    _, job, folder, ffmpeg = media_client
    for path, frequency, level in [(job["audio_path"], 400, .02),
                                   (job["dubbed_tracks"]["es"]["path"], 1000, .03)]:
        data = Path(path).read_bytes()
        assert amplitude(data, folder, ffmpeg, frequency) == pytest.approx(level, abs=.0001)


def test_many_gain_choices_retain_default_and_only_latest_gain(media_client):
    client, _, folder, _ = media_client
    url = "/dub/preview-video/gain_fixture?lang=es"
    for gain in [1, -0.0, 1e-7, 1.01, 1.02, 2, 3, 4]:
        response = client.get(url + f"&bed_gain={gain}")
        assert response.status_code == 200, response.text
        assert response.content[4:8] == b"ftyp"
    previews = list((folder / "exports").glob("preview_v2_es_*.mp4"))
    assert len(previews) == 2
    assert sum(p.name.endswith("_gain_4.mp4") for p in previews) == 1


@pytest.mark.parametrize("ending", ["complete", "cancel", "send_failure", "revive"])
@pytest.mark.parametrize("pathsend", [False, True])
def test_a_superseded_preview_waits_for_every_lazy_reader(media_client, ending, pathsend):
    import httpx
    client, _, folder, _ = media_client

    async def run():
        started = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        bodies = [bytearray(), bytearray()]
        pathsend_seen = [False, False]
        async def held_reader(index):
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": "/dub/preview-video/gain_fixture",
                     "raw_path": b"/dub/preview-video/gain_fixture", "query_string": b"lang=es&bed_gain=2",
                     "headers": [(b"host", b"testserver")], "server": ("testserver", 80), "client": ("127.0.0.1", 1234)}
            if pathsend:
                scope["extensions"] = {"http.response.pathsend": {}}
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def wait_for_client():
                started[index].set()
                await release[index].wait()
                if index == 1 and ending == "send_failure":
                    raise OSError("owned disconnected client")
            async def send(message):
                if message["type"] == "http.response.start":
                    assert message["status"] == 200
                    if not pathsend:
                        await wait_for_client()  # before FileResponse opens the file
                elif message["type"] == "http.response.pathsend":
                    assert pathsend
                    pathsend_seen[index] = True
                    await wait_for_client()
                    # Exercise the ASGI file-send contract by consuming the real
                    # MP4 before returning, rather than only recording its path.
                    bodies[index].extend(Path(message["path"]).read_bytes())
                elif message["type"] == "http.response.body":
                    assert not pathsend
                    bodies[index].extend(message.get("body", b""))
            await client.app(scope, receive, send)
        tasks = [asyncio.create_task(held_reader(i)) for i in range(2)]
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 10)
            assert pathsend_seen == [pathsend, pathsend]
            old = next((folder / "exports").glob("*_gain_2.mp4"))
            expected = old.read_bytes()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://testserver") as active:
                newer = await active.get("/dub/preview-video/gain_fixture?lang=es&bed_gain=3")
                assert newer.status_code == 200
                assert old.exists()
                release[0].set()
                await asyncio.wait_for(tasks[0], 10)
                assert bytes(bodies[0]) == expected
                assert old.exists()  # second lazy response still owns it
                if ending == "revive":
                    revived = await active.get("/dub/preview-video/gain_fixture?lang=es&bed_gain=2")
                    assert revived.status_code == 200 and revived.content == expected
                if ending == "cancel":
                    tasks[1].cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await tasks[1]
                else:
                    release[1].set()
                    if ending == "send_failure":
                        with pytest.raises(OSError, match="owned disconnected"):
                            await tasks[1]
                    else:
                        await asyncio.wait_for(tasks[1], 10)
                        assert bytes(bodies[1]) == expected
                assert old.exists() is (ending == "revive")
                retry = await active.get("/dub/preview-video/gain_fixture?lang=es&bed_gain=2")
                assert retry.status_code == 200
                assert retry.content[4:8] == b"ftyp"
            assert not dub_export._preview_readers
            assert not dub_export._preview_superseded
        finally:
            for event in release:
                event.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_gain_pruning_preserves_other_languages_defaults_and_unowned_files(media_client, symlink_or_skip):
    client, job, folder, _ = media_client
    job["dubbed_tracks"]["fr"] = dict(job["dubbed_tracks"]["es"], language_code="fr", language="French")
    es = "/dub/preview-video/gain_fixture?lang=es"
    assert client.get(es).status_code == 200
    assert client.get(es + "&preserve_bg=false").status_code == 200
    assert client.get("/dub/preview-video/gain_fixture?lang=fr&bed_gain=2").status_code == 200
    assert client.get(es + "&bed_gain=2").status_code == 200
    exports = folder / "exports"
    older = next(exports.glob("preview_v2_es_*_gain_2.mp4"))
    family = older.name.removesuffix("_gain_2.mp4")
    preserved = [exports / (family + suffix) for suffix in ["_gain_notes.mp4", "_gain_02.mp4", "_gain_1.mp4", "_gain_2.tmp.mp4", "_gain_9.mp4"]]
    preserved += [exports / "unrelated-export.mp4", exports / "preview_retimed_es_work.tmp.mp4"]
    for path in preserved:
        path.write_bytes(b"owned unrelated fixture")
    outside = folder / "outside.mp4"
    outside.write_bytes(b"must survive")
    symlink = exports / (family + "_gain_0.5.mp4")
    symlink_or_skip(symlink, outside)
    default_paths = list(exports.glob("preview_v2_es_*.mp4"))
    keep = {p: p.read_bytes() for p in default_paths if "_gain_" not in p.name}
    french = next(exports.glob("preview_v2_fr_*_gain_2.mp4"))
    french_bytes = french.read_bytes()
    assert client.get(es + "&bed_gain=3").status_code == 200
    assert not older.exists()
    assert all(p.read_bytes() == data for p, data in keep.items())
    assert all(p.is_file() for p in preserved)
    assert french.read_bytes() == french_bytes
    assert symlink.is_symlink() and outside.read_bytes() == b"must survive"


def test_failed_preview_publication_keeps_previous_gain_and_retry_works(media_client, monkeypatch):
    import errno
    import os
    client, _, folder, _ = media_client
    url = "/dub/preview-video/gain_fixture?lang=es"
    assert client.get(url + "&bed_gain=2").status_code == 200
    old = next((folder / "exports").glob("*_gain_2.mp4"))
    previous = old.read_bytes()
    replace = os.replace
    def fail_preview_replace(source, target):
        if str(target).endswith("_gain_3.mp4"):
            raise OSError(errno.EIO, "owned preview publish failure")
        return replace(source, target)
    with monkeypatch.context() as failure:
        failure.setattr(os, "replace", fail_preview_replace)
        with pytest.raises(OSError, match="owned preview publish failure"):
            client.get(url + "&bed_gain=3")
    assert old.read_bytes() == previous
    assert not list((folder / "exports").glob("*.tmp.mp4"))
    assert not dub_export._preview_readers
    assert client.get(url + "&bed_gain=3").status_code == 200
    assert not old.exists()
