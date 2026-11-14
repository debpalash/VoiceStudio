"""Actual HTTP transfers must match model metadata before atomic publication."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from socketserver import TCPServer
import threading
from types import SimpleNamespace

import httpx
import pytest


CONTENT = b'{"model_type":"demo","label":"caf\xc3\xa9","layers":12}\n'
# Independent reference: git hash-object --stdin over CONTENT (50 bytes).
GIT_BLOB_OID = "0c71b8b8fcde9e02c1553af9cc390d565997fcfe"


@pytest.fixture
def origin(monkeypatch):
    from services import segmented_download as sd

    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 16)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 16)
    state = SimpleNamespace(content=CONTENT, ranges=True, failing_from=None, gets=[])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(state.content)))
            if state.ranges:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            bounds = self.headers.get("Range")
            segment = tuple(map(int, bounds.removeprefix("bytes=").split("-"))) if bounds else None
            state.gets.append(segment)
            if segment is not None and state.failing_from is not None and segment[0] >= state.failing_from:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = state.content
            self.send_response(206 if segment is not None else 200)
            if segment is not None:
                lo, hi = segment
                self.send_header("Content-Range", f"bytes {lo}-{hi}/{len(body)}")
                body = body[lo:hi + 1]
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class LoopbackServer(ThreadingHTTPServer):
        def server_bind(self):
            # No reverse-DNS dependency for this numerical loopback fixture.
            TCPServer.server_bind(self)
            self.server_name = "127.0.0.1"
            self.server_port = self.server_address[1]

    server = LoopbackServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/config.json"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _etag(kind, content=CONTENT):
    digest = GIT_BLOB_OID if kind == "git-blob" else hashlib.sha256(content).hexdigest()
    return f'"{digest.upper()}"'


def _download(origin, destination, kind, *, content=CONTENT, **kwargs):
    from services.segmented_download import segmented_download

    async def run():
        # Keep this real HTTP traffic local even on proxy-configured CI hosts.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client:
            return await segmented_download(
                origin.url, str(destination), client=client, expected_size=len(content),
                expected_etag=_etag(kind, content), num_connections=1, **kwargs,
            )

    return asyncio.run(run())


def _completed_partial(origin, destination, kind, monkeypatch):
    """Finish a real range transfer but interrupt its atomic publication."""
    replace = os.replace

    def interrupted_publish(source, target):
        if source == str(destination) + ".part" and target == str(destination):
            raise OSError("controlled interruption before publication")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted_publish)
        with pytest.raises(OSError, match="controlled interruption"):
            _download(origin, destination, kind)
    assert os.path.exists(str(destination) + ".part.done")


@pytest.mark.parametrize("kind", ["git-blob", "sha256"])
@pytest.mark.parametrize("ranged", [True, False])
def test_same_size_corruption_preserves_destination_and_correct_retry_heals(
    origin, tmp_path, monkeypatch, kind, ranged,
):
    destination = tmp_path / "config.json"
    previous = b"previous installed file"
    destination.write_bytes(previous)
    part = str(destination) + ".part"
    checkpoint = str(destination) + ".part.done"
    if not ranged:
        # A previous range transfer finished but failed at atomic publication.
        # The CDN can subsequently stop accepting ranges, overwriting that
        # partial through the single-stream path while its checkpoint exists.
        _completed_partial(origin, destination, kind, monkeypatch)
        assert destination.read_bytes() == previous

    origin.ranges = ranged
    origin.content = b"!" + CONTENT[1:]  # malformed JSON, identical byte count
    origin.gets.clear()
    with pytest.raises(ValueError, match="mismatch"):
        _download(origin, destination, kind)
    assert origin.gets  # corruption came from an actual HTTP response
    assert destination.read_bytes() == previous
    assert not os.path.exists(checkpoint)  # rejected bytes must not certify ranges

    origin.content = CONTENT
    origin.ranges = True
    origin.gets.clear()
    _download(origin, destination, kind)
    assert destination.read_bytes() == CONTENT
    assert sorted(origin.gets) == [(0, 15), (16, 31), (32, 47), (48, 49)]
    assert not os.path.exists(part) and not os.path.exists(checkpoint)


@pytest.mark.parametrize("kind", ["git-blob", "sha256"])
def test_valid_completed_ranges_survive_a_connection_failure(origin, tmp_path, kind):
    destination = tmp_path / "config.json"
    destination.write_bytes(b"previous installed file")
    origin.failing_from = 16
    with pytest.raises(httpx.HTTPStatusError):
        _download(origin, destination, kind)
    assert destination.read_bytes() == b"previous installed file"
    assert os.path.exists(str(destination) + ".part.done")

    origin.failing_from = None
    origin.gets.clear()
    _download(origin, destination, kind)
    assert destination.read_bytes() == CONTENT
    assert sorted(origin.gets) == [(16, 31), (32, 47), (48, 49)]


def test_cancel_during_large_hash_preserves_completed_ranges(origin, tmp_path, monkeypatch):
    import builtins
    from services import segmented_download as sd

    content = bytes(range(256)) * 8193  # more than two hash blocks
    origin.content = content
    monkeypatch.setattr(sd, "_MIN_SEGMENT_BYTES", 1024 * 1024)
    monkeypatch.setattr(sd, "_MAX_SEGMENT_BYTES", 1024 * 1024)
    destination = tmp_path / "weights.bin"
    destination.write_bytes(b"previous installed file")
    part = str(destination) + ".part"
    state = {"cancelled": False, "reads": 0}

    class CancelOnRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, *args):
            state["reads"] += 1
            block = self.handle.read(*args)
            if block:
                state["cancelled"] = True
            return block

    def opened(path, *args, **kwargs):
        handle = builtins.open(path, *args, **kwargs)
        if path == part and args and args[0] == "rb":
            return CancelOnRead(handle)
        return handle

    with monkeypatch.context() as patch:
        patch.setattr(sd, "open", opened, raising=False)
        with pytest.raises(sd.DownloadCancelled):
            _download(origin, destination, "sha256", content=content,
                      cancel_check=lambda: state["cancelled"])
    assert state == {"cancelled": True, "reads": 1}
    assert destination.read_bytes() == b"previous installed file"
    assert os.path.exists(part + ".done")
    state["cancelled"] = False
    origin.gets.clear()
    _download(origin, destination, "sha256", content=content,
              cancel_check=lambda: state["cancelled"])
    assert origin.gets == []  # a cancel never invalidates valid completed ranges
    assert destination.read_bytes() == content


@pytest.mark.parametrize("kind", ["git-blob", "sha256"])
def test_cancel_at_publication_preserves_valid_checkpoint(origin, tmp_path, monkeypatch, kind):
    from services import segmented_download as sd

    destination = tmp_path / "config.json"
    destination.write_bytes(b"previous installed file")
    cancelled = False
    makedirs = os.makedirs

    def before_publication(path, *args, **kwargs):
        nonlocal cancelled
        result = makedirs(path, *args, **kwargs)
        if path == str(tmp_path):
            cancelled = True
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "makedirs", before_publication)
        with pytest.raises(sd.DownloadCancelled):
            _download(origin, destination, kind, cancel_check=lambda: cancelled)
    assert cancelled
    assert destination.read_bytes() == b"previous installed file"
    assert os.path.exists(str(destination) + ".part.done")
    origin.gets.clear()
    _download(origin, destination, kind)
    assert origin.gets == []
    assert destination.read_bytes() == CONTENT


@pytest.mark.parametrize("ranged", [True, False])
def test_checkpoint_cleanup_failure_preserves_partial_and_destination(
    origin, tmp_path, monkeypatch, ranged,
):
    from services import segmented_download as sd

    destination = tmp_path / "config.json"
    destination.write_bytes(b"previous installed file")
    part = str(destination) + ".part"
    checkpoint = part + ".done"
    if not ranged:
        _completed_partial(origin, destination, "sha256", monkeypatch)
    origin.content = b"!" + CONTENT[1:]
    origin.ranges = ranged
    remove = os.remove

    def cannot_invalidate(path):
        if path == checkpoint and os.path.exists(checkpoint):
            raise PermissionError("controlled checkpoint cleanup failure")
        return remove(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "remove", cannot_invalidate)
        expected = sd.DownloadIntegrityError if ranged else PermissionError
        with pytest.raises(expected) as error:
            _download(origin, destination, "sha256")
        if ranged:
            assert isinstance(error.value.__cause__, PermissionError)
            assert "mismatch" in str(error.value)
        else:
            assert "controlled checkpoint cleanup" in str(error.value)
    assert destination.read_bytes() == b"previous installed file"
    assert os.path.exists(checkpoint)
    # Ranges have received rejected bytes; a single-stream fallback must not
    # truncate an existing partial until its old checkpoint is invalidated.
    with open(part, "rb") as handle:
        assert handle.read() == (origin.content if ranged else CONTENT)
