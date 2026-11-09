"""Tab completion: candidates from the command index, real shell scripts."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli import completion
from voicestudio_cli.__main__ import RENAMED, build_parser


def names(words):
    return [line.split('\t')[0] for line in completion.complete(words)]


def test_top_level_groups_and_commands_with_descriptions():
    lines = completion.complete([''])
    assert 'voices\tYour saved voices: create, change, share' in lines
    assert {'speak', 'status', 'completion'} <= set(names(['']))


def test_completes_prefixes_nested_paths_choices_and_options():
    assert names(['vo']) == ['voices']
    assert 'reference' in names(['voices', 're'])
    assert names(['voices', 'reference', '']) == ['download', 'set']
    assert names(['settings', 'get', 'compute']) == ['compute-device']
    assert names(['transcribe', 'a.wav', '--format', 's']) == ['srt']
    assert '--audio' in names(['voices', 'clone', '--a'])
    assert names(['--url', 'http://127.0.0.1:1', 'voices', 'sh']) == ['show', 'show-file']


def test_former_names_and_hidden_options_are_never_offered():
    offered = set()
    for path in {tuple(name.split()[:-1]) for name in RENAMED}:
        offered |= {' '.join([*path, name]) for name in names([*path, ''])}
    assert not offered & set(RENAMED)
    assert names(['stories', '']) == []
    assert names(['update', '']) == []  # update check is a former spelling


def test_values_the_user_types_fall_back_to_files():
    assert names(['--url', '']) == []
    assert names(['voices', 'clone', '--audio', '']) == []


def test_every_listed_command_is_a_real_command():
    _, nodes = build_parser()
    commands, groups = completion._index()
    for path in [*commands, *groups]:
        assert tuple(path.split()) in nodes, path


def test_completion_builds_only_the_command_being_typed(monkeypatch):
    built = []
    from voicestudio_cli import __main__ as cli
    original = cli.build_parser
    monkeypatch.setattr(cli, 'build_parser', lambda only=None: built.append(only) or original(only))
    names(['voices', ''])
    assert built == []  # command names come from the index
    names(['voices', 'clone', '--'])
    assert built == [('voices', 'clone')]


def test_a_tab_press_is_fast():
    started = time.perf_counter()
    for words in [[''], ['voices', 'clone', '--'], ['settings', 'get', '']]:
        completion.complete(words)
    assert time.perf_counter() - started < 1.5


def run_shell(shell, script, env):
    return subprocess.run([shell, '-c', script], capture_output=True, text=True, timeout=30, env=env)


@pytest.fixture
def shell_env(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    if os.name == 'nt':
        # Windows runs commands by extension; an extensionless script is opened, not run.
        (bin_dir / 'voicestudio.cmd').write_text(f'@"{sys.executable}" -m voicestudio_cli %*\r\n', encoding='utf-8')
    else:
        launcher = bin_dir / 'voicestudio'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m voicestudio_cli "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    return {**os.environ, 'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
            'PYTHONPATH': str(ROOT / 'cli'), 'HOME': str(tmp_path)}


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='bash is not installed')
def test_bash_script_completes_in_a_real_bash(shell_env):
    result = run_shell('bash', 'eval "$(voicestudio completion bash)"; COMP_WORDS=(voicestudio voices re); '
                               'COMP_CWORD=2; _voicestudio; printf "%s\\n" "${COMPREPLY[@]}"', shell_env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['reference']


@pytest.mark.skipif(not shutil.which('zsh'), reason='zsh is not installed')
def test_zsh_script_loads_and_registers(shell_env):
    result = run_shell('zsh', 'eval "$(voicestudio completion zsh)"; print -r -- ${_comps[voicestudio]}', shell_env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '_voicestudio'


@pytest.mark.skipif(not shutil.which('fish'), reason='fish is not installed')
def test_fish_script_completes_in_a_real_fish(shell_env):
    result = run_shell('fish', 'voicestudio completion fish | source; complete -C "voicestudio voices re"', shell_env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('reference')


@pytest.mark.skipif(not shutil.which('pwsh'), reason='PowerShell is not installed')
def test_powershell_script_registers(shell_env):
    result = subprocess.run(['pwsh', '-NoProfile', '-Command',
                             'voicestudio completion powershell | Out-String | Invoke-Expression; '
                             '(TabExpansion2 "voicestudio voices re" 21).CompletionMatches.CompletionText'],
                            capture_output=True, text=True, timeout=60, env=shell_env)
    assert result.returncode == 0, result.stderr
    assert 'reference' in result.stdout.split()
