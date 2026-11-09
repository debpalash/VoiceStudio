"""A slow log reader must never block the backend (#2970, #2876, #2840, #2794, #2756).

The shell reads stdout/stderr through pipes. uvicorn writes its access log on
the event loop thread, so a full pipe froze ``/health`` while the log showed
only 200s. Measured on the real backend with an unread stderr pipe: it stopped
answering after ~1100 logged requests; with the buffer it served 6000.

Swapping ``sys.stdout`` is not enough on its own. uvicorn configures its
loggers in ``Config.__init__`` — before the app module is imported — so the
desktop launch path (``python -m uvicorn main:app``) hands the access log the
real pipe first and the buffer second. The access log then kept writing
straight to the pipe from the event loop thread, which is the very stall this
buffer exists to prevent: the shell reported "Backend is running but
temporarily not responding on port 3900. Waiting for recovery." while the
backend was alive and answering every other route (#3108).
"""
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from core.nonblocking_stdio import DecoupledStream  # noqa: E402


class _BlockedSink:
    encoding = "utf-8"

    def __init__(self):
        self.gate = threading.Event()
        self.received = []

    def write(self, text):
        self.gate.wait()
        self.received.append(text)

    def flush(self):
        pass


def test_write_returns_immediately_while_sink_is_blocked():
    sink = _BlockedSink()
    stream = DecoupledStream(sink, limit_bytes=4096)
    start = time.monotonic()
    for i in range(20000):
        stream.write(f"line {i}\n")
    assert time.monotonic() - start < 2.0
    sink.gate.set()
    stream.drain(5)
    out = "".join(sink.received)
    assert "dropped" in out and "line 19999\n" in out  # newest text survives
    assert len(out) < 2 * 4096 + 200  # one batch in flight + one buffered


def test_text_is_delivered_in_order_when_reader_keeps_up():
    sink = _BlockedSink()
    sink.gate.set()
    stream = DecoupledStream(sink)
    for i in range(50):
        stream.write(f"{i}\n")
    stream.drain(5)
    assert "".join(sink.received) == "".join(f"{i}\n" for i in range(50))


def test_broken_sink_never_raises_into_the_producer():
    class Gone(_BlockedSink):
        def write(self, text):
            raise BrokenPipeError

    sink = Gone()
    stream = DecoupledStream(sink)
    stream.write("x\n")
    stream.drain(2)
    assert stream.write("y\n") == 2


_CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {backend!r})
    from core.nonblocking_stdio import install
    install()
    chunk = "x" * 1023 + "\\n"
    for _ in range(8192):  # 8 MB, far beyond any pipe buffer
        sys.stderr.write(chunk)
    print("finished", flush=True)
    """
)


def _run_child(env_extra: dict[str, str], timeout: float):
    import os

    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", _CHILD.format(backend=str(BACKEND))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # never read: this is the slow log reader
        env={**os.environ, **env_extra},
        text=True,
    )
    try:
        out = proc.stdout.readline()  # type: ignore[union-attr]
        return out.strip()
    finally:
        proc.kill()
        proc.wait()


def test_unread_stderr_pipe_does_not_block_the_process():
    result: list[str] = []
    t = threading.Thread(target=lambda: result.append(_run_child({}, 30)), daemon=True)
    t.start()
    t.join(20)
    assert result == ["finished"], "writing to an unread stderr pipe blocked the process"


def test_control_unwrapped_stderr_pipe_blocks():
    """Proves the test above measures the real hazard, not a trivial pass."""
    result: list[str] = []
    t = threading.Thread(
        target=lambda: result.append(_run_child({"OMNIVOICE_BLOCKING_STDIO": "1"}, 30)),
        daemon=True,
    )
    t.start()
    t.join(4)
    assert t.is_alive() and result == []


def test_main_installs_it_before_logging_and_servers_start():
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    assert src.index("_install_nonblocking_stdio()") < src.index("import math")


# -- #3108: the access log must go through the buffer too --------------------
#
# The desktop shell launches the backend as `python -m uvicorn main:app`, so
# uvicorn builds its Config — and with it the log handlers — BEFORE the app
# module is imported. A handler keeps the stream it was built with, so the
# access log went on writing to the real pipe from the event loop thread and a
# slow log reader froze /health exactly as the unbuffered stdout did.

_HANDLER_PROBE = textwrap.dedent(
    """
    import logging, sys
    sys.path.insert(0, @@BACKEND@@)

    # uvicorn configures its loggers in Config.__init__, before the app
    # module is imported: this is the desktop shell's launch order
    # (`python -m uvicorn main:app --app-dir backend`).
    from uvicorn.config import Config
    Config(app="main:app", host="127.0.0.1", port=3900)

    # The real app module installs the buffer while it is imported.
    import main  # noqa: F401

    from core.nonblocking_stdio import DecoupledStream

    def _buffered(name):
        handlers = logging.getLogger(name).handlers
        return bool(handlers) and all(
            isinstance(getattr(h, "stream", None), DecoupledStream) for h in handlers
        )

    print("ACCESS=" + str(_buffered("uvicorn.access")), file=sys.stderr, flush=True)
    print("ERROR=" + str(_buffered("uvicorn")), file=sys.stderr, flush=True)
    """
)


def test_uvicorn_log_handlers_are_repointed_at_the_buffer():
    """uvicorn's access log must not keep writing to the real pipe."""
    probe = _HANDLER_PROBE.replace("@@BACKEND@@", repr(str(BACKEND)))
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", probe],
        stdout=subprocess.PIPE,  # the pipe the shell reads; never drained here
        stderr=subprocess.PIPE,
    )
    try:
        _out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hung child
            proc.kill()
            proc.wait()
    reported = {}
    for line in err.decode("utf-8", "replace").splitlines():
        key, _, value = line.strip().partition("=")
        if key in ("ACCESS", "ERROR"):
            reported[key] = value.strip()
    assert reported.get("ACCESS") == "True", (
        "uvicorn's access log still writes to the real pipe, so a slow log "
        "reader blocks the event loop and /health stops answering"
    )
    assert reported.get("ERROR") == "True", (
        "uvicorn's error log still writes to the real pipe"
    )


def test_a_handler_that_captured_the_real_stream_is_repointed(tmp_path):
    """The mechanism, without uvicorn: a handler built before install()."""
    probe = textwrap.dedent(
        """
        import logging, sys
        sys.path.insert(0, @@BACKEND@@)
        from core.nonblocking_stdio import DecoupledStream, install

        handler = logging.StreamHandler(sys.stdout)
        logging.getLogger("probe").addHandler(handler)
        install()
        print("REPOINTED=" + str(isinstance(handler.stream, DecoupledStream)),
              file=sys.stderr, flush=True)
        """
    ).replace("@@BACKEND@@", repr(str(BACKEND)))
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", probe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hung child
            proc.kill()
            proc.wait()
    assert "REPOINTED=True" in err.decode("utf-8", "replace"), (
        "a log handler that captured the real stream was left writing to it"
    )


_APP_MODULE = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, @@BACKEND@@)
    from core.nonblocking_stdio import DecoupledStream, install
    install()

    import logging
    if os.environ.get("OMNIVOICE_TEST_UNBUFFERED_ACCESS_LOG") == "1":
        # The state before #3108: the access log still writes to the real
        # pipe, so a full pipe blocks the event loop.
        real = sys.__stdout__
        for handler in logging.getLogger("uvicorn.access").handlers:
            if isinstance(getattr(handler, "stream", None), DecoupledStream):
                handler.stream = real


    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
    """
)

_CHILD_SERVER = textwrap.dedent(
    """
    import os, sys, threading
    sys.path.insert(0, @@APP_DIR@@)
    sys.path.insert(0, @@BACKEND@@)

    def _flood():
        # Nobody ever reads this pipe. Fill it, the way a model download's
        # progress bar does while the shell is slow to read.
        chunk = b"x" * 65536
        try:
            while os.write(1, chunk):
                pass
        except OSError:
            return

    threading.Thread(target=_flood, daemon=True).start()

    from uvicorn.config import Config
    from uvicorn.server import Server
    config = Config(app="probe_app:app", host="127.0.0.1", port=@@PORT@@,
                    log_level="info")
    Server(config=config).run()
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _consecutive_answers(port: int, attempts: int = 200, timeout: float = 2.0) -> int:
    """How many requests in a row the server answered before it stopped."""
    url = f"http://127.0.0.1:{port}/ping"
    answered = 0
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                response.read()
        except Exception:  # noqa: BLE001 - any failure means "not answering"
            return answered
        answered += 1
    return answered


def _start_server(tmp_path, *, unbuffered_access_log: bool = False):
    (tmp_path / "probe_app.py").write_text(
        _APP_MODULE.replace("@@BACKEND@@", repr(str(BACKEND))), encoding="utf-8"
    )
    port = _free_port()
    env = dict(os.environ)
    if unbuffered_access_log:
        env["OMNIVOICE_TEST_UNBUFFERED_ACCESS_LOG"] = "1"
    stderr_path = tmp_path / "child-stderr.log"
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    child = (
        _CHILD_SERVER.replace("@@APP_DIR@@", repr(str(tmp_path)))
        .replace("@@BACKEND@@", repr(str(BACKEND)))
        .replace("@@PORT@@", str(port))
    )
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", child],
        stdout=subprocess.PIPE,  # never read: the slow log reader
        stderr=stderr_handle,
        env=env,
    )
    return proc, port, stderr_path, stderr_handle


def _stop(proc: subprocess.Popen) -> None:
    """Kill the probe server, tolerating one that already exited."""
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def test_the_server_keeps_answering_when_nobody_reads_its_log(tmp_path):
    """The user-visible symptom: /health must not stop answering."""
    proc, port, stderr_path, stderr_handle = _start_server(tmp_path)
    try:
        assert _wait_for_port(port, time.monotonic() + 60), (
            "the probe server never came up: "
            + stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        )
        answered = _consecutive_answers(port)
        assert answered == 200, (
            f"the backend stopped answering after {answered} requests with a "
            "log pipe nobody reads — the shell would report it as "
            "'temporarily not responding'"
        )
    finally:
        _stop(proc)
        stderr_handle.close()


def test_control_the_unbuffered_access_log_stops_the_server(tmp_path):
    """Proves the test above measures the real hazard, not a trivial pass."""
    proc, port, stderr_path, stderr_handle = _start_server(
        tmp_path, unbuffered_access_log=True
    )
    try:
        assert _wait_for_port(port, time.monotonic() + 60), (
            "the probe server never came up: "
            + stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        )
        answered = _consecutive_answers(port)
        assert answered < 200, (
            "the control did not reproduce the stall, so the test above would "
            "pass for the wrong reason"
        )
    finally:
        _stop(proc)
        stderr_handle.close()
