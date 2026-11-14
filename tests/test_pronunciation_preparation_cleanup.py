"""Cancelling speech preparation releases the request-owned reference upload."""
import asyncio
import importlib
import io
from pathlib import Path
import threading
import wave

import httpx
import pytest

from test_text_normalization_routes import fake_engine, saved_pronunciations  # noqa: F401


@pytest.mark.parametrize("stream", ["0", "1"])
def test_cancel_during_preparation_deletes_uploaded_reference(
    fake_engine, saved_pronunciations, monkeypatch, tmp_path, stream,
):
    from main import app
    from api.routers import generation

    pronunciation = importlib.import_module("services.pronunciation")
    prepare = pronunciation.prepare_speech_text
    started, release = threading.Event(), threading.Event()
    references = []
    temporary_file = generation.tempfile.NamedTemporaryFile

    def owned_reference(*args, **kwargs):
        kwargs["dir"] = tmp_path
        handle = temporary_file(*args, **kwargs)
        references.append(Path(handle.name))
        return handle

    def delayed_prepare(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5), "preparation worker was not released"
        return prepare(*args, **kwargs)

    monkeypatch.setattr(generation.tempfile, "NamedTemporaryFile", owned_reference)
    monkeypatch.setattr(pronunciation, "prepare_speech_text", delayed_prepare)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as handle:
        handle.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        handle.writeframes(bytes(4800))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost") as api:
            request = asyncio.create_task(api.post("/generate", data={
                "engine": "fake-norm-route", "text": "cats", "language": "English",
                "ref_text": "reference transcript", "stream": stream,
            }, files={"ref_audio": ("reference.wav", audio.getvalue(), "audio/wav")}))
            try:
                assert await asyncio.to_thread(started.wait, 5), "request never reached preparation"
                assert len(references) == 1 and references[0].exists()
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                # Text preparation does not read the reference, so the request
                # can delete it even while that unrelated worker is draining.
                assert not references[0].exists()
                assert not fake_engine.calls
            finally:
                release.set()
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    asyncio.run(run())
