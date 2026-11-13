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

from services import network_share as ns


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


def test_share_server_does_not_rerun_app_lifespan(monkeypatch):
    """The share server serves the already-started app, so it must not run the
    app lifespan a second time: a failing second startup makes uvicorn call
    sys.exit() inside the share task, which takes the whole backend down."""
    from services import network_share as ns

    configs = []

    class Server:
        def __init__(self, config):
            configs.append(config)
            self.started = False
            self._exit = asyncio.Event()

        @property
        def should_exit(self):
            return self._exit.is_set()

        @should_exit.setter
        def should_exit(self, value):
            if value:
                self._exit.set()

        async def serve(self):
            self.started = True
            await self._exit.wait()

    async def exercise():
        monkeypatch.setattr(ns, '_runtime', ns._ShareRuntime())
        monkeypatch.setattr(ns.uvicorn, 'Server', Server)
        monkeypatch.setattr(ns, '_find_free_port', lambda *_: 3901)
        monkeypatch.setattr(ns, 'lan_ipv4_addresses', lambda: ['192.168.1.42'])
        app = SimpleNamespace(state=SimpleNamespace())
        await ns.enable(app)
        await ns.disable(app)

    asyncio.run(exercise())
    assert len(configs) == 1
    assert configs[0].lifespan == "off"


def test_share_startup_failure_does_not_kill_the_event_loop(monkeypatch):
    """uvicorn calls sys.exit() when it cannot bind or start. Inside the share
    task that SystemExit would escape run_forever() and stop the main server,
    so enable() must see an ordinary error and stay Local."""
    from services import network_share as ns

    class Server:
        def __init__(self, _config):
            self.started = False
            self.should_exit = False

        async def serve(self):
            raise SystemExit(3)

    async def exercise():
        monkeypatch.setattr(ns, '_runtime', ns._ShareRuntime())
        monkeypatch.setattr(ns.uvicorn, 'Server', Server)
        monkeypatch.setattr(ns, '_find_free_port', lambda *_: 3901)
        monkeypatch.setattr(ns, 'lan_ipv4_addresses', lambda: ['192.168.1.42'])
        app = SimpleNamespace(state=SimpleNamespace())
        with pytest.raises(RuntimeError, match='share listener failed to start'):
            await ns.enable(app)
        assert not ns.get_state().enabled
        assert ns._runtime.task is None and ns._runtime.server is None
        assert app.state.network_share.enabled is False

    # A SystemExit leaking out of the share task would propagate from here.
    asyncio.run(exercise())


def test_share_port_taken_after_probe_stays_local(monkeypatch):
    """Real uvicorn: the port is taken between the free-port probe and bind."""
    from fastapi import FastAPI
    from services import network_share as ns

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        blocker.bind((ns.SHARE_BIND_HOST, 0))
    except OSError as e:
        blocker.close()
        pytest.skip(f"binding 0.0.0.0 not permitted in this sandbox: {e}")
    blocker.listen()
    taken = blocker.getsockname()[1]

    async def exercise():
        monkeypatch.setattr(ns, '_runtime', ns._ShareRuntime())
        monkeypatch.setattr(ns, '_find_free_port', lambda *_: taken)
        with pytest.raises(RuntimeError, match='share listener failed to start'):
            await ns.enable(FastAPI())
        assert not ns.get_state().enabled

    try:
        asyncio.run(exercise())
    finally:
        blocker.close()


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
