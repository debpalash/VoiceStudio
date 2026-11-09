"""Streaming /generate failures name their cause and flag unretryable ones."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest

from core import failure
from core.public_errors import stream_failure, stream_generation_failure

CUDA = "CUDA error: no kernel image is available for execution on the device"


def test_gpu_arch_stream_payload_is_terminal():
    p = stream_generation_failure(RuntimeError(CUDA))
    assert p["docs_topic"] == "GPU_ARCH_UNSUPPORTED"
    assert p["docs_url"].endswith("#generation-failure-diagnosis")
    assert "CPU" in p["hint"] and "PyTorch" in p["hint"]
    floor = stream_failure("generation_failed")["detail"]
    assert p["detail"].startswith(floor) and len(p["detail"]) > len(floor)
    assert p["terminal"] is True and p["retryable"] is False
    assert p["error_class"] == "RuntimeError"


def test_app_control_is_terminal():
    p = stream_generation_failure(OSError("[WinError 4551] An Application Control policy has blocked this file"))
    assert p["docs_topic"] == "WINDOWS_APP_CONTROL_BLOCKED"
    assert p["hint"] and p["terminal"] is True


def test_audio_io_is_retryable_not_terminal():
    p = stream_generation_failure(RuntimeError("LibsndfileError: System error."))
    assert p["docs_topic"] == "AUDIO_IO_FAILED"
    assert p["hint"] and p["retryable"] is True and "terminal" not in p


def test_unclassified_matches_floor():
    p = stream_generation_failure(RuntimeError("Something went sideways"))
    expected = stream_failure("generation_failed")
    expected["error_class"] = "RuntimeError"
    assert p == expected
    assert p["retryable"] is True
    assert not ({"hint", "docs_topic", "terminal"} & p.keys())


def test_oom_stays_retryable():
    p = stream_generation_failure(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert p["docs_topic"] == "GPU_OOM"
    assert p["retryable"] is True and "terminal" not in p


@pytest.mark.parametrize("message", [
    "httpx.ReadTimeout: The read operation timed out",
    "ConnectionResetError: [Errno 104] Connection reset by peer",
    "BrokenPipeError: [Errno 32] Broken pipe",
    "PermissionError: [Errno 13] Permission denied: 'output.wav'",
])
def test_ordinary_failures_carry_no_hint(message):
    payload = stream_generation_failure(RuntimeError(message))
    assert "hint" not in payload


def test_no_exception_text_leaks():
    secret = "/home/zed/private/voice.wav"
    p = stream_generation_failure(RuntimeError(f"{CUDA} while reading {secret}"))
    assert all(secret not in str(v) for v in p.values())


@pytest.mark.parametrize("text", [
    CUDA,
    "CUDA error: no kernel image is available for execution on the device\n"
    "CUDA kernel errors might be asynchronously reported at some other API call",
])
def test_classify_gpu_arch(text):
    assert failure.classify(text) == "GPU_ARCH_UNSUPPORTED"


def test_terminal_helper():
    assert failure.is_terminal_failure_topic("GPU_ARCH_UNSUPPORTED")
    for t in (None, "", "GPU_OOM", "AUDIO_IO_FAILED", "HOST_MEMORY_EXHAUSTED"):
        assert not failure.is_terminal_failure_topic(t)


def test_invariants():
    assert failure._TERMINAL_FAILURE_CLASSES <= failure._CONTEXT_FREE_HINT_CLASSES
    for t in failure._CONTEXT_FREE_HINT_CLASSES:
        assert failure._HINTS.get(t), t
