import sys
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_gateway_runs_local_call_through_guarded_pool(monkeypatch):
    from services import gpu_gateway

    seen = {}

    async def guarded(fn, **kwargs):
        seen.update(kwargs)
        return fn()

    monkeypatch.setitem(
        sys.modules,
        "services.model_manager",
        SimpleNamespace(
            check_gpu_admission=lambda **kwargs: seen.update(admission=kwargs),
            run_on_gpu_pool_guarded=guarded,
        ),
    )

    result = await gpu_gateway.run(
        local=gpu_gateway.LocalCall(
            fn=lambda: "done",
            what="local test",
            timeout=12,
            min_vram_gb=4,
        ),
        admit=True,
    )

    assert result == "done"
    assert seen["what"] == "local test"
    assert seen["timeout"] == 12
    assert seen["min_vram_gb"] == 4
    assert seen["admission"]["what"] == "local test"


@pytest.mark.asyncio
async def test_gateway_prepares_local_call_lazily(monkeypatch):
    from services import gpu_gateway

    async def guarded(fn, **_kwargs):
        return fn()

    monkeypatch.setitem(
        sys.modules,
        "services.model_manager",
        SimpleNamespace(
            check_gpu_admission=lambda **_kwargs: None,
            run_on_gpu_pool_guarded=guarded,
        ),
    )

    async def prepare():
        return gpu_gateway.LocalCall(fn=lambda: 42)

    assert await gpu_gateway.run(local=gpu_gateway.LocalCall(prepare=prepare)) == 42


@pytest.mark.asyncio
async def test_prewarm_uses_the_model_load_budget_and_calls_ensure_ready(monkeypatch):
    """prewarm() must resolve the load budget through model_manager's real helper
    (an ImportError here made every non-native TTS engine answer 503)."""
    from services import gpu_gateway

    seen = {}

    async def run_local(call, **_kwargs):
        seen["timeout"] = call.timeout
        seen["fn"] = call.fn
        return None

    monkeypatch.setattr(gpu_gateway, "_run_local", run_local)
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "321")
    backend = SimpleNamespace(ensure_ready=lambda: None, engine_id="fake")

    await gpu_gateway.prewarm(backend=backend, engine="fake")

    assert seen["timeout"] == 321.0
    assert seen["fn"] is backend.ensure_ready
    await gpu_gateway.prewarm(backend=backend, engine="fake", timeout=7)
    assert seen["timeout"] == 7
