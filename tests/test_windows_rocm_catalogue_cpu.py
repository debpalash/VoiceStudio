"""Both catalogue entries must keep native CT2 imports out of the API parent."""
import builtins
import importlib

import pytest


@pytest.mark.parametrize('ready', [False, True])
@pytest.mark.parametrize('reject_gpu_probe', [False, True])
def test_cpu_catalogue_uses_child_proof_for_both_engine_ids(monkeypatch, ready, reject_gpu_probe):
    """Catalogue enumeration shares CPU admission without Torch/CT2 imports or GPU checks."""
    asr = importlib.import_module('services.asr_backend')
    isolated = importlib.import_module('services.subprocess_asr').IsolatedFasterWhisperBackend
    isolation = importlib.import_module('core.windows_rocm.asr_isolation')
    caps = importlib.import_module('core.device_caps')
    ct2 = importlib.import_module('core.windows_rocm.ct2')
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(isolation, 'isolation_required', lambda: True)
    monkeypatch.setattr(isolation.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(caps, 'requested_device_override', lambda: 'cpu')
    monkeypatch.setattr(caps, 'detect_host_caps', lambda: caps.HostCaps(family='cpu', available_families=('cpu',)))
    monkeypatch.setattr(ct2, 'availability_status', lambda: (
        pytest.fail('CPU queried GPU') if reject_gpu_probe else None))
    monkeypatch.setattr(ct2, 'gpu_compatible', lambda: ('cpu',))
    monkeypatch.setattr(asr, '_REGISTRY', {'faster-whisper': asr.FasterWhisperBackend,
                                         'faster-whisper-isolated': isolated})
    monkeypatch.setattr(asr, '_DEEP_IMPORT_BROKEN', {})
    monkeypatch.setattr(probe, '_CACHE', {})
    monkeypatch.setattr(probe, '_PACKAGES', ())
    queries = []

    def query(family, engine, broken):
        """Supply the real cache with a controlled child result."""
        queries.append((family, engine))
        return {'ready': ready, 'engine': engine}

    monkeypatch.setattr(probe, '_run_query', query)
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        """Reject native libraries in the API parent throughout catalogue enumeration."""
        if name.split('.')[0] in {'torch', 'faster_whisper', 'ctranslate2'}:
            pytest.fail('Catalogue imported a native library in the parent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    rows = asr.list_backends()
    assert {row['id']: row['available'] for row in rows} == {
        'faster-whisper': ready, 'faster-whisper-isolated': ready}
    assert queries == [('asr-import', 'faster-whisper')]


def test_isolated_cpu_admission_still_requires_sidecar(monkeypatch, tmp_path):
    """A successful native import cannot authorize an absent worker script."""
    cls = importlib.import_module('services.subprocess_asr').IsolatedFasterWhisperBackend
    isolation = importlib.import_module('core.windows_rocm.asr_isolation')
    monkeypatch.setattr(isolation, 'availability_status', lambda: (True, 'ready'))
    monkeypatch.setattr(cls, 'sidecar_script', classmethod(lambda cls: tmp_path / 'missing.py'))
    assert cls.is_available()[0] is False
