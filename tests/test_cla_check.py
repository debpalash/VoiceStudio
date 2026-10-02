"""Unit tests for .github/scripts/cla_check.py (the CLA pull-request check)."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "cla_check.py"
_spec = importlib.util.spec_from_file_location("cla_check", _SCRIPT)
cla = importlib.util.module_from_spec(_spec)
sys.modules["cla_check"] = cla  # dataclasses resolve their module by name
_spec.loader.exec_module(cla)

OPENER = cla.Person("alice", 1001)
MAINTAINER = cla.Person("debpalash", 4178343)


def actor(login=None, id=None, name="Someone", email="someone@example.com"):
    return {"name": name, "email": email, "login": login, "id": id}


# ── Signing comments ────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    cla.SIGN_PHRASE,
    cla.SIGN_PHRASE + "\n",
    "  " + cla.SIGN_PHRASE.upper() + "  ",
    "Thanks!\n\n" + cla.SIGN_PHRASE + "\n\nCheers",
    cla.SIGN_PHRASE.replace(" ", "  "),
])
def test_sign_phrase_is_recognised_on_its_own_line(body):
    assert cla.is_sign_comment(body)


@pytest.mark.parametrize("body", [
    "> " + cla.SIGN_PHRASE,  # quoting the bot is not signing
    "I will sign: " + cla.SIGN_PHRASE,
    cla.SIGN_PHRASE.replace("1.0", "0.9"),
    "",
    None,
])
def test_quotes_and_other_text_are_not_signatures(body):
    assert not cla.is_sign_comment(body)


# ── Who must sign ───────────────────────────────────────────────────────


def test_signed_opener_and_authors_pass():
    result = cla.evaluate(OPENER, False, [actor("alice", 1001), actor("bob", 1002)], {1001, 1002})
    assert result.passed


def test_unsigned_linked_author_is_listed_once():
    result = cla.evaluate(OPENER, False, [actor("bob", 1002), actor("bob", 1002)], {1001})
    assert result.unsigned == [cla.Person("bob", 1002)]


def test_opener_must_sign_even_without_commits():
    result = cla.evaluate(OPENER, False, [], set())
    assert result.unsigned == [OPENER]


def test_git_name_cannot_impersonate_the_maintainer():
    # Unlinked email with the maintainer's name: matched by account, not name.
    spoof = actor(name="debpalash", email="someone@gmail.com")
    result = cla.evaluate(OPENER, False, [spoof], {1001})
    assert result.unknown == ["debpalash <someone@gmail.com>"]


def test_maintainer_and_bots_are_exempt_by_id():
    actors = [actor("debpalash", 4178343), actor("dependabot[bot]", 49699333)]
    assert cla.evaluate(MAINTAINER, False, actors, set()).passed


def test_co_author_trailer_noreply_email_maps_to_account_id():
    actors = cla.co_authors("fix: x\n\nCo-authored-by: Carol <2002+carol@users.noreply.github.com>")
    result = cla.evaluate(OPENER, False, actors, {1001})
    assert result.unsigned == [cla.Person("carol", 2002)]


def test_unlinked_commit_author_noreply_email_is_not_trusted():
    # GitHub links real no-reply addresses itself; an unlinked one may name
    # someone else's account, so it must be reviewed rather than mapped.
    result = cla.evaluate(OPENER, False, [actor(name="Carol", email="2002+carol@users.noreply.github.com")],
                          {1001, 2002})
    assert not result.passed and result.unknown == ["Carol <2002+carol@users.noreply.github.com>"]


@pytest.mark.parametrize("email", ["someone@openai.com", "someone@anthropic.com", "dev@cursor.com"])
def test_people_with_work_emails_at_ai_companies_still_sign(email):
    assert cla.evaluate(OPENER, False, [actor(email=email)], {1001}).unknown == [f"Someone <{email}>"]
    assert cla.evaluate(OPENER, False, [actor("pat", 2020, email=email)], {1001}).unsigned == [cla.Person("pat", 2020)]


def test_ai_tool_co_authors_are_skipped_but_people_are_not():
    actors = cla.co_authors(
        "fix: thing\n\nCo-authored-by: Cursor Agent <cursoragent@cursor.com>\n"
        "Co-authored-by: Dave <3003+dave@users.noreply.github.com>\n"
    )
    result = cla.evaluate(OPENER, False, actors, {1001})
    assert result.unsigned == [cla.Person("dave", 3003)] and not result.unknown


def test_bot_opened_pr_skips_bot_commits_but_not_people():
    bot = cla.Person("dependabot[bot]", 49699333)
    actors = [actor(email="49699333+dependabot[bot]@users.noreply.github.com"), actor("erin", 4004)]
    result = cla.evaluate(bot, True, actors, set())
    assert result.unsigned == [cla.Person("erin", 4004)]


def test_unknown_identity_fails_the_check():
    result = cla.evaluate(OPENER, False, [actor(name="mergetest", email="test@local")], {1001})
    assert not result.passed and result.unknown == ["mergetest <test@local>"]


# ── Store and comment ───────────────────────────────────────────────────


def test_signatures_are_added_once_per_account():
    store = cla.empty_store()
    assert cla.add_signatures(store, [{"id": 1, "login": "a"}])
    assert not cla.add_signatures(store, [{"id": 1, "login": "a-renamed"}])
    assert [s["id"] for s in store["signatures"]] == [1]


def test_comment_renders_untrusted_identities_as_inert_code():
    evaluation = cla.Evaluation(unknown=["[click](https://evil.example) @everyone <a`b@x.test>"])
    body = cla.render_comment(evaluation, "https://example.test/cla")
    line = next(l for l in body.splitlines() if "evil" in l)
    assert line.startswith("- `") and line.count("`") == 2
    assert cla.SIGN_PHRASE not in body  # nobody listed as unsigned → no sign instructions


# ── End-to-end with a fake GitHub ───────────────────────────────────────


class FakeGitHub:
    repo = "debpalash/VoiceStudio"

    def __init__(self, comments, actors, store=None, pr=None, others=None, other_commits=None):
        self.comments, self.actors = comments, actors
        self.other_commits = other_commits or {}  # PR number -> one actor per commit
        self.pr, self.others = pr or {}, others or {}
        self.files = {} if store is None else {cla.SIGNATURE_PATH: store}
        self.branch = store is not None
        self.open_prs = None
        self.statuses, self.posted, self.patched = [], [], []

    def get(self, path):
        assert path.endswith(f"/pulls/{self.pr.get('number', 7)}")
        return {"number": 7, "state": "open", "user": {"login": "alice", "id": 1001, "type": "User"},
                "head": {"sha": "abc"}, "base": {"repo": {"id": 99, "default_branch": "main"}}, **self.pr}

    def paginate(self, path, limit=None):
        if path.endswith('/pulls?state=open'):
            return self.open_prs if self.open_prs is not None else [self.get(f"/pulls/{self.pr.get('number', 7)}")]
        return self.comments

    def graphql(self, query, variables):
        commits = self.actors if variables["number"] == 7 else self.other_commits.get(variables["number"], [])
        nodes = [{"commit": {"oid": "f" * 40, "authors": {
            "pageInfo": {"hasNextPage": bool(a.get("more"))},
            "nodes": [{"name": a["name"], "email": a["email"],
                       "user": {"login": a["login"], "databaseId": a["id"]} if a["id"] else None}]}}}
            for a in commits]
        return {"repository": {"pullRequest": {"commits": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}

    def request(self, method, path, body=None):
        if method == "GET" and "/pulls/" in path:
            number = int(path.rsplit("/", 1)[1])
            return (200, {"user": self.others[number]}, {}) if number in self.others else (404, None, {})
        if "/contents/" in path and method == "GET":
            if cla.SIGNATURE_PATH not in self.files:
                return 404, None, {}
            raw = base64.b64encode(json.dumps(self.files[cla.SIGNATURE_PATH]).encode()).decode()
            return 200, {"content": raw, "sha": "s1"}, {}
        if "/contents/" in path and method == "PUT":
            self.files[cla.SIGNATURE_PATH] = json.loads(base64.b64decode(body["content"]))
            return 201, {}, {}
        if "/git/ref/heads/" in path:
            return (200 if self.branch else 404), {}, {}
        if path.endswith("/git/trees"):
            return 201, {"sha": "t"}, {}
        if path.endswith("/git/commits"):
            assert body["parents"] == []
            return 201, {"sha": "c"}, {}
        if path.endswith("/git/refs"):
            self.branch = True
            return 201, {}, {}
        if "/statuses/" in path:
            self.statuses.append(body)
            return 201, {}, {}
        if method == "POST" and path.endswith("/comments"):
            self.posted.append(body["body"])
            return 201, {}, {}
        if method == "PATCH":
            self.patched.append(body["body"])
            return 200, {}, {}
        raise AssertionError(f"unexpected {method} {path}")


def test_first_signature_creates_the_store_and_turns_the_status_green():
    comments = [{"id": 5, "user": {"id": 1001, "login": "alice", "type": "User"},
                 "body": cla.SIGN_PHRASE, "created_at": "2026-10-02T00:00:00Z"}]
    gh = FakeGitHub(comments, [actor("alice", 1001)])
    result = cla.run(gh, "issue_comment", {"issue": {"number": 7}})
    assert result.passed and gh.branch
    saved = gh.files[cla.SIGNATURE_PATH]["signatures"]
    assert len(saved) == 1 and len(saved[0].pop("body_sha256")) == 64
    assert saved == [{"login": "alice", "id": 1001, "pull_request": 7, "comment_id": 5,
                      "signed_at": "2026-10-02T00:00:00Z", "version": cla.CLA_VERSION, "repo_id": 99}]
    assert gh.statuses[-1]["state"] == "success" and gh.statuses[-1]["context"] == "CLA"
    assert not gh.posted  # nothing to ask for, so no new comment


def test_unsigned_pr_fails_and_asks_once():
    gh = FakeGitHub([], [actor("alice", 1001)], store=cla.empty_store())
    result = cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}})
    assert not result.passed and gh.statuses[-1]["state"] == "failure"
    assert len(gh.posted) == 1 and "@alice" in gh.posted[0]


def test_someone_elses_sign_comment_does_not_count():
    comments = [{"id": 5, "user": {"id": 2002, "login": "mallory", "type": "User"},
                 "body": cla.SIGN_PHRASE, "created_at": "2026-10-02T00:00:00Z"}]
    gh = FakeGitHub(comments, [actor("alice", 1001)], store=cla.empty_store())
    assert not cla.run(gh, "issue_comment", {"issue": {"number": 7}}).passed
    assert gh.files[cla.SIGNATURE_PATH]["signatures"] == []


def _comment(user_id=1001, login="alice", body=None, edited=False, user_type="User", cid=5):
    created = "2026-10-02T00:00:00Z"
    return {"id": cid, "user": {"id": user_id, "login": login, "type": user_type},
            "body": cla.SIGN_PHRASE if body is None else body,
            "created_at": created, "updated_at": "2026-10-03T00:00:00Z" if edited else created}


def test_edited_sign_comments_do_not_count():
    gh = FakeGitHub([_comment(edited=True)], [actor("alice", 1001)], store=cla.empty_store())
    assert not cla.run(gh, "issue_comment", {"issue": {"number": 7}}).passed
    assert gh.files[cla.SIGNATURE_PATH]["signatures"] == []


def test_stored_signature_records_a_hash_of_the_comment():
    gh = FakeGitHub([_comment()], [actor("alice", 1001)], store=cla.empty_store())
    cla.run(gh, "issue_comment", {"issue": {"number": 7}})
    stored = gh.files[cla.SIGNATURE_PATH]["signatures"][0]
    assert len(stored["body_sha256"]) == 64 and stored["pull_request"] == 7


@pytest.mark.parametrize("email", [
    "noreply@openai.com", "codex@openai.com", "175728472+Copilot@users.noreply.github.com",
    "cursoragent@cursor.com", "41898282+github-actions[bot]@users.noreply.github.com",
])
def test_agent_co_author_trailers_never_need_to_sign(email):
    trailer = {**actor(email=email), "trailer": True}
    assert cla.evaluate(OPENER, False, [trailer], {1001}).passed


@pytest.mark.parametrize("email", ["claude@users.noreply.github.com", "noreply@openai.com"])
def test_unlinked_commit_author_with_a_tool_address_fails_closed(email):
    # Anyone can commit under a tool address; only GitHub's account link counts.
    result = cla.evaluate(OPENER, False, [actor(email=email)], {1001})
    assert not result.passed and result.unknown


def test_linked_bot_accounts_are_skipped_but_linked_people_are_not():
    bot = actor("renovate[bot]", 29139614, email="29139614+renovate[bot]@users.noreply.github.com")
    person = actor("claude", 5005, email="claude@users.noreply.github.com")
    result = cla.evaluate(OPENER, False, [bot, person], {1001})
    assert result.unsigned == [cla.Person("claude", 5005)]


def test_superseded_pull_request_authors_must_sign():
    assert cla.superseded_numbers("Consolidates fixes.\nSupersedes #12, #15 and #12.\nCloses #99") == [12, 15]


def _issue_event(labels):
    return {"issue": {"number": 42, "labels": [{"name": l} for l in labels]}, "repository": {"id": 99}}


def _with_reactions(gh):
    gh.reactions = []
    original = gh.request

    def request(method, path, body=None):
        if path.endswith("/reactions"):
            gh.reactions.append(path)
            return 201, {}, {}
        return original(method, path, body)

    gh.request = request
    return gh


def test_past_contributors_sign_on_a_labelled_issue_and_none_are_lost():
    comments = [_comment(270455167, "velixio", cid=1), _comment(2002, "carol", cid=2),
                _comment(3003, "dave", body="> " + cla.SIGN_PHRASE, cid=3),
                _comment(4004, "erin", edited=True, cid=4), _comment(5005, "bot", user_type="Bot", cid=5)]
    gh = _with_reactions(FakeGitHub(comments, [], store=cla.empty_store()))
    assert cla.sign_on_issue(gh, _issue_event(["cla"])) == 2
    stored = gh.files[cla.SIGNATURE_PATH]["signatures"]
    assert [s["id"] for s in stored] == [270455167, 2002] and stored[0]["issue"] == 42
    assert len(gh.reactions) == 2
    assert cla.sign_on_issue(gh, _issue_event(["cla"])) == 0  # idempotent


def test_unlabelled_issues_record_nothing():
    gh = FakeGitHub([_comment()], [], store=cla.empty_store())
    assert cla.sign_on_issue(gh, _issue_event([])) == 0
    assert gh.files[cla.SIGNATURE_PATH]["signatures"] == []


def test_superseding_pr_waits_for_the_original_authors():
    others = {12: {"login": "frank", "id": 6006, "type": "User"},
              13: {"login": "dependabot[bot]", "id": 49699333, "type": "Bot"}}
    commits = {12: [actor("frank", 6006), actor("gina", 7007), actor(name="hal", email="hal@example.com")],
               13: [actor("dependabot[bot]", 49699333, email="49699333+dependabot[bot]@users.noreply.github.com")]}
    gh = FakeGitHub([], [actor("alice", 1001)], store=cla.empty_store(),
                    pr={"body": "Supersedes #12 and #13"}, others=others, other_commits=commits)
    result = cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}})
    assert result.unsigned == [cla.Person("alice", 1001), cla.Person("frank", 6006), cla.Person("gina", 7007)]
    assert result.unknown == ["hal <hal@example.com>"]


def test_superseded_pr_with_too_many_commits_fails_closed(monkeypatch):
    monkeypatch.setattr(cla, "MAX_COMMITS", 2)
    gh = FakeGitHub([], [actor("alice", 1001)], store={"version": "1.0", "signatures": [{"id": 1001}]},
                    pr={"body": "Supersedes #12"}, others={12: {"login": "alice", "id": 1001, "type": "User"}},
                    other_commits={12: [actor("alice", 1001)] * 2})
    result = cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}})
    assert not result.passed and not result.unsigned
    assert len(result.unknown) == 1 and result.unknown[0].startswith("#12 has more than 2 commits")


def test_commit_with_more_authors_than_read_fails_closed():
    signed = {"version": "1.0", "signatures": [{"id": 1001}]}
    gh = FakeGitHub([], [{**actor("alice", 1001), "more": True}], store=signed)
    result = cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}})
    assert not result.passed and result.unknown == [
        f"commit fffffff has more than {cla.MAX_AUTHORS} authors; a maintainer must review it by hand"]
    assert gh.statuses[-1]["state"] == "failure"
    assert f"authors(first: {cla.MAX_AUTHORS})" in cla.COMMITS_QUERY and "hasNextPage" in cla.COMMITS_QUERY


def _implicitly_concatenated_elements(source: str) -> list[int]:
    """Line numbers of list/tuple/set elements made of adjacent string literals."""
    import ast
    import io
    import tokenize

    starts = {"STRING", "FSTRING_START", "TSTRING_START"}
    found, lines = [], source.splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                if isinstance(element, (ast.Constant, ast.JoinedStr)) and isinstance(getattr(element, "value", ""), str):
                    segment = ast.get_source_segment(source, element) or ""
                    before = "".join(lines[: element.lineno - 1]) + lines[element.lineno - 1].encode()[: element.col_offset].decode()
                    if before.rstrip().endswith("("):
                        continue  # parenthesized, so the join is deliberate
                    tokens = tokenize.generate_tokens(io.StringIO(segment).readline)
                    if sum(tokenize.tok_name[t.type] in starts for t in tokens) > 1:
                        found.append(element.lineno)
    return found


def test_detector_flags_implicit_concatenation_in_lists():
    assert _implicitly_concatenated_elements('x = ["a" "b", "c"]\n') == [1]
    assert _implicitly_concatenated_elements('x = ["a", ("b" "c")]\ny = ("a" "b")\n') == []


@pytest.mark.parametrize("path", [_SCRIPT, _SCRIPT.parents[2] / "scripts" / "cla_audit.py",
                                  _SCRIPT.parents[2] / "scripts" / "check_commit_identities.py"])
def test_no_implicit_string_concatenation_in_lists(path):
    # Adjacent literals in a list read like a missing comma (CodeQL
    # py/implicit-string-concatenation-in-list): join them in parentheses.
    assert _implicitly_concatenated_elements(path.read_text(encoding="utf-8")) == []


def test_maintainer_override_label_passes_without_checking():
    gh = FakeGitHub([], [actor(email="you@example.com")], store=cla.empty_store(),
                    pr={"labels": [{"name": cla.OVERRIDE_LABEL}]})
    assert cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}}).passed
    assert gh.statuses[-1]["state"] == "success" and "maintainer" in gh.statuses[-1]["description"]


@pytest.mark.parametrize("failed_step", ["actors", "signatures", "comments", "superseded"])
def test_recheck_invalidates_old_approval_before_api_evaluation(monkeypatch, failed_step):
    gh = FakeGitHub([], [actor("alice", 1001)],
                    store={"version": "1.0", "signatures": [{"id": 1001}]},
                    pr={"body": "Supersedes #12"} if failed_step == "superseded" else None)
    gh.statuses.append({"state": "success", "context": "CLA"})
    original_request = gh.request

    def request(method, path, body=None):
        if (failed_step == "signatures" and "/contents/" in path
                or failed_step == "superseded" and path.endswith("/pulls/12")):
            return 503, {"message": "temporarily unavailable"}, {}
        return original_request(method, path, body)

    def fail(*args, **kwargs):
        raise RuntimeError("temporary API failure")

    monkeypatch.setattr(gh, "request", request)
    if failed_step == "actors":
        monkeypatch.setattr(gh, "graphql", fail)
    if failed_step == "comments":
        monkeypatch.setattr(gh, "paginate", fail)
    with pytest.raises(RuntimeError):
        cla.run(gh, "issue_comment", {"issue": {"number": 7}})
    assert gh.statuses[-1]["state"] == "pending"


def test_event_head_is_invalidated_even_when_pr_fetch_fails(monkeypatch):
    gh = FakeGitHub([], [], store=cla.empty_store())
    gh.statuses.append({"state": "success", "context": "CLA"})

    def fail(path):
        raise RuntimeError("PR lookup failed")

    monkeypatch.setattr(gh, "get", fail)
    with pytest.raises(RuntimeError):
        cla.run(gh, "pull_request_target", {"pull_request": {"number": 7, "head": {"sha": "abc"}}})
    assert gh.statuses[-1]["state"] == "pending"


@pytest.mark.parametrize("rejected_state", ["pending", "success"])
def test_status_write_failures_are_not_reported_as_success(monkeypatch, rejected_state):
    gh = FakeGitHub([], [actor("alice", 1001)],
                    store={"version": "1.0", "signatures": [{"id": 1001}]})
    original = gh.request

    def request(method, path, body=None):
        if "/statuses/" in path and body["state"] == rejected_state:
            return 403, {"message": "not permitted"}, {}
        return original(method, path, body)

    monkeypatch.setattr(gh, "request", request)
    with pytest.raises(RuntimeError, match="status"):
        cla.run(gh, "pull_request_target", {"pull_request": {"number": 7}})
    if rejected_state == "success":
        assert gh.statuses[-1]["state"] == "pending"


@pytest.mark.parametrize('peer_metadata', [
    {'user': {'login': 'bob', 'id': 1002, 'type': 'User'}},
    {'body': 'Supersedes #12'},
    {'labels': [{'name': 'cla-override'}]},
])
def test_same_head_prs_cannot_overwrite_one_anothers_cla_result(peer_metadata):
    shared_statuses = []
    prs = [
        {'number': 7, 'state': 'open', 'head': {'sha': 'abc'}},
        {'number': 8, 'state': 'open', 'head': {'sha': 'abc'}, **peer_metadata},
    ]
    for pr in prs:
        gh = FakeGitHub([], [actor('alice', 1001)],
                        store={'signatures': [{'id': 1001}]}, pr=pr)
        gh.open_prs = prs
        gh.statuses = shared_statuses
        result = cla.run(gh, 'pull_request_target', {'pull_request': pr})
        assert not result.passed
        assert gh.statuses[-1]['state'] == 'failure'
        assert 'same head' in gh.posted[-1].lower()
    assert not any(status['state'] == 'success' for status in shared_statuses)


@pytest.mark.parametrize('failure', ['api', 'overflow', 'malformed'])
def test_duplicate_head_enumeration_fails_closed(monkeypatch, failure):
    gh = FakeGitHub([], [], store=cla.empty_store(), pr={'labels': [{'name': 'cla-override'}]})
    original = gh.paginate
    def paginate(path, limit=None):
        if path.endswith('/pulls?state=open'):
            if failure == 'api':
                raise RuntimeError('listing failed')
            if failure == 'malformed':
                return [{'number': 8}]
            return [dict(gh.get('/pulls/7'), number=i) for i in range(1000)]
        return original(path, limit)
    monkeypatch.setattr(gh, 'paginate', paginate)
    with pytest.raises(RuntimeError):
        cla.run(gh, 'pull_request_target', {'pull_request': {'number': 7, 'head': {'sha': 'abc'}}})
    assert gh.statuses[-1]['state'] == 'pending'


def test_duplicate_opened_during_evaluation_blocks_final_success(monkeypatch):
    gh = FakeGitHub([], [actor('alice', 1001)], store={'signatures': [{'id': 1001}]})
    original = gh.graphql
    def graphql(*args):
        gh.open_prs = [gh.get('/pulls/7'), dict(gh.get('/pulls/7'), number=8)]
        return original(*args)
    monkeypatch.setattr(gh, 'graphql', graphql)
    assert not cla.run(gh, 'pull_request_target', {'pull_request': {'number': 7}}).passed
    assert gh.statuses[-1]['state'] == 'failure'


def test_closing_duplicate_refreshes_the_remaining_pr(monkeypatch):
    gh = FakeGitHub([], [], store={'signatures': [{'id': 1001}]})
    closed = dict(gh.get('/pulls/7'), state='closed')
    remaining = dict(gh.get('/pulls/7'), number=8)
    gh.open_prs = [remaining]
    monkeypatch.setattr(gh, 'get', lambda path: closed if path.endswith('/7') else remaining)
    assert cla.run(gh, 'pull_request_target', {'pull_request': closed}).passed
    assert gh.statuses[-1]['state'] == 'success'


def test_metadata_change_during_cla_evaluation_stays_pending(monkeypatch):
    gh = FakeGitHub([], [actor('alice', 1001)], store={'signatures': [{'id': 1001}]})
    original = gh.graphql
    def changed(*args):
        gh.pr['body'] = 'Supersedes #12'
        return original(*args)
    monkeypatch.setattr(gh, 'graphql', changed)
    with pytest.raises(RuntimeError, match='metadata changed'):
        cla.run(gh, 'pull_request_target', {'pull_request': {'number': 7}})
    assert gh.statuses[-1]['state'] == 'pending'
