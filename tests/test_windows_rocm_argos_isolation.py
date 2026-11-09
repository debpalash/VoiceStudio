"""Argos must not load CTranslate2 into Torch's Windows ROCm 10 process.

Torch's LLVM OpenMP and CTranslate2's Intel OpenMP crash when both load in
one process, and Argos only runs in-process, so ROCm 10 refuses it before
`argostranslate.translate` is imported: in the engine list and on translate.
"""
import builtins
import importlib
import json

import pytest


@pytest.fixture
def rocm10(monkeypatch):
    isolation = importlib.import_module("core.windows_rocm.isolation_policy")
    monkeypatch.setattr(isolation, "isolation_required", lambda: True)
    original = builtins.__import__

    def no_ct2(name, *args, **kwargs):
        if name.startswith("argostranslate.translate") or name.startswith("ctranslate2"):
            pytest.fail(f"{name} was imported in the ROCm 10 backend process")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_ct2)
    real_import_module = importlib.import_module

    def guarded(name, *args, **kwargs):
        if name.startswith("argostranslate.translate"):
            pytest.fail(f"{name} was imported in the ROCm 10 backend process")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", guarded)
    return isolation


@pytest.mark.parametrize("import_check", [True, False])
def test_engine_list_refuses_argos_without_importing_it(rocm10, import_check):
    engines = importlib.import_module("services.translation_engines")
    ready, reason = engines._probe(engines.REGISTRY["argos"], import_check=import_check)
    assert ready is False
    assert reason == rocm10.ARGOS_UNAVAILABLE and "OpenMP" in reason and "NLLB" in reason


@pytest.mark.asyncio
async def test_translate_refuses_argos_before_loading_ctranslate2(rocm10):
    from api.routers.dub_translate import dub_translate
    from schemas.requests import TranslateRequest

    response = await dub_translate(TranslateRequest(
        provider="argos", source_lang="en", target_lang="es", segments=[{"id": "1", "text": "Hello"}]))
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["detail"]["code"] == "argos_runtime_unavailable"
    assert "ROCm 10" in body["error"]


def test_other_environments_keep_argos(monkeypatch):
    isolation = importlib.import_module("core.windows_rocm.isolation_policy")
    monkeypatch.setattr(isolation, "isolation_required", lambda: False)
    assert isolation.argos_error() is None
