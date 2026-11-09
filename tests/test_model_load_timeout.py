"""Regression: model load/download must never hang forever (Windows demo-voice
"runs indefinitely, no audio, no error" report).

`get_model()` wraps the blocking load in an `asyncio.wait_for` deadline; on
timeout it drops the poisoned GPU pool and raises a clear RuntimeError instead
of leaving `/generate` (and the UI spinner) wedged forever.
"""
from __future__ import annotations

import asyncio
import sys
import threading

import pytest

@pytest.fixture
def model_manager(fresh_model_manager, monkeypatch):
    manager = fresh_model_manager()
    monkeypatch.setattr(manager, "model", None)
    return manager


def test_model_load_timeout_respects_env(model_manager, monkeypatch):
    mm = model_manager
    monkeypatch.delenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", raising=False)
    assert mm._model_load_timeout() == 1200.0
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "5000")
    assert mm._model_load_timeout() == 5000.0
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "not-a-number")
    assert mm._model_load_timeout() == 1200.0
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "1")  # below the safety floor
    assert mm._model_load_timeout() == 30.0


def test_get_model_times_out_and_resets_pool(model_manager, monkeypatch):
    mm = model_manager
    # Isolated lock so we never reuse one bound to a previous test's loop.
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock())
    monkeypatch.setattr(mm, "_model_load_timeout", lambda: 0.3)

    release = threading.Event()

    def _hang():  # simulates a wedged download that never returns
        release.wait(2.0)
        return object()

    monkeypatch.setattr(mm, "_load_model_sync", _hang)
    assert mm._get_gpu_pool() is not None  # a pool exists before the timeout

    try:
        with pytest.raises(RuntimeError, match="timed out"):
            asyncio.run(mm.get_model())
        assert mm.model is None                 # no half-loaded model
        # The resilient wrapper survives (importers hold its identity); only its
        # inner worker pool is dropped, so a retry builds a fresh worker.
        assert mm._gpu_pool_singleton is not None
        assert mm._gpu_pool_singleton._pool is None
        assert not mm._model_lock.locked()       # lock released for a retry
    finally:
        release.set()  # let the orphaned worker exit immediately
