"""Argos pack requests must recover from an unknown source language.

A whisper.cpp run can transcribe without language metadata, so the job's
source language stays unknown. The pack endpoints used to answer "Transcribe
the source before installing its language pack" - advice for a job that is
already transcribed, while translation is blocked waiting for a pack. The
remedy is choosing the source language, so the gate now returns the same
structured ``source_language_required`` code the translation path uses.

The server-known job fallback and the explicit request precedence are
unchanged: a job that knows its language still answers without a client
source, and an explicit source still wins over the stored one. No pack
installer or download is ever reached here.
"""
import asyncio

import pytest
from api.routers import dub_core, engines
from fastapi import HTTPException


@pytest.fixture
def pack_calls(monkeypatch):
    """Record the pack status/install calls; never touch a real installer."""
    calls = []

    def _status(source_lang, target_langs):
        calls.append(("status", source_lang, list(target_langs)))
        return {"source_lang": source_lang, "pairs": []}

    def _install(source_lang, target_langs):
        calls.append(("install", source_lang, list(target_langs)))
        return {"source_lang": source_lang, "pairs": []}

    monkeypatch.setattr(engines.translation_engines, "argos_pack_status", _status)
    monkeypatch.setattr(engines.translation_engines, "install_argos_packs", _install)
    return calls


def _job_with(monkeypatch, job):
    monkeypatch.setattr(dub_core, "_get_job", lambda job_id: job)


def _request(source_lang=None, job_id=None):
    return engines.ArgosPackRequest(
        source_lang=source_lang,
        target_langs=["fr"],
        job_id=job_id,
    )


def test_pack_status_asks_for_the_source_language_when_it_is_unknown(pack_calls, monkeypatch):
    _job_with(monkeypatch, {"job_id": "job-1"})
    with pytest.raises(HTTPException) as excinfo:
        engines.argos_pack_status(_request(job_id="job-1"))

    error = excinfo.value
    assert error.status_code == 422
    detail = error.detail
    assert isinstance(detail, dict), detail
    assert detail["code"] == "source_language_required"
    # The old advice ("transcribe the source first") sends an already
    # transcribed user down the wrong path.
    assert "transcribe" not in str(detail["message"]).lower()
    assert "source language" in str(detail["message"]).lower()
    # Nothing reached the pack status/installer.
    assert pack_calls == []


def test_pack_install_asks_for_the_source_language_when_it_is_unknown(pack_calls, monkeypatch):
    _job_with(monkeypatch, {"job_id": "job-1"})
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(engines.install_argos_packs(_request(job_id="job-1")))

    error = excinfo.value
    assert error.status_code == 422
    detail = error.detail
    assert isinstance(detail, dict), detail
    assert detail["code"] == "source_language_required"
    # No installer, no download.
    assert pack_calls == []


def test_pack_status_without_a_job_still_asks_for_the_source_language(pack_calls, monkeypatch):
    _job_with(monkeypatch, None)
    with pytest.raises(HTTPException) as excinfo:
        engines.argos_pack_status(_request())

    detail = excinfo.value.detail
    assert isinstance(detail, dict), detail
    assert detail["code"] == "source_language_required"
    assert pack_calls == []


def test_pack_status_falls_back_to_the_source_language_the_job_knows(pack_calls, monkeypatch):
    # The server may know a restored job's language, so a client without a
    # source must not disable the request.
    _job_with(monkeypatch, {"job_id": "job-1", "source_lang": "nl"})
    result = engines.argos_pack_status(_request(job_id="job-1"))

    assert pack_calls == [("status", "nl", ["fr"])]
    assert result["source_lang"] == "nl"


def test_pack_install_falls_back_to_the_source_language_the_job_knows(pack_calls, monkeypatch):
    _job_with(monkeypatch, {"job_id": "job-1", "source_lang": "nl"})
    asyncio.run(engines.install_argos_packs(_request(job_id="job-1")))

    assert pack_calls == [("install", "nl", ["fr"])]


def test_explicit_source_language_wins_over_the_stored_job_language(pack_calls, monkeypatch):
    # The deliberate choice is what the translation runs on, so the pack
    # request must use it rather than the language stored with the job.
    _job_with(monkeypatch, {"job_id": "job-1", "source_lang": "en"})
    engines.argos_pack_status(_request(source_lang="nl", job_id="job-1"))
    asyncio.run(engines.install_argos_packs(_request(source_lang="nl", job_id="job-1")))

    assert pack_calls == [("status", "nl", ["fr"]), ("install", "nl", ["fr"])]


def test_pack_status_keeps_every_requested_target(pack_calls, monkeypatch):
    _job_with(monkeypatch, {"job_id": "job-1", "source_lang": "nl"})
    request = engines.ArgosPackRequest(source_lang="nl", target_langs=["fr", "de"], job_id="job-1")
    engines.argos_pack_status(request)

    assert pack_calls == [("status", "nl", ["fr", "de"])]
