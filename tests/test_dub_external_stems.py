"""External separation uses job-owned WAVs and an administrative API."""

import asyncio
import importlib
import json
import os
import shutil
import uuid
from collections import OrderedDict

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    """Create a fresh job with a synthetic upload and full-length WAV stems."""
    from core.db import init_db

    dp = importlib.import_module("services.dub_pipeline")
    init_db()
    monkeypatch.setattr(dp, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dp, "_DUB_DIR_REAL", os.path.realpath(tmp_path))
    monkeypatch.setattr(dp, "_dub_jobs", {})
    monkeypatch.setattr(dp, "_inflight_jobs", set())
    monkeypatch.setattr(dp, "_withdrawn_jobs", OrderedDict())
    job_id = "external-stems-" + uuid.uuid4().hex
    folder = tmp_path / job_id
    folder.mkdir()
    sf.write(folder / "audio.wav", np.zeros(2 * 16000), 16000)
    for name in ("vocals.wav", "bed.wav"):
        sf.write(folder / name, np.zeros((2 * 44100, 2)), 44100)
    audio = str(folder / "audio.wav")
    dp.put_job(job_id, {
        "external_stems": True, "background_separation": "skipped", "duration": 2.0,
        "filename": "fixture.wav", "audio_path": audio, "vocals_path": audio,
        "no_vocals_path": None, "segments": None, "dubbed_tracks": {},
    })
    return dp, job_id, folder


@pytest.fixture
def clients():
    """Mount the real router with loopback and remote peers."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    router = importlib.import_module("api.routers.dub_core").router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, client=("127.0.0.1", 50000)), TestClient(app, client=("198.51.100.10", 50000))


def test_attach_stems_changes_both_paths_and_persists(clients, prepared):
    """The route publishes both stems together and stores the result for restart."""
    dp, job_id, folder = prepared
    local, _ = clients
    request = {"vocals": "vocals.wav", "no_vocals": "bed.wav"}
    response = local.post(f"/dub/stems/{job_id}", json=request)
    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": job_id, "has_bg": True}
    dp._dub_jobs.clear()
    job = dp.get_job(job_id)
    assert job["vocals_path"] == str((folder / "vocals.wav").resolve())
    assert job["no_vocals_path"] == str((folder / "bed.wav").resolve())
    assert job["background_separation"] == "ok"


@pytest.mark.parametrize("name", ["../bed.wav", "..\\bed.wav", "C:bed.wav", "missing.wav", "short.wav", "broken.wav"])
def test_bad_bed_leaves_the_original_audio_available(clients, prepared, name):
    """Reject invalid filenames and WAVs before attaching either stem."""
    dp, job_id, folder = prepared
    local, _ = clients
    sf.write(folder / "short.wav", np.zeros(1600), 16000)
    (folder / "broken.wav").write_bytes(b"invalid WAV")
    response = local.post(f"/dub/stems/{job_id}", json={"vocals": "vocals.wav", "no_vocals": name})
    assert response.status_code == 400
    job = dp.get_job(job_id)
    assert job["vocals_path"] == job["audio_path"]
    assert job["no_vocals_path"] is None


def test_attach_requires_admin_and_completed_external_preparation(clients, prepared):
    """Only an administrator can attach stems to a prepared, opted-in job."""
    dp, job_id, _ = prepared
    local, remote = clients
    url = f"/dub/stems/{job_id}"
    request = {"vocals": "vocals.wav"}
    assert remote.post(url, json=request).status_code == 403
    assert local.post("/dub/stems/unknown-stem-job", json=request).status_code == 404
    dp._inflight_jobs.add(job_id)
    assert local.post(url, json=request).status_code == 400
    dp._inflight_jobs.clear()
    dp.get_job(job_id)["external_stems"] = False
    assert local.post(url, json=request).status_code == 400
    dp.get_job(job_id)["external_stems"] = True
    response = local.post(url, json=request)
    assert response.status_code == 200
    assert response.json()["has_bg"] is False


def test_upload_passes_external_stems_opt_in(monkeypatch, clients, prepared):
    """Multipart upload forwards the caller's opt-in to the ingest worker."""
    local, _ = clients
    core = importlib.import_module("api.routers.dub_core")
    seen = {}

    async def capture(task_id, kind, worker, job_id, job_dir, source, filename):
        """Capture the queued ingest options without running a media task."""
        seen.update(source)

    monkeypatch.setattr(core.task_manager, "add_task", capture)
    response = local.post("/dub/upload", files={"video": ("fixture.wav", b"RIFF0000WAVE", "audio/wav")},
                          data={"input_type": "audio", "external_stems": "true"})
    assert response.status_code == 202, response.text
    assert seen["external_stems"] is True


def test_real_ffmpeg_extracts_mono_and_stereo_for_external_stems(prepared):
    """The local audio ingest retains the HQ WAV that a separator consumes."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for this test")
    dp, job_id, folder = prepared
    output = folder.parent / "extracted"
    output.mkdir()

    async def collect():
        """Run the real extraction on the synthetic source without a model."""
        return [json.loads(event.removeprefix("data: ")) async for event in dp.ingest_pipeline(
            job_id + "-extracted", str(output), {
                "kind": "file", "path": str(folder / "audio.wav"),
                "input_type": "audio", "external_stems": True,
            }, "fixture.wav",
        )]

    events = asyncio.run(collect())
    assert events[-1]["type"] == "ready", events
    assert next(event for event in events if event["type"] == "demucs_done")["skipped"] is True
    mono, stereo = (sf.info(output / name) for name in ("audio.wav", "audio_hq.wav"))
    assert (mono.samplerate, mono.channels, mono.frames) == (16000, 1, 32000)
    assert (stereo.samplerate, stereo.channels, stereo.frames) == (44100, 2, 88200)


def test_attaching_stems_supersedes_a_running_transcription(clients, prepared):
    """A transcript of the old audio cannot publish over the attached stems."""
    dp, job_id, _ = prepared
    local, _ = clients
    started = dp.segments_revision(dp.get_job(job_id))
    response = local.post(f"/dub/stems/{job_id}", json={"vocals": "vocals.wav", "no_vocals": "bed.wav"})
    assert response.status_code == 200, response.text
    assert dp.segments_revision(dp.get_job(job_id)) != started
    dp._dub_jobs.clear()
    assert dp.segments_revision(dp.get_job(job_id)) != started


def test_a_reused_external_job_id_never_serves_the_ingest_cache(clients, prepared):
    """An external job's row can keep an older hash; it is still not a cache hit."""
    dp, job_id, folder = prepared
    local, _ = clients
    sf.write(folder / "audio_hq.wav", np.zeros((2 * 44100, 2)), 44100)
    response = local.post(f"/dub/stems/{job_id}", json={"vocals": "vocals.wav", "no_vocals": "bed.wav"})
    assert response.status_code == 200, response.text
    with dp.db_conn() as conn:
        conn.execute("UPDATE dub_history SET content_hash=? WHERE id=?", ("earlier-hash", job_id))
    assert dp.find_cached_job("earlier-hash", "another-job") is None
