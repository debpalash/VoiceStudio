"""#4347 untranslated renders are refused; #4346 a missing bed falls back to speech-only."""
import asyncio
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

def _req(texts, ids=None, code="pt", dur=1):
    DubRequest = importlib.import_module("schemas.requests").DubRequest
    return DubRequest(
        segments=[{"start": i, "end": i + dur, "text": t} for i, t in enumerate(texts)],
        segment_ids=ids or [f"s{i}" for i in range(len(texts))],
        language="Portuguese", language_code=code,
    )


def _guard():
    return importlib.import_module("api.routers.dub_generate")._untranslated_track_error


SOURCE = [{"id": "s0", "text": "This is helium.", "start": 0, "end": 1},
          {"id": "s1", "text": "It is cold.", "start": 1, "end": 2}]


def test_untranslated_rows_with_unknown_source_are_refused():
    job = {"source_lang": None, "source_lang_detection_missing": True, "segments": SOURCE}
    assert _guard()(job, _req(["This is helium.", "It is cold."]), "pt") == "no_translation"


def test_untranslated_rows_with_known_other_source_are_refused():
    job = {"source_lang": "en", "segments": SOURCE}
    assert _guard()(job, _req(["This is helium.", "It is cold."]), "pt") == "no_translation"


def test_legacy_job_without_any_source_language_marker_is_unchanged():
    job = {"segments": SOURCE}
    assert _guard()(job, _req(["This is helium.", "It is cold."]), "pt") is None


def test_translated_rows_are_allowed():
    job = {"source_lang": None, "segments": SOURCE}
    assert _guard()(job, _req(["Isto e helio.", "Faz frio."]), "pt") is None


def test_partially_translated_rows_are_allowed():
    job = {"source_lang": "en", "segments": SOURCE}
    assert _guard()(job, _req(["This is helium.", "Faz frio."]), "pt") is None


def test_same_language_source_is_allowed():
    job = {"source_lang_override": "pt-BR", "segments": SOURCE}
    assert _guard()(job, _req(["This is helium.", "It is cold."]), "pt") is None


def test_regenerating_a_translated_track_compares_against_text_original():
    job = {"segments": [{**SOURCE[0], "text": "Isto e helio.", "text_original": "This is helium."}]}
    assert _guard()(job, _req(["Isto e helio."], ids=["s0"]), "pt") is None


def test_rows_without_letters_do_not_count():
    job = {"segments": [{"id": "s0", "text": "...", "start": 0, "end": 1}]}
    assert _guard()(job, _req(["..."], ids=["s0"]), "pt") is None


def test_rows_the_renderer_silences_do_not_count_as_speech():
    """A changed row <= the silence threshold must not admit a request whose
    audible rows are all still the source text."""
    job = {"source_lang": "en", "segments": [
        {"id": "s0", "text": "This is helium.", "start": 0, "end": 1},
        {"id": "s1", "text": "Hi.", "start": 1, "end": 1.04},
    ]}
    req = _req(["This is helium.", "Oi."], dur=1)
    req.segments[1].end = req.segments[1].start + 0.04
    assert _guard()(job, req, "pt") == "no_translation"


# 4346

@pytest.fixture
def export_module(monkeypatch):
    core = types.ModuleType("api.routers.dub_core")
    core._get_job = lambda job_id: None
    core._safe_lang_or_400 = lambda lang: lang
    monkeypatch.setitem(sys.modules, "api.routers.dub_core", core)
    path = Path(__file__).parents[1] / "backend/api/routers/dub_export.py"
    spec = importlib.util.spec_from_file_location("bed_fallback_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_optional_dub_artifact", lambda value, *a: value or None)
    return module


def test_missing_bed_falls_back_to_speech_only(export_module, caplog):
    job = {"video_path": "v.mp4", "no_vocals_path": None, "background_separation": "failed",
           "segments": [{"start": 0, "end": 1}]}
    with caplog.at_level("WARNING"):
        assert asyncio.run(export_module._preserved_background(job, "j", "pt")) is None
    assert "speech-only" in caplog.text


def test_legacy_job_without_bed_or_flag_also_falls_back(export_module):
    assert asyncio.run(export_module._preserved_background({"video_path": "v.mp4"}, "j", "pt")) is None


def test_missing_source_with_bed_is_still_a_409(export_module):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        asyncio.run(export_module._preserved_background({"no_vocals_path": "bed.wav"}, "j", "pt"))
    assert caught.value.status_code == 409


def test_cache_does_not_reuse_a_job_whose_bed_is_missing(tmp_path, monkeypatch):
    """Legacy failed-separation jobs (no flag) held vocals == source and no bed."""
    from core.db import db_conn, init_db
    from services import dub_pipeline as dp

    init_db()
    d = tmp_path / "legacy"
    d.mkdir()
    for name in ("vocals.wav", "audio_hq.wav"):
        (d / name).write_bytes(b"RIFF")
    monkeypatch.setattr(dp, "safe_job_dir", lambda jid: str(tmp_path / jid))

    def seed(extra):
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dub_history (id, job_data, content_hash, created_at)"
                " VALUES (?, ?, ?, datetime('now'))",
                ("legacy", json.dumps({"vocals_path": str(d / "vocals.wav"), **extra}), "hash_legacy"),
            )
            conn.commit()

    seed({"no_vocals_path": None})
    assert dp.find_cached_job("hash_legacy", "other") is None
    (d / "no_vocals.wav").write_bytes(b"RIFF")
    seed({"no_vocals_path": str(d / "no_vocals.wav")})
    assert dp.find_cached_job("hash_legacy", "other")["job_id"] == "legacy"


def test_each_download_track_resolves_its_own_bed(export_module):
    src = Path(export_module.__file__).read_text()
    loop = src[src.index("for lang_code, track_info in filtered_tracks.items():"):]
    loop = loop[: loop.index("tracks_to_process.append")]
    assert loop.index("bg_idx = None") < loop.index("_preserved_background")
