"""Invalid advisory configuration must not disable memory probes."""
import importlib.util
from pathlib import Path

import pytest


def _load_budget():
    path = Path(__file__).parents[1] / 'backend/services/memory_budget.py'
    spec = importlib.util.spec_from_file_location('owned_memory_budget', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('value', ['oops', '', 'nan', 'inf', '-inf', '-1', '1e400'])
def test_invalid_headroom_env_falls_back_to_default_advisory(monkeypatch, value):
    monkeypatch.setenv('OMNIVOICE_LOW_MEMORY_HEADROOM_GB', value)
    budget = _load_budget()
    monkeypatch.setattr(budget, 'available_memory', lambda: {'ram_available_gb': 1.0})
    assert budget._LOW_RAM_HEADROOM_GB == 2.0
    assert budget.low_memory_warning() is not None


@pytest.mark.parametrize('value,expected', [('0', 0.0), ('1.5', 1.5), ('4', 4.0)])
def test_valid_headroom_env_remains_authoritative(monkeypatch, value, expected):
    monkeypatch.setenv('OMNIVOICE_LOW_MEMORY_HEADROOM_GB', value)
    budget = _load_budget()
    assert budget._LOW_RAM_HEADROOM_GB == expected
