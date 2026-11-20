"""The parent/sidecar wire protocol: one framing contract, eleven implementations.

Every out-of-process engine speaks the same framing (4-byte big-endian length, then
UTF-8 JSON, with a 64 MiB cap on both sides). The parent implements it once in
`SubprocessBackend._send`/`._recv`; each sidecar reimplements it, so the contract
can drift silently -- and the failure mode is the one the engines' own comments
describe (`OSError: frame too large: 1044258881`) or a desynchronised stream that
hangs a render.

These tests need no GPU, no model and no weights: they drive the parent's framing
over `io.BytesIO` and check every sidecar's source for the same invariants.
"""
from __future__ import annotations

import io
import json
import pathlib
import struct
import sys
from types import SimpleNamespace

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from services.subprocess_backend import (  # noqa: E402
    MAX_FRAME_BYTES,
    PARENT_INBOUND_OPS,
    SIDECAR_INBOUND_OPS,
    SubprocessBackend,
)

#: Sidecar entrypoints that implement the framing. `omnivoice_gguf/backend.py` is
#: deliberately absent: it parses a WAV-style stream from the C++ server, not the
#: JSON frame protocol.
SIDECARS = (
    "backend/engines/_asr_sidecar/main.py",
    "backend/engines/_echo/main.py",
    "backend/engines/confucius4/main.py",
    "backend/engines/cosyvoice_subprocess/main.py",
    "backend/engines/dots_tts/main.py",
    "backend/engines/indextts/main.py",
    "backend/engines/ming_tts/main.py",
    "backend/engines/moss_sfx/main.py",
    "backend/engines/moss_tts_nano_subprocess/main.py",
    "backend/engines/moss_tts_v15/main.py",
    "backend/engines/omnivoice_subprocess/main.py",
    "backend/engines/pockettts/main.py",
    "backend/engines/supertonic3/sidecar.py",
    "backend/engines/voxcpm2_subprocess/main.py",
)


class _FramingBackend(SubprocessBackend):
    """The abstract surface stubbed so the framing methods can be driven directly."""

    id = "test-engine"
    sample_rate = 24000
    supported_languages = ["en"]

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "test"


def _backend(stdout_bytes: bytes = b"") -> SubprocessBackend:
    """A backend whose pipe ends are in-memory, so framing is exercised directly."""
    backend = object.__new__(_FramingBackend)
    backend._proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(stdout_bytes))
    return backend


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(body)) + body


# ── The parent's framing ────────────────────────────────────────────────────


def test_send_writes_a_four_byte_big_endian_length_then_the_body():
    backend = _backend()
    backend._send({"op": "ping"})
    written = backend._proc.stdin.getvalue()
    (declared,) = struct.unpack("!I", written[:4])
    assert declared == len(written) - 4
    assert json.loads(written[4:]) == {"op": "ping"}


def test_send_refuses_a_body_over_the_cap():
    backend = _backend()
    with pytest.raises(IOError):
        backend._send({"op": "synthesize", "text": "x" * (MAX_FRAME_BYTES + 1)})


def test_recv_returns_the_frame():
    backend = _backend(stdout_bytes=_frame({"op": "ready"}))
    assert backend._recv() == {"op": "ready"}


def test_recv_returns_none_on_a_clean_eof():
    assert _backend(stdout_bytes=b"")._recv() is None


def test_recv_refuses_a_declared_length_over_the_cap_without_reading_the_body():
    """The guard must fire before the allocation, not after reading it.

    The message matters: a missing guard reads the (absent) body and fails as a
    "short read", which looks like a broken pipe instead of a refused frame. Only
    the pre-allocation refusal says "frame too large".
    """
    header = struct.pack("!I", MAX_FRAME_BYTES + 1)
    with pytest.raises(IOError) as excinfo:
        _backend(stdout_bytes=header)._recv()
    assert "frame too large" in str(excinfo.value)


def test_recv_refuses_a_short_body():
    payload = _frame({"op": "ready"})
    with pytest.raises(IOError):
        _backend(stdout_bytes=payload[:-3])._recv()


def test_recv_drops_an_op_outside_the_allowlist_and_reads_the_next_frame():
    stream = _frame({"op": "definitely_not_allowed"}) + _frame({"op": "ready"})
    assert _backend(stdout_bytes=stream)._recv() == {"op": "ready"}


def test_the_op_allowlists_are_disjoint_and_meaningful():
    assert {"ready", "error", "progress", "audio"} <= PARENT_INBOUND_OPS
    assert {"ping", "shutdown"} <= SIDECAR_INBOUND_OPS
    assert not (PARENT_INBOUND_OPS & SIDECAR_INBOUND_OPS), (
        "an op accepted on both sides means one direction is acting on its own frame"
    )


# ── Every sidecar's copy of the same contract ───────────────────────────────


@pytest.mark.parametrize("relative", SIDECARS)
def test_each_sidecar_uses_the_same_framing_invariants(relative):
    source = (_ROOT / relative).read_text(encoding="utf-8")
    assert 'struct.pack("!I"' in source, (
        f"{relative} does not write a 4-byte big-endian length prefix; a different "
        "prefix desynchronises the parent's reader"
    )
    assert 'struct.unpack("!I"' in source, (
        f"{relative} does not read the length as a 4-byte big-endian integer"
    )
    assert "MAX_FRAME_BYTES" in source, (
        f"{relative} declares no frame cap, so a hostile or corrupt length can be "
        "allocated"
    )
    assert "if n > MAX_FRAME_BYTES" in source, (
        f"{relative} does not compare the declared length against its cap before "
        "reading the body"
    )


@pytest.mark.parametrize("relative", SIDECARS)
def test_each_sidecar_caps_a_frame_at_the_parent_value(relative):
    """A sidecar with a larger cap than the parent would just be killed by it."""
    source = (_ROOT / relative).read_text(encoding="utf-8")
    assert "64 * 1024 * 1024" in source, (
        f"{relative} must mirror the parent's MAX_FRAME_BYTES (64 MiB); the engines' "
        "own comments call the mismatch out as a hang/DOS source"
    )


def test_the_sidecar_list_covers_every_engine_that_implements_framing():
    """A new engine must be added here, not silently left out of the matrix."""
    implements = {
        str(path.relative_to(_ROOT)).replace("\\", "/")
        for path in (_ROOT / "backend" / "engines").rglob("*.py")
        if 'struct.pack("!I"' in path.read_text(encoding="utf-8", errors="replace")
    }
    assert implements == set(SIDECARS), (
        "the conformance matrix is out of step with the engines that implement the "
        f"framing:\n  missing from the matrix: {sorted(implements - set(SIDECARS))}\n"
        f"  listed but no longer framing: {sorted(set(SIDECARS) - implements)}"
    )
