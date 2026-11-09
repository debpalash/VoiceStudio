"""The idle signal the desktop app uses to stop a terminal-started backend."""
import asyncio

import pytest
from core import activity


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(activity, '_probes', {})
    monkeypatch.setattr(activity, '_inflight', 0)


def run(app, path, scope_type='http'):
    async def receive():
        return {'type': 'http.request', 'body': b''}

    async def send(_message):
        pass

    asyncio.run(app({'type': scope_type, 'path': path}, receive, send))


def test_requests_reset_idle_time_but_owner_polls_do_not(isolated, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(activity.time, 'monotonic', lambda: clock[0])
    activity.touch()
    seen = []

    async def endpoint(scope, receive, send):
        seen.append(activity.snapshot()['inflight'])

    middleware = activity.ActivityMiddleware(endpoint)
    clock[0] = 160.0
    run(middleware, '/health')
    run(middleware, '/system/activity')
    assert activity.snapshot()['idle_seconds'] == 60.0
    run(middleware, '/profiles')
    assert seen == [0, 0, 1]
    clock[0] = 170.0
    assert activity.snapshot() == {'inflight': 0, 'busy': [], 'idle_seconds': 10.0}


def test_open_stream_keeps_the_backend_busy(isolated):
    gate = asyncio.Event()
    snapshots = []

    async def stream(scope, receive, send):
        await gate.wait()

    async def scenario():
        task = asyncio.create_task(activity.ActivityMiddleware(stream)(
            {'type': 'websocket', 'path': '/ws/events'}, None, None))
        await asyncio.sleep(0)
        snapshots.append(activity.snapshot())
        gate.set()
        await task

    asyncio.run(scenario())
    assert snapshots[0]['inflight'] == 1 and snapshots[0]['idle_seconds'] == 0
    assert activity.snapshot()['inflight'] == 0


def test_running_work_and_unreadable_registries_keep_the_backend_alive(isolated):
    def broken():
        raise RuntimeError('registry changed during iteration')

    activity.register_probe('downloads', lambda: True)
    activity.register_probe('calls', lambda: False)
    activity.register_probe('jobs', broken)
    state = activity.snapshot()
    assert state['busy'] == ['downloads', 'jobs']
    assert state['idle_seconds'] == 0


def test_owning_modules_register_their_work():
    import importlib
    for module in ['core.job_store', 'services.proc_registry', 'services.dub_pipeline',
                   'services.sidecar_install', 'services.audiocpp_runtime_install',
                   'api.routers.batch', 'api.routers.setup.download', 'api.routers.project_bundles',
                   'services.telephony.calls']:
        importlib.import_module(module)
    assert {'jobs', 'subprocesses', 'dub-ingest', 'engine-installs', 'runtime-install', 'batch',
            'model-downloads', 'project-transfers', 'calls'} <= set(activity._probes)
