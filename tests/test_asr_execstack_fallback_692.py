"""Regression tests for #692 — CTranslate2's native lib is rejected by hardened
kernels / newer glibc with "libctranslate2…cannot enable executable stack" (an
OSError, not ImportError). The ASR availability probes must REPORT this rather
than raise, and engine auto-detect must fall back to a non-CTranslate2 engine
(pytorch-whisper) instead of crashing the dub/transcribe preflight.
"""
import builtins

from services import asr_backend as ab

_EXECSTACK = OSError(
    "libctranslate2-d3638643.so.4.4.0: cannot enable executable stack "
    "as shared object requires: Invalid argument"
)


def test_probe_available_never_raises():
    class Boom:
        @classmethod
        def is_available(cls):
            raise _EXECSTACK
    assert ab._probe_available(Boom) is False


def test_auto_detect_falls_back_to_pytorch_when_ctranslate2_so_fails(monkeypatch):
    def boom():
        raise _EXECSTACK
    monkeypatch.setattr(ab.WhisperXBackend, "is_available", classmethod(lambda cls: boom()))
    monkeypatch.setattr(ab.FasterWhisperBackend, "is_available", classmethod(lambda cls: boom()))
    # Pin MLX unavailable so the result is platform-independent (MPS hosts would
    # otherwise probe it before the pytorch-whisper last resort).
    monkeypatch.setattr(ab.MLXWhisperBackend, "is_available", classmethod(lambda cls: (False, "n/a")))
    assert ab._auto_detect() == "pytorch-whisper"


def test_is_available_reports_not_raises_on_so_load_failure(monkeypatch):
    """whisperx + faster-whisper probes return (False, reason) — not raise — when
    the import dies loading the native .so (a non-ImportError)."""
    monkeypatch.setattr("core.windows_rocm.pipelines.windows_hip_installed", lambda: False)
    monkeypatch.setattr("core.windows_rocm.ct2.windows_hip_installed", lambda: False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("whisperx", "faster_whisper"):
            raise _EXECSTACK
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    okx, msgx = ab.WhisperXBackend.is_available()
    okf, msgf = ab.FasterWhisperBackend.is_available()
    assert okx is False and "failed to load" in msgx and "executable stack" in msgx
    assert okf is False and "failed to load" in msgf and "executable stack" in msgf
