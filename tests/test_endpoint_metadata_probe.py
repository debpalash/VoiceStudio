import importlib.util
from pathlib import Path
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('valid', [True, False])
def test_probe_requires_usable_hub_metadata(monkeypatch, valid):
    # Fresh module exercises the real transport wrapper; the suite replaces
    # the production module's probe with an offline fake for unrelated tests.
    spec = importlib.util.spec_from_file_location('metadata_probe_test', Path(__file__).resolve().parents[1] / 'backend/services/endpoint_race.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    import huggingface_hub
    calls = []
    def metadata(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(etag='abc' if valid else None, commit_hash='def' if valid else None, size=123 if valid else None)
    monkeypatch.setattr(huggingface_hub, 'get_hf_file_metadata', metadata)
    # Ensure the old root-only probe also stays offline while failing the test.
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: nullcontext())
    result = module.probe_endpoint(module.COMMUNITY_MIRROR)
    assert result.reachable is valid
    assert len(calls) == 1
    assert '/resolve/' in calls[0][0]
    assert calls[0][1]['token'] is False
