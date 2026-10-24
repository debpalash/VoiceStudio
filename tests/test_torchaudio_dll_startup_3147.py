"""#3147: torchaudio DLL load failures on Windows must not abort backend startup.

On Windows (Python 3.8+), native extension libraries (such as
``libtorchaudio.pyd``) dynamically link against PyTorch core DLLs (``c10.dll``,
``torch_cpu.dll``) in ``torch/lib``. Under Python 3.8+, DLL search does not look
in ``PATH`` or package directories unless explicitly added via
``os.add_dll_directory()``. If DLL registration is omitted or if dependent
runtime libraries are missing, ``import torchaudio`` raises:
    FileNotFoundError: Could not find module '...\\torchaudio\\lib\\libtorchaudio.pyd'
    (or one of its dependencies). Try using the full path with the constructor syntax.

Because VoiceStudio routes all audio I/O through ``services.audio_io`` with
audited ``soundfile`` and ``ffmpeg`` fallbacks, an unimportable torchaudio
extension must never crash backend startup.
"""
from __future__ import annotations

import ast
import builtins
import importlib
import io
import logging
import os
import re
from pathlib import Path

import pytest
import torch

_REPO = Path(__file__).resolve().parents[1]
_MAIN = _REPO / "backend" / "main.py"
_AUDIO_IO = _REPO / "backend" / "services" / "audio_io.py"
_WATERMARK = _REPO / "backend" / "api" / "routers" / "watermark.py"
_BACKEND_CRASH = _REPO / "electron" / "src" / "shared" / "utils" / "backendCrash.ts"


def test_main_startup_guards_torchaudio_import():
    """`backend/main.py` must guard `import torchaudio` against ImportError and OSError."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    phase_a = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_phase_a_build_inner":
            phase_a = node
            break
    assert phase_a is not None, "_phase_a_build_inner not found in main.py"

    # Find the Try block that contains `import torchaudio`
    found_guarded_import = False
    for child in ast.walk(phase_a):
        if isinstance(child, ast.Try):
            has_torchaudio_import = any(
                isinstance(n, ast.Import) and any(alias.name == "torchaudio" for alias in n.names)
                for n in ast.walk(child)
            )
            if has_torchaudio_import:
                handler_types = set()
                for handler in child.handlers:
                    if handler.type is None:
                        continue
                    if isinstance(handler.type, ast.Tuple):
                        for elt in handler.type.elts:
                            if isinstance(elt, ast.Name):
                                handler_types.add(elt.id)
                    elif isinstance(handler.type, ast.Name):
                        handler_types.add(handler.type.id)
                assert "ImportError" in handler_types, "Handler must catch ImportError"
                assert "OSError" in handler_types, "Handler must catch OSError (WinError 126 / FileNotFoundError)"
                found_guarded_import = True
                break

    assert found_guarded_import, "import torchaudio in _phase_a_build_inner must be guarded inside a try/except"


def test_main_registers_torch_lib_on_windows():
    """`backend/main.py` must register torch/lib via os.add_dll_directory on Windows."""
    source = _MAIN.read_text(encoding="utf-8")
    assert "add_dll_directory" in source
    assert "_dll_dir_cookies" in source


def test_watermark_router_has_no_unused_torchaudio_import():
    """`backend/api/routers/watermark.py` must not import torchaudio directly."""
    source = _WATERMARK.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "torchaudio", (
                    "watermark router must not carry an unused top-level import torchaudio (#3147)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "torchaudio", (
                "watermark router must not carry an unused import from torchaudio (#3147)"
            )


def test_audio_io_module_import_handles_oserror(monkeypatch, tmp_path):
    """services.audio_io must set torchaudio=None when importing torchaudio raises OSError."""
    import services.audio_io as aio

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "torchaudio":
            raise OSError("Could not find module 'C:\\Users\\alice\\torchaudio\\lib\\libtorchaudio.pyd'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    try:
        importlib.reload(aio)
        assert aio.torchaudio is None, "torchaudio must be None when import raises OSError"

        # Verify fallback write works after simulated import failure
        out_path = tmp_path / "saved_after_import_error.wav"
        dummy_audio = torch.zeros(1, 16000, dtype=torch.float32)
        aio._safe_torchaudio_save(out_path, dummy_audio, 16000, format="wav")
        assert out_path.exists()
        assert out_path.stat().st_size > 0
    finally:
        monkeypatch.undo()
        importlib.reload(aio)


def test_audio_io_handles_missing_or_failing_torchaudio(monkeypatch, tmp_path):
    """services.audio_io must succeed with soundfile when torchaudio is None or raises OSError."""
    import soundfile as sf
    import services.audio_io as aio

    # Ensure soundfile is functional for wav read/write
    wav_path = tmp_path / "test_io.wav"
    sf.write(str(wav_path), [0.0, 0.1, -0.1, 0.0], 16000, subtype="PCM_16")

    # Simulate torchaudio being completely unavailable (ImportError / OSError at import)
    monkeypatch.setattr(aio, "torchaudio", None)

    # 1. load_audio must fall back to soundfile and return a valid float tensor
    waveform, sr = aio.load_audio(str(wav_path))
    assert sr == 16000
    assert isinstance(waveform, torch.Tensor)
    assert waveform.ndim == 2

    # 2. _safe_torchaudio_save must fall back to _safe_soundfile_write
    out_path = tmp_path / "saved.wav"
    aio._safe_torchaudio_save(out_path, waveform, sr, format="wav")
    assert out_path.exists()
    assert out_path.stat().st_size > 0

    # 3. audio_info must succeed on fixed RIFF WAV or via soundfile
    info = aio.audio_info(out_path)
    assert info.sample_rate == 16000
    assert info.num_channels == waveform.shape[0]


def test_audio_io_handles_torchaudio_oserror_during_save(monkeypatch, tmp_path, caplog):
    """services.audio_io._safe_torchaudio_save must catch OSError from torchaudio and fallback."""
    import logging
    import services.audio_io as aio

    class FailingTorchaudio:
        @staticmethod
        def save(*_args, **_kwargs):
            raise OSError("Could not find module 'C:\\Users\\alice\\torchaudio\\lib\\libtorchaudio.pyd'")

    monkeypatch.setattr(aio, "torchaudio", FailingTorchaudio)

    out_path = tmp_path / "saved_oserror.wav"
    dummy_audio = torch.zeros(1, 16000, dtype=torch.float32)
    with caplog.at_level(logging.WARNING, logger="omnivoice.audio_io"):
        aio._safe_torchaudio_save(out_path, dummy_audio, 16000, format="wav")
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    # Diagnostic detail in warning must redact the home-directory path
    assert "alice" not in caplog.text


def test_backend_crash_classifier_matches_windows_dll_load_error():
    """backendCrash.ts must recognize 'could not find module' but not bare 'filenotfounderror'."""
    src = _BACKEND_CRASH.read_text(encoding="utf-8")
    assert "'could not find module'" in src
    # Bare FileNotFoundError is too broad (a missing user audio file would match)
    assert "'filenotfounderror'" not in src

    # Simulate regex / logic check from isBrokenEnvironmentCrash
    markers = [m.strip("'\"") for m in re.findall(r"'([^']+)'", src.split("const _IMPORT_FAILURE_MARKERS = [")[1].split("];")[0])]
    assert "could not find module" in markers
    assert "filenotfounderror" not in markers

    # Verify that the exact #3147 traceback matches the markers and core dependencies
    sample_traceback = (
        "Traceback (most recent call last):\n"
        "  File \"torchaudio\\_extension\\utils.py\", line 75, in _load_lib\n"
        "    torch.ops.load_library(path)\n"
        "  File \"torch\\_ops.py\", line 1259, in load_library\n"
        "    ctypes.CDLL(path)\n"
        "FileNotFoundError: Could not find module '...\\torchaudio\\lib\\libtorchaudio.pyd' "
        "(or one of its dependencies). Try using the full path with the constructor syntax."
    ).lower()

    assert any(m in sample_traceback for m in markers)
    assert any(dep in sample_traceback for dep in ["torch", "torchaudio"])
