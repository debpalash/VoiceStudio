"""Regression: the GPU pool must survive a reset without breaking long-lived
references (#589 #599 — "cannot schedule new futures after shutdown").

`_reset_gpu_pool()` fires on a model-load timeout. Before the fix, consumers
that did a *module-level* `from services.model_manager import _gpu_pool`
(generation, dub_generate, dub_core, dub_translate, openai_compat) captured the
ThreadPoolExecutor object once — so after a reset they kept submitting to the
shut-down pool and every generate/dub 500'd with "cannot schedule new futures
after shutdown". The resilient wrapper keeps a stable identity and rebuilds its
inner pool on demand, so those references self-heal.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def mm(fresh_model_manager, monkeypatch):
    manager = fresh_model_manager()
    monkeypatch.setattr(manager, "_pick_gpu_workers", lambda: 1)
    monkeypatch.setattr(manager, "_gpu_pool_singleton", None)
    return manager


def test_stale_reference_survives_reset(mm):
    # Mimic a module-level `from services.model_manager import _gpu_pool`.
    captured = mm._gpu_pool                      # triggers __getattr__ → wrapper
    assert captured.submit(lambda: 7).result(timeout=5) == 7

    mm._reset_gpu_pool()                          # the load-timeout recovery path

    # The SAME captured reference must still work — no "cannot schedule new
    # futures after shutdown".
    assert captured.submit(lambda: 11).result(timeout=5) == 11


def test_reset_keeps_wrapper_identity_drops_inner_pool(mm):
    pool = mm._get_gpu_pool()
    pool.submit(lambda: None).result(timeout=5)   # force-build the inner pool
    assert pool._pool is not None

    mm._reset_gpu_pool()
    assert mm._get_gpu_pool() is pool             # stable identity
    assert pool._pool is None                     # inner worker pool dropped

    pool.submit(lambda: None).result(timeout=5)   # rebuilds transparently
    assert pool._pool is not None


def test_submit_after_inner_shutdown_self_heals(mm):
    pool = mm._get_gpu_pool()
    # Simulate the exact failure: a stale inner pool that's been shut down.
    pool.submit(lambda: None).result(timeout=5)
    pool._pool.shutdown(wait=True)
    # Without the retry this raises RuntimeError("cannot schedule new futures
    # after shutdown"); the wrapper rebuilds and succeeds.
    assert pool.submit(lambda: 42).result(timeout=5) == 42


def test_wrapper_usable_with_asyncio_run_in_executor(mm):
    import asyncio

    pool = mm._get_gpu_pool()

    async def _go():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, lambda: 5)

    assert asyncio.run(_go()) == 5
