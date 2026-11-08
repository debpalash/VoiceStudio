"""Interpreter metadata must follow bootstrap selection, without executing probes."""
import importlib
from pathlib import Path
from types import SimpleNamespace


def test_unresolved_candidates_do_not_establish_the_selected_interpreter(tmp_path):
    """An existing candidate may fail imports and be skipped by the resolver."""
    module = importlib.import_module('services.subprocess_backend')
    candidate = tmp_path / 'python'
    candidate.touch()
    bootstrap = SimpleNamespace(_resolved_python=None, _probe_paths=lambda: [candidate])
    assert module.bootstrap_python_hint(bootstrap) is None


def test_resolved_interpreter_is_reused_without_probing():
    """Metadata reads the resolver's decision without repeating import probes."""
    module = importlib.import_module('services.subprocess_backend')
    def forbidden():
        raise AssertionError('candidate probing is forbidden')
    bootstrap = SimpleNamespace(_resolved_python=Path('selected/python'), _probe_paths=forbidden)
    assert module.bootstrap_python_hint(bootstrap) == Path('selected/python')
