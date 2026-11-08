"""Dubbing's real chunk worker must retain the user's source-language hint."""

import ast
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from services.asr_backend import transcribe_request_kwargs
from services.dub_language import asr_source_language


@pytest.fixture
def chunk_worker():
    """Compile the actual chunk worker with model-free dependencies for each test."""
    # Compile the nested worker from the route to exercise its actual call
    # without loading ASR weights, diarisation or the application lifespan.
    path = Path(__file__).resolve().parents[1] / "backend/api/routers/dub_core.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    worker = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_transcribe_chunk"
    )
    code = compile(ast.Module(body=[worker], type_ignores=[]), str(path), "exec")

    def run(backend, language):
        """Run a chunk and ensure its source-language choice remains unchanged."""
        job = {"source_lang_override": language}
        scope = {
            "chunk_arr": [],
            "t0": 30.0,
            "sr": 16000,
            "job": job,
            "_asr_backend": backend,
            "_safe_soundfile_write": lambda *args: None,
            "transcribe_request_kwargs": transcribe_request_kwargs,
            "asr_source_language": asr_source_language,
            "tempfile": tempfile,
            "os": os,
        }
        # Execute only this repository's worker, never caller-provided source.
        exec(code, scope)
        result = scope["_transcribe_chunk"]()
        assert job["source_lang_override"] == language
        return result

    return run


@pytest.mark.parametrize(
    "language,expected",
    [
        (None, None),
        ("ja", "ja"),
        ("es", "es"),
        ("zh", "zh"),
        ("cmn-hans", "zh"),
        ("cmn-hant", "zh"),
        ("yue", "yue"),
    ],
)
@pytest.mark.parametrize("supports_language", [True, False])
def test_selected_language_reaches_only_a_language_aware_backend(chunk_worker, language, expected, supports_language):
    """Forward normalized hints only when supported, preserving timestamp offsets."""
    calls = []

    def transcribe(path, *, word_timestamps=True, language=None):
        """Record supported ASR options and return a synthetic timed chunk."""
        calls.append((word_timestamps, language))
        return {
            "chunks": [{"text": "Synthetic words.", "timestamp": (0.25, 0.75)}],
            "language": language or "en",
        }

    def without_language(path, *, word_timestamps=True):
        """Stand in for an ASR backend without a language parameter."""
        return transcribe(path, word_timestamps=word_timestamps)

    backend = SimpleNamespace(id="faster-whisper", transcribe=transcribe if supports_language else without_language)
    result = chunk_worker(backend, language)

    assert calls == [(True, expected if supports_language else None)]
    assert result["chunks"] == [{"text": "Synthetic words.", "timestamp": (30.25, 30.75)}]


@pytest.mark.parametrize(
    "backend_id,accepts_language",
    [
        ("whisperx", False),
        ("faster-whisper", False),
        ("faster-whisper-isolated", False),
        ("mlx-whisper", False),
        ("pytorch-whisper", False),
        ("openai-compat-asr", True),
        ("custom-asr", True),
    ],
)
@pytest.mark.parametrize("language", ["ku", "xh", "zu"])
def test_non_whisper_language_choices_do_not_break_whisper_chunks(chunk_worker, backend_id, accepts_language, language):
    """Use auto-detection for unsupported Whisper codes without restricting other engines."""
    calls = []

    def transcribe(path, *, word_timestamps=True, language=None):
        """Record the hint and produce a chunk without loading an ASR model."""
        calls.append((word_timestamps, language))
        return {"chunks": [{"text": "Synthetic words.", "timestamp": (0.25, 0.75)}]}

    backend = SimpleNamespace(id=backend_id, transcribe=transcribe)
    result = chunk_worker(backend, language)

    assert calls == [(True, language if accepts_language else None)]
    assert result["chunks"] == [{"text": "Synthetic words.", "timestamp": (30.25, 30.75)}]


@pytest.mark.parametrize("error", [TypeError, ValueError])
def test_backend_with_unreadable_signature_keeps_automatic_detection(chunk_worker, monkeypatch, error):
    """Keep ASR working without a hint when its callable cannot be inspected."""
    calls = []

    def transcribe(path, *, word_timestamps=True):
        """Record the timestamp request while simulating automatic detection."""
        calls.append(word_timestamps)
        return {"chunks": [], "language": "en"}

    def unavailable_signature(value):
        """Reproduce a signature-inspection failure from an unsupported callable."""
        raise error("Signature unavailable")

    monkeypatch.setattr("services.asr_backend.inspect.signature", unavailable_signature)
    result = chunk_worker(SimpleNamespace(transcribe=transcribe), "ja")
    assert calls == [True]
    assert result["language"] == "en"
