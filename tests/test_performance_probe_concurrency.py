"""Concurrent requests share bounded readiness work without blocking other keys."""
from concurrent.futures import Future, ThreadPoolExecutor
import importlib
import threading

import pytest


@pytest.mark.parametrize('outcome', [None, {'ready': False}, {'ready': True}])
def test_cold_queries_share_completed_result(monkeypatch, outcome):
    """Duplicate same-key requests must reuse even a failed child result."""
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(probe, '_CACHE', {})
    monkeypatch.setattr(probe, '_INFLIGHT', {}, raising=False)
    monkeypatch.setattr(probe, '_identity', lambda family, engine, broken: (family, engine))
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            """Observe a real waiter so the test does not depend on scheduling delays."""
            waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(probe, "Future", ObservedFuture)
    calls = []

    def run(family, engine, broken):
        """Hold one child while unrelated keys remain available."""
        if engine == 'other':
            return {'ready': True}
        calls.append(engine)
        entered.set()
        assert release.wait(5)
        return outcome

    monkeypatch.setattr(probe, '_run_query', run)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(probe._query, 'asr', 'pytorch-whisper')
        assert entered.wait(5)
        second = pool.submit(probe._query, 'asr', 'pytorch-whisper')
        try:
            assert pool.submit(probe._query, 'asr', 'other').result(5) == {'ready': True}
            assert waiting.wait(5), 'Concurrent request did not join the in-flight query'
        finally:
            release.set()
        assert first.result(5) == second.result(5) == outcome
        assert calls == ['pytorch-whisper']
        assert not probe._INFLIGHT


def test_unexpected_failure_releases_waiters_and_allows_retry(monkeypatch):
    """Even an unexpected owner exception must not strand a per-key waiter."""
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(probe, '_CACHE', {})
    monkeypatch.setattr(probe, '_INFLIGHT', {})
    monkeypatch.setattr(probe, '_identity', lambda *args: 'same')
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            """Signal that the duplicate joined the real shared future."""
            waiting.set()
            return super().result(timeout)

    def fail(*args):
        """Hold an unexpected failure until a duplicate is already waiting."""
        entered.set()
        assert release.wait(5)
        raise RuntimeError('unexpected child transport failure')

    monkeypatch.setattr(probe, 'Future', ObservedFuture)
    monkeypatch.setattr(probe, '_run_query', fail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(probe._query, 'asr', 'pytorch-whisper')
        assert entered.wait(5)
        second = pool.submit(probe._query, 'asr', 'pytorch-whisper')
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        for caller in (first, second):
            with pytest.raises(RuntimeError, match='unexpected child transport failure'):
                caller.result(5)
    assert not probe._INFLIGHT
    monkeypatch.setattr(probe, '_run_query', lambda *args: {'ready': True})
    assert probe._query('asr', 'pytorch-whisper') == {'ready': True}
