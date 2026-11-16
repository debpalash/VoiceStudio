"""Execute the Nix workflow's real scope step without GitHub, Git or Nix."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/nix.yml'


def _scope(tmp_path: Path, files: Any, *, event: str = 'pull_request',
           held: bool = False, api_error: bool = False) -> tuple[dict[str, str], str, list[list[str]]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    step = next(step for step in workflow['jobs']['flake']['steps'] if step.get('id') == 'scope')
    commands = tmp_path / 'bin'
    commands.mkdir()
    requests = tmp_path / 'requests.jsonl'
    fake = commands / 'gh'
    fake.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
with open(os.environ['FIXTURE_REQUESTS'], 'a', encoding='utf-8') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ['FIXTURE_API_ERROR'] == 'true':
    sys.exit(1)
sys.stdout.write(Path(os.environ['FIXTURE_PAYLOAD_FILE']).read_text(encoding='utf-8'))
''', encoding='utf-8')
    fake.chmod(0o755)
    git = commands / 'git'
    git.write_text('#!/bin/sh\necho "Git is forbidden in this scope fixture" >&2\nexit 91\n', encoding='utf-8')
    git.chmod(0o755)
    event_file = tmp_path / 'event.json'
    data = ({'number': 7} if event == 'pull_request' else
            {'before': 'a' * 40, 'after': 'b' * 40} if event == 'push' else
            {'merge_group': {'base_sha': 'a' * 40, 'head_sha': 'b' * 40}})
    event_file.write_text(json.dumps(data), encoding='utf-8')
    output = tmp_path / 'output'
    payload = [files] if event == 'pull_request' and isinstance(files, list) else files
    payload_file = tmp_path / 'payload.json'
    payload_file.write_text(json.dumps(payload), encoding='utf-8')
    env = dict(os.environ, PATH=f'{commands}{os.pathsep}{os.environ.get("PATH", "")}',
               EVENT=event, GITHUB_EVENT_NAME=event, GITHUB_REPOSITORY='owner/repo',
               GITHUB_EVENT_PATH=str(event_file), GITHUB_OUTPUT=str(output),
               GH_TOKEN='offline-fixture', CLA_HOLD='held' if held else 'clear',
               BASE_SHA='a' * 40, HEAD_SHA='b' * 40,
               FIXTURE_PAYLOAD_FILE=str(payload_file), FIXTURE_REQUESTS=str(requests),
               FIXTURE_API_ERROR=str(api_error).lower())
    shell = shutil.which('bash')
    assert shell, 'This workflow fixture requires Bash'
    result = subprocess.run([shell, '-e', '-c', step['run']], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split('=', 1) for line in output.read_text(encoding='utf-8').splitlines())
    calls = [json.loads(line) for line in requests.read_text(encoding='utf-8').splitlines()] if requests.exists() else []
    return values, result.stdout, calls


@pytest.mark.parametrize('event', ['pull_request', 'push', 'merge_group'])
@pytest.mark.parametrize('path', ['electron/tests/deb-scripts.test.mjs', 'electron/src/main/backend.test.ts',
                                  'docs/install/nix.md'])
def test_nix_scope_skips_actual_docs_and_test_only_changes(tmp_path: Path, event: str, path: str) -> None:
    values, _, calls = _scope(tmp_path, [{'status': 'modified', 'filename': path}], event=event)
    assert values['run'] == 'false'
    assert calls, 'A detected skip must inspect the event change list'


@pytest.mark.parametrize('path', ['electron/src/main/backend.ts', 'flake.nix', 'flake.lock', 'bun.lock',
                                  'config/diagnostics.json', 'docs/logo.svg', 'docs/logo.png',
                                  'docs/licensing/model-license-data.json', '.github/workflows/nix.yml',
                                  'electron/tests/ui-crawler.mjs'])
@pytest.mark.parametrize('event', ['pull_request', 'push', 'merge_group'])
def test_nix_scope_keeps_real_inputs_and_mixed_changes(tmp_path: Path, path: str, event: str) -> None:
    values, _, _ = _scope(tmp_path, [{'status': 'modified', 'filename': 'README.md'},
                                    {'status': 'modified', 'filename': path}], event=event)
    assert values['run'] == 'true'


@pytest.mark.parametrize('event', ['pull_request', 'push', 'merge_group'])
@pytest.mark.parametrize('files', [[], {}, [{'status': 'unknown', 'filename': 'README.md'}],
                                   [{'status': 'renamed', 'filename': 'docs/old.md'}]])
def test_nix_scope_unknown_evidence_runs_fully(tmp_path: Path, event: str, files: Any) -> None:
    values, _, _ = _scope(tmp_path, files, event=event)
    assert values['run'] == 'true'


@pytest.mark.parametrize('event,limit', [('pull_request', 3000), ('push', 300), ('merge_group', 300)])
def test_nix_scope_truncated_change_lists_run_fully(tmp_path: Path, event: str, limit: int) -> None:
    values, _, _ = _scope(tmp_path, [{'status': 'modified', 'filename': 'README.md'}] * limit, event=event)
    assert values['run'] == 'true'


@pytest.mark.parametrize('event', ['pull_request', 'push', 'merge_group'])
def test_nix_scope_failed_api_runs_fully(tmp_path: Path, event: str) -> None:
    values, _, _ = _scope(tmp_path, [], event=event, api_error=True)
    assert values['run'] == 'true'


@pytest.mark.parametrize('event', ['workflow_dispatch', 'release'])
def test_nix_scope_manual_and_release_always_run(tmp_path: Path, event: str) -> None:
    values, _, calls = _scope(tmp_path, [], event=event)
    assert values['run'] == 'true'
    assert calls == []


def test_nix_scope_keeps_existing_cla_hold(tmp_path: Path) -> None:
    values, _, calls = _scope(tmp_path, [{'status': 'modified', 'filename': 'flake.nix'}], held=True)
    assert values['run'] == 'false'
    assert calls == []


@pytest.mark.parametrize('path', ['mobile/android/app/src/main/Example.kt', '.github/workflows/docs-drift.yml'])
def test_nix_merge_group_keeps_package_exclusions(tmp_path: Path, path: str) -> None:
    values, _, _ = _scope(tmp_path, [{'status': 'modified', 'filename': path}], event='merge_group')
    assert values['run'] == 'false'


def test_nix_trigger_includes_bundled_docs_inputs_and_read_permission() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    triggers = workflow.get('on', workflow.get(True))
    for event in ('pull_request', 'push'):
        for path in ('docs/logo.svg', 'docs/logo.png', 'docs/licensing/model-license-data.json'):
            assert path in triggers[event]['paths']
    assert workflow['permissions']['pull-requests'] == 'read'
