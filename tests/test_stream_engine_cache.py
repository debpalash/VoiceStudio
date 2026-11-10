"""Live-stream engine resolution (_resolve_stream_backend) and the per-request lease."""
import asyncio
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture
def tts(monkeypatch):
    from services import tts_backend

    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    monkeypatch.setattr(tts_backend, "_active_instance", None)
    monkeypatch.setattr(tts_backend, "_active_instance_id", None)
    return tts_backend


def _engine_class(tts, engine_id, log):
    class _E(tts.TTSBackend):
        id = engine_id
        display_name = engine_id
        gpu_compat = ("cpu",)

        def __init__(self):
            log.append(("new", engine_id))

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            raise NotImplementedError

        def unload(self):
            log.append(("unload", engine_id))

    return _E


@pytest.fixture
def engines(tts, monkeypatch):
    log: list = []
    a = _engine_class(tts, "stream-a", log)
    b = _engine_class(tts, "stream-b", log)
    monkeypatch.setitem(tts._REGISTRY, "stream-a", a)
    monkeypatch.setitem(tts._REGISTRY, "stream-b", b)
    monkeypatch.setattr(tts, "ensure_engine_licence", lambda cls: None)

    # Eviction must never run from the resolver.
    from services import engine_memory

    async def _no_evict(*_a, **_k):
        raise AssertionError("resolver evicted engines")

    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", _no_evict)
    return type("Engines", (), {"a": a, "b": b, "log": log})


def _resolve(engine_id):
    from api.routers.tts_stream import _resolve_stream_backend

    return asyncio.run(_resolve_stream_backend(engine_id))


def test_override_is_cached_and_overrides_coexist(tts, engines, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    first = _resolve("stream-b")
    assert _resolve("stream-b") is first
    other = _resolve("stream-a")
    assert other is not first
    assert set(tts._ENGINE_INSTANCES) == {engines.a, engines.b}
    assert _resolve("stream-b") is first
    assert not [e for e in engines.log if e[0] == "unload"]
    assert [e for e in engines.log if e[0] == "new"] == [("new", "stream-b"), ("new", "stream-a")]


def test_no_override_uses_active_backend_and_leaves_cache_empty(tts, engines, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    inst = _resolve(None)
    assert isinstance(inst, engines.a)
    assert tts._ENGINE_INSTANCES == {}


def test_override_of_other_engine_leaves_active_untouched(tts, engines, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    active = _resolve(None)
    other = _resolve("stream-b")
    assert isinstance(other, engines.b)
    assert tts._active_instance is active
    assert tts._active_instance_id == "stream-a"
    assert ("unload", "stream-a") not in engines.log


def test_override_matching_active_reuses_it(tts, engines, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    active = _resolve(None)
    assert _resolve("stream-a") is active
    assert engines.a not in tts._ENGINE_INSTANCES


@pytest.mark.usefixtures("cpu_host")
def test_explicit_omnivoice_override_never_loads_the_shared_model(tts, monkeypatch):
    from services import model_manager

    async def _boom(*_a, **_k):
        raise AssertionError("get_model must not run for an override")

    monkeypatch.setattr(model_manager, "get_model", _boom)
    monkeypatch.setattr(tts, "ensure_engine_licence", lambda cls: None)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    inst = _resolve("omnivoice")
    assert isinstance(inst, tts.OmniVoiceBackend)


def test_changed_model_preference_swaps_active_without_unloading_the_old(tts, engines, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    old = _resolve(None)
    tts._active_instance, tts._active_instance_id = old, "stream-a"
    monkeypatch.setattr(tts, "_built_for_other_model", lambda cls, inst: inst is old)

    def _swap():
        new = engines.a()
        tts._active_instance, tts._active_instance_id = new, "stream-a"
        return new

    monkeypatch.setattr(tts, "get_active_tts_backend", lambda **_k: _swap())
    new = _resolve("stream-a")
    assert new is not old and tts._active_instance is new
    assert ("unload", "stream-a") not in engines.log


# -- lease on the websocket ----------------------------------------------------------

class _FakeSocket:
    def __init__(self, request):
        self._requests = [request]
        self.sent = []

    async def accept(self):
        pass

    async def receive_json(self):
        from fastapi import WebSocketDisconnect

        if self._requests:
            return self._requests.pop(0)
        raise WebSocketDisconnect()

    async def send_json(self, data):
        self.sent.append(data)


@pytest.mark.parametrize("exit_kind", ["unavailable", "cancelled"])
def test_ws_holds_the_engine_during_routing_and_releases_after(tts, engines, monkeypatch, exit_kind):
    from api.routers import tts_stream
    from services import engine_routing

    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "stream-a")
    seen = {}

    async def _profile(backend, _caps):
        seen["in_use"] = dict(tts._ENGINE_IN_USE)
        seen["swept"] = tts.release_idle_engines(idle_seconds=0)
        if exit_kind == "cancelled":
            raise asyncio.CancelledError()
        return {"routing_status": "unavailable", "routing_reason": "no"}

    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", _profile)
    sock = _FakeSocket({"text": "hello", "engine": "stream-b"})

    async def _drive():
        try:
            await tts_stream.ws_tts(sock)
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())
    assert seen["in_use"] == {engines.b: 1}
    assert seen["swept"] == []
    assert tts._ENGINE_IN_USE == {}
    assert engines.b in tts._ENGINE_INSTANCES


@pytest.mark.parametrize("disconnect", [False, True])
def test_ws_native_stream_sends_first_chunk_before_next_decode(tts, engines, monkeypatch, disconnect):
    import threading

    import torch
    from fastapi import WebSocketDisconnect

    from api.routers import openai_compat, tts_stream
    from services import engine_memory, engine_routing, watermark

    first_sent = threading.Event()
    closed = threading.Event()
    decoded = []
    marked = []
    engine = engines.b()
    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", threading.Lock())
    monkeypatch.setattr(engine, "can_stream", lambda **kwargs: True, raising=False)

    def stream_audio(text, **kwargs):
        assert text == "A single long sentence."
        try:
            decoded.append(1)
            yield torch.ones((1, 240), dtype=torch.float32) * 0.1
            assert first_sent.is_set()
            decoded.append(2)
            yield torch.ones((1, 240), dtype=torch.float32) * 0.2
        finally:
            closed.set()

    async def profile(*args):
        return {"routing_status": "accelerated", "routing_reason": ""}

    async def noop(*args):
        pass

    async def resolve(*args):
        return engine

    def mark(audio, rate, **kwargs):
        marked.append((rate, kwargs["context"]))
        return audio

    monkeypatch.setattr(engine, "stream_audio", stream_audio, raising=False)
    monkeypatch.setattr(tts_stream, "_resolve_stream_backend", resolve)
    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", profile)
    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", noop)
    monkeypatch.setattr(watermark, "mark_synthetic", mark)

    class Socket(_FakeSocket):
        async def send_bytes(self, data):
            self.sent.append(data)
            if disconnect:
                raise WebSocketDisconnect()
            first_sent.set()

    socket = Socket({"text": "A single long sentence.", "engine": "stream-b"})
    try:
        asyncio.run(tts_stream.ws_tts(socket))
    finally:
        worker.shutdown()

    assert closed.is_set()
    assert not tts._ENGINE_IN_USE
    assert socket.sent[0]["type"] == "start"
    assert socket.sent[0]["sample_rate"] == 24000
    assert len(socket.sent[1]) == 480
    if disconnect:
        assert decoded == [1]
    else:
        assert decoded == [1, 2]
        assert socket.sent[-1]["type"] == "done"
        assert socket.sent[-1]["samples"] == 480
    assert marked == [(24000, "tts_stream.native")] * len(decoded)


def _native_fake(engines, monkeypatch, chunks=2, *, wait_for_cleanup=None):
    """A native-streaming engine plus a private worker/lock and lease probe."""
    import threading
    import time
    from contextlib import contextmanager

    import torch

    from api.routers import openai_compat
    from services import tts_backend

    engine = engines.b()
    worker = openai_compat.ThreadPoolExecutor(max_workers=1)
    lock = threading.Lock()
    exited = []
    monkeypatch.setattr(openai_compat, "_native_pcm_worker", worker)
    monkeypatch.setattr(openai_compat, "_native_pcm_lock", lock)
    monkeypatch.setattr(engine, "can_stream", lambda **kwargs: True, raising=False)

    def stream_audio(text, **kwargs):
        for _ in range(chunks):
            yield torch.ones((1, 240), dtype=torch.float32) * 0.1

    monkeypatch.setattr(engine, "stream_audio", stream_audio, raising=False)

    @contextmanager
    def lease(backend, native=False, **kwargs):
        try:
            yield
        finally:
            if native:
                # Widen the window in which the queued cleanup is pending.
                if wait_for_cleanup is None:
                    time.sleep(0.2)
                else:
                    wait_for_cleanup()
                exited.append(True)

    monkeypatch.setattr(tts_backend, "engine_in_use", lease)
    return engine, worker, lock, exited


@pytest.mark.parametrize("sse", [False, True])
def test_native_stream_releases_lock_and_lease_before_completion(engines, monkeypatch, sse):
    from api.routers import openai_compat

    engine, worker, lock, exited = _native_fake(engines, monkeypatch)

    async def run():
        async for _ in openai_compat._native_pcm_stream(engine, "Hi.", {}, sse=sse):
            pass

    try:
        asyncio.run(run())
        assert exited == [True]
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        worker.shutdown()


def test_native_stream_early_close_returns_promptly_and_still_cleans_up(engines, monkeypatch):
    from api.routers import openai_compat

    import threading

    release = threading.Event()
    engine, worker, lock, exited = _native_fake(
        engines, monkeypatch, chunks=5, wait_for_cleanup=lambda: release.wait(5),
    )

    async def run():
        stream = openai_compat._native_pcm_stream(engine, "Hi.", {}, sse=False)
        await anext(stream)
        await stream.aclose()

    try:
        asyncio.run(run())
        # aclose() returned while the cleanup is still held on the worker.
        assert not exited
        assert lock.locked()
        release.set()
        worker.shutdown(wait=True)  # drains the queued cleanup
        assert exited == [True]
        assert not lock.locked()
    finally:
        release.set()
        worker.shutdown()


def test_ws_native_back_to_back_requests_never_report_busy(tts, engines, monkeypatch):
    from api.routers import tts_stream
    from services import engine_memory, engine_routing

    engine, worker, lock, exited = _native_fake(engines, monkeypatch)

    async def profile(*args):
        return {"routing_status": "accelerated", "routing_reason": ""}

    async def noop(*args):
        pass

    async def resolve(*args):
        return engine

    monkeypatch.setattr(tts_stream, "_resolve_stream_backend", resolve)
    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", profile)
    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", noop)

    class Socket(_FakeSocket):
        def __init__(self, *requests):
            super().__init__(requests[0])
            self._requests = list(requests)

        async def send_bytes(self, data):
            self.sent.append(data)

    request = {"text": "A single long sentence.", "engine": "stream-b"}
    socket = Socket(request, dict(request))
    try:
        asyncio.run(tts_stream.ws_tts(socket))
    finally:
        worker.shutdown()

    frames = [m["type"] for m in socket.sent if isinstance(m, dict)]
    assert frames == ["start", "done", "start", "done"]
    assert exited == [True, True]



def test_ws_reports_cleanup_timeout_without_releasing_a_live_worker(engines, monkeypatch):
    import threading

    from api.routers import openai_compat, tts_stream
    from services import engine_memory, engine_routing

    unblock = threading.Event()
    engine, worker, lock, exited = _native_fake(
        engines, monkeypatch, wait_for_cleanup=lambda: unblock.wait(5),
    )
    monkeypatch.setattr(openai_compat, "_NATIVE_PCM_CLEANUP_TIMEOUT_S", 0.05)

    async def profile(*args):
        return {"routing_status": "accelerated", "routing_reason": ""}

    async def noop(*args):
        pass

    async def resolve(*args):
        return engine

    monkeypatch.setattr(tts_stream, "_resolve_stream_backend", resolve)
    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", profile)
    monkeypatch.setattr(engine_memory, "evict_other_tts_engines", noop)

    class Socket(_FakeSocket):
        async def send_bytes(self, data):
            self.sent.append(data)

    socket = Socket({"text": "Hello.", "engine": "stream-b"})
    try:
        asyncio.run(tts_stream.ws_tts(socket))
        frames = [msg for msg in socket.sent if isinstance(msg, dict)]
        assert [frame["type"] for frame in frames] == ["start", "error"]
        assert "cleanup is still pending" in frames[-1]["detail"]
        assert lock.locked() and not exited
    finally:
        unblock.set()
        worker.shutdown()

    assert exited == [True] and not lock.locked()
