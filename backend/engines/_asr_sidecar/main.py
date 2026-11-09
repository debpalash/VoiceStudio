"""Crash-isolated faster-whisper ASR sidecar (Wave 4.2 / Spec 7).

Runs faster-whisper in a child process so a CTranslate2 GPU-teardown segfault
becomes a failed job, not a dead backend. Speaks the SubprocessBackend wire
protocol (length-prefixed JSON over stdin/stdout):

    on start          → {"op":"ready","engine":"faster-whisper-isolated"}
    {"op":"ping"}     → {"op":"pong"}
    {"op":"transcribe","audio_path":...,"word_timestamps":bool}
                      → {"op":"segments","result":{"segments":[...],"language":...}}
    {"op":"shutdown"} → exit 0
    error             → {"op":"error","message":...}

Runs under the PARENT venv (faster-whisper is already a dependency) — only the
process boundary is new. torch/CTranslate2 import lazily inside transcribe so
the ready handshake fits the spawn timeout.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import traceback

MAX_FRAME_BYTES = 64 * 1024 * 1024

# The parent's cuDNN 8 preload lives in the parent PROCESS, and this is a child
# with its own clean import path — so without this, an install whose side-loaded
# cuDNN 8 makes the in-process engine work still had the isolated engine die on
# every transcribe (#1371). Crash isolation turns that into a failed job rather
# than a dead backend, which is why it went unnoticed: it fails quietly forever.
# core.cudnn8 is stdlib-only, so importing it does not undo the cheap-startup
# rule the module docstring sets out (unlike the heavy `services` package).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from core.cudnn8 import preload as _preload_cudnn8

    _preload_cudnn8()
except Exception:  # noqa: BLE001 — best-effort; never block the ready handshake
    pass


def _send(stream, obj):
    """Write one flushed JSON frame to the dedicated protocol stream."""
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _recv(stream):
    """Read one bounded request frame or return None at clean EOF."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (n,) = struct.unpack("!I", header)
    if n > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {n}")
    body = bytearray()
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            raise IOError("short read")
        body.extend(chunk)
    return json.loads(bytes(body).decode("utf-8"))


# Keep these names at the entrypoint for existing integrations and tests.
from engines._asr_sidecar.runtime import get_model as _get_model
from engines._asr_sidecar.decoding import transcribe as _decode
from engines._asr_sidecar.runtime import execution_evidence


def _transcribe(audio_path, word_timestamps, decode_options=None,
                request_options=None, configuration=None):
    """Decode one frame through the shared, replaceable model loader."""
    return _decode(_get_model, audio_path, word_timestamps, decode_options,
                   request_options, configuration)


def main() -> int:
    """Serve ASR requests while keeping native stdout outside protocol frames."""
    stdin = sys.stdin.buffer
    # Frames go down a PRIVATE fd, and fd 1 is pointed at stderr (#1428).
    #
    # This sidecar's protocol is length-prefixed binary on stdout, but it is
    # not the only thing writing there: the libraries it loads print freely to
    # fd 1 — wetextprocessing's FST logs, tqdm bars, native prints from torch
    # and ONNX runtime. Those bytes interleave with frames, and the parent
    # then reads four bytes of log text as a length prefix, which is how a
    # generation dies with `OSError: frame too large: 1044258881` (that number
    # is ASCII). Worse, it desyncs the stream, so every later request on the
    # same sidecar reads stale bytes and no retry can recover.
    #
    # Duplicating fd 1 first keeps a clean channel only this module can write
    # to; redirecting fd 1 to fd 2 sends the library noise to stderr, which
    # the parent already drains into its own log (through the HF-token
    # redactor). Nothing is lost and the frame stream cannot be corrupted.
    _frame_fd = os.dup(1)
    os.dup2(2, 1)
    stdout = os.fdopen(_frame_fd, "wb")
    _send(stdout, {"op": "ready", "engine": "faster-whisper-isolated"})
    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            _send(stdout, {"op": "error", "stage": "recv", "message": f"{type(exc).__name__}: {exc}"})
            return 1
        if msg is None:
            return 0
        op = msg.get("op")
        try:
            if op == "ping":
                _send(stdout, {"op": "pong"})
            elif op == "transcribe":
                result = _transcribe(
                    msg.get("audio_path"), bool(msg.get("word_timestamps", True)),
                    msg.get("decode_options"), msg.get("request_options"), msg.get("configuration"),
                )
                _send(stdout, {"op": "segments", "result": result,
                               **({"execution": execution_evidence()} if msg.get("configuration") is not None else {})})
            elif op == "shutdown":
                return 0
            else:
                _send(stdout, {"op": "error", "stage": "dispatch", "message": f"unknown op: {op!r}"})
        except Exception as exc:
            _send(stdout, {
                "op": "error", "stage": "handler",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
    return 0


if __name__ == "__main__":
    sys.exit(main())
