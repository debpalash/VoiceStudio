"""Streaming overrides reuse cached engines without unloading concurrent streams."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture()
def engines(monkeypatch):
    from services import tts_backend

    class First:
        id = "first-test"
        created = 0
        unloaded = 0

        def __init__(self):
            type(self).created += 1

        def unload(self):
            type(self).unloaded += 1

    class Second(First):
        id = "second-test"
        created = 0
        unloaded = 0

    monkeypatch.setitem(tts_backend._REGISTRY, First.id, First)
    monkeypatch.setitem(tts_backend._REGISTRY, Second.id, Second)
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    fake_router = ModuleType("api.routers.engines")
    fake_router._ENGINE_INSTANCES = tts_backend._ENGINE_INSTANCES
    monkeypatch.setitem(sys.modules, "api.routers.engines", fake_router)
    yield First, Second, tts_backend._ENGINE_INSTANCES


def test_stream_overrides_reuse_without_evicting_other_streams(engines):
    from api.routers.tts_stream import _resolve_stream_backend

    first_cls, second_cls, cache = engines

    async def run():
        first = await _resolve_stream_backend("first-test")
        again = await _resolve_stream_backend("first-test")
        assert first is again
        assert first_cls.created == 1
        assert first_cls.unloaded == 0

        second = await _resolve_stream_backend("second-test")
        assert second_cls.created == 1
        assert first_cls.unloaded == 0
        assert cache[first_cls] is first
        assert cache[second_cls] is second

        back = await _resolve_stream_backend("first-test")
        assert back is first
        assert first_cls.created == 1
        assert second_cls.unloaded == 0
        assert cache[first_cls] is first
        assert cache[second_cls] is second

    asyncio.run(run())


def test_stream_without_override_keeps_active_backend_path(engines, monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import tts_backend

    first_cls, _, cache = engines
    active = object()
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "first-test")
    monkeypatch.setattr(tts_backend, "get_active_tts_backend", lambda: active)

    assert asyncio.run(_resolve_stream_backend(None)) is active
    assert first_cls.created == 0  # no explicit override instance was made
    assert not cache


def test_stream_override_does_not_unload_separate_active_engine(engines, monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import tts_backend

    first_cls, second_cls, cache = engines
    active = first_cls()
    monkeypatch.setattr(tts_backend, "_active_instance", active)
    monkeypatch.setattr(tts_backend, "_active_instance_id", first_cls.id)

    selected = asyncio.run(_resolve_stream_backend(second_cls.id))

    assert selected is cache[second_cls]
    assert first_cls.unloaded == 0
    assert tts_backend._active_instance is active
    assert tts_backend._active_instance_id == first_cls.id


def test_stream_override_reuses_matching_active_instance(engines, monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import tts_backend

    first_cls, _, cache = engines
    active = first_cls()
    monkeypatch.setattr(tts_backend, "_active_instance", active)
    monkeypatch.setattr(tts_backend, "_active_instance_id", first_cls.id)

    selected = asyncio.run(_resolve_stream_backend(first_cls.id))

    assert selected is active
    assert first_cls.created == 1
    assert first_cls.unloaded == 0
    assert not cache  # do not create a duplicate instance in the other cache


def test_explicit_omnivoice_override_keeps_lazy_core_path(engines, monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import model_manager, tts_backend

    class FakeOmni:
        id = "omnivoice"
        constructed = 0

        def __init__(self):
            type(self).constructed += 1

    monkeypatch.setattr(tts_backend, "_effective_backend_class", lambda _id, cls: cls)
    monkeypatch.setitem(tts_backend._REGISTRY, "omnivoice", FakeOmni)
    monkeypatch.setattr(tts_backend, "OmniVoiceBackend", FakeOmni)

    async def unexpected_model_load():
        pytest.fail("explicit OmniVoice override should load lazily")

    monkeypatch.setattr(model_manager, "get_model", unexpected_model_load)
    assert isinstance(asyncio.run(_resolve_stream_backend("omnivoice")), FakeOmni)
    assert FakeOmni.constructed == 1


def test_stream_follows_active_model_change_without_unloading_in_flight(monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import tts_backend

    monkeypatch.setattr(tts_backend, '_active_instance', None)
    monkeypatch.setattr(tts_backend, '_active_instance_id', None)
    monkeypatch.setattr(tts_backend, '_active_mlx_model_key', None)
    monkeypatch.setattr(tts_backend, '_ENGINE_IN_USE', {})
    monkeypatch.setattr(tts_backend, '_RETIRED_ENGINES', {})
    monkeypatch.setattr(tts_backend, 'active_backend_id', lambda: 'mlx-audio')
    monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', 'kokoro')
    first = tts_backend.get_active_tts_backend()
    unloaded = []
    first.unload = lambda: unloaded.append(True)
    with tts_backend.engine_in_use(first):
        monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', 'outetts')
        second = asyncio.run(_resolve_stream_backend('mlx-audio'))
        assert second is not first
        assert second is tts_backend._active_instance
        assert second.model_identity() == second.CURATED_MODELS['outetts']
        assert not unloaded
    assert unloaded == [True]


def test_resolver_does_not_invoke_eviction_during_another_stream(engines, monkeypatch):
    from api.routers.tts_stream import _resolve_stream_backend
    from services import engine_memory

    first_cls, second_cls, cache = engines
    first = asyncio.run(_resolve_stream_backend(first_cls.id))

    async def unexpected_eviction(_selected_id):
        pytest.fail("stream resolver must not unload another socket's backend")

    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", unexpected_eviction)
    second = asyncio.run(_resolve_stream_backend(second_cls.id))

    assert cache[first_cls] is first
    assert cache[second_cls] is second
    assert first_cls.unloaded == 0

@pytest.mark.parametrize("route_unavailable", [False, True])
def test_websocket_holds_cached_engine_through_routing_and_releases_on_exit(
    engines, monkeypatch, route_unavailable
):
    from api.routers.tts_stream import ws_tts
    from services import tts_backend

    first_cls, _, cache = engines
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    entered = asyncio.Event()
    resume = asyncio.Event()

    class Socket:
        received = 0
        frames = []

        async def accept(self):
            pass

        async def receive_json(self):
            self.received += 1
            if self.received == 1:
                return {"text": "hello", "engine": first_cls.id}
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()

        async def send_json(self, frame):
            self.frames.append(frame)

    device_caps = ModuleType("core.device_caps")
    device_caps.detect_host_caps = lambda: object()
    routing = ModuleType("services.engine_routing")

    async def profile(backend, _caps):
        entered.set()
        await resume.wait()
        return {
            "routing_status": "unavailable" if route_unavailable else "accelerated",
            "routing_reason": "unavailable",
        }

    routing.runtime_compute_profile_async = profile
    routing.routing_notice = lambda _profile: None
    monkeypatch.setitem(sys.modules, "core.device_caps", device_caps)
    monkeypatch.setitem(sys.modules, "services.engine_routing", routing)

    async def run():
        socket = Socket()
        task = asyncio.create_task(ws_tts(socket))
        await asyncio.wait_for(entered.wait(), 2)
        backend = cache[first_cls]
        assert tts_backend._ENGINE_IN_USE[first_cls] == 1
        assert tts_backend.release_idle_engines(idle_seconds=0, now=1e15) == []
        assert first_cls.unloaded == 0
        if route_unavailable:
            resume.set()
            await asyncio.wait_for(task, 2)
            assert socket.frames[-1]["type"] == "error"
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not tts_backend._ENGINE_IN_USE
        assert cache[first_cls] is backend

    asyncio.run(run())
