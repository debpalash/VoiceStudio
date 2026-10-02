"""QC scores the track actually selected, rather than the last generated text."""
import asyncio
import copy

import pytest


@pytest.mark.parametrize("requested_lang", ["es", None, "unknown"])
def test_qc_uses_selected_track_authoritative_text(monkeypatch, requested_lang):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline, model_manager

    job = {
        "segments": [{"id": "a", "start": 0.0, "end": 2.0, "text": "ohe prithibi"}],
        "segments_i18n": {"es": {"a": "hola mundo"}, "bn": {"a": "ohe prithibi"}},
        "dubbed_tracks": {"es": {"path": "spanish.wav"}, "bn": {"path": "bengali.wav"}},
        "seg_order": ["a"],
    }
    original = copy.deepcopy(job)
    heard = []

    class Backend:
        id = "test-local-asr"
        def transcribe(self, path, **kwargs):
            heard.append(path)
            return {"segments": [{"start": 0.0, "end": 2.0, "text": "hola mundo"}]}

    async def guarded(pool, fn, **kwargs):
        return fn()

    monkeypatch.setattr(dub_export, "_job_dir_or_400", lambda job_id: None)
    monkeypatch.setattr(dub_export, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dub_export, "_dub_artifact", lambda path, *a, **kw: path)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", Backend)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", guarded)
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)

    result = asyncio.run(dub_export.dub_qc_pass("test", lang=requested_lang, drift_threshold=0.5))
    assert heard == ["spanish.wav"]
    assert result["flagged_count"] == 0
    assert result["segments"][0]["drift"] == 0.0
    assert job["segments"][0]["text"] == original["segments"][0]["text"]
    assert job["segments_i18n"] == original["segments_i18n"]
    assert job["segments"][0]["qc_recognized"] == "hola mundo"
