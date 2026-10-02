"""Unit tests for scripts/cla_audit.py (who has code at HEAD but no CLA)."""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cla_audit.py"
_spec = importlib.util.spec_from_file_location("cla_audit", _SCRIPT)
audit = importlib.util.module_from_spec(_spec)
sys.modules["cla_audit"] = audit
_spec.loader.exec_module(audit)

PRIMARY = "primary@example.com"
CO = "co@example.com"


def _commit_payload(email, login=None, account_id=None):
    return {"commit": {"author": {"email": email}},
            "author": {"login": login, "id": account_id} if account_id else None}


def test_shallow_clone_is_refused(monkeypatch):
    monkeypatch.setattr(audit, "git", lambda *args: "true\n")
    with pytest.raises(SystemExit, match="git fetch --unshallow"):
        audit.require_full_history()
    monkeypatch.setattr(audit, "git", lambda *args: "false\n")
    audit.require_full_history()


def test_account_is_accepted_only_for_the_commits_own_author(monkeypatch):
    payloads = {"c1": _commit_payload(PRIMARY, "pat", 2020)}
    monkeypatch.setattr(audit, "github", lambda path: payloads[path.split("/")[1]])
    assert audit.account_for(PRIMARY, "c1") == ("pat", 2020, "api")
    # A trailer-only co-author looked up through the same commit would inherit
    # the primary author's account; it stays unresolved instead.
    assert audit.account_for(CO, "c1") == (None, None, "unresolved")
    assert audit.account_for(CO, None) == (None, None, "unresolved")


def test_unlinked_and_agent_emails_are_told_apart(monkeypatch):
    monkeypatch.setattr(audit, "github", lambda path: _commit_payload(PRIMARY))
    assert audit.account_for(PRIMARY, "c1") == (None, None, "unlinked")
    assert audit.account_for("cursoragent@cursor.com", "c1") == (None, None, "agent")


def _credits(*rows):
    return collections.Counter({(email, name, commit, "a.py"): n for email, name, commit, n in rows})


def test_unlinked_author_keeps_a_row_even_when_the_submitter_signed():
    credits = _credits((PRIMARY, "Pat", "c1", 10))
    people, _ = audit.attribute(credits, {PRIMARY: (None, None, "unlinked")},
                                {"c1": ("opener", 1001, "submitted")}, {}, {})
    assert set(people) == {PRIMARY}
    row = people[PRIMARY]
    assert row["id"] is None and row["lines"] == 10
    assert "submitted by @opener in a PR" in row["notes"]


def test_agent_commits_are_credited_to_the_submitter():
    credits = _credits(("cursoragent@cursor.com", "Cursor Agent", "c1", 4))
    people, _ = audit.attribute(credits, {"cursoragent@cursor.com": (None, None, "agent")},
                                {"c1": ("opener", 1001, "submitted")}, {}, {})
    assert set(people) == {"1001"} and people["1001"]["lines"] == 4


def test_unresolved_co_author_gets_no_pr_or_name_fallback():
    credits = _credits((CO, "Pat", "c1", 3))
    people, _ = audit.attribute(credits, {CO: (None, None, "unresolved")},
                                {"c1": ("opener", 1001, "submitted")}, {"pat": ("pat", 2020)}, {})
    assert set(people) == {CO} and people[CO]["id"] is None
    assert audit.NOTES["unresolved"] in people[CO]["notes"]


def test_failed_blame_cannot_silently_remove_contributors(monkeypatch):
    import subprocess

    def failed(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="history object unavailable")

    monkeypatch.setattr(audit.subprocess, "run", failed)
    with pytest.raises(SystemExit, match="git blame.*failed"):
        audit.blame("contributed.py")
