"""Integration test for the real network-share listener lifecycle.

enable() starts a SECOND in-process uvicorn.Server bound to 0.0.0.0 on a
dedicated port serving the same app; disable() stops it. This test exercises
the real socket: it confirms a TCP connection succeeds on the reported
share_port while enabled, and is refused after disable().

Wrapped in asyncio.run inside a sync test so it does not depend on a
pytest-asyncio event-loop mode being configured.
"""
import asyncio
from types import SimpleNamespace
import socket

import pytest


@pytest.mark.parametrize('second_operation', ['enable', 'disable'])
def test_overlapping_lifecycle_calls_leave_no_listener_or_mcp_hosts(monkeypatch, second_operation):
    from services import network_share as ns

    async def exercise():
        servers = []
        serving = []

        class Server:
            def __init__(self, _config):
                self.started = False
                self.should_exit = False
                servers.append(self)

            async def serve(self):
                serving.append(asyncio.current_task())
                self.started = True
                while not self.should_exit:
                    await asyncio.sleep(0)

        monkeypatch.setattr(ns, '_runtime', ns._ShareRuntime())
        monkeypatch.setattr(ns.uvicorn, 'Server', Server)
        monkeypatch.setattr(ns, '_find_free_port', lambda *_: 3901)
        monkeypatch.setattr(ns, 'lan_ipv4_addresses', lambda: ['192.168.1.42'])
        security = SimpleNamespace(allowed_hosts=['localhost:*'], allowed_origins=['http://localhost:*'])
        app = SimpleNamespace(state=SimpleNamespace(mcp_transport_security=security))
        try:
            states = await asyncio.gather(ns.enable(app), getattr(ns, second_operation)(app))
            assert len(servers) == 1
            if second_operation == 'enable':
                assert states[0] is states[1]
            else:
                assert not ns.get_state().enabled
            await ns.disable(app)
            assert security.allowed_hosts == ['localhost:*']
            assert security.allowed_origins == ['http://localhost:*']
            assert all(task.done() for task in serving)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*serving, return_exceptions=True)

    asyncio.run(exercise())


def _can_connect(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if a TCP connection to host:port is accepted."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _wait_closed(port: int, host: str = "127.0.0.1", tries: int = 40) -> bool:
    """Poll until the port stops accepting connections (tolerates teardown lag)."""
    for _ in range(tries):
        if not _can_connect(port, host):
            return True
        # Synchronous sleep is fine here — runs outside the event loop, between
        # connect probes, after the server has been asked to exit.
        socket_wait = 0.05
        import time

        time.sleep(socket_wait)
    return False


async def _exercise_lifecycle():
    from services import network_share as ns

    # A minimal FastAPI app is enough — enable() only needs an ASGI app object
    # and a place to stash app.state.network_share.
    from fastapi import FastAPI

    app = FastAPI()

    # Sanity: starts Local (nothing bound to 0.0.0.0).
    assert ns.get_state().enabled is False

    state = await ns.enable(app)
    try:
        assert state.enabled is True
        assert ns.get_state().enabled is True
        port = state.share_port
        assert isinstance(port, int) and port > 0
        # app.state is updated to the enabled state.
        assert app.state.network_share.enabled is True
        assert app.state.network_share.share_port == port
        # The listener is really up: a TCP connect to the reported port succeeds.
        # The server binds 0.0.0.0; connect via loopback, which 0.0.0.0 covers.
        assert _can_connect(port), f"expected a live listener on port {port}"
    finally:
        await ns.disable(app)

    # After disable(): state reset and the socket is closed.
    assert ns.get_state().enabled is False
    assert app.state.network_share.enabled is False
    assert _wait_closed(port), f"expected port {port} closed after disable()"
    return port


def test_share_listener_lifecycle():
    from services import network_share as ns

    # Ensure a clean starting state regardless of test ordering.
    if ns.get_state().enabled:
        asyncio.run(ns.disable(__import__("fastapi").FastAPI()))

    try:
        # Probe whether binding 0.0.0.0 is permitted in this sandbox.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("0.0.0.0", 0))
            except OSError as e:
                pytest.skip(f"binding 0.0.0.0 not permitted in this sandbox: {e}")

        asyncio.run(_exercise_lifecycle())
    finally:
        # Defensive cleanup so a failure mid-test never leaves a stray listener
        # or a dirty module-level _state for the next test.
        if ns.get_state().enabled:
            asyncio.run(ns.disable(__import__("fastapi").FastAPI()))


def test_share_listener_does_not_rerun_the_app_lifespan():
    """The share listener serves the app the main server already started.

    It must not run the app's lifespan again: enabling would repeat startup,
    and disabling would run the full shutdown on the still-serving backend.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from services import network_share as ns

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("0.0.0.0", 0))
        except OSError as e:
            pytest.skip(f"binding 0.0.0.0 not permitted in this sandbox: {e}")

    events = []

    @asynccontextmanager
    async def lifespan(_app):
        events.append("startup")
        yield
        events.append("shutdown")

    app = FastAPI(lifespan=lifespan)

    async def exercise():
        await ns.enable(app)
        await ns.disable(app)

    try:
        asyncio.run(exercise())
    finally:
        if ns.get_state().enabled:
            asyncio.run(ns.disable(FastAPI()))
    assert events == []
def test_share_port_bind_failure_raises_instead_of_exiting(monkeypatch):
    """A port taken after the free-port probe must not exit the backend.

    uvicorn calls sys.exit(1) when its bind fails. Inside the share task that
    SystemExit escaped asyncio and stopped the backend's own event loop instead
    of reaching enable()'s "listener failed to start" handling.
    """
    from fastapi import FastAPI
    from services import network_share as ns

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        try:
            taken.bind(("0.0.0.0", 0))
        except OSError as e:
            pytest.skip(f"binding 0.0.0.0 not permitted in this sandbox: {e}")
        taken.listen()
        port = taken.getsockname()[1]
        monkeypatch.setattr(ns, "_runtime", ns._ShareRuntime())
        monkeypatch.setattr(ns, "_find_free_port", lambda *_: port)
        app = FastAPI()

        with pytest.raises(RuntimeError, match="failed to start"):
            asyncio.run(ns.enable(app))
    assert ns.get_state().enabled is False
    assert app.state.network_share.enabled is False
