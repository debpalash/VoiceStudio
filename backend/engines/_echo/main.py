"""Permanent CI regression-test echo sidecar — DO NOT delete.

Keeps `backend/services/subprocess_backend.py::SubprocessBackend` round-trip
working as a green build gate. This file is the contract that proves the
length-prefixed JSON wire protocol still works even when no production
engine (IndexTTS, Supertonic-3, etc.) is installed.

Wire protocol (length-prefixed JSON over stdin/stdout, identical to
SubprocessBackend._send/_recv):

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow:
    1. on start: sidecar emits {"op":"ready","engine":"_echo"}
    2. parent → {"op":"ping"} → sidecar replies {"op":"pong"}
    3. parent → {"op":"synthesize","text":"...","sample_rate":24000} →
       sidecar replies {"op":"audio","audio_pcm_b64":<base64 1 s int16
       zeros>,"sample_rate":24000,"n_samples":24000}
    4. parent → {"op":"shutdown"} → sidecar returns 0
    5. unknown op → sidecar emits {"op":"error","stage":"dispatch",
       "message":"unknown op: ..."} and continues

Test-only ops (only registered when OMNIVOICE_ECHO_TEST_MODE=1):
    - {"op":"probe_env"} → echo back the four canonical HF env vars

Test-only crash hook (only when OMNIVOICE_ECHO_CRASH=1): the sidecar will
self-`os._exit(1)` after dispatching exactly one frame, to exercise the
parent's "sidecar died mid-generate" recovery path.

This script is stdlib-only on purpose — no torch, no numpy. The whole point
of the echo sidecar is that it can spawn under the bare system Python
interpreter without any engine venv.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import traceback


# Mirrors backend/services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _recv(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None  # EOF
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


def _silence_pcm_b64(sample_rate: int) -> tuple[str, int]:
    """Return base64-encoded 1 second of int16 silence at the given rate."""
    n_samples = int(sample_rate)
    # int16 silence = two zero bytes per sample. No numpy dependency.
    pcm = b"\x00\x00" * n_samples
    return base64.b64encode(pcm).decode("ascii"), n_samples


def main() -> int:
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

    test_mode = os.environ.get("OMNIVOICE_ECHO_TEST_MODE") == "1"
    crash_after_one = os.environ.get("OMNIVOICE_ECHO_CRASH") == "1"

    _send(stdout, {"op": "ready", "engine": "_echo"})

    frames_handled = 0
    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": "recv",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1
        if msg is None:
            return 0

        op = msg.get("op")
        # Wave 4.2: deterministic "crash mid-transcription" hook — exit BEFORE
        # sending any reply so the parent's blocking recv sees a dead pipe
        # (reply=None). The crash-after-one hook below replies first, so it
        # can't deterministically exercise the no-reply path.
        if op == "transcribe" and os.environ.get("OMNIVOICE_ECHO_CRASH_NO_REPLY") == "1":
            os._exit(1)
        try:
            if op == "ping":
                _send(stdout, {"op": "pong"})
            elif op == "synthesize":
                sr = int(msg.get("sample_rate", 24000) or 24000)
                pcm_b64, n_samples = _silence_pcm_b64(sr)
                _send(stdout, {
                    "op": "audio",
                    "audio_pcm_b64": pcm_b64,
                    "sample_rate": sr,
                    "n_samples": n_samples,
                })
            elif op == "transcribe":
                # Wave 4.2: echo ASR op — a canned segments result so the
                # SubprocessASRBackend round-trip + respawn path is testable
                # without a real ASR engine.
                _send(stdout, {
                    "op": "segments",
                    "result": {
                        "segments": [{"start": 0.0, "end": 1.0,
                                      "text": f"echo:{msg.get('audio_path', '')}"}],
                        "text": f"echo:{msg.get('audio_path', '')}",
                        "language": "en",
                    },
                })
            elif op == "shutdown":
                return 0
            elif op == "probe_env" and test_mode:
                _send(stdout, {
                    "op": "probe_env_result",
                    "keys": {
                        "HF_TOKEN": os.environ.get("HF_TOKEN"),
                        "HF_HOME": os.environ.get("HF_HOME"),
                        "HF_ENDPOINT": os.environ.get("HF_ENDPOINT"),
                        "HF_HUB_CACHE": os.environ.get("HF_HUB_CACHE"),
                    },
                })
            elif op == "emit_unknown" and test_mode:
                # Test hook for T-02-04: sidecar emits an op the parent's
                # allowlist must drop without crashing.
                _send(stdout, {"op": "exfiltrate", "payload": "ignored"})
                # Immediately follow with a valid pong so the parent test can
                # see that subsequent frames still flow after the rejection.
                _send(stdout, {"op": "pong"})
            else:
                _send(stdout, {
                    "op": "error",
                    "stage": "dispatch",
                    "message": f"unknown op: {op!r}",
                })
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": "handler",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1

        frames_handled += 1
        if crash_after_one and frames_handled >= 1:
            # Hard exit — mimics a sidecar segfault mid-session so the parent
            # finally-clause has to release the GPU slot.
            os._exit(1)


if __name__ == "__main__":
    sys.exit(main())
