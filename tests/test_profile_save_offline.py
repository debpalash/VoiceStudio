"""Saving a voice must not depend on the network (#2583).

Two independent guards:

* the best-effort reference transcript taken during save has a wall-clock
  deadline, so a stalled model load or network read cannot hold the save open;
* installed ASR weights load from their local snapshot directory instead of by
  repo id, which asked the Hub for ``main`` first (an untimed request that
  stalls on a packet-dropping network and cannot resolve a pinned install).
"""
import asyncio
import io
import os
import socket
import sys
import threading
import time
import types
import wave

import httpx
import pytest
from fastapi import FastAPI

_REPO = "deepdml/faster-whisper-large-v3-turbo-ct2"


def _wav() -> bytes:
    data = io.BytesIO()
    with wave.open(data, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x01\x00" * 4000)
    return data.getvalue()


@pytest.fixture
def stalled_transcription(monkeypatch):
    """transcribe_reference blocks like an untimed request on a dead network."""
    from services import asr_backend

    release = threading.Event()
    monkeypatch.setattr(asr_backend, "transcribe_reference", lambda _path: release.wait(30) and "late")
    monkeypatch.setenv("OMNIVOICE_PROFILE_TRANSCRIBE_TIMEOUT_S", "0.3")
    yield release
    release.set()


def _timed(coro_factory, release):
    """Seconds until the awaited result, measured inside the running loop
    (``asyncio.run`` itself joins the abandoned worker at shutdown)."""
    async def run():
        started = time.monotonic()
        try:
            return await coro_factory(), time.monotonic() - started
        finally:
            release.set()

    return asyncio.run(run())


def test_reference_transcription_has_a_wall_clock_deadline(stalled_transcription):
    from api.routers import profiles

    text, elapsed = _timed(lambda: profiles._auto_transcribe_reference("ref.wav"), stalled_transcription)
    assert text == ""
    assert elapsed < 5


def test_clone_profile_saves_while_transcription_is_stalled(stalled_transcription, tmp_path, monkeypatch):
    from api.routers import profiles
    from core import db

    monkeypatch.setattr(profiles, "VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr(profiles.event_bus, "emit", lambda *_a: None)
    db.init_db()
    app = FastAPI()
    app.include_router(profiles.router)

    async def save():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            return await http.post(
                "/profiles",
                data={"name": "Offline"},
                files={"ref_audio": ("voice.wav", _wav(), "audio/wav")},
            )

    response, elapsed = _timed(save, stalled_transcription)
    assert response.status_code == 200, response.text
    assert response.json()["ref_text"] == ""
    assert elapsed < 5


@pytest.fixture
def installed_snapshot(tmp_path, monkeypatch):
    """A complete pinned-revision install (no refs/main), as the store writes it."""
    from services.hf_revisions import revision_for

    cache = tmp_path / "hub"
    repo_dir = cache / ("models--" + _REPO.replace("/", "--"))
    snapshot = repo_dir / "snapshots" / revision_for(_REPO)
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    with open(snapshot / "model.bin", "wb") as weights:
        weights.truncate(64 * 1024 * 1024)
    (repo_dir / "voicestudio-revision").write_text(revision_for(_REPO) + "\n")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    return str(snapshot)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access while loading installed weights")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_installed_snapshot_path_prefers_the_recorded_revision(installed_snapshot):
    from api.routers.setup.models import installed_snapshot_path

    assert installed_snapshot_path(_REPO) == installed_snapshot
    assert installed_snapshot_path("not/in-catalogue") is None


def test_faster_whisper_loads_installed_weights_by_path(installed_snapshot, no_network, monkeypatch):
    from services import asr_backend

    loaded = []

    class FakeWhisper:
        def __init__(self, source, **_kwargs):
            loaded.append(source)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(asr_backend, "_ctranslate2_execstack_ok", lambda: (True, ""))
    monkeypatch.setattr(asr_backend, "_ctranslate2_cuda_ok", lambda: False)

    asr_backend.FasterWhisperBackend(model_name=_REPO)._ensure_model()
    assert loaded == [installed_snapshot]


def test_mlx_whisper_warmup_loads_the_path_transcribe_uses(installed_snapshot, no_network, monkeypatch):
    """Startup warmup loaded by repo id: offline it could not resolve a pinned
    install, and it warmed a different ModelHolder key than transcribe."""
    from services import asr_backend

    loaded = []

    class FakeHolder:
        @staticmethod
        def get_model(source, dtype):
            loaded.append(source)

    mlx = types.ModuleType("mlx")
    mlx.core = types.SimpleNamespace(float16="float16")
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mlx.core)
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", types.SimpleNamespace(ModelHolder=FakeHolder))

    asr_backend.MLXWhisperBackend(model_name=_REPO).warmup()
    assert loaded == [installed_snapshot]


def test_uninstalled_or_custom_names_keep_loading_by_name(tmp_path, monkeypatch):
    from services import asr_backend

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
    assert asr_backend._local_model_source(_REPO) == _REPO
    assert asr_backend._local_model_source("my-org/custom") == "my-org/custom"
    assert asr_backend._local_model_source(str(tmp_path)) == str(tmp_path)
    assert os.path.isdir(str(tmp_path))
