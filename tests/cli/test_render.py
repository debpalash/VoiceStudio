"""Every result shape renders as readable text; --json alone prints raw data."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli.render import render, label, value_text

VOICES = [{'id': 'p1', 'name': 'Narrator', 'language': 'en', 'ref_text': 'x' * 90,
           'tags': ['warm'], 'meta': {'a': 1}, 'created_at': 1762240000},
          {'id': 'p2', 'name': 'Deep Male', 'language': 'de', 'ref_text': '', 'tags': [], 'meta': {}}]
SHAPES = {
    'text': 'VoiceStudio 0.5.9',
    'number': 3,
    'message': {'stopped': False, 'message': 'VoiceStudio is not running.'},
    'flat': {'url': 'http://127.0.0.1:3900', 'authenticated': False},
    'list of objects': VOICES,
    'wrapped list': {'models': [{'repo_id': 'a/b', 'installed': True, 'size_bytes': 2_400_000_000}], 'total': 1},
    'nested': {'version': '1', 'gpu': {'name': 'RTX', 'vram_bytes': 2 ** 33},
               'engines': [{'id': 'kitten', 'active': False}], 'paths': ['/a', '/b'],
               'deep': {'inner': {'value': [1, {'x': 2}]}}},
    'list of values': ['en', 'de'],
    'mixed list': [{'id': 1}, 'loose'],
    'empty list': [],
    'empty object': {},
    'ok': {'ok': True},
}


def test_no_shape_falls_back_to_json_in_a_terminal():
    for name, data in SHAPES.items():
        text = render(data, tty=True, width=80)
        assert text, name
        assert not text.lstrip().startswith(('{', '[')), (name, text)
        assert '": ' not in text, (name, text)


def test_lists_become_tables_led_by_identifying_columns_that_fit():
    text = render(VOICES, tty=True, width=60)
    header, first, second = text.splitlines()
    assert header.split()[:3] == ['ID', 'NAME', 'LANGUAGE']
    assert first.startswith('p1') and 'Narrator' in first
    assert all(len(line) <= 60 for line in text.splitlines())
    assert 'x' * 50 not in text


def test_wrapped_list_prints_its_table_then_its_summary():
    text = render(SHAPES['wrapped list'], tty=True, width=100)
    assert text.splitlines()[0].split() == ['REPO', 'ID', 'INSTALLED', 'SIZE']
    assert '2.2 GB' in text and text.rstrip().endswith('Total: 1')


def test_nested_objects_render_as_indented_sections():
    text = render(SHAPES['nested'], tty=True, width=100)
    assert 'GPU:\n  Name:' in text and '8.0 GB' in text
    assert 'Engines:\n  ID' in text and 'kitten' in text


def test_piped_tables_are_complete_tab_separated_rows():
    rows = [line.split('\t') for line in render(VOICES, tty=False).splitlines()]
    assert rows[0][:3] == ['p1', 'Narrator', 'en']
    assert 'x' * 90 in rows[0]
    assert '' in rows[1]  # empty stays empty for scripts
    assert '{"a":1}' in rows[0]  # nested stays machine-readable


def test_values_are_formatted_by_field_name():
    assert value_text('installed', True) == 'yes'
    assert value_text('size_bytes', 1536) == '1.5 KB'
    assert value_text('note', None) == '—'
    assert value_text('count', 1762240000) == '1762240000'  # only *_at fields are dates
    assert label('repo_id') == 'Repo ID' and label('disk_free_bytes') == 'Disk free'
    assert render({'ok': True}, tty=True) == 'Done.'


def test_json_flag_prints_the_raw_result(tmp_path, monkeypatch, capsys):
    from voicestudio_cli import __main__ as cli
    monkeypatch.setattr(cli, 'dispatch', lambda args: VOICES)
    monkeypatch.setenv('VOICESTUDIO_CONFIG', str(tmp_path / 'cli.json'))
    assert cli.main(['voices', 'list', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == VOICES
    assert cli.main(['voices', 'list']) == 0
    assert not capsys.readouterr().out.startswith('[')


def test_unit_suffixes_move_from_the_name_to_the_value():
    text = render({'disk_free_gb': 71.8, 'generate_timeout_s': 300, 'latency_ms': 12.5}, tty=True)
    assert 'Disk free:        71.8 GB' in text
    assert 'Generate timeout: 300 s' in text
    assert 'Latency:          12.5 ms' in text
