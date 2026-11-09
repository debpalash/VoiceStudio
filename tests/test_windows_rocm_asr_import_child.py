"""Import-only readiness has no registry recursion, model load or GPU probe."""
import importlib
from types import SimpleNamespace

import pytest


def test_import_only_worker_prepares_dlls_without_calling_backend_availability(monkeypatch):
    """Prove the direct child mode cannot recursively launch an availability probe."""
    worker = importlib.import_module('services._performance_probe')
    runtime = importlib.import_module('core.windows_rocm.runtime')
    asr = importlib.import_module('services.asr_backend')
    initialized = []
    monkeypatch.setattr(runtime, 'initialize_dll_search', lambda: initialized.append(True))
    monkeypatch.setattr(asr, '_probe_available', lambda *args: pytest.fail('recursive probe'))

    def imported(name):
        """Expose a constructor only after the native DLL paths are registered."""
        assert initialized and name == 'faster_whisper'
        return SimpleNamespace(WhisperModel=lambda *args: pytest.fail('model loaded'))

    monkeypatch.setattr(importlib, 'import_module', imported)
    assert worker.check('asr-import', 'faster-whisper') == ('faster-whisper', True)


@pytest.mark.parametrize('broken', [False, True])
def test_real_child_import_result_preserves_parent_isolation(monkeypatch, tmp_path, broken):
    """Transport an actual child's import success/failure without importing its package here."""
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(probe, '_CACHE', {})
    package = tmp_path / 'faster_whisper'
    package.mkdir()
    code = (
        'import os, sys\n'
        'assert os.environ["HF_HUB_OFFLINE"] == "1"\n'
        'assert "torch" not in sys.modules\n'
        + ('raise ImportError("simulated missing native DLL")\n' if broken else
           'class WhisperModel:\n    def __init__(self): raise AssertionError("model loaded")\n')
    )
    (package / '__init__.py').write_text(code)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    result = probe._query('asr-import', 'faster-whisper')
    assert result and result['ready'] is not broken
    assert result['family'] == 'asr-import' and result['engine'] == 'faster-whisper'
