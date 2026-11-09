"""Resolve test seams after other suites isolate backend modules."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def m(neutral_asr_host):
    """Backend seams on a CPU-only host: a test opts in to its hardware (#3549)."""
    bootstrap = importlib.import_module('engines.whisper_cpp.bootstrap')
    backend = importlib.import_module('engines.whisper_cpp.backend')
    namespace = {
        'wc': backend,
        'WhisperCppBackend': backend.WhisperCppBackend,
        'ab': importlib.import_module('services.asr_backend'),
        'HostCaps': importlib.import_module('core.device_caps').HostCaps,
    }
    for name in ('BACKEND_ENV', 'BIN_ENV', 'DEVICE_ENV', 'DIR_ENV', 'MODEL_ENV',
                 'is_installed', 'locate_binary', 'resolve_model'):
        namespace[name] = getattr(bootstrap, name)
    return SimpleNamespace(**namespace)
