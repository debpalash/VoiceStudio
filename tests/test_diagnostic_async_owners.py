"""Actual streaming owners cannot lend a later grant to an awaited failure."""
import asyncio
import importlib
import threading
import time
from types import SimpleNamespace

import pytest

from test_diagnostic_reports import reporter  # noqa: F401


def test_grant_observation_uses_cached_authority_without_worker_io(reporter, monkeypatch):
    reports = importlib.import_module('core.diagnostic_reports')
    assert reports.observe_grant() is None
    reporter.consent(True)
    entered = threading.Event(); release = threading.Event()
    def hold_worker():
        with reporter.lock:
            entered.set(); release.wait(5)
    worker = threading.Thread(target=hold_worker)
    worker.start(); assert entered.wait(2)
    try:
        started = time.perf_counter()
        assert reports.observe_grant() == reporter.epoch
        assert time.perf_counter()-started < .1
    finally:
        release.set(); worker.join(2)
    monkeypatch.setattr(reports, '_reporter', None)
    monkeypatch.setattr(reports, 'initialize', lambda: pytest.fail('observation initialized diagnostics'))
    assert reports.observe_grant() is None


@pytest.mark.parametrize("owner", ["sherpa_load", "transcription", "tts"])
@pytest.mark.parametrize("choice", ["unchanged", "replacement", "later_opt_in"])
def test_stream_failure_keeps_the_pre_await_grant(reporter, monkeypatch, owner, choice):
    capture_ws = importlib.import_module("api.routers.capture_ws")
    tts_stream = importlib.import_module("api.routers.tts_stream")
    from services import asr_backend, sherpa_dictation
    from starlette.websockets import WebSocketDisconnect
    reporter.consent(choice != "later_opt_in")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        thread_release = threading.Event()
        loop = asyncio.get_running_loop()
        frames = []

        class Socket:
            query_params = {"sr": "16000"}
            url = SimpleNamespace(path="/ws/transcribe")
            calls = 0
            async def accept(self):
                pass
            async def close(self):
                pass
            async def send_json(self, value):
                frames.append(value)
            async def receive(self):
                self.calls += 1
                return {"type": "websocket.receive", **({"bytes": b"\0" * 20_000} if self.calls == 1 else {"text": "EOF"})}
            async def receive_json(self):
                self.calls += 1
                if self.calls > 1:
                    raise WebSocketDisconnect(1000)
                return {"text": "private input", "engine": "omnivoice"}

        async def failure(*args, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError("private awaited failure")

        async def allow(*args):
            return True

        if owner == "sherpa_load":
            def load():
                loop.call_soon_threadsafe(entered.set)
                assert thread_release.wait(5)
                raise RuntimeError("private awaited failure")
            monkeypatch.setattr(sherpa_dictation, "is_installed", lambda *_: True)
            work = capture_ws._sherpa_load_with_status(Socket(), SimpleNamespace(ensure_loaded=load), SimpleNamespace(id="stub"))
        elif owner == "transcription":
            monkeypatch.setattr(capture_ws, "require_ws_access", allow)
            monkeypatch.setattr(capture_ws, "_select_sherpa_spec", lambda *_: None)
            monkeypatch.setattr(capture_ws, "_dictation_licence_error", lambda *_: None)
            monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda **_: None)
            monkeypatch.setattr(capture_ws, "_transcribe_buffer_full", failure)
            work = capture_ws.ws_transcribe(Socket())
        else:
            monkeypatch.setattr(tts_stream, "require_ws_access", allow)
            monkeypatch.setattr(tts_stream, "_resolve_stream_backend", failure)
            work = tts_stream.ws_tts(Socket())
        task = asyncio.create_task(work)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if choice == "replacement":
                reporter.consent(False)
                reporter.consent(True)
            elif choice == "later_opt_in":
                reporter.consent(True)
            release.set()
            thread_release.set()
            await asyncio.wait_for(task, 5)
            assert any(frame.get("type") == "error" for frame in frames)
        finally:
            release.set()
            thread_release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    reporter._tick()
    saved = reporter._files()
    assert len(saved) == int(choice == "unchanged"), "an old awaited failure borrowed a new grant"
    if saved:
        assert "expected_epoch" not in saved[0]["props"] and "grant_epoch" not in saved[0]["props"]


def test_ws_tts_chunks_keep_message_owner_but_next_message_is_fresh(reporter, monkeypatch):
    from contextlib import nullcontext
    from starlette.websockets import WebSocketDisconnect
    reports = importlib.import_module('core.diagnostic_reports')
    route = importlib.import_module('api.routers.tts_stream')
    from services import tts_backend, engine_routing, engine_memory
    from core import device_caps
    reporter.consent(False)
    async def allow(*args): return True
    async def resolve(*args): return SimpleNamespace(id='omnivoice', sample_rate=24000)
    async def routing(*args): return {'routing_status': 'accelerated'}
    async def evict(*args): pass
    monkeypatch.setattr(route, 'require_ws_access', allow)
    monkeypatch.setattr(route, '_resolve_stream_backend', resolve)
    monkeypatch.setattr(route, 'build_stream_kwargs', lambda data: {})
    monkeypatch.setattr(tts_backend, 'engine_in_use', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(device_caps, 'detect_host_caps', lambda: None)
    monkeypatch.setattr(engine_routing, 'runtime_compute_profile_async', routing)
    monkeypatch.setattr(engine_routing, 'routing_notice', lambda value: None)
    monkeypatch.setattr(engine_memory, 'evict_other_tts_engines', evict)
    messages = [0]
    async def chunks(*args, **kwargs):
        if messages[0] == 1:
            yield b'\0\0', 24000, 0.01
        error = RuntimeError('synthetic current-message failure')
        reports.capture_failure('generation', error=error)
        raise error
    monkeypatch.setattr(route, '_ws_pcm_chunks', chunks)
    class Socket:
        async def accept(self): pass
        async def close(self): pass
        async def receive_json(self):
            messages[0] += 1
            if messages[0] == 2:
                assert reporter.intake.empty(), 'a later automatic chunk borrowed opt-in'
            if messages[0] > 2: raise WebSocketDisconnect(1000)
            return {'text': 'Neutral fixture', 'engine': 'omnivoice'}
        async def send_json(self, value): pass
        async def send_bytes(self, value):
            reporter.consent(True)
    asyncio.run(route.ws_tts(Socket()))
    reporter._tick()
    assert len(reporter._files()) == 1, 'a distinct later text request must still report'
