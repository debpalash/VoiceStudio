"""Unit tests for scripts/cla_audit.py (who has code at HEAD but no CLA)."""
from __future__ import annotations

import collections
import importlib.util
import json
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


def _audit_report(monkeypatch, tmp_path, capsys, *, owner_signed=False):
    """Run the real report/classification over a small attributed inventory, offline."""
    owner = next(iter(audit.cla.MAINTAINER_IDS))
    placeholder = "test@local"
    signed_email = "2002+signed@users.noreply.github.com"
    unlinked = "unlinked@example.invalid"
    credits = _credits(
        (placeholder, "Placeholder", "owner", 4),
        (placeholder, "Placeholder", "required", 5),
        (unlinked, "Owner hint", "unlinked", 6),
        (signed_email, "Signed", "signed", 7),
    )
    accounts = {
        placeholder: audit.account_for(placeholder, None),
        signed_email: audit.account_for(signed_email, None),
        unlinked: (None, None, "unlinked"),
    }
    submitters = {
        "owner": ("owner", owner, "submitted"),
        "required": ("required", 1001, "submitted"),
        "unlinked": ("owner", owner, "submitted"),
    }
    signature_ids = {2002} | ({owner} if owner_signed else set())
    monkeypatch.setattr(audit, "require_full_history", lambda: None)
    monkeypatch.setattr(audit, "signed_ids", lambda _allow_missing: signature_ids)
    monkeypatch.setattr(audit, "tracked_text_files", lambda: ["a.py"])
    monkeypatch.setattr(audit, "blame", lambda _path: credits)
    monkeypatch.setattr(audit, "commit_messages", lambda _commits: {})
    monkeypatch.setattr(audit, "authored_commits", lambda: {})
    monkeypatch.setattr(audit, "account_for", lambda email, _commit: accounts[email])
    monkeypatch.setattr(audit, "pr_author_for", lambda commit: submitters[commit])
    monkeypatch.setattr(audit, "names_to_logins", lambda: {"owner hint": ("owner", owner)})
    output = tmp_path / "audit.json"
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT), "--json", str(output)])
    audit.main()
    return json.loads(output.read_text(encoding="utf-8")), capsys.readouterr().out, str(owner), unlinked


def test_exempt_submitted_owner_keeps_provenance_without_unsigned_label(monkeypatch, tmp_path, capsys):
    report, markdown, owner, _ = _audit_report(monkeypatch, tmp_path, capsys)
    row = report["people"][owner]
    assert "2 unsigned of 4 contributors" in markdown
    assert row["lines"] == 4 and row["files"] == [["a.py", 4]]
    assert audit.NOTES["submitted"] in row["notes"]
    unsigned_table, provenance = markdown.split("## Exempt contributors requiring provenance review", 1)
    assert "\n| @owner (" not in unsigned_table
    assert "| @owner (" in provenance and audit.NOTES["submitted"] in provenance
    assert row["signing_required"] is False
    assert row["signature_present"] is False and row["signed"] is False
    assert row["provenance_review_required"] is True


@pytest.mark.parametrize("owner_signed", [False, True])
def test_signature_presence_is_independent_of_owner_exemption(monkeypatch, tmp_path, capsys, owner_signed):
    report, _, owner, _ = _audit_report(monkeypatch, tmp_path, capsys, owner_signed=owner_signed)
    row = report["people"][owner]
    assert row["signature_present"] is owner_signed
    assert row["signed"] is owner_signed  # Existing field keeps its actual-store meaning.
    assert row["signing_required"] is False
    assert row["provenance_review_required"] is True


def test_required_and_unlinked_contributors_do_not_borrow_owner_exemption(monkeypatch, tmp_path, capsys):
    report, markdown, _, unlinked = _audit_report(monkeypatch, tmp_path, capsys)
    for key in ("1001", unlinked):
        row = report["people"][key]
        assert row["signing_required"] is True and row["signature_present"] is False
        assert row["provenance_review_required"] is True
    assert report["people"][unlinked]["id"] is None
    assert "same display name as @owner; confirm" in report["people"][unlinked]["notes"]
    assert report["people"]["2002"]["signing_required"] is True
    assert report["people"]["2002"]["signature_present"] is True
    assert report["people"]["2002"]["provenance_review_required"] is False
    assert "| @required (" in markdown and f"| `{unlinked}` (" in markdown


def test_failed_blame_cannot_silently_remove_contributors(monkeypatch):
    import subprocess

    def failed(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="history object unavailable")

    monkeypatch.setattr(audit.subprocess, "run", failed)
    with pytest.raises(SystemExit, match="git blame.*failed"):
        audit.blame("contributed.py")


def test_head_file_inventory_excludes_gitlinks_and_uncommitted_files(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True)

    git("init", "-q")
    git("config", "user.name", "Audit Test")
    git("config", "user.email", "audit@example.com")
    unusual = "tab\tand\nnewline.py"
    (tmp_path / unusual).write_text("retained source\n")
    (tmp_path / "sample.wav").write_bytes(b"audio")
    git("add", ".")
    git("commit", "-qm", "source")
    source = git("rev-parse", "HEAD").strip()
    git("update-index", "--add", "--cacheinfo", f"160000,{source},submodule")
    git("commit", "-qm", "submodule")
    (tmp_path / "staged.py").write_text("not in HEAD\n")
    git("add", "staged.py")
    git("rm", "--cached", "--", unusual)
    monkeypatch.setattr(audit, "REPO", tmp_path)
    assert audit.tracked_text_files() == [unusual]
    assert sum(audit.blame(unusual).values()) == 1
