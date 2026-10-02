"""Commit-identity CI gate: scripts/check_commit_identities.py + workflow."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_commit_identities.py"
HASH_FILE = ROOT / "scripts" / "blocked_identity_hashes.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "commit-identity.yml"
CLEAN = ("Clean Dev", "12345+clean@users.noreply.github.com")


@pytest.fixture
def repo(tmp_path: Path):
    """Throwaway repo isolated from the developer's global git config/hooks."""
    work = tmp_path / "repo"
    work.mkdir()
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
    env.pop("GITHUB_BASE_REF", None)

    def git(*args: str, **extra: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=work, env={**env, **extra}, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(message: str, author=CLEAN, committer=CLEAN) -> str:
        git(
            "commit", "--allow-empty", "--no-verify", "-q", "-m", message,
            GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
            GIT_COMMITTER_NAME=committer[0], GIT_COMMITTER_EMAIL=committer[1],
        )
        return git("rev-parse", "HEAD")

    def check(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=work, env=env, capture_output=True, text=True
        )

    git("init", "-q", "-b", "main")
    commit("chore: base")
    git("tag", "base")
    return git, commit, check


@pytest.fixture
def secret_email(tmp_path: Path):
    email = f"{secrets.token_hex(8)}@example.invalid"
    hashes = tmp_path / "hashes.txt"
    hashes.write_text("# test list\n" + hashlib.sha256(email.encode()).hexdigest() + "\n")
    return email, str(hashes)


def _only(sha: str) -> list[str]:
    return ["--range", f"{sha}^..{sha}"]


def test_hashed_author_email_blocked_without_printing_it(repo, secret_email):
    _, commit, check = repo
    email, hashes = secret_email
    sha = commit("feat: leak", author=("Someone", "  " + email.upper() + " "))
    result = check(*_only(sha), "--hash-file", hashes)
    out = result.stdout + result.stderr
    assert result.returncode == 1
    assert sha[:10] in out and "author email: blocked personal email (hashed)" in out
    assert email.split("@")[0] not in out.lower()
    # Without the digest the same commit is clean: the match is hash-driven.
    assert check(*_only(sha)).returncode == 0


def test_blocked_trailer_emails(repo, secret_email):
    _, commit, check = repo
    email, hashes = secret_email
    co = commit(f"fix: thing\n\nCo-authored-by: Someone <{email}>")
    so = commit(f"fix: other\n\nsigned-off-by: Someone <{email.upper()}>")
    for sha, label in ((co, "Co-authored-by"), (so, "Signed-off-by")):
        result = check(*_only(sha), "--hash-file", hashes)
        assert result.returncode == 1
        assert f"{label} trailer email: blocked personal email (hashed)" in result.stdout
        assert email.split("@")[0] not in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize(
    "author,committer,expected",
    [
        (("mergetest", CLEAN[1]), CLEAN, "author name: placeholder identity name 'mergetest'"),
        (CLEAN, ("Dev", "test@local"), "committer email: placeholder email 'test@local'"),
        (("Your Name", "you@example.com"), CLEAN, "author email: placeholder email 'you@example.com'"),
        (CLEAN, ("Dev", "dev@Some-MacBook.LOCAL"), "committer email: hostname-style auto-detected email"),
        (CLEAN, ("Dev", "dev@box.localdomain"), "committer email: hostname-style auto-detected email"),
    ],
)
def test_literal_placeholder_identities_blocked(repo, author, committer, expected):
    _, commit, check = repo
    sha = commit("chore: tool commit", author=author, committer=committer)
    result = check(*_only(sha))
    assert result.returncode == 1
    assert expected in result.stdout
    assert "some-macbook" not in result.stdout.lower()


def test_clean_commits_pass_and_range_reports_each_offender(repo, secret_email):
    _, commit, check = repo
    email, hashes = secret_email
    commit("feat: clean one")
    assert check("--range", "base..HEAD", "--hash-file", hashes).returncode == 0
    bad = commit("chore: merge", author=("mergetest", "mergetest@example.com"))
    commit("feat: clean two")
    result = check("--range", "base..HEAD", "--hash-file", hashes)
    assert result.returncode == 1
    offenders = set(re.findall(r"^  ([0-9a-f]{10})  ", result.stdout, re.MULTILINE))
    assert offenders == {bad[:10]}


def test_default_range_uses_origin_base(repo):
    git, commit, check = repo
    git("update-ref", "refs/remotes/origin/main", "base")
    commit("chore: tool", committer=("mergetest", "mergetest@example.com"))
    assert check().returncode == 1
    assert check("--base", "nonexistent").returncode == 2


def test_malformed_hash_list_is_an_error(repo, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("not-a-digest\n")
    _, _, check = repo
    assert check("--range", "base..HEAD", "--hash-file", str(bad)).returncode == 2


def test_shipped_hash_list_is_digests_only():
    entries = [
        line.strip()
        for line in HASH_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert entries and len(set(entries)) == len(entries)
    assert all(re.fullmatch(r"[0-9a-f]{64}", e) for e in entries)


def test_workflow_runs_on_untrusted_safe_pull_request():
    wf = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(wf["on"]) == {"pull_request"}
    assert "edited" in wf["on"]["pull_request"]["types"]  # description edits are rechecked
    assert wf["permissions"] == {"contents": "read"}
    (job,) = wf["jobs"].values()
    checkout, run = job["steps"]
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert checkout["with"] == {"fetch-depth": "0", "persist-credentials": "false"}
    assert "${{" not in run["run"]
    assert run["env"]["BASE_REF"] == "${{ github.base_ref }}"
    assert "origin/${BASE_REF}..HEAD" in run["run"] and '--event "$GITHUB_EVENT_PATH"' in run["run"]
    # The gate runs the base branch's checker and lists, never the PR's copy.
    assert 'git archive "origin/${BASE_REF}"' in run["run"]
    assert 'python3 "$policy/scripts/check_commit_identities.py"' in run["run"]
    assert '--hash-file "$policy/' in run["run"]
    assert "python3 scripts/" not in run["run"]
    archived = re.search(r'files="([^"]+)"', run["run"]).group(1).split()
    assert {"scripts/check_commit_identities.py", "scripts/blocked_identity_hashes.txt",
            ".github/scripts/agent_identities.py"} == set(archived)


# ── AI agents ───────────────────────────────────────────────────────────
# Built at runtime so this file never contains a literal agent address.
AGENT_EMAIL = "noreply" + "@" + "anthropic.com"
HUMAN = "Dana <4242+dana@users.noreply.github.com>"


@pytest.mark.parametrize(
    "message,expected",
    [
        (f"feat: x\n\nCo-authored-by: Claude <{AGENT_EMAIL}>", "Co-authored-by trailer email: AI agent identity"),
        ("feat: x\n\nCo-authored-by: Claude Opus 5.5 <x@example.com>", "Co-authored-by trailer name: AI agent identity"),
        ("feat: x\n\nCo-authored-by: Cursor Agent <cursoragent@cursor.com>", "Co-authored-by trailer name: AI agent identity"),
        ("feat: x\n\nCo-authored-by: Copilot <198982749+Copilot@users.noreply.github.com>", "AI agent identity"),
        ("feat: x\n\n\U0001f916 Generated with [Some Agent](https://example.com)", "message: AI agent attribution"),
        ("feat: x\n\nGenerated with Codex", "message: AI agent attribution"),
        ("feat: x\n\nClaude-Session: abc123", "message: AI agent attribution"),
        ("feat: x\n\nSession: https://claude.ai/code/session_abc", "message: AI agent attribution"),
        ("feat: x\n\nSee https://claude.ai/share/0f1e2d", "message: AI agent attribution"),
        ("feat: x\n\nSee https://claude.ai/chat/0f1e2d", "message: AI agent attribution"),
        ("feat: x\n\nSee claude.ai/c/0f1e2d", "message: AI agent attribution"),
        ("feat: x\n\nVia https://claude.ai/new", "message: AI agent attribution"),
        ("feat: x\n\nBuilt in https://claude.com/claude-code", "message: AI agent attribution"),
        ("feat: x\n\nPlan: https://chatgpt.com/share/abc-123", "message: AI agent attribution"),
        ("feat: x\n\nPlan: https://chatgpt.com/c/abc-123", "message: AI agent attribution"),
        ("feat: x\n\nPlan: https://chat.openai.com/share/abc-123", "message: AI agent attribution"),
    ],
)
def test_ai_agent_attribution_in_commits_is_blocked(repo, message, expected):
    _, commit, check = repo
    sha = commit(message)
    result = check(*_only(sha))
    assert result.returncode == 1 and expected in result.stdout


def test_ai_agent_as_author_is_blocked(repo):
    _, commit, check = repo
    sha = commit("feat: x", author=("Agent", AGENT_EMAIL))
    result = check(*_only(sha))
    assert result.returncode == 1 and "author email: AI agent identity" in result.stdout


def _at(local: str, domain: str) -> str:
    return local + "@" + domain


@pytest.mark.parametrize(
    "email",
    [
        _at("noreply", "anthropic.com"), _at("cursoragent", "cursor.com"), _at("codex", "openai.com"),
        _at("noreply", "openai.com"), _at("noreply", "coderabbit.ai"), _at("codex", "users.noreply.github.com"),
        _at("198982749+Copilot", "users.noreply.github.com"), _at("209825114+claude[bot]", "users.noreply.github.com"),
        _at("158243242+devin-ai-integration[bot]", "users.noreply.github.com"),
        _at("161369871+google-labs-jules[bot]", "users.noreply.github.com"),
        _at("136622811+coderabbitai[bot]", "users.noreply.github.com"),
    ],
)
def test_agent_emails_are_blocked(repo, email):
    _, commit, check = repo
    sha = commit("feat: x", author=("Someone", email))
    result = check(*_only(sha))
    assert result.returncode == 1 and "author email: AI agent identity" in result.stdout


@pytest.mark.parametrize(
    "email", [_at("someone", "anthropic.com"), _at("someone", "openai.com"), _at("dev", "cursor.com"),
              _at("dev", "coderabbit.ai"), _at("dev", "devin.ai")],
)
def test_people_with_work_emails_at_ai_companies_pass(repo, email):
    _, commit, check = repo
    sha = commit(f"feat: x\n\nCo-authored-by: Sam <{email}>", author=("Sam", email), committer=("Sam", email))
    assert check(*_only(sha)).returncode == 0


@pytest.mark.parametrize(
    "name",
    ["Claude Code", "Claude Opus 5.5", "claude 3.5 sonnet", "Cursor Agent", "cursoragent", "GitHub Copilot",
     "Copilot", "Codex", "OpenAI Codex", "Devin AI", "devin-ai-integration[bot]", "google-labs-jules[bot]",
     "coderabbitai[bot]", "claude[bot]"],
)
def test_agent_author_and_committer_names_are_blocked(repo, name):
    _, commit, check = repo
    for field, kwargs in (("author", {"author": (name, CLEAN[1])}), ("committer", {"committer": (name, CLEAN[1])})):
        sha = commit("feat: x", **kwargs)
        result = check(*_only(sha))
        assert result.returncode == 1 and f"{field} name: AI agent identity" in result.stdout, (field, name)


@pytest.mark.parametrize("name", ["Claude", "Claude Monet", "Jules", "Devin", "Cursor Smith"])
def test_people_named_like_agents_can_author(repo, name):
    _, commit, check = repo
    sha = commit("feat: x", author=(name, CLEAN[1]), committer=(name, CLEAN[1]))
    assert check(*_only(sha)).returncode == 0


@pytest.mark.parametrize(
    "message",
    [
        f"feat: pair work\n\nCo-authored-by: {HUMAN}",
        "feat: x\n\nCo-authored-by: Claude Monet <monet@example.com>",  # a person called Claude
        "fix: audio generated with the default model sounded clipped",
        "docs: link the claude.ai status page and chatgpt.com/pricing",
    ],
)
def test_people_and_ordinary_prose_pass(repo, message):
    _, commit, check = repo
    sha = commit(message)
    assert check(*_only(sha)).returncode == 0


@pytest.mark.parametrize("name", ["Claude", "Jules", "Devin", "Gemini"])
def test_human_first_names_in_commit_trailers_pass(repo, name):
    _, commit, check = repo
    sha = commit(
        f"feat: pair work\n\nCo-authored-by: {name} <human@example.com>\n"
        f"Signed-off-by: {name} <human@example.com>"
    )
    assert check(*_only(sha)).returncode == 0


@pytest.mark.parametrize("name", ["Claude", "Jules", "Devin", "Gemini"])
def test_ambiguous_trailer_name_with_agent_email_stays_blocked(repo, name):
    _, commit, check = repo
    sha = commit(f"feat: x\n\nCo-authored-by: {name} <{AGENT_EMAIL}>")
    result = check(*_only(sha))
    assert result.returncode == 1
    assert "Co-authored-by trailer email: AI agent identity" in result.stdout


@pytest.mark.parametrize(
    "name", ["Claude Code 2", "Google Jules", "Devin AI", "Gemini Code Assist", "claude[bot]"]
)
def test_unambiguous_agent_trailers_stay_blocked(repo, name):
    _, commit, check = repo
    sha = commit(f"feat: x\n\nCo-authored-by: {name} <tool@example.com>")
    result = check(*_only(sha))
    assert result.returncode == 1
    assert "Co-authored-by trailer name: AI agent identity" in result.stdout


def test_bot_authors_that_are_not_agents_pass(repo):
    _, commit, check = repo
    sha = commit("chore(deps): bump", author=("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"))
    assert check(*_only(sha)).returncode == 0


def test_pull_request_description_attribution_is_blocked(repo, tmp_path):
    _, commit, check = repo
    sha = commit("feat: clean")
    event = tmp_path / "event.json"
    event.write_text('{"pull_request": {"body": "Adds X.\\n\\nGenerated with Claude"}}')
    result = check(*_only(sha), "--event", str(event))
    assert result.returncode == 1 and "PR  description: AI agent attribution" in result.stdout
    event.write_text('{"pull_request": {"body": null}}')
    assert check(*_only(sha), "--event", str(event)).returncode == 0


@pytest.mark.parametrize(
    "text,flagged",
    [
        ("Written by an LLM engineer on the team.", False),
        ("Generated by AI researchers at the lab", False),
        ("Generated with an AI model.", True),
        ("Written by AI", True),
        ("Created using an LLM", True),
        ("Generated with AI assistance", True),
        ("Generated with an AI model for this change", True),
        ("Generated by AI assistant and reviewed by me", True),
        ("Generated with an AI tool for docs", True),
        ("Written by the AI team at Example Corp", False),
        ("Written by an AI consultant", False),
        ("Authored by the LLM working group", False),
        ("Written by AI, then edited", True),
        ("Generated with AI-assisted tooling", True),
        ("Created with an AI coding agent", True),
        ("Generated by Claude", True),
        ("Generated with Cursor today", True),
    ],
)
def test_generic_ai_credit_only_when_it_names_no_person(text, flagged):
    spec = importlib.util.spec_from_file_location("cci", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert bool(module.classify_text(text)) is flagged


def test_remedy_rewrites_only_the_listed_commits():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "git rebase --exec 'git commit --amend --no-edit --reset-author'" not in text
    contributing = (ROOT / ".github" / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "mark each listed commit `edit`" in contributing
