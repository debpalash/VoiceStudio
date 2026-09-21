"""Local render diagnostics: bounded, content-free, isolated across jobs."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

@pytest.fixture
def rt():
    import importlib
    return importlib.import_module("core.render_trace")


def test_stages_accumulate_failures_without_capturing_arguments(rt):
    trace = rt.RenderTrace('generate')
    with trace.activate():
        assert rt.call('synthesis', lambda text: 42, 'private script') == 42
        with pytest.raises(ValueError):
            rt.call('synthesis', lambda: (_ for _ in ()).throw(ValueError('secret path')))
    record = trace.snapshot()
    assert record['stages']['synthesis']['calls'] == 2
    assert record['stages']['synthesis']['failures'] == 1
    assert record['stages']['synthesis']['seconds'] >= 0
    assert 'private' not in json.dumps(record)
    assert 'secret' not in json.dumps(record)


def test_context_binding_isolated_and_finished_trace_immutable(rt):
    a, b = rt.RenderTrace('generate'), rt.RenderTrace('longform')
    with ThreadPoolExecutor(1) as pool:
        with a.activate():
            bound = rt.bind(lambda: rt.call('join', lambda: None))
        pool.submit(bound).result()
        with b.activate():
            pool.submit(rt.bind(lambda: rt.call('save', lambda: None))).result()
        pool.submit(lambda: rt.call('effects', lambda: None)).result()
    assert set(a.snapshot()['stages']) == {'join'}
    assert set(b.snapshot()['stages']) == {'save'}
    a.finish('complete')
    before = a.snapshot()
    bound()
    assert a.snapshot() == before


def test_bounded_ring_and_no_mutable_aliases(rt, monkeypatch):
    from collections import deque
    monkeypatch.setattr(rt, '_recent', deque(maxlen=2))
    for _ in range(3):
        rt.RenderTrace('generate').finish('complete')
    records = rt.recent()
    assert len(records) == 2
    records[0]['stages']['injected'] = {}
    assert 'injected' not in rt.recent()[0]['stages']


def test_stream_lifetime_and_concurrent_request_isolation(rt, monkeypatch):
    from collections import deque
    monkeypatch.setattr(rt, '_recent', deque(maxlen=8))

    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await asyncio.sleep(0)
        rt.call('synthesis', lambda: None)
        await send({'type': 'http.response.body', 'body': b'chunk', 'more_body': True})
        await asyncio.sleep(0)
        rt.call('join', lambda: None)
        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    async def run():
        async def receive():
            return {'type': 'http.request', 'body': b''}
        async def send(message):
            pass
        middleware = rt.RenderTraceMiddleware(app)
        await asyncio.gather(*[middleware({'type': 'http', 'method': 'POST', 'path': '/generate'}, receive, send) for _ in range(2)])
    asyncio.run(run())
    records = rt.recent()
    assert len(records) == 2
    assert len({r['id'] for r in records}) == 2
    for r in records:
        assert r['outcome'] == 'complete'
        assert r['stages']['synthesis']['calls'] == 1
        assert r['stages']['join']['calls'] == 1


def test_guarded_gpu_pool_propagates_trace(rt):
    from services.model_manager import run_on_gpu_pool_guarded
    trace = rt.RenderTrace('generate')
    async def run():
        with ThreadPoolExecutor(1) as pool, trace.activate():
            assert await run_on_gpu_pool_guarded(
                lambda: rt.call('synthesis', lambda: 17), executor=pool,
                timeout=5, queue_timeout=5,
            ) == 17
    asyncio.run(run())
    assert trace.snapshot()['stages']['synthesis']['calls'] == 1


@pytest.mark.parametrize('mode', ['cancel', 'error', 'http_error', 'disconnect'])
def test_failed_or_interrupted_stream_keeps_partial_trace(rt, monkeypatch, mode):
    from collections import deque
    monkeypatch.setattr(rt, '_recent', deque(maxlen=2))
    async def app(scope, receive, send):
        rt.call('synthesis', lambda: None)
        if mode == 'cancel':
            raise asyncio.CancelledError()
        if mode == 'error':
            raise RuntimeError('private details')
        if mode == 'disconnect':
            await receive()
            return
        await send({'type': 'http.response.start', 'status': 503, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
    async def run():
        async def receive():
            return {'type': 'http.disconnect'}
        async def send(message):
            pass
        try:
            await rt.RenderTraceMiddleware(app)({'type': 'http', 'method': 'POST', 'path': '/longform/render'}, receive, send)
        except (asyncio.CancelledError, RuntimeError):
            pass
    asyncio.run(run())
    record, = rt.recent()
    assert record['outcome'] == ('interrupted' if mode in {'cancel', 'disconnect'} else 'failed')
    assert record['stages']['synthesis']['calls'] == 1
    assert 'private' not in json.dumps(record)


@pytest.mark.parametrize('count', [100, 400])
@pytest.mark.parametrize('surface', ['generate', 'audiobook'])
def test_longform_stage_and_copy_budgets(rt, monkeypatch, count, surface):
    """Real orchestration + joins, fake engine: no hardware-sensitive timing gates."""
    import torch
    from services import chunked_tts
    from services.audiobook import Span, synthesize_chapter
    from api.routers.generation import _run_backend_inference
    # Each unit emits 40 samples; growing-cat would move O(N²) samples.
    real_cat = torch.cat
    copied = 0
    def cat(tensors, *args, **kwargs):
        nonlocal copied
        copied += sum(t.numel() for t in tensors)
        return real_cat(tensors, *args, **kwargs)
    monkeypatch.setattr(torch, 'cat', cat)
    monkeypatch.setattr(chunked_tts, 'split_text_into_chunks', lambda text, *a: ['chunk'] * count)
    class Engine:
        sample_rate = 1000
        applies_own_mastering = True
        def generate(self, text, **kwargs):
            return torch.ones(1, 40)
    trace = rt.RenderTrace(surface)
    with trace.activate():
        if surface == 'generate':
            result = _run_backend_inference(
                Engine(), 'private script', None, None, None, None, None,
                1, 2, 1, False, False, None, 'raw', crossfade_ms=0,
            )
        else:
            result, _ = synthesize_chapter([Span(text='private script', voice_id=None)],
                lambda *args: torch.ones(1, 40), 1000, crossfade_ms=0)
    assert result.shape[-1] == count * 40
    assert copied <= count * 40 * 2
    stats = trace.snapshot()['stages']
    assert stats['synthesis']['calls'] == count
    assert stats['join']['calls'] == 1
    if surface == 'generate':
        assert stats['effects']['calls'] == 1
