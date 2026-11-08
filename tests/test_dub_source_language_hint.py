"""Dubbing's real chunk worker must retain the user's source-language hint."""

import ast
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from services.asr_backend import transcribe_request_kwargs


@pytest.fixture
def chunk_worker():
    # Compile the nested worker from the route to exercise its actual call
    # without loading ASR weights, diarisation or the application lifespan.
    path = Path(__file__).resolve().parents[1] / "backend/api/routers/dub_core.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    worker = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_transcribe_chunk"
    )
    code = compile(ast.Module(body=[worker], type_ignores=[]), str(path), "exec")

    def run(backend, language):
        scope = {
            "chunk_arr": [],
            "t0": 30.0,
            "sr": 16000,
            "job": {"source_lang_override": language},
            "_asr_backend": backend,
            "_safe_soundfile_write": lambda *args: None,
            "transcribe_request_kwargs": transcribe_request_kwargs,
            "tempfile": tempfile,
            "os": os,
        }
        exec(code, scope)
        return scope["_transcribe_chunk"]()

    return run


@pytest.mark.parametrize("language", [None, "ja", "es"])
@pytest.mark.parametrize("supports_language", [True, False])
def test_selected_language_reaches_only_a_language_aware_backend(chunk_worker, language, supports_language):
    calls = []

    def transcribe(path, *, word_timestamps=True, language=None):
        calls.append((word_timestamps, language))
        return {
            "chunks": [{"text": "Synthetic words.", "timestamp": (0.25, 0.75)}],
            "language": language or "en",
        }

    def without_language(path, *, word_timestamps=True):
        return transcribe(path, word_timestamps=word_timestamps)

    backend = SimpleNamespace(transcribe=transcribe if supports_language else without_language)
    result = chunk_worker(backend, language)

    assert calls == [(True, language if supports_language else None)]
    assert result["chunks"] == [{"text": "Synthetic words.", "timestamp": (30.25, 30.75)}]


@pytest.mark.parametrize("error", [TypeError, ValueError])
def test_backend_with_unreadable_signature_keeps_automatic_detection(chunk_worker, monkeypatch, error):
    calls = []

    def transcribe(path, *, word_timestamps=True):
        calls.append(word_timestamps)
        return {"chunks": [], "language": "en"}

    def unavailable_signature(value):
        raise error("Signature unavailable")

    monkeypatch.setattr("services.asr_backend.inspect.signature", unavailable_signature)
    result = chunk_worker(SimpleNamespace(transcribe=transcribe), "ja")
    assert calls == [True]
    assert result["language"] == "en"
