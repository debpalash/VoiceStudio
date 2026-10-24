"""Explicit language selection must never export a different language."""
import asyncio
import importlib.util
import io
from pathlib import Path
import sys
import types
import zipfile

import pytest


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
    return module


@pytest.mark.parametrize("endpoint", ["dub_download_audio", "dub_download_mp3", "dub_export_stems"])
def test_explicit_missing_language_never_exports_a_different_track(export_module, monkeypatch, tmp_path, endpoint):
    from fastapi import HTTPException
    module = export_module
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_existing_job_dir_or_404", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: {"dubbed_tracks": {"de": {"path": "german.wav"}}})
    def reject_wrong_track(*args, **kwargs):
        pytest.fail("An unavailable Spanish track must not resolve German audio")
    monkeypatch.setattr(module, "_dub_artifact", reject_wrong_track)
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(module, endpoint)("job", lang="es"))
    assert error.value.status_code == 404


@pytest.mark.parametrize("lang", [None, "de"])
def test_stems_default_and_existing_language_keep_working(export_module, monkeypatch, tmp_path, lang):
    module = export_module
    source = tmp_path / "german.wav"
    source.write_bytes(b"German voice")
    monkeypatch.setattr(module, "_job_dir_or_400", lambda _: str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: {"dubbed_tracks": {"de": {"path": str(source)}}})
    monkeypatch.setattr(module, "_dub_artifact", lambda value, *a, **kw: value)
    response = asyncio.run(module.dub_export_stems("job", lang=lang))
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.read("vocals_dubbed_de.wav") == b"German voice"
