"""Runtime recognizer readiness must prove imports while inventory stays shallow."""
import importlib

import pytest


@pytest.mark.parametrize('failure', [None, 'import', 'lazy', 'missing'])
def test_real_transformers_child_controls_runtime_availability(monkeypatch, tmp_path, failure):
    """Reject an installed package with a broken lazy pipeline import in a child."""
    asr = importlib.import_module('services.asr_backend')
    probe = importlib.import_module('services.performance_probe')
    readiness = importlib.import_module('services.performance_readiness')
    package = tmp_path / 'transformers'
    package.mkdir()
    source = {
        None: 'def pipeline(*args, **kwargs): raise AssertionError("model loaded")\n',
        'import': 'raise ImportError("missing transitive audio helper")\n',
        'lazy': 'def __getattr__(name): raise RuntimeError("broken lazy pipeline")\n',
        'missing': 'pipeline = None\n',
    }[failure]
    (package / '__init__.py').write_text(source, encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setattr(probe, '_CACHE', {})
    monkeypatch.setattr(readiness, 'package_available', lambda name: True)
    ready, detail = asr.PyTorchWhisperBackend.is_available()
    assert ready == (failure is None)
    assert detail == 'ready' if failure is None else 'import' in detail


def test_inventory_availability_never_launches_runtime_probe(monkeypatch):
    """Inventory may expose installed choices without starting an import child."""
    asr = importlib.import_module('services.asr_backend')
    readiness = importlib.import_module('services.performance_readiness')
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(readiness, 'package_available', lambda name: True)
    monkeypatch.setattr(probe, '_query', lambda *args: pytest.fail('inventory started child'))
    assert readiness.inventory_only(asr.PyTorchWhisperBackend.is_available)() == (True, 'ready')

@pytest.mark.parametrize('requested', ['auto', 'pytorch-whisper'])
@pytest.mark.parametrize('usable', [False, True])
def test_runtime_worker_checks_pipeline_without_recursive_child(monkeypatch, requested, usable):
    """An ASR worker validates PyTorch locally instead of recursively probing itself."""
    import sys
    from types import SimpleNamespace

    worker = importlib.import_module('services._performance_probe')
    asr = importlib.import_module('services.asr_backend')
    runtime = importlib.import_module('core.windows_rocm.runtime')
    readiness = importlib.import_module('services.performance_readiness')
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(runtime, 'initialize_dll_search', lambda: None)
    monkeypatch.setattr(readiness, 'package_available', lambda name: True)
    monkeypatch.setattr(asr, '_DEEP_IMPORT_BROKEN', {})
    monkeypatch.setattr(asr, '_auto_detect', lambda: 'pytorch-whisper')
    monkeypatch.setattr(probe, '_query', lambda *args: pytest.fail('recursive import child'))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        pipeline=(lambda *args: pytest.fail('model loaded')) if usable else None))
    assert worker.check('asr', requested) == ('pytorch-whisper', usable)
