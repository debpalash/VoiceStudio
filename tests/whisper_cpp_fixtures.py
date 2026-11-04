"""Resolve test seams after other suites isolate backend modules."""
import importlib
import sys
from types import SimpleNamespace

import pytest


_PACKAGE = 'engines.whisper_cpp'
_MISSING = object()


def _drop_engine_modules(engines):
    for name in list(sys.modules):
        if name == _PACKAGE or name.startswith(_PACKAGE + '.'):
            del sys.modules[name]
    if hasattr(engines, 'whisper_cpp'):
        delattr(engines, 'whisper_cpp')


@pytest.fixture
def m():
    """Reimport the whole CLI graph against the current ASR dependency objects.

    Backend module-purge suites can leave process.ASRTimeoutError and the
    backend's ASRBackend base pointing at old classes. Importing cached leaves
    alone cannot repair that split. Restore the previous graph on teardown so
    this fixture does not invalidate collection-time aliases in other suites.
    """
    engines = importlib.import_module('engines')
    before = {
        name: module for name, module in sys.modules.items()
        if name == _PACKAGE or name.startswith(_PACKAGE + '.')
    }
    binding = getattr(engines, 'whisper_cpp', _MISSING)
    _drop_engine_modules(engines)
    try:
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
        yield SimpleNamespace(**namespace)
    finally:
        _drop_engine_modules(engines)
        sys.modules.update(before)
        if binding is not _MISSING:
            engines.whisper_cpp = binding
