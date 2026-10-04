"""WhisperX CUDA-OOM → CPU fallback (api/services parity for small GPUs).

On an 8 GB laptop GPU with the TTS model resident, whisperx's CTranslate2
load of large-v3 dies with `RuntimeError: CUDA failed with error out of
memory`, which previously surfaced as a bare 500 from /dub/transcribe. The
backend now retries on CPU (slower, same model/accuracy). This test forces the
OOM deterministically (no GPU needed) and asserts the device switch.
"""
from __future__ import annotations

import sys
import types

import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before this module imports the REAL core.config (the old
# sys.modules stub leaked at collection time and broke mixed runs).
whisperx = pytest.importorskip("whisperx")

from services.asr_backend import (  # noqa: E402
    WhisperXBackend,
    _is_compute_type_error,
)

# The exact ValueError CTranslate2 raises at model construction on a GPU
# without efficient fp16 (older Maxwell/Pascal, GTX 16xx) or a cuDNN mismatch.
_FP16_ERR = (
    "Requested float16 compute type, but the target device or backend do not "
    "support efficient float16 computation"
)


@pytest.fixture(autouse=True)
def cuda_runtime(monkeypatch):
    """Exercise mocked CUDA loaders independently of the host's HIP stack."""
    monkeypatch.setattr("services.asr_backend._rocm_torch", lambda: False)


def test_cuda_oom_falls_back_to_cpu(monkeypatch):
    calls = []

    def fake_load_model(name, device, compute_type, **kw):
        calls.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return object()  # CPU load succeeds

    monkeypatch.setattr(whisperx, "load_model", fake_load_model)
    # Exercise load-time OOM, independent of actual free VRAM on this host.
    monkeypatch.setattr(WhisperXBackend, "_free_vram_gb", staticmethod(lambda: 10.0))

    be = WhisperXBackend()
    # Force the CUDA starting point regardless of the CI host's hardware.
    be._device, be._compute_type = "cuda", "float16"
    be._allow_vad_pickle_globals = lambda: None  # skip torch pickle allowlist

    be._ensure_asr()

    assert be._asr is not None                      # didn't raise — recovered
    assert be._device == "cpu" and be._compute_type == "int8"
    assert [d for d, _ in calls] == ["cuda", "cpu"]  # tried CUDA, then CPU


def test_non_oom_runtime_error_still_raises(monkeypatch):
    msg = "some other failure"
    # A generic non-OOM, non-compute-type RuntimeError must still propagate —
    # the new compute_type fallback must NOT swallow it.
    assert _is_compute_type_error(msg) is False

    def fake_load_model(name, device, compute_type, **kw):
        raise RuntimeError(msg)  # not an OOM, not compute-type → must propagate

    monkeypatch.setattr(whisperx, "load_model", fake_load_model)

    be = WhisperXBackend()
    be._device, be._compute_type = "cuda", "float16"
    be._allow_vad_pickle_globals = lambda: None
    with pytest.raises(RuntimeError, match="some other failure"):
        be._ensure_asr()


def test_float16_unsupported_falls_back_to_int8(monkeypatch):
    """#551: a GPU without efficient fp16 raises a ValueError at load for both
    float16 AND int8_float16; the backend must degrade to int8 on the SAME
    device (cuda) without raising — not fall to CPU and not crash."""
    calls = []

    def fake_load_model(name, device, compute_type, **kw):
        calls.append((device, compute_type))
        if device == "cuda" and compute_type in ("float16", "int8_float16"):
            raise ValueError(_FP16_ERR)
        return object()  # cuda int8 succeeds

    monkeypatch.setattr(whisperx, "load_model", fake_load_model)
    monkeypatch.setattr(WhisperXBackend, "_free_vram_gb", staticmethod(lambda: 10.0))

    be = WhisperXBackend()
    be._device, be._compute_type = "cuda", "float16"
    be._allow_vad_pickle_globals = lambda: None

    be._ensure_asr()

    assert be._asr is not None                       # recovered, no raise
    assert be._device == "cuda" and be._compute_type == "int8"  # same device, int8
    assert calls == [("cuda", "float16"), ("cuda", "int8_float16"), ("cuda", "int8")]


def test_faster_whisper_float16_unsupported_falls_back_to_int8(monkeypatch):
    """Mirror for FasterWhisperBackend: float16 + int8_float16 raise the fp16
    ValueError, int8 succeeds → loads on (cuda, int8) without raising."""
    import services.asr_backend as asr_backend
    from services.asr_backend import FasterWhisperBackend

    calls = []

    class FakeWhisperModel:
        def __init__(self, name, device, compute_type, **kw):
            calls.append((device, compute_type))
            if device == "cuda" and compute_type in ("float16", "int8_float16"):
                raise ValueError(_FP16_ERR)
            # cuda int8 succeeds

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    # Force the CUDA starting point regardless of the CI host's hardware by
    # making torch.cuda.is_available() return True inside _ensure_model.
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(
        is_available=lambda: True, empty_cache=lambda: None
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    # The compute-device override gate consults the capability probe before
    # the torch mock above — pin it to a CUDA family so the fallback chain
    # under test is reachable on a cpu-only CI host.
    from core.device_caps import HostCaps

    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: HostCaps(family="cuda", available_families=("cuda", "cpu")),
    )

    be = FasterWhisperBackend()
    be._ensure_model()

    assert be._model is not None                     # recovered, no raise
    assert be._device == "cuda" and be._compute_type == "int8"
    assert calls == [("cuda", "float16"), ("cuda", "int8_float16"), ("cuda", "int8")]
