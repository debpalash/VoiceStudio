"""A sidecar's frame channel must not be shared with library logging (#1428).

Every sidecar speaks a length-prefixed binary protocol on stdout. It is not
the only thing writing there: the libraries it loads print freely to fd 1 —
wetextprocessing's FST logs, tqdm bars, native prints from torch and ONNX
runtime. Those bytes interleave with frames, the parent reads four bytes of
log text as a length prefix, and the generation dies with

    OSError: frame too large: 1044258881

(that number is ASCII text). The worse half is what it leaves behind: the
stream is now desynced, so every later request on the same sidecar reads
stale bytes and no retry can recover — which is why the reporter saw
alternating failures rather than a clean one.

Reported by @1335-Group against IndexTTS-2 with a tested fix. The defect is
not IndexTTS's: all nine sidecars wrote frames to `sys.stdout.buffer` and none
protected fd 1, so any of them breaks the moment a dependency prints.

These tests are the deterministic guard — a new sidecar copied from an old one
must not be able to reintroduce it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ENGINES = Path("backend/engines")

#: Every sidecar entry point: the script `SubprocessBackend` spawns.
SIDECARS = sorted(
    p for p in list(ENGINES.glob("*/main.py")) + list(ENGINES.glob("*/sidecar.py"))
)


def test_the_scan_finds_the_sidecars_it_is_meant_to_guard():
    """A guard that silently matched nothing would pass while protecting
    nothing."""
    assert len(SIDECARS) >= 8, [str(p) for p in SIDECARS]


@pytest.mark.parametrize("path", SIDECARS, ids=lambda p: p.parent.name)
def test_frames_do_not_go_to_the_shared_stdout(path):
    src = path.read_text(encoding="utf-8")
    assert "sys.stdout.buffer" not in src, (
        f"{path} writes frames to the shared stdout — any library that prints "
        "to fd 1 will corrupt the protocol (#1428)"
    )


@pytest.mark.parametrize("path", SIDECARS, ids=lambda p: p.parent.name)
def test_fd_one_is_redirected_to_stderr(path):
    """Both halves are required. `os.dup(1)` alone keeps a private channel but
    leaves library output going to the parent's frame reader; `os.dup2(2, 1)`
    alone sends frames to stderr."""
    src = path.read_text(encoding="utf-8")
    assert re.search(r"_frame_fd\s*=\s*os\.dup\(1\)", src), (
        f"{path} does not keep a private fd for frames (#1428)"
    )
    assert re.search(r"os\.dup2\(2,\s*1\)", src), (
        f"{path} does not redirect fd 1 to stderr, so library output still "
        "lands in the frame stream (#1428)"
    )
    # Ordering is load-bearing: dup2 before dup would duplicate stderr.
    assert src.index("os.dup(1)") < src.index("os.dup2(2, 1)"), (
        f"{path} redirects fd 1 before duplicating it — the 'private' frame "
        "channel is then just stderr"
    )


def test_a_printing_library_cannot_corrupt_the_frame_stream(tmp_path):
    """End-to-end proof, with a real subprocess and a real print().

    Fail-before: with `stdout = sys.stdout.buffer`, the `print()` below lands
    between the frames and the reader takes `b'nois'` as a length prefix.
    """
    script = tmp_path / "fake_sidecar.py"
    script.write_text(
        "import json, os, struct, sys\n"
        "_frame_fd = os.dup(1)\n"
        "os.dup2(2, 1)\n"
        "stdout = os.fdopen(_frame_fd, 'wb')\n"
        "\n"
        "def send(msg):\n"
        "    body = json.dumps(msg).encode()\n"
        "    stdout.write(struct.pack('!I', len(body)))\n"
        "    stdout.write(body)\n"
        "    stdout.flush()\n"
        "\n"
        "send({'op': 'ready'})\n"
        "print('noisy library banner on fd 1')\n"          # the whole point
        "sys.stdout.write('and another one\\n')\n"
        "os.write(1, b'native print straight to fd 1\\n')\n"
        "send({'op': 'audio', 'n': 1})\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode()

    # The frame channel must contain exactly the two frames and nothing else.
    import json
    import struct

    buf, frames = proc.stdout, []
    while buf:
        (n,) = struct.unpack("!I", buf[:4])
        assert n < 10_000, (
            f"read a {n}-byte length prefix — log text was parsed as a frame "
            "header, which is the #1428 failure exactly"
        )
        frames.append(json.loads(buf[4:4 + n]))
        buf = buf[4 + n:]
    assert [f["op"] for f in frames] == ["ready", "audio"]

    # And the noise is on stderr, where the parent already drains it.
    err = proc.stderr.decode()
    assert "noisy library banner" in err
    assert "native print straight to fd 1" in err
