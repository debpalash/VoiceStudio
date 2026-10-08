"""Language metadata must use the same declarations as synthesis guards."""
import pytest
@pytest.fixture
def tts():
    from services import tts_backend
    return tts_backend

@pytest.mark.parametrize('engine,allowed,rejected', [
    ('kittentts', 'english', 'polish'),
    ('audiocpp', 'chinese', 'polish'),
    ('indextts2', 'spanish', 'polish'),
    ('confucius4-tts', 'french', 'polish'),
])
def test_finite_language_options_without_loading_models(engine, allowed, rejected, monkeypatch, tts):
    monkeypatch.delenv('OMNIVOICE_INDEXTTS_DIR', raising=False)
    options = tts.language_options(engine)
    assert allowed in options
    assert rejected not in options


def test_open_ended_engine_keeps_all_language_options(tts):
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    assert tts.language_options('omnivoice') == sorted(LANG_NAME_TO_ID)


def test_installed_kokoro_tables_are_read_without_importing_model(tmp_path, monkeypatch, tts):
    import importlib.metadata
    from types import SimpleNamespace
    table = tmp_path / 'pipeline.py'
    table.write_text("raise RuntimeError('must not import')\nALIASES = {'en': 'a', 'ja': 'j'}\nLANG_CODES = {'a': 'English', 'j': 'Japanese'}\n")
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda _: SimpleNamespace(locate_file=lambda _: table))
    assert tts._installed_kokoro_language_options() == ['english', 'japanese']


def test_kokoro_tables_declared_as_dict_calls_are_read(tmp_path, monkeypatch, tts):
    """mlx-audio writes ``LANG_CODES = dict(a=...)``; literal-only parsing opened every language."""
    import importlib.metadata
    from types import SimpleNamespace
    table = tmp_path / 'pipeline.py'
    table.write_text("ALIASES = {'en': 'a', 'hi': 'h'}\nLANG_CODES = dict(\n    # comment\n    a='American English',\n    h='hi',\n)\n")
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda _: SimpleNamespace(locate_file=lambda _: table))
    assert tts._installed_kokoro_language_options() == ['english', 'hindi']


def test_unreadable_kokoro_metadata_uses_declared_set_not_all_languages(tmp_path, monkeypatch, tts):
    import importlib.metadata
    from types import SimpleNamespace
    table = tmp_path / 'pipeline.py'
    table.write_text("ALIASES = get_aliases()\n")
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda _: SimpleNamespace(locate_file=lambda _: table))
    options = tts._installed_kokoro_language_options()
    assert options == ['chinese', 'english', 'french', 'hindi', 'italian', 'japanese',
                       'portuguese', 'spanish']
    assert 'bengali' not in options


def test_installed_mlx_audio_kokoro_tables_are_readable(tts):
    """Guards the real vendored layout, not a hand-written fixture of it."""
    import ast
    import importlib.metadata
    try:
        distribution = importlib.metadata.distribution('mlx-audio')
    except importlib.metadata.PackageNotFoundError:
        pytest.skip('mlx-audio is only installed on Apple Silicon')
    path = distribution.locate_file('mlx_audio/tts/models/kokoro/pipeline.py')
    tables = {
        target.id: tts._literal_table(node.value)
        for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {'ALIASES', 'LANG_CODES'}
    }
    assert set(tables) == {'ALIASES', 'LANG_CODES'}
    options = tts._installed_kokoro_language_options()
    assert 'english' in options and 'bengali' not in options


def test_active_kokoro_inventory_limits_picker_languages(monkeypatch, tts):
    from api.routers import engines
    monkeypatch.setattr(tts, 'active_backend_id', lambda: 'mlx-audio')
    monkeypatch.setattr(tts, 'list_backends', lambda: [{'id': 'mlx-audio', 'available': True}])
    monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', 'kokoro')
    names = engines._family_payload('tts', tts)['backends'][0]['supported_language_names']
    assert 'english' in names and 'bengali' not in names


def test_unknown_engine_does_not_break_inventory(tts):
    assert tts.language_options('third-party-unknown') is None


def test_renderer_code_catalog_matches_backend_vocabulary():
    import json
    from pathlib import Path
    from omnivoice.utils.lang_map import LANG_NAME_TO_ID
    path = Path(__file__).resolve().parents[1] / 'electron/src/shared/language-codes.json'
    assert json.loads(path.read_text(encoding='utf-8')) == LANG_NAME_TO_ID


def test_flag_search_regions_match_the_rendered_flags():
    import json
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'electron/src/shared'
    source = (root / 'components/LanguageFlag.jsx').read_text(encoding='utf-8')
    body = source.split('export const LANGUAGE_FLAGS = {')[1].split('};')[0]
    expected = {key.strip("'\""): region.replace('_', '-')
                for key, region in re.findall(r"\s+([\w'\"-]+): ([A-Z_]+),", body)}
    assert json.loads((root / 'language-regions.json').read_text(encoding='utf-8')) == expected


def test_active_engine_inventory_carries_language_choices(monkeypatch, tts):
    from api.routers import engines
    monkeypatch.setattr(tts, 'active_backend_id', lambda: 'kittentts')
    monkeypatch.setattr(tts, 'list_backends', lambda: [
        {'id': 'kittentts', 'available': True},
        {'id': 'omnivoice', 'available': False},
    ])
    response = engines._family_payload('tts', tts)
    assert 'english' in response['backends'][0]['supported_language_names']
    assert 'polish' not in response['backends'][0]['supported_language_names']
    assert 'supported_language_names' not in response['backends'][1]


@pytest.mark.parametrize('engine', ['indextts2', 'omnivoice-subprocess'])
def test_metadata_releases_temporary_sidecar_exit_handlers(monkeypatch, engine, tts):
    import atexit
    callbacks = []
    monkeypatch.setattr(atexit, 'register', lambda callback: callbacks.append(callback))
    monkeypatch.setattr(atexit, 'unregister', lambda callback: callbacks.remove(callback))
    for _ in range(3):
        tts.language_options(engine)
    assert callbacks == []


@pytest.mark.parametrize('model,allowed,rejected', [
    ('csm', 'english', 'french'),
    ('dia', 'english', 'german'),
    ('chatterbox', 'english', 'spanish'),
    ('melotts', 'english', 'japanese'),
    ('qwen3-tts', 'korean', 'bengali'),
    ('outetts', 'bengali', 'hindi'),
])
def test_curated_mlx_models_declare_documented_languages(model, allowed, rejected, monkeypatch, tts):
    monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', model)
    options = tts.language_options('mlx-audio')
    assert allowed in options and rejected not in options
    backend = tts.MLXAudioBackend()
    assert backend._check_language(allowed) is not None
    with pytest.raises(ValueError, match='support language'):
        backend._check_language(rejected)


def test_every_curated_mlx_model_has_a_language_declaration(tts):
    assert set(tts.MLXAudioBackend.CURATED_MODEL_LANGUAGES) == set(tts.MLXAudioBackend.CURATED_MODELS)


def test_custom_mlx_repo_stays_open_ended(monkeypatch, tts):
    monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', 'someone/custom-tts')
    assert tts.language_options('mlx-audio') is None
    assert tts.MLXAudioBackend()._check_language('bengali') is None


def test_kokoro_guard_uses_installed_labels(monkeypatch, tts):
    monkeypatch.setenv('OMNIVOICE_MLX_AUDIO_MODEL', 'kokoro')
    calls = []
    monkeypatch.setattr(tts, 'resolve_kokoro_lang_code', lambda language: calls.append(language) or 'b')
    backend = tts.MLXAudioBackend()
    assert backend._check_language('British English') is None
    assert backend._check_language('Auto') is None
    assert calls == ['British English']
