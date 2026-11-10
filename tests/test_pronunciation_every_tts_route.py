"""The pronunciation dictionary reaches every route that hands text to TTS.

Before this fix only POST /generate applied the saved dictionary and inline
``[[word|respelling]]`` overrides. Dubbing (render, segment preview, batch),
the OpenAI-compatible API, streaming TTS and voice conversion each called
``normalize_for_tts`` directly, so a user who taught the app "đầu" (or "GIF")
heard the dub mispronounce it anyway, and an inline override was spoken with
its brackets. Every route now goes through ``services.pronunciation.
prepare_tts_text``; the AST guard below keeps a new route from bypassing it.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import ast
import asyncio
import importlib
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# Files allowed to call normalize_for_tts directly, with the reason.
_ALLOWED_DIRECT_NORMALIZE = {
    # The pass itself.
    "services/text_normalization.py",
    # The shared choke point that wraps it.
    "services/pronunciation.py",
    # Longform chapters apply the per-project lexicon + inline overrides in
    # synthesize_chapter, folded into the chapter cache key.
    "api/routers/audiobook.py",
}


def _direct_normalize_callers() -> list[str]:
    hits = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "omnivoice_data/")) or "/.venv/" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "normalize_for_tts" and rel not in _ALLOWED_DIRECT_NORMALIZE:
                hits.append(f"{rel}:{node.lineno}")
    return hits


def test_no_tts_route_bypasses_the_pronunciation_choke_point():
    assert _direct_normalize_callers() == [], (
        "These call normalize_for_tts directly and so skip the pronunciation "
        "dictionary; call services.pronunciation.prepare_tts_text instead."
    )


# ── Behaviour ────────────────────────────────────────────────────────────────

_ROWS = [{
    "id": "1", "term": "khởi đầu", "replacement": "khởi đầuu", "type": "respelling",
    "language": "*", "enabled": 1, "created_at": "2026-11-10",
}]


@pytest.fixture()
def dictionary(monkeypatch):
    # Import the app first: routers bind load_entries_from_db by name at
    # import time, and must keep the real one after this test.
    importlib.import_module("main")
    pron = importlib.import_module("services.pronunciation")
    monkeypatch.setattr(pron, "load_entries_from_db", lambda: list(_ROWS))
    monkeypatch.delenv("OMNIVOICE_PRONUNCIATION", raising=False)
    return pron


def test_prepare_tts_text_applies_dictionary_and_inline_overrides(dictionary):
    out = dictionary.prepare_tts_text("Vũ trụ có một khởi đầu, [[GIF|jiff]].", "vi")
    assert out == "Vũ trụ có một khởi đầuu, jiff."


def test_prepare_tts_text_dictionary_off_still_resolves_inline(dictionary, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PRONUNCIATION", "0")
    out = dictionary.prepare_tts_text("một khởi đầu, [[GIF|jiff]]", "vi")
    assert out == "một khởi đầu, jiff"
    assert dictionary.prepare_tts_text(
        "một khởi đầu", "vi", use_dictionary=False) == "một khởi đầu"


def test_prepare_tts_text_survives_a_broken_db(monkeypatch):
    pron = importlib.import_module("services.pronunciation")

    def _boom():
        raise RuntimeError("no such table: pronunciation_entries")

    monkeypatch.setattr(pron, "load_entries_from_db", _boom)
    assert pron.prepare_tts_text("một khởi đầu", "vi") == "một khởi đầu"


def _fake_engine_cls():
    tb = importlib.import_module("services.tts_backend")

    class _Fake(tb.TTSBackend):
        id = "fake-pron-route"
        display_name = "Fake Pronunciation Route Engine (test)"
        supports_cloning = True
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append(text)
            return torch.zeros(1, 2400)

    return _Fake


def test_dub_segment_preview_speaks_the_dictionary_respelling(dictionary, monkeypatch):
    import api.routers.dub_generate as dg

    fake = _fake_engine_cls()
    fake.calls = []

    async def _resolve(**kw):
        return fake()

    monkeypatch.setattr(dg, "resolve_generation_backend", _resolve)
    monkeypatch.setattr(dg, "_get_job", lambda job_id: {"speaker_clones": {}})

    asyncio.run(dg.preview_segment("jobP", dg.SegmentPreviewRequest(
        text="Vũ trụ có một khởi đầu, [[GIF|jiff]].", language="vi")))
    assert fake.calls == ["Vũ trụ có một khởi đầuu, jiff."]


def test_openai_speech_speaks_the_dictionary_respelling(dictionary, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    tb = importlib.import_module("services.tts_backend")
    tb.reset_active_backend()
    fake = _fake_engine_cls()
    fake.calls = []
    monkeypatch.setitem(tb._REGISTRY, "fake-pron-route", fake)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        res = client.post("/v1/audio/speech", json={
            "model": "fake-pron-route", "input": "một khởi đầu",
            "language": "vi", "response_format": "wav",
        })
        assert res.status_code == 200, res.text
        assert fake.calls == ["một khởi đầuu"]
    finally:
        tb.reset_active_backend()
