"""CPU capture must prefer a usable recognizer over a merely present package."""
import builtins
import importlib

import pytest


@pytest.fixture
def cpu_capture(monkeypatch):
    """Provide isolated ROCm CPU routing without importing native model runtimes."""
    asr = importlib.import_module('services.asr_backend')
    isolation = importlib.import_module('core.windows_rocm.asr_isolation')
    caps = importlib.import_module('core.device_caps')
    ct2 = importlib.import_module('core.windows_rocm.ct2')
    probe = importlib.import_module('services.performance_probe')
    monkeypatch.setattr(isolation, 'isolation_required', lambda: True)
    original = isolation.util.find_spec
    monkeypatch.setattr(isolation.util, 'find_spec', lambda name: (
        object() if name == 'faster_whisper' else original(name)))
    monkeypatch.setattr(caps, 'requested_device_override', lambda: 'cpu')
    monkeypatch.setattr(ct2, 'availability_status', lambda: pytest.fail('CPU checked GPU'))
    monkeypatch.setattr(asr, '_capture_backend', None)
    monkeypatch.setattr(asr, '_capture_backend_key', None)
    monkeypatch.setattr(asr, '_touch_capture', lambda: None)
    monkeypatch.setattr(asr, '_capture_prefers_parakeet', lambda: False)
    monkeypatch.setattr(asr.MLXWhisperBackend, 'is_available', classmethod(
        lambda cls: (False, 'not installed')))
    monkeypatch.setattr(asr.PyTorchWhisperBackend, 'is_available', classmethod(
        lambda cls: (True, 'ready')))
    monkeypatch.setattr(asr, 'ensure_backend_licence', lambda backend: None)
    return asr, isolation, probe


@pytest.mark.parametrize('ready', [False, True])
def test_capture_checks_native_import_before_caching_engine(cpu_capture, monkeypatch, ready):
    """Retain PyTorch fallback on broken CT2 and reuse a proven capture selection."""
    asr, _isolation, probe = cpu_capture
    calls = []

    def query(family, engine):
        """Simulate the bounded child's identity-matched readiness result."""
        calls.append((family, engine))
        return {'ready': ready, 'engine': engine}

    monkeypatch.setattr(probe, '_query', query)
    engine = asr.get_capture_asr_backend(skip_sherpa=True)
    expected = asr.FasterWhisperBackend if ready else asr.PyTorchWhisperBackend
    assert isinstance(engine, expected)
    assert asr.get_capture_asr_backend(skip_sherpa=True) is engine
    assert calls == [('asr-import', 'faster-whisper')]


@pytest.mark.parametrize('result', [None, {'ready': False, 'engine': 'faster-whisper'}])
def test_failed_or_timed_out_child_does_not_admit_capture(cpu_capture, monkeypatch, result):
    """An absent or negative child proof must not select the CT2 capture backend."""
    asr, isolation, probe = cpu_capture
    monkeypatch.setattr(probe, '_query', lambda *args: result)
    assert not isolation.availability_status()[0]
    assert isinstance(asr.get_capture_asr_backend(skip_sherpa=True), asr.PyTorchWhisperBackend)


def test_cpu_availability_does_not_import_native_libraries_in_parent(cpu_capture, monkeypatch):
    """Keep native imports in the child and avoid GPU discovery in CPU mode."""
    _asr, isolation, probe = cpu_capture
    monkeypatch.setattr(probe, '_query', lambda family, engine: {'ready': True, 'engine': engine})
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        """Reject an attempted native import in this parent process."""
        if name.split('.')[0] in {'torch', 'faster_whisper', 'ctranslate2'}:
            pytest.fail('native runtime imported in parent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    assert isolation.availability_status()[0]


@pytest.mark.parametrize('verified', [False, True])
def test_import_success_does_not_bypass_gpu_verification(cpu_capture, monkeypatch, verified):
    """A proven import must still retain the existing native HIP admission result."""
    asr, _isolation, probe = cpu_capture
    caps = importlib.import_module('core.device_caps')
    ct2 = importlib.import_module('core.windows_rocm.ct2')
    monkeypatch.setattr(caps, 'requested_device_override', lambda: 'auto')
    monkeypatch.setattr(probe, '_query', lambda family, engine: {'ready': True, 'engine': engine})
    monkeypatch.setattr(ct2, 'availability_status', lambda: (verified, 'native GPU result'))
    assert asr.FasterWhisperBackend.is_available() == (verified, 'native GPU result')
