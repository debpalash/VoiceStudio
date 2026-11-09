"""Concrete crash-isolated faster-whisper backend and compatible base exports."""
from __future__ import annotations

import sys
from pathlib import Path

from services.subprocess_backend import RECV_TIMEOUT_S as RECV_TIMEOUT_S
from services.subprocess_asr_base import (
    ASR_RECV_TIMEOUT_S, SubprocessASRBackend, SubprocessBackend,
)


class IsolatedFasterWhisperBackend(SubprocessASRBackend):
    """faster-whisper (CTranslate2) in a child process — opt-in.

    CTranslate2's GPU teardown can segfault (the endemic faster-whisper crash);
    running it isolated keeps that from killing the backend. Uses the PARENT
    venv (faster-whisper is already installed) — only the process boundary is
    new. Select with ``OMNIVOICE_ASR_BACKEND=faster-whisper-isolated``.
    """

    id = "faster-whisper-isolated"
    display_name = "Faster-Whisper (crash-isolated subprocess)"
    # Same engine as FasterWhisperBackend, so the same device support — the
    # sidecar picks cuda/cpu itself via `_device()`. Without this the registry
    # default ("cpu",) would dishonestly report cpu_only routing on CUDA hosts.
    gpu_compat = ("cuda", "cpu")

    @classmethod
    def runtime_gpu_compat(cls):
        from core.windows_rocm.ct2 import gpu_compatible

        return gpu_compatible()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        """Require the sidecar and admit ROCm through a child before native imports."""
        from core.execstack import ensure_ctranslate2_loadable
        from core.windows_rocm.asr_isolation import availability_status as isolated_status
        from core.windows_rocm.ct2 import availability_status

        status = isolated_status()
        if status is None:
            status = availability_status()
        if status is not None:
            if not cls.sidecar_script().is_file():
                return False, "ASR sidecar script missing; reinstall VoiceStudio"
            return status

        ok, detail = ensure_ctranslate2_loadable()
        if not ok:
            return False, f"faster-whisper cannot load CTranslate2: {detail}"
        try:
            from core.windows_rocm.ct2 import prepare_import

            prepare_import()
            import faster_whisper  # noqa: F401
        except Exception as e:
            return False, f"faster-whisper not installed: {e}"
        if not cls.sidecar_script().is_file():
            return False, f"ASR sidecar script missing at {cls.sidecar_script()}"
        # Same CTranslate2 engine, same cuDNN 8 requirement (#1371). Crash
        # isolation means a missing cuDNN 8 kills only the child — so instead of
        # a dead backend the user gets a sidecar that fails every transcribe
        # with no explanation. Report it here, where Model Catalogue shows it.
        from services.asr_backend import _ctranslate2_cudnn_ok

        return _ctranslate2_cudnn_ok()

    @classmethod
    def shares_parent_torch(cls) -> bool:
        return True

    @classmethod
    def venv_python(cls) -> Path:
        # faster-whisper lives in the parent venv — isolation is process-only.
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "engines" / "_asr_sidecar" / "main.py"
