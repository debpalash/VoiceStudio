#!/usr/bin/env python3
"""Contributor License Agreement check for pull requests.

Runs from .github/workflows/cla.yml on the base branch's copy of this file, so
pull-request code never executes with the write token. Standard library only.

A pull request passes when the person who opened it and every commit author and
co-author has signed .github/CLA-<version>.md, or is a maintainer or GitHub App
bot recognised by account ID. People are matched by GitHub account ID, never by
git name, so changing `user.name` cannot impersonate a signer. Authors whose
commit email is not linked to a GitHub account cannot be verified and fail the
check with instructions.

Each run rescans every comment on the pull request (or signing issue) and
records any valid signature not yet stored, so the next run picks up anything a
run cancelled while queued missed. Edited comments never count. The result is a
commit status named "CLA" on the head commit, which branch protection can
require. A maintainer can apply the `cla-override` label after reviewing a pull
request by hand, for example one that folds in commits from agent identities.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# cla.yml checks out only .github/scripts, so shared code lives beside this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_identities import AGENT_EMAILS

CLA_VERSION = "1.0"
SIGN_PHRASE = f"I have read the VoiceStudio CLA {CLA_VERSION} and I hereby sign it."
DOCUMENT_PATH = f".github/CLA-{CLA_VERSION}.md"
SIGNATURE_BRANCH = "cla-signatures"
SIGNATURE_PATH = f"signatures/v{CLA_VERSION}/cla.json"
STATUS_CONTEXT = "CLA"
# Issues carrying this label (only maintainers can apply labels) accept
# signatures from anyone, so past contributors can sign without a pull request.
SIGNING_LABEL = "cla"
OVERRIDE_LABEL = "cla-override"
COMMENT_MARKER = "<!-- voicestudio-cla -->"
MAX_COMMITS = 250
MAX_AUTHORS = 20  # authors (author plus co-authors) read per commit

# GitHub account IDs exempt from signing. IDs cannot be claimed by anyone else.
MAINTAINER_IDS = {4178343}  # debpalash
BOT_IDS = {
    49699333,  # dependabot[bot]
    41898282,  # github-actions[bot]
}
# Co-author identities that name a tool rather than a person. The human who
# submits the work signs for it (CLA section 5.5).
# GitHub App bots also commit as "<id>+<name>[bot]@users.noreply.github.com";
# a person cannot hold such a login, and the authenticated opener still signs.
TOOL_EMAILS = re.compile(
    rf"{AGENT_EMAILS.pattern}|^\d+\+[^@]*\[bot\]@users\.noreply\.github\.com$",
    re.I,
)
# A pull request that says it supersedes others must also be signed by their authors.
SUPERSEDES = re.compile(r"supersed", re.I)
PR_REF = re.compile(r"#(\d+)")
NOREPLY = re.compile(r"^(?:(\d+)\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$", re.I)
CO_AUTHOR = re.compile(r"^co-authored-by:\s*(.*?)\s*<([^>]*)>\s*$", re.I | re.M)


# ── Pure logic (unit-tested) ────────────────────────────────────────────


@dataclass(frozen=True)
class Person:
    login: str
    id: int


@dataclass
class Evaluation:
    unsigned: list[Person] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)  # git identities with no GitHub account

    @property
    def passed(self) -> bool:
        return not self.unsigned and not self.unknown


def is_sign_comment(body: str) -> bool:
    """True when a line of the comment is exactly the sign phrase (not a quote)."""
    target = " ".join(SIGN_PHRASE.lower().split())
    return any(" ".join(line.lower().split()) == target for line in (body or "").splitlines())


def is_valid_signature(comment: dict) -> bool:
    """A signing comment that was never edited after it was posted."""
    edited = comment.get("updated_at") and comment.get("updated_at") != comment.get("created_at")
    return is_sign_comment(comment.get("body")) and not edited


def superseded_numbers(body: str) -> list[int]:
    """Pull request numbers on lines of a description that say they are superseded."""
    found: list[int] = []
    for line in (body or "").splitlines():
        if SUPERSEDES.search(line):
            found += [n for n in map(int, PR_REF.findall(line)) if n not in found]
    return list(dict.fromkeys(found))


def signature(user: dict, comment: dict, repo_id, **where) -> dict:
    return {"login": user["login"], "id": int(user["id"]), **where, "comment_id": comment["id"],
            "signed_at": comment["created_at"], "version": CLA_VERSION, "repo_id": repo_id,
            "body_sha256": hashlib.sha256((comment.get("body") or "").encode()).hexdigest()}


def exempt(person: Person) -> bool:
    return person.id in MAINTAINER_IDS or person.id in BOT_IDS


def evaluate(opener: Person, opener_is_bot: bool, actors: list[dict], signed_ids: set[int]) -> Evaluation:
    """Decide who still has to sign.

    `actors` holds one entry per commit author or co-author:
    {"name", "email", "login" or None, "id" or None}, plus "trailer": True for
    co-authors parsed from a commit message, or {"unknown": text} for anything
    that must be reviewed by hand.
    """
    result = Evaluation()
    seen: set[int] = set()

    def require(person: Person) -> None:
        if person.id in seen or exempt(person):
            return
        seen.add(person.id)
        if person.id not in signed_ids:
            result.unsigned.append(person)

    if not opener_is_bot:
        require(opener)
    for actor in actors:
        if actor.get("unknown"):
            if actor["unknown"] not in result.unknown:
                result.unknown.append(actor["unknown"])
            continue
        email = (actor.get("email") or "").strip()
        if actor.get("id"):
            # GitHub verified this account; a person cannot hold a [bot] login.
            if not (actor.get("login") or "").lower().endswith("[bot]"):
                require(Person(actor["login"], int(actor["id"])))
            continue
        # A tool named in a co-author trailer is skipped: the person who
        # submits the work signs for it. An unlinked commit author with a tool
        # address is not, because anyone can commit under that address.
        if actor.get("trailer") and TOOL_EMAILS.match(email):
            continue
        # A commit author counts only through the account GitHub linked to it.
        # An unlinked no-reply address may name someone else's account, so it
        # stays unknown. Co-author trailers are self-declared either way (the
        # opener answers for them), so their no-reply account ID is taken as given.
        if actor.get("trailer") and (match := NOREPLY.match(email)) and match.group(1):
            require(Person(match.group(2), int(match.group(1))))
            continue
        if opener_is_bot and "[bot]" in email:
            continue
        identity = f"{actor.get('name') or '?'} <{email or 'no email'}>"
        if identity not in result.unknown:
            result.unknown.append(identity)
    return result


def co_authors(message: str) -> list[dict]:
    return [{"name": n, "email": e, "login": None, "id": None, "trailer": True}
            for n, e in CO_AUTHOR.findall(message or "")]


def add_signatures(store: dict, new: list[dict]) -> bool:
    """Append signatures whose account ID is not stored yet. Returns True if changed."""
    have = {s["id"] for s in store["signatures"]}
    added = [s for s in new if s["id"] not in have]
    store["signatures"].extend(added)
    return bool(added)


def empty_store() -> dict:
    return {"version": CLA_VERSION, "document": DOCUMENT_PATH, "signatures": []}


def _safe(text: str) -> str:
    """Render untrusted git identities as inert inline code."""
    return "`" + re.sub(r"[`\r\n]", "'", text)[:200] + "`"


def render_comment(evaluation: Evaluation, doc_url: str) -> str:
    if evaluation.passed:
        return f"{COMMENT_MARKER}\nAll contributors on this pull request have signed the VoiceStudio CLA. Thank you!"
    intro = (
        "Thank you for contributing to VoiceStudio. Before this pull request can merge, everyone who "
        f"contributed to it must sign the [Contributor License Agreement {CLA_VERSION}]({doc_url}) once. "
        "You keep your copyright; the agreement lets Yupcha Softwares Private Limited, the company that "
        "maintains VoiceStudio, ship your work in both the AGPL-3.0 app and commercial builds."
    )
    lines = [COMMENT_MARKER, intro]
    if evaluation.unsigned:
        lines += ["", "**Still to sign:** " + ", ".join(f"@{p.login}" for p in evaluation.unsigned), "",
                  "To sign, post this as a new comment on its own line:", "", "```text", SIGN_PHRASE, "```"]
    if evaluation.unknown:
        lines += ["", "**Commits we cannot link to a GitHub account:**", ""]
        lines += [f"- {_safe(identity)}" for identity in evaluation.unknown]
        fix = (
            "Add that email to your GitHub account (Settings → Emails), or rewrite the commits with "
            "an email that is on it (`git commit --amend --reset-author`, or an interactive rebase), "
            "then push. Each push re-runs this check."
        )
        lines += ["", fix]
    lines += ["", "Comment `recheck` to run the check again."]
    return "\n".join(lines)


# ── GitHub API ──────────────────────────────────────────────────────────


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object, dict]:
        url = path if path.startswith("http") else f"{self.api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None, dict(resp.headers)
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                payload = json.loads(raw) if raw else None
            except ValueError:
                payload = None
            return err.code, payload, dict(err.headers)

    def get(self, path: str) -> object:
        status, payload, _ = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} failed with {status}: {payload}")
        return payload

    def paginate(self, path: str, limit: int | None = None) -> list:
        items: list = []
        url = f"{self.api}{path}{'&' if '?' in path else '?'}per_page=100"
        while url:
            status, payload, headers = self.request("GET", url)
            if status != 200:
                raise RuntimeError(f"GET {url} failed with {status}: {payload}")
            items.extend(payload)
            if limit and len(items) >= limit:
                return items
            links = headers.get("Link") or headers.get("link") or ""
            url = next((part.split(";")[0].strip(" <>") for part in links.split(",") if 'rel="next"' in part), None)
        return items

    def graphql(self, query: str, variables: dict) -> dict:
        status, payload, _ = self.request("POST", "/graphql", {"query": query, "variables": variables})
        if status != 200 or payload.get("errors"):
            raise RuntimeError(f"GraphQL failed with {status}: {payload}")
        return payload["data"]


COMMITS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      commits(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { commit { oid authors(first: MAX_AUTHORS) {
          pageInfo { hasNextPage }
          nodes { name email user { login databaseId } }
        } } }
      }
    }
  }
}
""".replace("MAX_AUTHORS", str(MAX_AUTHORS))


def pr_actors(gh: GitHub, number: int) -> tuple[list[dict], int]:
    """Commit authors and co-authors of a pull request, and how many commits were read.

    Stops after MAX_COMMITS commits; the caller fails the check when it does.
    """
    owner, name = gh.repo.split("/")
    actors: list[dict] = []
    count, after = 0, None
    while True:
        data = gh.graphql(COMMITS_QUERY, {"owner": owner, "name": name, "number": number, "after": after})
        commits = data["repository"]["pullRequest"]["commits"]
        for node in commits["nodes"]:
            count += 1
            authors = node["commit"]["authors"]
            for author in authors["nodes"]:
                user = author.get("user") or {}
                actors.append({"name": author.get("name"), "email": author.get("email"),
                               "login": user.get("login"), "id": user.get("databaseId")})
            if (authors.get("pageInfo") or {}).get("hasNextPage"):
                # Unread co-authors could include someone who has not signed.
                actors.append({"unknown": f"commit {(node['commit'].get('oid') or '?')[:7]} has more than "
                                          f"{MAX_AUTHORS} authors; a maintainer must review it by hand"})
        if not commits["pageInfo"]["hasNextPage"] or count >= MAX_COMMITS:
            return actors, count
        after = commits["pageInfo"]["endCursor"]


def load_store(gh: GitHub) -> tuple[dict, str | None]:
    status, payload, _ = gh.request("GET", f"/repos/{gh.repo}/contents/{SIGNATURE_PATH}?ref={SIGNATURE_BRANCH}")
    if status == 404:
        return empty_store(), None
    if status != 200:
        raise RuntimeError(f"Could not read signatures ({status}): {payload}")
    return json.loads(base64.b64decode(payload["content"])), payload["sha"]


def ensure_branch(gh: GitHub) -> None:
    """Create the signature branch as an orphan commit if it does not exist yet."""
    status, _, _ = gh.request("GET", f"/repos/{gh.repo}/git/ref/heads/{SIGNATURE_BRANCH}")
    if status == 200:
        return
    readme = ("Signatures for the VoiceStudio Contributor License Agreement, written by "
              ".github/workflows/cla.yml. Do not edit by hand.\n")
    tree = gh.request("POST", f"/repos/{gh.repo}/git/trees", {"tree": [
        {"path": "README.md", "mode": "100644", "type": "blob", "content": readme}]})[1]
    commit = gh.request("POST", f"/repos/{gh.repo}/git/commits", {
        "message": "chore(cla): create signature store", "tree": tree["sha"], "parents": []})[1]
    status, payload, _ = gh.request("POST", f"/repos/{gh.repo}/git/refs",
                                    {"ref": f"refs/heads/{SIGNATURE_BRANCH}", "sha": commit["sha"]})
    if status not in (201, 422):  # 422: another run created it first
        raise RuntimeError(f"Could not create {SIGNATURE_BRANCH} ({status}): {payload}")


def save_signatures(gh: GitHub, new: list[dict], number: int) -> None:  # number: PR or issue
    """Write new signatures, retrying when another run updated the file first."""
    ensure_branch(gh)
    for attempt in range(6):
        store, sha = load_store(gh)
        if not add_signatures(store, new):
            return
        logins = ", ".join(f"@{s['login']}" for s in new)
        body = {"message": f"chore(cla): record signature for {logins} (#{number})", "branch": SIGNATURE_BRANCH,
                "content": base64.b64encode((json.dumps(store, indent=2) + "\n").encode()).decode()}
        if sha:
            body["sha"] = sha
        status, payload, _ = gh.request("PUT", f"/repos/{gh.repo}/contents/{SIGNATURE_PATH}", body)
        if status in (200, 201):
            return
        if status not in (409, 422):
            raise RuntimeError(f"Could not save signatures ({status}): {payload}")
        time.sleep(1 + attempt)
    raise RuntimeError("Could not save signatures after retries")


def upsert_comment(gh: GitHub, number: int, body: str, create: bool) -> None:
    for comment in gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments"):
        if COMMENT_MARKER in (comment.get("body") or "") and (comment.get("user") or {}).get("type") == "Bot":
            if comment["body"] != body:
                gh.request("PATCH", f"/repos/{gh.repo}/issues/comments/{comment['id']}", {"body": body})
            return
    if create:
        gh.request("POST", f"/repos/{gh.repo}/issues/{number}/comments", {"body": body})


def sign_on_issue(gh: GitHub, event: dict) -> int:
    """Record every valid signature on a maintainer-labelled signing issue.

    Rescans all comments, so signatures posted while another run was queued are
    not lost. Returns the number of new signatures.
    """
    issue = event.get("issue") or {}
    if SIGNING_LABEL not in {label.get("name") for label in issue.get("labels") or []}:
        return 0
    store, _ = load_store(gh)
    have = {int(s["id"]) for s in store["signatures"]}
    new, comment_ids = [], []
    for comment in gh.paginate(f"/repos/{gh.repo}/issues/{issue['number']}/comments"):
        user = comment.get("user") or {}
        if user.get("type") == "User" and int(user.get("id") or 0) not in have and is_valid_signature(comment):
            have.add(int(user["id"]))
            new.append(signature(user, comment, (event.get("repository") or {}).get("id"), issue=issue["number"]))
            comment_ids.append(comment["id"])
    if new:
        save_signatures(gh, new, issue["number"])
        for comment_id in comment_ids:
            gh.request("POST", f"/repos/{gh.repo}/issues/comments/{comment_id}/reactions", {"content": "+1"})
    return len(new)


def set_status(gh: GitHub, sha: str, state: str, description: str, doc_url: str | None = None) -> None:
    """Require GitHub to acknowledge each transition of the merge gate."""
    body = {"state": state, "context": STATUS_CONTEXT, "description": description[:140]}
    if doc_url:
        body["target_url"] = doc_url
    status, _, _ = gh.request("POST", f"/repos/{gh.repo}/statuses/{sha}", body)
    if status != 201:
        raise RuntimeError(f"Could not write CLA status ({status})")


def run(gh: GitHub, event_name: str, event: dict, server_url: str = "https://github.com") -> Evaluation:
    event_pr = event.get("pull_request") or {}
    number = (event_pr or event.get("issue") or {}).get("number")
    # On PR events the payload supplies the head even if the first API read
    # fails. Comment events need the PR lookup before its head is available.
    event_sha = (event_pr.get("head") or {}).get("sha")
    pending_sha = None
    if event_sha and event_pr.get("state", "open") == "open":
        set_status(gh, event_sha, "pending", "Checking contributor signatures")
        pending_sha = event_sha
    pr = gh.get(f"/repos/{gh.repo}/pulls/{number}")
    if pr.get("state") != "open":
        return Evaluation()
    if pr["head"]["sha"] != pending_sha:
        set_status(gh, pr["head"]["sha"], "pending", "Checking contributor signatures")
    opener = Person(pr["user"]["login"], int(pr["user"]["id"]))
    opener_is_bot = pr["user"].get("type") == "Bot" and opener.id in BOT_IDS
    doc_url = f"{server_url}/{gh.repo}/blob/{pr['base']['repo']['default_branch']}/{DOCUMENT_PATH}"
    if OVERRIDE_LABEL in {label.get("name") for label in pr.get("labels") or []}:
        set_status(gh, pr["head"]["sha"], "success", "CLA reviewed by a maintainer", doc_url)
        return Evaluation()

    actors, count = pr_actors(gh, number)
    overflow = [number] if count >= MAX_COMMITS else []
    # A superseded pull request's opener, commit authors and co-authors sign too.
    for ref in superseded_numbers(pr.get("body")):
        if ref == number:
            continue
        status, other, _ = gh.request("GET", f"/repos/{gh.repo}/pulls/{ref}")
        if status == 404:
            continue  # an issue or a missing number, not a pull request
        if status != 200:
            raise RuntimeError(f"Could not read superseded PR #{ref} ({status})")
        if (other.get("user") or {}).get("type") == "User":
            actors.append({"name": other["user"]["login"], "email": "",
                           "login": other["user"]["login"], "id": other["user"]["id"]})
        more, more_count = pr_actors(gh, ref)
        actors += more
        if more_count >= MAX_COMMITS:
            overflow.append(ref)
    store, _ = load_store(gh)
    signed_ids = {int(s["id"]) for s in store["signatures"]}
    evaluation = evaluate(opener, opener_is_bot, actors, signed_ids)

    # Record signatures from any comment by someone who still has to sign.
    pending = {p.id: p for p in evaluation.unsigned}
    new = []
    for comment in gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments"):
        user = comment.get("user") or {}
        if user.get("id") in pending and is_valid_signature(comment):
            pending.pop(user["id"])
            new.append(signature(user, comment, pr["base"]["repo"]["id"], pull_request=number))
    if new:
        save_signatures(gh, new, number)
        evaluation = evaluate(opener, opener_is_bot, actors, signed_ids | {s["id"] for s in new})

    for ref in overflow:
        evaluation.unknown.append(f"#{ref} has more than {MAX_COMMITS} commits; a maintainer can review them "
                                  f"and apply `{OVERRIDE_LABEL}`")
    description = ("All contributors have signed the CLA" if evaluation.passed
                   else f"{len(evaluation.unsigned) + len(evaluation.unknown)} contributor(s) still need to sign")
    upsert_comment(gh, number, render_comment(evaluation, doc_url), create=not evaluation.passed)
    set_status(gh, pr["head"]["sha"], "success" if evaluation.passed else "failure", description, doc_url)
    return evaluation


def main() -> int:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    if event_name == "issue_comment":
        if not (event.get("issue") or {}).get("pull_request"):
            print("CLA: recorded", sign_on_issue(gh, event), "signature(s) from the issue")
            return 0
    evaluation = run(gh, event_name, event, os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    print("CLA:", "passed" if evaluation.passed else f"waiting on {evaluation.unsigned} {evaluation.unknown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
