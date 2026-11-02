"""Tests for backend/services/subprocess_backend.py — Plan 02-01 Task 1.

Covers the SubprocessBackend round-trip via the permanent echo sidecar at
backend/engines/_echo/main.py.

These tests intentionally spawn a real subprocess (the echo sidecar uses
the parent's `sys.executable` so no engine venv is required). They run on
macOS, Linux, and Windows.
"""
from __future__ import annotations

import collections
import io
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import psutil
import pytest
import torch

# tests/conftest.py prepends ./backend to sys.path so `services.*` resolves.
from services.subprocess_backend import (
    MAX_FRAME_BYTES,
    PARENT_INBOUND_OPS,
    SubprocessBackend,
    _read_exact,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ECHO_SCRIPT = REPO_ROOT / "backend" / "engines" / "_echo" / "main.py"


@pytest.mark.parametrize("home", ["/Users/private-user", "/home/private-user", "C:\\Users\\private-user"])
def test_stderr_scrubbed_before_logging_and_buffering(home, caplog):
    from types import SimpleNamespace
    backend = EchoBackend()
    tail = collections.deque()
    separator = "\\" if home.startswith("C:") else "/"
    proc = SimpleNamespace(stderr=io.BytesIO(f"transcribing {home}{separator}reference.wav\n".encode()))
    with caplog.at_level("INFO"):
        backend._drain_stderr(proc, tail)
    assert "private-user" not in caplog.text
    assert "private-user" not in str(list(tail))
    assert f"~{separator}reference.wav" in caplog.text


# ── test-only subclass ─────────────────────────────────────────────────────


class EchoBackend(SubprocessBackend):
    """In-test subclass — runs the echo sidecar under `sys.executable`."""

    id = "_echo"
    display_name = "Echo (test)"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if ECHO_SCRIPT.is_file():
            return True, "ready"
        return False, f"echo sidecar missing at {ECHO_SCRIPT}"

    @classmethod
    def venv_python(cls) -> Path:
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return ECHO_SCRIPT


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def echo_backend():
    backend = EchoBackend()
    yield backend
    try:
        backend.shutdown()
    except Exception:
        pass


# ── round-trip tests ───────────────────────────────────────────────────────


def test_echo_script_exists():
    """Permanent CI regression infrastructure assertion."""
    assert ECHO_SCRIPT.is_file(), (
        f"Echo sidecar missing at {ECHO_SCRIPT}. This file is permanent "
        "CI regression infrastructure for SubprocessBackend — do not delete."
    )


def test_echo_round_trip(echo_backend):
    """Spawn → synthesize("hello") → expect 1 s of int16 silence as float32."""
    audio = echo_backend.generate("hello")
    assert isinstance(audio, torch.Tensor)
    assert audio.shape == (1, 24000), f"got shape {tuple(audio.shape)}"
    assert audio.dtype == torch.float32
    # Silence — exact zeros after int16 → float32 division.
    assert torch.max(torch.abs(audio)).item() == 0.0


def test_health_check_pings(echo_backend):
    ok, msg = echo_backend.health_check()
    assert ok, f"health_check failed: {msg}"
    assert msg == "pong"


def test_no_zombie_after_shutdown(echo_backend):
    """Spawn → record PID → shutdown → assert PID is gone within 3 s."""
    # Trigger spawn via a ping.
    ok, _ = echo_backend.health_check()
    assert ok
    assert echo_backend._proc is not None
    pid = echo_backend._proc.pid

    echo_backend.shutdown()

    # Poll for up to 3 s; the sidecar should be reaped by wait()/kill().
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            break
        # Or: zombie state is OK (parent reaped it via wait()).
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                # Reap it ourselves for the assertion below.
                p.wait(timeout=0.5)
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.1)

    assert not psutil.pid_exists(pid) or _is_zombie_or_dead(pid), (
        f"sidecar PID {pid} still alive after shutdown"
    )


def _is_zombie_or_dead(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        return True


def test_shutdown_idempotent(echo_backend):
    """Calling shutdown twice must not raise."""
    echo_backend.health_check()
    echo_backend.shutdown()
    echo_backend.shutdown()  # second call is a no-op


def test_env_forwarding_contract(monkeypatch, tmp_path):
    """HF_TOKEN, HF_HOME, HF_ENDPOINT, HF_HUB_CACHE all reach the sidecar.

    Locked Decision D5 — verifies the os.environ.copy() contract that Phase
    2 Wave 2 (IndexTTS migration) relies on for cache discovery.
    """
    monkeypatch.setenv("HF_TOKEN", "hf_test_abc")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))
    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf_cache"))
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")

    backend = EchoBackend()
    try:
        # Use the raw send/recv path to invoke the test-only probe_env op.
        with backend._lock:
            backend._spawn()
            backend._send({"op": "probe_env"})
        # probe_env_result isn't in the public PARENT_INBOUND_OPS allowlist
        # by design (it's test-only), so we read it directly via the wire
        # to avoid the allowlist drop. Reach into stdout for one frame.
        proc = backend._proc
        assert proc is not None
        header = _read_exact(proc.stdout, 4)
        assert header is not None
        (n,) = struct.unpack("!I", header)
        body = _read_exact(proc.stdout, n)
        assert body is not None
        import json as _json
        reply = _json.loads(body.decode("utf-8"))
        assert reply["op"] == "probe_env_result"
        keys = reply["keys"]
        assert keys["HF_TOKEN"] == "hf_test_abc"
        assert keys["HF_HOME"] == str(tmp_path / "hf_home")
        assert keys["HF_ENDPOINT"] == "https://huggingface.co"
        assert keys["HF_HUB_CACHE"] == str(tmp_path / "hf_cache")
    finally:
        backend.shutdown()


@pytest.mark.parametrize("gated", [False, True])
def test_automatic_endpoint_reaches_sidecar_without_token_or_parent_mutation(monkeypatch, gated):
    from services import endpoint_race, subprocess_backend
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_test_secret")
    monkeypatch.setenv("YOUR_HF_TOKEN", "hf_test_secret")
    monkeypatch.setattr(endpoint_race, "effective_endpoint", lambda: "https://hf-mirror.com")
    monkeypatch.setattr(endpoint_race, "explicit_mirror", lambda: "")
    monkeypatch.setattr(EchoBackend, "requires_hf_auth", gated, raising=False)
    captured = {}
    def spawn(*args, **kwargs):
        captured.update(kwargs["env"])
        raise RuntimeError("captured spawn")
    monkeypatch.setattr(subprocess_backend, "spawn_owned", spawn)
    with pytest.raises(RuntimeError, match="captured spawn"):
        EchoBackend()._spawn()
    if gated:
        assert captured["HF_ENDPOINT"] == endpoint_race.CANONICAL_ENDPOINT
        assert captured["HF_TOKEN"] == "hf_test_secret"
    else:
        assert captured["HF_ENDPOINT"] == "https://hf-mirror.com"
        assert "HF_TOKEN" not in captured
        assert "YOUR_HF_TOKEN" not in captured
        assert captured["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    assert "HF_ENDPOINT" not in os.environ
    assert os.environ["HF_TOKEN"] == "hf_test_secret"


@pytest.mark.parametrize("failure,changed,preselected,manual,gated,expected_restarts", [
    ("Connection reset by peer", True, False, False, False, 1),
    ("Connection reset by peer", False, False, False, False, 0),
    ("Connection reset by peer", False, True, False, False, 1),
    ("Connection reset by peer", True, False, True, False, 0),
    ("Connection reset by peer", True, False, False, True, 0),
    ("CUDA out of memory", True, False, False, False, 0),
])
@pytest.mark.parametrize("retry_fails", [False, True])
def test_live_sidecar_restarts_only_for_changed_automatic_download_endpoint(
    monkeypatch, failure, changed, preselected, manual, gated, expected_restarts, retry_fails,
):
    from services import endpoint_race, model_manager
    from types import SimpleNamespace

    backend = EchoBackend()
    monkeypatch.setattr(backend, "requires_hf_auth", gated)
    spawns, retired, reselections = [], [], []
    monkeypatch.setattr(model_manager, "running_on_gpu_pool", lambda: True)
    monkeypatch.setattr(endpoint_race, "mode", lambda: "manual" if manual else "auto")
    endpoint = ["https://hf-mirror.com"]
    def reselect(repo, reason):
        reselections.append((repo, reason))
        if changed:
            endpoint[0] = None
        return changed
    monkeypatch.setattr(endpoint_race, "reselect_after_failure", reselect)
    monkeypatch.setattr(endpoint_race, "download_endpoint", lambda *, gated=False: endpoint_race.CANONICAL_ENDPOINT if gated else endpoint[0])
    def spawn():
        assert backend._lock.locked()
        backend._proc = SimpleNamespace(number=len(spawns), stdin=None, wait=lambda timeout=None: 0)
        backend._spawn_hf_endpoint = endpoint_race.download_endpoint(gated=gated) or endpoint_race.CANONICAL_ENDPOINT
        spawns.append(backend._proc)
    def retire(proc):
        assert backend._lock.locked()
        retired.append(proc)
        backend._proc = None
    monkeypatch.setattr(backend, "_spawn", spawn)
    monkeypatch.setattr(backend, "_reap_unusable_process", retire)
    monkeypatch.setattr(backend, "_send", lambda msg: None)
    replies = iter([
        {"op": "error", "stage": "synthesize", "message": failure},
        {"op": "error", "stage": "synthesize", "message": failure} if retry_fails else {"op": "audio", "audio_pcm_b64": "AAA="},
    ])
    def receive(timeout):
        if preselected:
            # Another download selected the official endpoint while this child
            # was still using the mirror. No new global reselection is needed.
            endpoint[0] = None
        return next(replies)
    monkeypatch.setattr(backend, "_recv_with_timeout", receive)
    try:
        if expected_restarts and not retry_fails:
            assert backend.generate("Hello").shape == (1, 1)
        else:
            with pytest.raises(RuntimeError, match=failure):
                backend.generate("Hello")
        assert len(spawns) == 1 + expected_restarts
        assert len(retired) == expected_restarts
        if expected_restarts:
            assert retired[0] is spawns[0]
            assert backend._spawn_hf_endpoint == endpoint_race.CANONICAL_ENDPOINT
            assert len(reselections) == 1
    finally:
        # Backend instances remain in the global sidecar registry until GC
        # (and have an atexit shutdown callback). Never expose a protocol fake
        # to later reaper/lifecycle tests, including when an assertion fails.
        backend._proc = None


# ── frame protocol DoS guards ──────────────────────────────────────────────


class _FakeStdout:
    """Mock for stdin/stdout pipe in _recv unit tests."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, n: int) -> bytes:
        return self._buf.read(n)


class _MockProc:
    def __init__(self, stdout_data: bytes):
        self.stdout = _FakeStdout(stdout_data)
        self.stdin = None

    def poll(self) -> Optional[int]:
        return 0  # claim "already exited" so shutdown is a no-op

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def test_oversize_frame_rejected():
    """A length-prefix > MAX_FRAME_BYTES must raise IOError before allocation.

    T-02-01 — defends against malicious sidecar sending 0xFFFFFFFF to make
    the parent allocate 4 GB.
    """
    backend = EchoBackend()
    try:
        # Inject a fake proc whose stdout starts with a 100 MB length prefix.
        huge = MAX_FRAME_BYTES + 1
        backend._proc = _MockProc(struct.pack("!I", huge))  # type: ignore[assignment]
        with pytest.raises(IOError, match="frame too large"):
            backend._recv()
    finally:
        # Drop the mock so atexit shutdown doesn't try to wait on it.
        backend._proc = None


def test_short_read_rejected():
    """Length prefix says 100 bytes; only 50 arrive before EOF → IOError."""
    backend = EchoBackend()
    try:
        bad = struct.pack("!I", 100) + b"\x00" * 50  # only 50 of 100 bytes
        backend._proc = _MockProc(bad)  # type: ignore[assignment]
        with pytest.raises(IOError, match="short read"):
            backend._recv()
    finally:
        backend._proc = None


def test_op_allowlist_drops_unknown(monkeypatch):
    """An unknown sidecar op is logged and discarded; parent keeps reading.

    T-02-04 — set up the echo sidecar to emit an `exfiltrate` op followed
    by a legitimate `pong`; parent must silently drop the first and return
    the second.
    """
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    backend = EchoBackend()
    try:
        with backend._lock:
            backend._spawn()
            backend._send({"op": "emit_unknown"})
            # The sidecar emits {exfiltrate}, then {pong}. _recv must skip
            # the first frame and return the pong.
            reply = backend._recv_with_timeout(5.0)
        assert reply is not None
        assert reply["op"] == "pong"
    finally:
        backend.shutdown()


def test_op_allowlist_constant_shape():
    """PARENT_INBOUND_OPS must contain exactly the documented set."""
    assert PARENT_INBOUND_OPS == frozenset({
        "ready", "pong", "audio", "segments", "progress", "error",
        "gpu_acquire", "gpu_release",
    })
    # Sentinel — common typo / accidental addition would fail this test.
    assert "exfiltrate" not in PARENT_INBOUND_OPS
    assert "synthesize" not in PARENT_INBOUND_OPS  # that's a sidecar-inbound op


# ── crash-recovery / GPU slot accounting ───────────────────────────────────


def test_sidecar_crash_releases_resources(monkeypatch, echo_backend):
    """A sidecar that os._exit(1)'s mid-frame must not leave the parent
    holding state — `_proc` becomes detectable as dead and a fresh spawn
    works."""
    monkeypatch.setenv("OMNIVOICE_ECHO_CRASH", "1")
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")

    # First generate causes the sidecar to crash after sending the audio
    # frame (the crash hook fires after frames_handled >= 1).
    audio = echo_backend.generate("hello")
    assert audio.shape == (1, 24000)

    # Give the crashed child a moment to actually exit.
    time.sleep(0.5)

    # The next generate must spawn a fresh sidecar — the broken pipe from
    # the dead child should be detected, shutdown clears _proc, and a new
    # spawn succeeds. We allow the generate to fail OR to succeed after
    # respawn; the invariant is "the backend doesn't deadlock or wedge".
    try:
        echo_backend.shutdown()
    except Exception:
        pass
    # Disable the crash hook so the next spawn lives.
    monkeypatch.delenv("OMNIVOICE_ECHO_CRASH", raising=False)
    # Fresh spawn works.
    ok, msg = echo_backend.health_check()
    assert ok, f"recovery failed: {msg}"


# ── module-level helpers ───────────────────────────────────────────────────


def test_no_multiprocessing_imports():
    """Locked decision D4 — no `mp.Process`/`mp.fork`/`mp.spawn` allowed."""
    src = (REPO_ROOT / "backend" / "services" / "subprocess_backend.py").read_text(encoding="utf-8")
    for bad in ("mp.Process", "mp.fork", "mp.spawn",
                "multiprocessing.Process", "multiprocessing.fork",
                "multiprocessing.spawn"):
        assert bad not in src, (
            f"Found forbidden multiprocessing pattern {bad!r} in "
            "subprocess_backend.py — see locked decision D4 / Pitfall 1."
        )


def test_max_frame_bytes_is_64mb():
    assert MAX_FRAME_BYTES == 64 * 1024 * 1024

# ── a failed ready handshake names its cause (#2026) ──────────────────────


def _spawn_expecting_failure(backend) -> str:
    with backend._lock:
        with pytest.raises(RuntimeError) as err:
            backend._spawn()
    return str(err.value)


def test_an_early_exit_names_its_exit_code_and_stderr(monkeypatch, echo_backend):
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_ECHO_EXIT_BEFORE_READY", "3")
    msg = _spawn_expecting_failure(echo_backend)
    assert "did not signal ready: it exited with code 3 before signalling ready" in msg
    assert "exiting before ready on purpose" in msg
    assert "None" not in msg


def test_a_deadline_kill_says_it_was_the_deadline(monkeypatch, echo_backend):
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_ECHO_STALL_BEFORE_READY", "1")
    echo_backend.spawn_ready_timeout_s = 1.0
    msg = _spawn_expecting_failure(echo_backend)
    assert "no ready frame within 1s, so it was stopped" in msg
    assert "stalling before ready on purpose" in msg
    assert "exited with code" not in msg


def test_a_wrong_first_frame_is_a_protocol_mismatch(monkeypatch, echo_backend):
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_ECHO_WRONG_READY", "1")
    msg = _spawn_expecting_failure(echo_backend)
    assert "it sent op='pong' instead of 'ready'" in msg


def test_each_spawn_quotes_only_its_own_stderr(monkeypatch, echo_backend):
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_ECHO_EXIT_BEFORE_READY", "3")
    _spawn_expecting_failure(echo_backend)
    first = echo_backend._stderr_tail
    monkeypatch.setenv("OMNIVOICE_ECHO_EXIT_BEFORE_READY", "4")
    msg = _spawn_expecting_failure(echo_backend)
    assert "exited with code 4" in msg
    # The first process's drain, still finishing, writes to its own buffer.
    first.append("a late line from the previous process")
    assert echo_backend._stderr_tail is not first
    assert "late line" not in echo_backend._stderr_tail_text()


def test_an_error_frame_before_ready_is_quoted_and_scrubbed(monkeypatch, echo_backend):
    monkeypatch.setenv("OMNIVOICE_ECHO_TEST_MODE", "1")
    monkeypatch.setenv(
        "OMNIVOICE_ECHO_ERROR_BEFORE_READY", "import failed in C:\\Users\\alice\\engine"
    )
    msg = _spawn_expecting_failure(echo_backend)
    assert "it reported an error instead: import failed in" in msg
    assert "alice" not in msg


class _LinesProc:
    def __init__(self, *lines: bytes):
        self.stderr = io.BytesIO(b"".join(line + b"\n" for line in lines))


def test_a_late_drain_reads_its_own_process_not_the_replacement(echo_backend):
    old, new = _LinesProc(b"old process line"), _LinesProc(b"new process line")
    old_tail = collections.deque(maxlen=12)
    # The replacement is already published when the old drain finally runs.
    echo_backend._proc = new
    try:
        echo_backend._drain_stderr(old, old_tail)
    finally:
        echo_backend._proc = None
    assert list(old_tail) == ["old process line"]
    assert new.stderr.read() == b"new process line\n"  # untouched


def test_the_quoted_stderr_is_scrubbed_and_bounded(echo_backend):
    echo_backend._stderr_tail.clear()
    echo_backend._stderr_tail.append("loading C:\\Users\\alice\\venv hf_" + "a" * 34)
    for i in range(40):
        echo_backend._stderr_tail.append("x" * 60 + str(i))
    text = echo_backend._stderr_tail_text()
    assert "alice" not in text and "hf_aaaa" not in text
    assert len(text) <= 801


def test_float_transport_retains_pcm16_duration_budget(monkeypatch):
    import json
    import services.subprocess_backend as protocol
    monkeypatch.setitem(EchoBackend._recv.__globals__, "MAX_FRAME_BYTES", 128)
    backend = EchoBackend()
    backend.supports_float_transport = True
    frame = json.dumps({"op": "audio", "audio_format": "f32le", "audio_pcm_b64": "A" * 100}).encode()
    assert 128 < len(frame) < 256
    try:
        backend._proc = _MockProc(struct.pack("!I", len(frame)) + frame)
        assert backend._recv()["audio_format"] == "f32le"
        backend._proc = _MockProc(struct.pack("!I", 257))
        with pytest.raises(IOError, match="frame too large"):
            backend._recv()
    finally:
        backend._proc = None
