"""Shared wire-protocol tests for every subprocess sidecar.

Every sidecar (TTS + ASR + echo) carries its own copy of the length-prefixed
JSON-over-stdio wire protocol (_send, _recv, MAX_FRAME_BYTES). This file
parametrizes the protocol invariants across all of them, so a bug in one
sidecar's copy is caught without a per-engine test file.

Inspired by debpalash's test_pockettts_sidecar.py (PR
paoloantinori/OmniVoice-Studio#1), which established the pattern for testing
the wire protocol against a single sidecar. This lifts the generic protocol
tests (send/recv roundtrip, EOF, oversized-frame cap, truncated-body) so every
sidecar inherits the same coverage. Engine-specific tests (language mapping, PCM
conversion, voice cache) stay in each engine's own test file.
"""
from __future__ import annotations

import importlib
import io
import struct

import pytest

#: Every sidecar module that carries the wire protocol. All are stdlib-only at
#: import time (heavy deps load lazily inside the synthesize handler), so this
#: list imports cleanly without optional wheels or child processes.
SIDECARS = [
    "engines._asr_sidecar.main",
    "engines._echo.main",
    "engines.confucius4.main",
    "engines.dots_tts.main",
    "engines.indextts.main",
    "engines.ming_tts.main",
    "engines.moss_sfx.main",
    "engines.moss_tts_v15.main",
    "engines.omnivoice_subprocess.main",
    "engines.pockettts.main",
    "engines.supertonic3.sidecar",
]


@pytest.fixture(params=SIDECARS, ids=lambda s: s.rsplit(".", 1)[0])
def sc(request):
    """Import each sidecar module (stdlib-only at import time)."""
    return importlib.import_module(request.param)


def test_send_recv_roundtrip(sc):
    """A frame sent and received back is byte-identical."""
    buf = io.BytesIO()
    sc._send(buf, {"op": "audio", "n_samples": 5})
    buf.seek(0)
    assert sc._recv(buf) == {"op": "audio", "n_samples": 5}


def test_recv_returns_none_at_eof(sc):
    """A closed pipe is an orderly parent shutdown, not an error."""
    assert sc._recv(io.BytesIO(b"")) is None


def test_recv_rejects_an_oversized_frame(sc):
    """Without the cap a corrupt length header allocates unbounded memory."""
    with pytest.raises(IOError, match="frame too large"):
        sc._recv(io.BytesIO(struct.pack("!I", sc.MAX_FRAME_BYTES + 1)))


def test_recv_raises_on_a_truncated_body(sc):
    """A body shorter than its header means the child died mid-write; looping on
    a stream that will never yield more would hang the parent instead."""
    header = struct.pack("!I", 100)
    with pytest.raises(IOError, match="short read"):
        sc._recv(io.BytesIO(header + b"only a few bytes"))
