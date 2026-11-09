"""Readiness must keep CT2 out of a worker that already inspected Torch."""
import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('engine', ['faster-whisper', 'faster-whisper-isolated'])
@pytest.mark.parametrize('requested_auto', [False, True])
@pytest.mark.parametrize('ready', [False, True])
def test_rocm_worker_uses_separate_import_proof(monkeypatch, engine, requested_auto, ready):
    """Reuse CT2 import proof rather than mixing OpenMP runtimes after detection."""
    worker = importlib.import_module('services._performance_probe')
    probe = importlib.import_module('services.performance_probe')
    asr = importlib.import_module('services.asr_backend')
    policy = importlib.import_module('core.windows_rocm.isolation_policy')
    runtime = importlib.import_module('core.windows_rocm.runtime')
    cls = SimpleNamespace(id=engine)
    monkeypatch.setattr(asr, '_REGISTRY', {engine: cls})
    monkeypatch.setattr(asr, '_DEEP_IMPORT_BROKEN', {})
    monkeypatch.setattr(asr, '_probe_available', lambda cls: True)
    monkeypatch.setattr(asr, '_auto_detect', lambda: engine)
    monkeypatch.setattr(runtime, 'initialize_dll_search', lambda: None)
    monkeypatch.setattr(policy, 'isolation_required', lambda: True)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace())
    monkeypatch.setattr(probe, 'isolated_asr_import_ready', lambda: ready)
    original = importlib.import_module

    def guarded(name, *args):
        """Reject a CT2 runtime import in this hardware-inspecting process."""
        if name in {'faster_whisper', 'ctranslate2'}:
            pytest.fail('CT2 imported alongside Torch in readiness worker')
        return original(name, *args)

    monkeypatch.setattr(importlib, 'import_module', guarded)
    assert worker.check('asr', 'auto' if requested_auto else engine) == (engine, ready)
