"""backend_module_state must survive an import that lands mid-scan (CI flake).

A thread importing while ``restore()`` iterated ``sys.modules`` raised
"dictionary changed size during iteration" and broke ~90 unrelated tests.
"""
import sys
import types

import backend_module_state as state


def _importing_filter(monkeypatch):
    original = state.is_backend_module
    added = []

    def is_backend_module(name):
        if not added:
            mod = types.ModuleType("services._concurrent_import_probe")
            sys.modules[mod.__name__] = mod
            added.append(mod.__name__)
        return original(name)

    monkeypatch.setattr(state, "is_backend_module", is_backend_module)
    return added


def test_snapshot_purge_restore_tolerate_a_concurrent_import(monkeypatch):
    before = {name: mod for name, mod in sys.modules.items() if state.is_backend_module(name)}
    try:
        for step in (state.snapshot, state.purge, lambda: state.restore(before)):
            added = _importing_filter(monkeypatch)
            step()
            monkeypatch.undo()
            for name in added:
                sys.modules.pop(name, None)
    finally:
        sys.modules.update(before)
