"""An explicit missing checkpoint must never select different cached weights."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def cached_model(tmp_path, monkeypatch, request):
    bootstrap = importlib.import_module('engines.whisper_cpp.bootstrap')
    package = tmp_path / 'package'
    home = tmp_path / 'home'
    cache = {'package': package, 'whisper': home / '.cache' / 'whisper',
             'hf': tmp_path / 'hf'}[request.param]
    cache.mkdir(parents=True)
    model = cache / 'ggml-base.bin'
    model.write_bytes(b'other cached weights')
    monkeypatch.setattr(bootstrap, '_PKG_MODELS_DIR', package)
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.delenv(bootstrap.MODEL_ENV, raising=False)
    monkeypatch.setattr('huggingface_hub.try_to_load_from_cache',
                        lambda _repo, filename: str(model) if filename == model.name else None)
    return bootstrap, model


@pytest.mark.parametrize('cached_model', ['package', 'whisper', 'hf'], indirect=True)
@pytest.mark.parametrize('via_env', [False, True])
@pytest.mark.parametrize('path_kind', ['absolute', 'relative', 'dot', 'dot_windows', 'parent',
                                       'windows', 'drive_relative', 'unc'])
def test_missing_explicit_model_does_not_fall_back(cached_model, tmp_path, monkeypatch,
                                                  via_env, path_kind):
    bootstrap, _model = cached_model
    target = {
        'absolute': str(tmp_path / 'missing' / 'ggml-base.bin'),
        'relative': 'missing/ggml-base.bin',
        'dot': './ggml-base.bin',
        'dot_windows': r'.\ggml-base.bin',
        'parent': '../ggml-base.bin',
        'windows': r'missing\ggml-base.bin',
        'drive_relative': 'Q:ggml-base.bin',
        'unc': r'\\unavailable-test-host\models\ggml-base.bin',
    }[path_kind]
    # Avoid contacting an actual UNC server, and isolate paths from the host.
    original_is_file = Path.is_file
    direct = Path(target)
    monkeypatch.setattr(Path, 'is_file', lambda path: False if path == direct
                        else original_is_file(path))
    if via_env:
        monkeypatch.setenv(bootstrap.MODEL_ENV, target)
        target = None
    assert bootstrap.resolve_model(target) is None
    assert bootstrap.resolve_model_target(target) is None


@pytest.mark.parametrize('cached_model', ['package', 'whisper', 'hf'], indirect=True)
@pytest.mark.parametrize('name', ['base', 'ggml-base.bin'])
def test_bare_model_name_still_searches_local_caches(cached_model, name):
    bootstrap, model = cached_model
    assert bootstrap.resolve_model(name) == model


@pytest.mark.parametrize('cached_model', ['package', 'whisper', 'hf'], indirect=True)
def test_missing_argument_takes_precedence_over_valid_environment_model(
    cached_model, tmp_path, monkeypatch,
):
    bootstrap, model = cached_model
    monkeypatch.setenv(bootstrap.MODEL_ENV, str(model))
    assert bootstrap.resolve_model(str(tmp_path / 'missing' / model.name)) is None
