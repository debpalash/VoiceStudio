"""Trusted identity orchestration reads PR git objects without executing its policy."""
import copy
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('identity_gate', ROOT / '.github/scripts/identity_check.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class GitHub:
    repo = 'owner/repo'

    def __init__(self, pr):
        self.pr = pr
        self.statuses = []
        self.reads = 0
        self.open_prs = None

    def paginate(self, path, *, limit):
        assert path == f"/repos/{self.repo}/pulls?state=open"
        return copy.deepcopy(self.open_prs if self.open_prs is not None else [self.pr])

    def get(self, path):
        self.reads += 1
        return copy.deepcopy(self.pr)

    def request(self, method, path, body):
        assert method == 'POST' and '/statuses/' in path
        self.statuses.append(body)
        return 201, {}, {}


@pytest.fixture
def repository(tmp_path):
    remote, work, runner = (tmp_path / name for name in ('origin.git', 'work', 'runner'))
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(work)], check=True)

    def git(*args, cwd=work):
        return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()

    git('config', 'user.name', 'Human Developer')
    git('config', 'user.email', '1001+human@users.noreply.github.com')
    for name in ('scripts/check_commit_identities.py', 'scripts/blocked_identity_hashes.txt',
                 '.github/scripts/agent_identities.py'):
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    git('add', '.')
    git('commit', '-qm', 'base policy')
    base = git('rev-parse', 'HEAD')
    git('remote', 'add', 'origin', str(remote))
    git('push', '-q', 'origin', 'HEAD:refs/heads/main')
    subprocess.run(['git', 'clone', '-q', '-b', 'main', str(remote), str(runner)], check=True)

    def head(*, malicious=False):
        if malicious:
            (work / 'scripts/check_commit_identities.py').write_text(
                'from pathlib import Path\nPath("PR-CODE-RAN").touch()\nraise SystemExit(0)\n')
            workflow = work / '.github/workflows/commit-identity.yml'
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text('on: pull_request\njobs:\n  forged:\n    name: Commit identities\n    steps:\n      - run: true\n')
        (work / 'change.txt').write_text('contribution')
        git('add', '.')
        git('commit', '-qm', 'Generated with Claude' if malicious else 'add contribution')
        sha = git('rev-parse', 'HEAD')
        git('push', '-q', 'origin', 'HEAD:refs/pull/7/head')
        pr = {'number': 7, 'state': 'open', 'head': {'sha': sha}, 'base': {'sha': base}, 'body': ''}
        return GitHub(pr), {'pull_request': copy.deepcopy(pr)}, runner
    return head


def test_pr_cannot_replace_the_checker_or_trusted_workflow(repository):
    gh, event, runner = repository(malicious=True)
    assert gate.run(gh, event, runner) is False
    assert gh.statuses[-1]['state'] == 'failure'
    assert gh.statuses[-1]['context'] == 'Commit identity policy'
    assert not (runner / 'PR-CODE-RAN').exists()
    assert 'PR-CODE-RAN' not in (runner / 'scripts/check_commit_identities.py').read_text()
    assert not (runner / '.github/workflows/commit-identity.yml').exists()


def test_clean_contribution_gets_privileged_status(repository):
    gh, event, runner = repository()
    assert gate.run(gh, event, runner) is True
    assert [status['state'] for status in gh.statuses] == ['pending', 'success']


def test_missing_base_policy_has_no_pr_owned_fallback(repository):
    gh, event, runner = repository(malicious=True)
    (runner / 'scripts/check_commit_identities.py').unlink()
    with pytest.raises(RuntimeError, match='could not be evaluated'):
        gate.run(gh, event, runner)
    assert gh.statuses[-1]['state'] == 'pending'
    assert not (runner / 'PR-CODE-RAN').exists()


def test_head_change_during_fetch_is_not_approved(repository):
    gh, event, runner = repository()
    gh.pr['head']['sha'] = 'f' * 40
    with pytest.raises(RuntimeError, match='head changed'):
        gate.run(gh, event, runner)
    assert gh.statuses[-1]['state'] == 'pending'


def test_description_edit_during_check_is_not_approved(repository, monkeypatch):
    gh, event, runner = repository()
    original = gh.get
    def changed(path):
        result = original(path)
        if gh.reads == 2:
            result['body'] = 'Generated with Claude'
        return result
    monkeypatch.setattr(gh, 'get', changed)
    with pytest.raises(RuntimeError, match='metadata changed'):
        gate.run(gh, event, runner)
    assert gh.statuses[-1]['state'] == 'pending'


@pytest.mark.parametrize('state', ['pending', 'success'])
def test_rejected_status_write_fails_native_job(repository, monkeypatch, state):
    gh, event, runner = repository()
    original = gh.request
    def reject(method, path, body):
        return (403, {}, {}) if body['state'] == state else original(method, path, body)
    monkeypatch.setattr(gh, 'request', reject)
    with pytest.raises(RuntimeError, match='Could not write Commit identity policy status'):
        gate.run(gh, event, runner)
    assert not any(status['state'] == 'success' for status in gh.statuses)


def test_old_base_checkout_cannot_approve_with_obsolete_policy(repository):
    gh, event, runner = repository()
    gh.pr['base']['sha'] = 'e' * 40
    with pytest.raises(RuntimeError, match='base changed'):
        gate.run(gh, event, runner)
    assert gh.statuses[-1]['state'] == 'pending'


@pytest.mark.parametrize('late', [False, True])
def test_duplicate_heads_cannot_publish_reusable_identity_success(repository, monkeypatch, late):
    gh, event, runner = repository()
    peer = copy.deepcopy(gh.pr)
    peer.update(number=8, body='Generated with Claude')
    if late:
        original = gh.get
        def changed(path):
            result = original(path)
            if gh.reads == 2:
                gh.open_prs = [gh.pr, peer]
            return result
        monkeypatch.setattr(gh, 'get', changed)
    else:
        gh.open_prs = [gh.pr, peer]
    assert gate.run(gh, event, runner) is False
    assert gh.statuses[-1]['state'] == 'failure'
    assert not any(status['state'] == 'success' for status in gh.statuses)
    # Removing the sibling requires a fresh trusted evaluation to restore success.
    gh.open_prs = [gh.pr]
    assert gate.run(gh, event, runner) is True
    assert gh.statuses[-1]['state'] == 'success'


def test_identity_duplicate_enumeration_error_fails_closed(repository, monkeypatch):
    gh, event, runner = repository()
    def unavailable(*args, **kwargs):
        raise RuntimeError('GitHub unavailable')
    monkeypatch.setattr(gh, 'paginate', unavailable)
    with pytest.raises(RuntimeError, match='GitHub unavailable'):
        gate.run(gh, event, runner)
    assert gh.statuses[-1]['state'] == 'pending'


def test_rerun_uses_current_description_instead_of_old_event(repository):
    gh, event, runner = repository()
    gh.pr['body'] = 'Generated with Claude'
    assert event['pull_request']['body'] == ''
    assert gate.run(gh, event, runner) is False
    assert gh.statuses[-1]['state'] == 'failure'
