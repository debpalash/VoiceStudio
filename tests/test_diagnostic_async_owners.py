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
