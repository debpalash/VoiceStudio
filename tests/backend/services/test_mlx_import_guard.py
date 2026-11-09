"""MLX import-guard hardening (Wave 4.4).

A PyInstaller-bundled mlx whose native dylib/metallib fails to load raises
OSError/RuntimeError on import — not ImportError. is_available() must report
unavailable instead of letting that propagate and crash the registry scan.
"""
import builtins
import sys

import pytest


def _block_import(monkeypatch, name, exc):
    real = builtins.__import__

    def fake(n, *a, **k):
        if n == name:
            raise exc
        return real(n, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)


def _broken_package(monkeypatch, tmp_path, name, exc):
    """A package whose import raises ``exc``. The ASR probe imports in a child
    process (#3924), so a patched ``__import__`` here would not reach it."""
    from core import import_probe

    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(f"raise {exc!r}\n")
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    import_probe.clear_cache()
    monkeypatch.setattr(import_probe, "_cache", {})


@pytest.mark.parametrize("exc", [OSError("dlopen metallib failed"),
                                 RuntimeError("metal device init failed"),
                                 ImportError("No module named mlx_whisper")])
def test_mlx_whisper_unavailable_not_crash(monkeypatch, tmp_path, exc):
    # #390: is_available now gates on the shared mlx_supported() platform check
    # BEFORE importing the package. Force the gate open so this test exercises
    # the import-guard branch (its actual subject) rather than the platform gate.
    monkeypatch.setattr("core.device_caps.mlx_supported", lambda: (True, ""))
    from services.asr_backend import MLXWhisperBackend
    _broken_package(monkeypatch, tmp_path, "mlx_whisper", exc)
    ok, msg = MLXWhisperBackend.is_available()
    assert ok is False
    assert "mlx-whisper" in msg


@pytest.mark.parametrize("exc", [OSError("dlopen failed"),
                                 RuntimeError("metal init failed"),
                                 ImportError("no mlx_audio")])
def test_mlx_audio_unavailable_not_crash(monkeypatch, exc):
    # #390: force the platform gate open (see whisper test above) so the
    # OSError/RuntimeError import-guard is the code path under test.
    monkeypatch.setattr("core.device_caps.mlx_supported", lambda: (True, ""))
    from services.tts_backend import MLXAudioBackend
    _block_import(monkeypatch, "mlx_audio", exc)
    ok, msg = MLXAudioBackend.is_available()
    assert ok is False
    assert "mlx-audio" in msg


def test_mlx_whisper_unavailable_off_apple_platform(monkeypatch):
    # The #390 fix itself: on a non-Apple host the platform gate reports
    # unavailable before any package import (so a stray wheel can't advertise it).
    monkeypatch.setattr(
        "core.device_caps.mlx_supported",
        lambda: (False, "MLX requires Apple Silicon; this host is linux/x86_64"),
    )
    from services.asr_backend import MLXWhisperBackend
    ok, msg = MLXWhisperBackend.is_available()
    assert ok is False
    assert "Apple Silicon" in msg
