"""#2574: dub QC scores the recognized audio against the SELECTED track's text.

`job["segments"]` holds whichever language was generated last; QC of another
track must compare that track's speech with that track's text
(`job["segments_i18n"]`), not with a different language.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")


@pytest.fixture
def qc_env(tmp_path, monkeypatch):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline

    job_id = "job-qc"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    for lang in ("es", "fr"):
        (job_dir / f"dubbed_{lang}.wav").write_bytes(b"RIFF")
    job = {
        "id": job_id,
        "dubbed_tracks": {
            "es": {"path": str(job_dir / "dubbed_es.wav")},
            "fr": {"path": str(job_dir / "dubbed_fr.wav")},
        },
        # French was generated last, so the flat segments carry French text.
        "segments": [
            {"id": "a", "start": 0.0, "end": 1.0, "text": "bonjour le monde"},
            {"id": "b", "start": 1.0, "end": 2.0, "text": "merci beaucoup"},
        ],
        "segments_i18n": {
            "es": {"a": "hola mundo", "b": "muchas gracias"},
            "fr": {"a": "bonjour le monde", "b": "merci beaucoup"},
        },
    }
    heard: dict[str, list] = {}
    transcribed: list[str] = []

    async def _guarded(_pool, fn, **_kw):
        return fn()

    class _Backend:
        id = "fake-asr"

        def transcribe(self, path, **_kw):
            transcribed.append(os.path.basename(path))
            return {"segments": heard[os.path.basename(path)]}

    monkeypatch.setattr(dub_export, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dub_export, "_get_job", lambda jid: job if jid == job_id else None)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", _guarded)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", lambda: _Backend())
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)
    import services.model_manager as model_manager
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    heard["dubbed_es.wav"] = [
        {"start": 0.0, "end": 1.0, "text": "hola mundo"},
        {"start": 1.0, "end": 2.0, "text": "muchas gracias"},
    ]
    heard["dubbed_fr.wav"] = [
        {"start": 0.0, "end": 1.0, "text": "bonjour le monde"},
        {"start": 1.0, "end": 2.0, "text": "merci beaucoup"},
    ]
    return {"module": dub_export, "job_id": job_id, "job": job, "transcribed": transcribed}


def _qc(env, lang):
    return asyncio.run(env["module"].dub_qc_pass(env["job_id"], lang=lang, drift_threshold=0.5))


def test_selected_track_is_scored_against_its_own_language(qc_env):
    res = _qc(qc_env, "es")
    assert qc_env["transcribed"] == ["dubbed_es.wav"]
    assert res["flagged_count"] == 0
    segs = qc_env["job"]["segments"]
    assert [s["qc_drift"] for s in segs] == [0.0, 0.0]
    # Annotations only — the stored text is untouched.
    assert segs[0]["text"] == "bonjour le monde"


def test_default_track_uses_its_own_language_text(qc_env):
    # No (or an unknown) language falls back to the first track: Spanish here.
    for lang in (None, "de"):
        assert _qc(qc_env, lang)["flagged_count"] == 0
    assert qc_env["transcribed"] == ["dubbed_es.wav", "dubbed_es.wav"]


def test_last_generated_track_and_legacy_jobs_keep_flat_text(qc_env):
    assert _qc(qc_env, "fr")["flagged_count"] == 0
    del qc_env["job"]["segments_i18n"]
    # A job predating per-language text can only score the flat text.
    assert _qc(qc_env, "es")["flagged_count"] == 2


def test_idless_segments_use_index_keys(qc_env):
    job = qc_env["job"]
    for seg in job["segments"]:
        del seg["id"]
    job["segments_i18n"]["es"] = {"0": "hola mundo", "1": "muchas gracias"}
    assert _qc(qc_env, "es")["flagged_count"] == 0
