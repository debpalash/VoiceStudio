"""Generation audio guards (#629).

A numerical glitch (seen on MPS) could leave NaN/inf in the rendered audio,
which writes an unreadable WAV that then fails decoding with an opaque
"ffmpeg returned error code: 183 / Invalid data" — surfaced to the user as a
misleading "ran out of memory". Two guards: sanitize non-finite samples before
any encode, and classify a decode/ffmpeg failure as unreadable-audio (not OOM).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from api.routers.generation import (  # noqa: E402
    _oom_friendly_reraise,
    _safe_exc_text,
    _sanitize_audio,
)


def _missing_media_process_error(tmp_path: Path) -> FileNotFoundError:
    """Capture a real process-launch ENOENT so traceback origin is preserved."""
    executable = tmp_path / "missing-bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    try:
        subprocess.run([str(executable)], check=False)
    except FileNotFoundError as exc:
        return exc
    raise AssertionError("missing ffmpeg unexpectedly launched")


def test_sanitize_replaces_non_finite_with_silence():
    t = torch.tensor([0.1, float("nan"), float("inf"), -float("inf"), 0.2])
    out = _sanitize_audio(t)
    assert torch.isfinite(out).all()
    assert out[0].item() == pytest.approx(0.1)
    assert out[1].item() == 0.0 and out[2].item() == 0.0 and out[3].item() == 0.0


def test_sanitize_leaves_finite_audio_unchanged():
    t = torch.tensor([0.0, 0.5, -0.5, 0.25])
    out = _sanitize_audio(t)
    assert torch.equal(out, t)


def test_sanitize_passes_through_non_tensor():
    assert _sanitize_audio(None) is None
    obj = object()
    assert _sanitize_audio(obj) is obj


def test_ffmpeg_decode_failure_is_not_labelled_oom():
    err = RuntimeError(
        "Decoding failed. ffmpeg returned error code: 183\n"
        "Invalid data found when processing input"
    )
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "unreadable audio" in msg
    assert "out of memory" not in msg


def test_generic_failure_still_uses_oom_hint():
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(RuntimeError("CUDA error: out of memory"))
    assert "ran out of memory" in str(ei.value)


def test_httpx_closed_client_is_a_download_failure_not_oom():
    # #880: kittentts's first-use HF download died with httpx's closed-client
    # lifecycle error, and the OOM catch-all told a user running a CPU-only
    # ~80 MB ONNX engine on a 12 GB-VRAM box to press Flush. It's a network
    # failure — say so, and don't send them to the Flush button.
    err = RuntimeError("Cannot send a request, as the client has been closed.")
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "network" in msg
    assert "download" in msg
    assert "Retry" in msg
    assert "client has been closed" in msg  # underlying detail preserved
    assert "ran out of memory" not in msg
    assert "Try the Flush button" not in msg


@pytest.mark.parametrize("exc_name", ["ConnectError", "ReadTimeout"])
def test_httpx_transport_error_in_chain_is_a_download_failure(exc_name):
    # #880: engines wrap the original httpx error, so classification must
    # look at exception TYPE NAMES anywhere in the chain, not just the
    # outermost message (which here carries no network signature at all).
    fake_httpx_exc = type(exc_name, (Exception,), {})
    try:
        try:
            raise fake_httpx_exc("")
        except Exception as inner:
            raise RuntimeError("model load failed") from inner
    except RuntimeError as wrapped:
        err = wrapped
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "network" in msg
    assert "ran out of memory" not in msg
    assert "Try the Flush button" not in msg


def test_unknown_error_is_not_labelled_oom():
    # #880 (the class bug): the OOM hint was the catch-all fallback, so ANY
    # unrecognized error claimed "ran out of memory" + Flush. A genuinely
    # unknown error must surface as unknown, detail intact.
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(RuntimeError("segfault in frobnicator: code 7"))
    msg = str(ei.value)
    assert "segfault in frobnicator: code 7" in msg
    assert "ran out of memory" not in msg
    assert "Try the Flush button" not in msg


def test_bare_windows_missing_process_error_is_actionable(tmp_path):
    """CreateProcess omits the executable name from WinError 2 on Windows."""
    err = _missing_media_process_error(tmp_path)
    err.filename = None
    err.strerror = "[WinError 2] The system cannot find the file specified"
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "doesn't recognize" not in msg
    assert "Audio tools" in msg
    assert "required media program" in msg


def test_posix_missing_media_process_error_is_actionable(tmp_path):
    """Equivalent missing-tool failures receive the same guidance on POSIX."""
    err = _missing_media_process_error(tmp_path)
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "Audio tools" in msg
    assert "required media program" in msg


def test_missing_file_named_like_media_tool_keeps_missing_file_diagnosis():
    """A reference/model basename collision is not a process-launch failure."""
    err = FileNotFoundError(2, "No such file or directory", "ffmpeg")
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    assert "Audio tools" not in str(ei.value)


@pytest.mark.parametrize("reason", [
    "CUDA out of memory. Tried to allocate 20.00 MiB",
    "MPS backend out of memory (MPS allocated: 8.00 GB)",
    "DefaultCPUAllocator: not enough memory: you tried to allocate 1073741824 bytes",
    "[enforce fail at alloc_cpu.cpp] posix_memalign. Cannot allocate memory",
])
def test_real_oom_signatures_still_classify_as_oom(reason):
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(RuntimeError(reason))
    assert "ran out of memory" in str(ei.value)
    assert "Try the Flush button" in str(ei.value)


def test_the_windows_paging_file_case_gets_its_own_advice():
    """#1334: WinError 1455 used to sit in the list above and get the generic
    "ran out of memory / try Flush" message.

    It is still a memory-class failure — the guard this file exists for, that a
    real memory problem never falls through to the unknown catch-all, is intact
    and asserted below. But Flush cannot fix it: Windows is refusing to back a
    large mapping because the PAGING FILE is too small, which is not the same
    as the working set being full, and no amount of reloading the model or
    closing other apps changes it. So it gets the specific remedy instead of
    advice that cannot work.
    """
    for reason in (
        "[WinError 1455] The paging file is too small for this operation to complete",
        "The paging file is too small for this operation to complete. (os error 1455)",
    ):
        with pytest.raises(RuntimeError) as ei:
            _oom_friendly_reraise(RuntimeError(reason))
        msg = str(ei.value)
        # Still recognised — not the unknown-error catch-all.
        assert "doesn't recognize" not in msg
        assert "paging file" in msg.lower()
        # ...and pointed at the setting that actually governs it.
        assert "Virtual memory" in msg
        assert "Try the Flush button" not in msg


def test_typed_oom_without_oom_message_still_classifies_as_oom():
    # torch.cuda.OutOfMemoryError can carry an opaque allocator message; the
    # tightened OOM branch must also match the exception type name.
    fake_torch_oom = type("OutOfMemoryError", (RuntimeError,), {})
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(fake_torch_oom("CUBLAS workspace reservation failed"))
    assert "ran out of memory" in str(ei.value)


def test_unsupported_instruct_is_a_validation_error_not_oom():
    # #664: free-form prose in the instruct field must surface as a 400-mapped
    # ValueError with the instruct guidance — NOT a 500 "ran out of memory".
    err = ValueError(
        "Unsupported instruct items found in Speak with high energy:\n"
        "  'Speak with high energy' -> 'speak with high energy' (unsupported)\n\n"
        "Valid English items: male, whisper, ..."
    )
    with pytest.raises(ValueError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "Unsupported instruct items" in msg
    assert "ran out of memory" not in msg


def test_instruct_error_wrapped_in_runtimeerror_is_still_validation():
    # A lower layer can wrap the original ValueError; we must classify on the
    # message signature, not the type, so the route still returns a clean 400.
    err = RuntimeError(
        "model.generate failed: Conflicting instruct items within the same "
        "category: 'male' vs 'female'."
    )
    with pytest.raises(ValueError) as ei:
        _oom_friendly_reraise(err)
    assert "Conflicting instruct items" in str(ei.value)
    assert "ran out of memory" not in str(ei.value)


def test_broken_pipe_is_a_lost_pipe_not_oom():
    # #715: a "[Errno 32] Broken pipe" surfacing from generation means the
    # backend's stdout/stderr pipe to the desktop shell closed mid-render (an
    # orphaned/relaunched backend) — NOT out of memory. Telling the user to
    # press Flush for memory they never ran out of is the wrong next step;
    # restarting the app re-parents the backend. Covers both the typed
    # BrokenPipeError and a string-wrapped "[Errno 32] Broken pipe".
    for err in (
        BrokenPipeError(32, "Broken pipe"),
        RuntimeError("model.generate failed: [Errno 32] Broken pipe"),
    ):
        with pytest.raises(RuntimeError) as ei:
            _oom_friendly_reraise(err)
        msg = str(ei.value)
        assert "pipe" in msg.lower()
        assert "Restart the app" in msg
        assert "ran out of memory" not in msg


def test_no_kernel_image_is_an_unsupported_gpu_not_oom():
    # #756: a GPU whose compute capability isn't in the torch build's arch list
    # (Pascal sm_61 on new wheels, Blackwell sm_120 on old wheels) raises "CUDA
    # error: no kernel image is available for execution". That's NOT OOM and Flush
    # won't help — point at CPU / a matching torch.
    err = RuntimeError(
        "CUDA error: no kernel image is available for execution on the device"
    )
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "GPU isn't supported" in msg or "isn't supported by the installed" in msg
    assert "CPU" in msg
    assert "ran out of memory" not in msg


def test_winerror_193_is_a_corrupt_binary_not_oom():
    # #705: a corrupt / wrong-architecture native component (torch, ffmpeg, an
    # engine binary) fails on Windows with "[WinError 193] %1 is not a valid
    # Win32 application". That is NOT OOM and Flush won't help — say so.
    err = RuntimeError(
        "TTS engine stopped mid-generation: [WinError 193] %1 is not a valid "
        "Win32 application"
    )
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "WinError 193" in msg
    assert "corrupt" in msg or "wrong architecture" in msg
    assert "ran out of memory" not in msg


def test_sherpa_model_not_set_is_a_config_error_not_oom():
    # #919: the reporter selected sherpa-onnx and hit "OMNIVOICE_SHERPA_MODEL
    # not set. Point it to a sherpa-onnx TTS model directory …" — a pure setup
    # problem — but the OOM catch-all told them (63 GB RAM) to press Flush for
    # memory they never ran out of. It must classify as a CONFIGURATION error:
    # name the env var, point at Model Catalogue, and never mention memory
    # or the Flush button.
    err = RuntimeError(
        "OMNIVOICE_SHERPA_MODEL not set. Point it to a sherpa-onnx TTS model "
        "directory (containing model.onnx + tokens.txt)."
    )
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "OMNIVOICE_SHERPA_MODEL" in msg      # names the exact env var
    assert "Model Catalogue" in msg    # tells the user where to fix it
    assert "out of memory" not in msg
    assert "ran out of memory" not in msg
    assert "Flush" not in msg


def test_engine_not_configured_class_is_not_oom():
    # #919 (the class, not just the one string): any opt-in engine failing
    # because its required model path / env var isn't set is configuration, not
    # OOM — including when a lower layer wraps the original message. Covers
    # sherpa's is_available() wrapper, sherpa's "no model.onnx" variant, and a
    # Confucius4/dots/MOSS-style "venv not found. Set OMNIVOICE_…_DIR".
    for raw in (
        "Sherpa-ONNX unavailable: OMNIVOICE_SHERPA_MODEL not set. Point it to a "
        "sherpa-onnx TTS model directory (containing model.onnx + tokens.txt).",
        "No model.onnx found in /tts/models. Download a model from "
        "https://github.com/k2-fsa/sherpa-onnx/releases",
        "Confucius4-TTS venv not found. Set OMNIVOICE_CONFUCIUS4_TTS_DIR to your "
        "clone and restart OmniVoice.",
    ):
        with pytest.raises(RuntimeError) as ei:
            _oom_friendly_reraise(RuntimeError(raw))
        msg = str(ei.value)
        assert "isn't set up yet" in msg
        assert "ran out of memory" not in msg
        assert "out of memory" not in msg
        assert "Flush" not in msg


def test_config_failure_does_not_swallow_real_oom():
    # Guard the ordering: a genuine OOM must still be OOM even though the config
    # branch now runs first.
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(RuntimeError("CUDA error: out of memory"))
    assert "ran out of memory" in str(ei.value)


# ── #977: generic exception formatters never leak a raw container repr ─────
# mlx-audio's vendored Kokoro pipeline raises
# `assert lang_code in LANG_CODES, (lang_code, LANG_CODES)` — an
# AssertionError whose .args is a (str, dict) tuple. str(e) on that
# interpolates the ENTIRE table straight into the user-facing 500 message
# ("Underlying error: ('du', {'a': 'American English', ...})"). Any engine's
# generate() can raise something shaped like this, not just Kokoro, so the
# guard is class-level: _safe_exc_text() backs both of generation.py's
# generic (catch-all) exception formatters.


def test_safe_exc_text_plain_message_uses_house_style():
    err = RuntimeError("plain readable message")
    assert _safe_exc_text(err) == "RuntimeError: plain readable message"


def test_safe_exc_text_container_args_do_not_leak_raw_repr():
    # Mirrors the exact #977 AssertionError shape.
    err = AssertionError(("du", {"a": "American English", "b": "British English"}))
    text = _safe_exc_text(err)
    assert text.startswith("AssertionError")
    assert "American English" not in text
    assert "{" not in text and "}" not in text
    assert "(" not in text and ")" not in text


def test_unrecognized_error_catchall_does_not_leak_container_repr():
    # End-to-end through _oom_friendly_reraise's catch-all fallback (none of
    # the specific classifiers above it match an AssertionError like this).
    err = AssertionError(("du", {"a": "American English", "b": "British English"}))
    with pytest.raises(RuntimeError) as ei:
        _oom_friendly_reraise(err)
    msg = str(ei.value)
    assert "AssertionError" in msg
    assert "American English" not in msg
    assert "{" not in msg and "}" not in msg
