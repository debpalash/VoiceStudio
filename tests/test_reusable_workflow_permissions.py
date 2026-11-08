"""A called workflow's job cannot ask for more token access than its caller grants.

GitHub checks this when the run starts, even for jobs whose `if` would skip
them, and fails the whole run with `startup_failure`. The v0.5.8 tag build hit
it: `mobile-release.yml`'s release job asks for `contents: write`, while the
`mobile` job in `electron-release.yml` granted only `contents: read`. No
pull request runs the tag workflow, so only this test catches it before a tag.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
LEVEL = {'none': 0, 'read': 1, 'write': 2}
SCOPES = (
    'actions', 'attestations', 'checks', 'contents', 'deployments', 'discussions',
    'id-token', 'issues', 'models', 'packages', 'pages', 'pull-requests',
    'security-events', 'statuses',
)


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding='utf-8'))


def grants(permissions):
    """Scope -> level, or None when the token keeps the repository default."""
    if permissions is None:
        return None
    if isinstance(permissions, str):
        level = {'read-all': 1, 'write-all': 2}[permissions]
        return dict.fromkeys(SCOPES, level)
    granted = dict.fromkeys(SCOPES, 0)
    granted.update({scope: LEVEL[level] for scope, level in permissions.items()})
    return granted


def excess(caller, callee):
    if caller is None or callee is None:
        return {} if caller is None else {'(default)': 'needs explicit permissions'}
    return {s: lvl for s, lvl in callee.items() if lvl > caller.get(s, 0)}


def calls():
    for path in sorted(WORKFLOWS.glob('*.yml')):
        data = load(path.name)
        for job_id, job in (data.get('jobs') or {}).items():
            uses = job.get('uses', '')
            if uses.startswith('./.github/workflows/'):
                caller = grants(job.get('permissions', data.get('permissions')))
                yield path.name, job_id, uses.rsplit('/', 1)[1], caller


def violations(callee_name, caller, seen=()):
    called = load(callee_name)
    top = called.get('permissions')
    found = []
    for job_id, job in (called.get('jobs') or {}).items():
        wanted = grants(job.get('permissions', top))
        # A job without permissions of its own inherits the caller's grant.
        if wanted is None:
            wanted = caller
        over = excess(caller, wanted)
        if over:
            found.append(f'{callee_name}:{job_id} asks for {over}')
        uses = job.get('uses', '')
        nested = uses.rsplit('/', 1)[1] if uses.startswith('./.github/workflows/') else None
        if nested and nested not in seen:
            found += violations(nested, wanted, (*seen, callee_name))
    return found


CALLS = list(calls())


def test_every_local_workflow_call_is_checked():
    assert ('electron-release.yml', 'mobile', 'mobile-release.yml') in {c[:3] for c in CALLS}


@pytest.mark.parametrize('caller_file,job_id,callee,caller', CALLS, ids=[f'{c[0]}:{c[1]}' for c in CALLS])
def test_called_jobs_stay_within_the_callers_permissions(caller_file, job_id, callee, caller):
    assert violations(callee, caller) == [], f'{caller_file}:{job_id} calls {callee}'


def test_the_check_catches_the_v058_startup_failure():
    assert violations('mobile-release.yml', grants({'contents': 'read'}))
