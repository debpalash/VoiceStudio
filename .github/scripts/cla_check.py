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
run cancelled while queued missed. Edited comments never count; the check
replies once to a comment that looks like a signature but cannot be recorded,
saying why. The result is a
commit status named "CLA" on the head commit, which branch protection can
require. A maintainer can apply the `cla-override` label after reviewing a pull
request by hand, for example one that folds in commits from agent identities.
The `cla-signed` label follows the result so people can filter on it: present
only while signatures clear a pull request, when the check's comment also links
the agreement and each signature. Neither affects the gate.

While the person who opened a pull request has not signed, the check also holds
it (see `gate_decision`): it converts the pull request to a draft, which the
review bots skip, and says so in its comment. CI skips its heavy jobs for the
same reason (.github/scripts/ci_scope.py). Once they sign, the check marks the
pull request ready for review if it drafted it and re-runs the CI it skipped.
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
NEAR_MISS_MARKER = "<!-- voicestudio-cla-near-miss:{} -->"
NEAR_MISS_ID = re.compile(r"<!-- voicestudio-cla-near-miss:(\d+) -->")
# Issues carrying this label (only maintainers can apply labels) accept
# signatures from anyone, so past contributors can sign without a pull request.
SIGNING_LABEL = "cla"
OVERRIDE_LABEL = "cla-override"
SIGNED_LABEL = "cla-signed"  # written by this check, never read as an input
COMMENT_MARKER = "<!-- voicestudio-cla -->"
MAX_OPEN_PRS = 1000  # reaching the cap is incomplete evidence, so fail closed
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
RECORDED_LOGIN = re.compile(r"@([A-Za-z0-9-]+)")
CO_AUTHOR = re.compile(r"^co-authored-by:\s*(.*?)\s*<([^>]*)>\s*$", re.I | re.M)
# The CLA hold on an unsigned opener's pull request, recorded in the check's comment:
# drafted (this check made, or is making, it a draft), waiting (it was already a
# draft, so it stays one), restarting (the opener signed; restarting skipped CI
# failed and is retried), lifted (done). Everything from the marker on is the
# hold's section. Each run re-derives the next step from the signatures, the
# pull request's draft flag and this marker, and records an intent before acting
# on it, so a failed write is retried by the next run.
GATE_PREFIX = "<!-- voicestudio-cla-gate:"
GATE_STATE = re.compile(r"<!-- voicestudio-cla-gate:(drafted|waiting|restarting|lifted) -->")
HELD = {"drafted", "waiting"}
OWES_RESTART = HELD | {"restarting"}
AGENT_GUIDE_PATH = ".github/AGENT_PR_GUIDE.md"
# Pull-request workflows whose heavy jobs ci_scope.py skips for an unsigned opener.
GATED_WORKFLOWS = {"ci.yml", "mobile.yml", "security.yml", "install-smoke.yml",
                   "cosyvoice-dependencies.yml", "nix.yml"}
DRAFT_MUTATION = ("mutation($id: ID!) { convertPullRequestToDraft(input: {pullRequestId: $id}) "
                  "{ pullRequest { isDraft } } }")
READY_MUTATION = ("mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
                  "{ pullRequest { isDraft } } }")


# ── Pure logic (unit-tested) ────────────────────────────────────────────


@dataclass(frozen=True)
class Person:
    login: str
    id: int


@dataclass
class Evaluation:
    unsigned: list[Person] = field(default_factory=list)
    signed: list[Person] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)  # git identities with no GitHub account

    blockers: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.unsigned and not self.unknown and not self.blockers


def _normal(text: str) -> str:
    """Case, spacing and a final full stop do not change what was signed."""
    return " ".join(text.lower().split()).removesuffix(".")


def is_sign_comment(body: str) -> bool:
    """True when a line of the comment is exactly the sign phrase (not a quote)."""
    target = _normal(SIGN_PHRASE)
    return any(_normal(line) == target for line in (body or "").splitlines())


def _edited(comment: dict) -> bool:
    return bool(comment.get("updated_at") and comment.get("updated_at") != comment.get("created_at"))


def is_valid_signature(comment: dict) -> bool:
    """A signing comment that was never edited after it was posted."""
    return is_sign_comment(comment.get("body")) and not _edited(comment)


# People who explain how to sign, quoting the line; they never get a near-miss reply.
STAFF_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def near_miss(comment: dict) -> str | None:
    """Why a comment that looks like a signature cannot be recorded, or None."""
    if comment.get("author_association") in STAFF_ASSOCIATIONS:
        return None
    if int((comment.get("user") or {}).get("id") or 0) in MAINTAINER_IDS:
        return None
    body = comment.get("body") or ""
    text = " ".join(body.lower().split())
    if "hereby sign" not in text or "voicestudio cla" not in text:
        return None
    if not is_sign_comment(body):
        return "it does not contain the exact line on a line of its own"
    if _edited(comment):
        return "it was edited after it was posted, and an edited comment cannot be recorded"
    return None


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
        (result.signed if person.id in signed_ids else result.unsigned).append(person)

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


def gate_decision(held: bool, is_draft: bool, previous: str | None,
                  newly_signed: bool = False) -> tuple[str | None, list[str]]:
    """The hold's target state and the steps to reach it, in order.

    `held` is True while the opener has not signed. A pull request its author
    made a draft stays a draft after signing; one this check drafted is marked
    ready. Skipped CI is restarted until a restart succeeds, and also when the
    opener signed in this very run (CI may have skipped before a hold was
    recorded). Pull requests that were never held are never touched.
    """
    if held:
        if is_draft:
            return ("drafted" if previous == "drafted" else "waiting"), []
        return "drafted", ["draft"]  # also when the opener clicked "Ready for review"
    steps = ["ready"] if previous == "drafted" and is_draft else []
    if previous in OWES_RESTART or newly_signed:
        steps.append("restart")
    return ("lifted" if previous else None), steps


def gate_state(comment: dict | None) -> str | None:
    match = GATE_STATE.search((comment or {}).get("body") or "")
    return match.group(1) if match else None


def agent_prompt(guide_url: str) -> str:
    return (f"Before changing anything, read {guide_url} and follow every step in it. "
            "Finish every step before you ask for review.")


def with_gate(body: str, state: str | None, guide_url: str) -> str:
    """Replace the hold's section of the check's comment; no state leaves the body unchanged."""
    body = body.split(GATE_PREFIX, 1)[0].rstrip()
    if state is None:
        return body
    lines = [body, "", f"{GATE_PREFIX}{state} -->"]
    if state == "lifted":
        return "\n".join(lines + ["The opener has signed: CI and automated reviews are no longer on hold."])
    if state == "restarting":
        retry = ("The opener has signed, but CI could not be restarted automatically yet. "
                 "Comment `recheck` to try again.")
        return "\n".join(lines + [retry])
    held = ("**CI and automated reviews wait for your signature.** Signing takes a minute; once you sign, "
            "CI starts automatically.")
    if state == "drafted":
        held = held[:-1] + " and this pull request is marked ready for review. Until then it stays a draft."
    else:
        held += " You made this pull request a draft, so it stays one until you mark it ready."
    return "\n".join(lines + [held, "", "<details><summary>Using an AI coding agent? Give it this</summary>",
                               "", "```text", agent_prompt(guide_url), "```", "", "</details>"])


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


def record_lines(text: str, account_id: int) -> tuple[int, int] | None:
    """First and last line (1-based) of an account's entry in the signature file."""
    lines = text.splitlines()
    for at, line in enumerate(lines):
        if line.strip().rstrip(",") == f'"id": {account_id}':
            start = next((i for i in range(at, -1, -1) if lines[i].strip() == "{"), None)
            end = next((i for i in range(at, len(lines)) if lines[i].strip() in ("}", "},")), None)
            return None if start is None or end is None else (start + 1, end + 1)
    return None


def render_comment(evaluation: Evaluation, doc_url: str, signatures: dict[str, str | None] | None = None) -> str:
    """`signatures` maps each signer's login to the link to their signature."""
    if evaluation.passed:
        passed = f"{COMMENT_MARKER}\nAll contributors on this pull request have signed the VoiceStudio CLA. Thank you!"
        if not signatures:
            return passed
        # Plain logins: this comment reports a result, so it should not notify anyone.
        lines = [f"- {_safe(login)}: [signature record]({url})" if url else f"- {_safe(login)}: signature on record"
                 for login, url in signatures.items()]
        return "\n".join([passed, "", f"Agreement: [VoiceStudio CLA {CLA_VERSION}]({doc_url})", "", *lines])
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
    if evaluation.blockers:
        lines += ["", "**Checks to resolve:**", *[f"- {reason}" for reason in evaluation.blockers]]
    lines += ["", "Comment `recheck` to run the check again."]
    return "\n".join(lines)


def render_near_miss(login: str, comment_id: int, reason: str) -> str:
    return "\n".join([NEAR_MISS_MARKER.format(comment_id),
                      (f"@{login}, thank you. Your comment could not be recorded because {reason}. "
                       "Please post a new comment, and do not edit it, with this line:"),
                      "", "```text", SIGN_PHRASE, "```"])


# ── GitHub API ──────────────────────────────────────────────────────────


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        """Keep the API details and the signature commits already looked up."""
        self.token, self.repo, self.api = token, repo, api.rstrip("/")
        self.signature_commits: dict[str, str] = {}  # login -> commit that first recorded it

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


def find_comment(gh: GitHub, number: int) -> dict | None:
    for comment in gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments"):
        if COMMENT_MARKER in (comment.get("body") or "") and (comment.get("user") or {}).get("type") == "Bot":
            return comment
    return None


_LOOKUP = object()


def upsert_comment(gh: GitHub, number: int, body: str, create: bool, existing=_LOOKUP) -> bool:
    """Write the check's comment. Returns False only when GitHub rejected the write."""
    comment = find_comment(gh, number) if existing is _LOOKUP else existing
    if comment:
        if comment["body"] == body:
            return True
        status, _, _ = gh.request("PATCH", f"/repos/{gh.repo}/issues/comments/{comment['id']}", {"body": body})
    elif create:
        status, _, _ = gh.request("POST", f"/repos/{gh.repo}/issues/{number}/comments", {"body": body})
    else:
        return True
    if status >= 300:
        print(f"CLA: could not write the comment ({status})")
        return False
    return True


def scan_comments(gh: GitHub, number: int, repo_id, where: dict,
                  signers: set[int] | None = None) -> tuple[list[dict], list[int]]:
    """Collect and save new signatures from an issue's or pull request's comments.

    `signers` limits signatures to those account IDs (a pull request records
    only the people it waits on). Replies once to each comment that looks like a
    signature but cannot be recorded, saying why. Returns (signatures, IDs of
    the comments recorded).
    """
    have = {int(s["id"]) for s in load_store(gh)[0]["signatures"]}
    comments = gh.paginate(f"/repos/{gh.repo}/issues/{number}/comments")
    answered = {int(found) for c in comments if (c.get("user") or {}).get("type") == "Bot"
                for found in NEAR_MISS_ID.findall(c.get("body") or "")}
    new, recorded = [], []
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("type") != "User":
            continue
        uid = int(user.get("id") or 0)
        if uid in have or (signers is not None and uid not in signers):
            continue
        if is_valid_signature(comment):
            have.add(uid)
            new.append(signature(user, comment, repo_id, **where))
            recorded.append(comment["id"])
        elif comment["id"] not in answered and (reason := near_miss(comment)):
            gh.request("POST", f"/repos/{gh.repo}/issues/{number}/comments",
                       {"body": render_near_miss(user["login"], comment["id"], reason)})
    if new:
        save_signatures(gh, new, number)
    return new, recorded


def sign_on_issue(gh: GitHub, event: dict, signer_ids: set[int] | None = None) -> int:
    """Record every valid signature on a maintainer-labelled signing issue.

    Rescans all comments, so signatures posted while another run was queued are
    not lost. Returns the number of new signatures and adds the account IDs of
    everyone who signed on this issue, now or before, to `signer_ids`: a re-run
    after a failed recheck must still release their pull requests.
    """
    issue = event.get("issue") or {}
    if SIGNING_LABEL not in {label.get("name") for label in issue.get("labels") or []}:
        return 0
    new, recorded = scan_comments(gh, issue["number"], (event.get("repository") or {}).get("id"),
                                  {"issue": issue["number"]})
    for comment_id in recorded:
        gh.request("POST", f"/repos/{gh.repo}/issues/comments/{comment_id}/reactions", {"content": "+1"})
    if signer_ids is not None:
        signer_ids.update(int(s["id"]) for s in new)
        signer_ids.update(int(s["id"]) for s in load_store(gh)[0]["signatures"] if s.get("issue") == issue["number"])
    return len(new)


def recheck_open_prs_of(gh: GitHub, signer_ids: set[int], server_url: str) -> list[int]:
    """Check the open pull requests of people who signed on an issue that the check has
    not cleared yet, so signing on the signing issue lifts their hold too. Returns the
    numbers checked."""
    if not signer_ids:
        return []
    checked = []
    for pr in gh.paginate(f"/repos/{gh.repo}/pulls?state=open"):
        labels = {label.get("name") for label in pr.get("labels") or []}
        if int((pr.get("user") or {}).get("id") or 0) in signer_ids and not labels & {SIGNED_LABEL, OVERRIDE_LABEL}:
            try:
                run(gh, "pull_request_target", {"pull_request": {"number": pr["number"], "head": pr["head"]}}, server_url)
                checked.append(pr["number"])
            except (RuntimeError, OSError) as err:
                print(f"CLA: could not recheck #{pr['number']}: {err}; comment recheck there")
    return checked


def set_status(gh: GitHub, sha: str, state: str, description: str, doc_url: str | None = None,
               *, context: str = STATUS_CONTEXT) -> None:
    """Require GitHub to acknowledge each transition of the merge gate."""
    body = {"state": state, "context": context, "description": description[:140]}
    if doc_url:
        body["target_url"] = doc_url
    status, _, _ = gh.request("POST", f"/repos/{gh.repo}/statuses/{sha}", body)
    if status != 201:
        raise RuntimeError(f"Could not write {context} status ({status})")


def signature_permalink(gh: GitHub, server_url: str, record: dict | None) -> str | None:
    """Link to a signature's entry in the commit that recorded it, which never changes.

    The signing comment can be edited or deleted, and the entry holds its ID and
    hash. None when it cannot be found: a missing link must not affect the gate.
    """
    login = ((record or {}).get("login") or "").lower()
    try:
        if login not in gh.signature_commits:
            # Newest first, so the oldest commit naming a login wins; a retried
            # save can name someone an earlier run already stored.
            for commit in gh.paginate(f"/repos/{gh.repo}/commits?sha={SIGNATURE_BRANCH}&path={SIGNATURE_PATH}"):
                first_line = commit["commit"]["message"].split("\n", 1)[0]
                for name in RECORDED_LOGIN.findall(first_line):
                    gh.signature_commits[name.lower()] = commit["sha"]
        sha = gh.signature_commits.get(login)
        if not sha:
            return None
        status, payload, _ = gh.request("GET", f"/repos/{gh.repo}/contents/{SIGNATURE_PATH}?ref={sha}")
        if status != 200:
            return None
        lines = record_lines(base64.b64decode(payload["content"]).decode(), int(record["id"]))
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as err:
        print(f"CLA: could not link the signature of {login}: {err}")
        return None
    return f"{server_url}/{gh.repo}/blob/{sha}/{SIGNATURE_PATH}#L{lines[0]}-L{lines[1]}" if lines else None


def document_permalink(gh: GitHub, fallback: str, server_url: str) -> str:
    """Link to the agreement at the commit that last changed it, which stays put across runs."""
    try:
        status, commits, _ = gh.request("GET", f"/repos/{gh.repo}/commits?path={DOCUMENT_PATH}&per_page=1")
    except OSError:
        return fallback
    if status != 200 or not commits:
        return fallback
    return f"{server_url}/{gh.repo}/blob/{commits[0]['sha']}/{DOCUMENT_PATH}"


def sync_signed_label(gh: GitHub, pr: dict, earned: bool) -> None:
    """Make `cla-signed` match the result. The status is the gate, so a failed write only warns.

    GITHUB_TOKEN label writes start no workflow run, so this cannot re-trigger the check.
    """
    if earned == (SIGNED_LABEL in {label.get("name") for label in pr.get("labels") or []}):
        return
    issue = f"/repos/{gh.repo}/issues/{pr['number']}/labels"
    try:
        if earned:
            status, _, _ = gh.request("POST", issue, {"labels": [SIGNED_LABEL]})
        else:
            status, _, _ = gh.request("DELETE", f"{issue}/{SIGNED_LABEL}")
    except OSError as err:
        print(f"CLA: could not update {SIGNED_LABEL}: {err}")
        return
    if status >= 400 and not (status == 404 and not earned):
        print(f"CLA: could not update {SIGNED_LABEL} ({status})")


def apply_gate(gh: GitHub, pr: dict, held: bool, previous: str | None, write_comment,
               newly_signed: bool = False) -> str | None:
    """Move the hold as `gate_decision` says and write the comment. Returns the state recorded.

    `write_comment(state)` writes the comment for a state and returns whether
    GitHub accepted it. Every write that can fail is ordered so the next run
    retries it: a hold records `drafted` before converting the pull request (a
    lost marker would make the draft look like the author's own), and a release
    marks it ready and restarts CI before recording the result. The `CLA`
    status stays the merge gate, so a failed step only warns.
    """
    state, steps = gate_decision(held, bool(pr.get("draft")), previous, newly_signed)
    if held:
        if write_comment(state) and steps:
            _mutate(gh, pr, DRAFT_MUTATION, "convert to draft")  # on failure `drafted` makes the next run retry
        return state
    if "ready" in steps and not _mutate(gh, pr, READY_MUTATION, "mark ready"):
        write_comment(previous)
        return previous  # still `drafted`: the next run marks it ready, then restarts CI
    if "restart" in steps:
        try:
            restarted_all = rerun_skipped_ci(gh, pr["head"]["sha"])[1]
        except (OSError, RuntimeError, KeyError, TypeError, ValueError) as err:
            print(f"CLA: could not restart CI: {err}")
            restarted_all = False  # stays owed; the next run retries
        if not restarted_all:
            state = "restarting"
    write_comment(state)
    return state


def _mutate(gh: GitHub, pr: dict, mutation: str, what: str) -> bool:
    try:
        gh.graphql(mutation, {"id": pr["node_id"]})
    except (RuntimeError, OSError, KeyError) as err:
        print(f"CLA: could not {what}: {err}")
        return False
    return True


def rerun_skipped_ci(gh: GitHub, sha: str) -> tuple[list[int], bool]:
    """Re-run the newest finished pull-request run of each gated workflow at `sha`.

    Only successful runs: a run waiting for approval (action_required) runs in
    full once approved, and a failed one needs a push, so neither is re-run here.
    A re-run keeps its original event, so a fork's run still gets only a
    read-only token. Returns the run IDs re-run, and False when a restart is
    still owed (listing or re-run rejected, or a skipped run not finished yet).
    """
    status, payload, _ = gh.request("GET", f"/repos/{gh.repo}/actions/runs?head_sha={sha}&event=pull_request&per_page=100")
    if status != 200 or not isinstance(payload, dict):
        print(f"CLA: could not list CI runs to restart ({status})")
        return [], False
    newest: dict[str, dict] = {}
    for item in payload.get("workflow_runs") or []:
        name = (item.get("path") or "").split("@", 1)[0].rsplit("/", 1)[-1]
        if name in GATED_WORKFLOWS and item.get("head_sha") == sha and item["id"] > newest.get(name, {}).get("id", 0):
            newest[name] = item
    restarted, done = [], True
    for item in newest.values():
        if int(item.get("run_attempt") or 1) > 1:
            continue  # already re-run (by an earlier check or a maintainer)
        if item.get("status") != "completed":
            print(f"CLA: CI run {item['id']} has not finished; restart it later")
            done = False
            continue
        if item.get("conclusion") != "success":
            continue
        status, _, _ = gh.request("POST", f"/repos/{gh.repo}/actions/runs/{item['id']}/rerun")
        if status == 201:
            restarted.append(item["id"])
        else:
            print(f"CLA: could not restart CI run {item['id']} ({status})")
            done = False
    return restarted, done


def open_prs_at_head(gh: GitHub, sha: str) -> list[dict]:
    """A commit status is shared by every PR at a SHA; require a unique owner."""
    pulls = gh.paginate(f"/repos/{gh.repo}/pulls?state=open", limit=MAX_OPEN_PRS)
    if len(pulls) >= MAX_OPEN_PRS:
        raise RuntimeError("Open PR listing reached its safety limit; cannot prove head uniqueness")
    if any(not isinstance(pr, dict) or not isinstance(pr.get("number"), int)
           or pr.get("state") != "open" or not (pr.get("head") or {}).get("sha") for pr in pulls):
        raise RuntimeError("Incomplete open PR listing; cannot prove head uniqueness")
    return [pr for pr in pulls if pr["head"]["sha"] == sha]


def _evaluation_snapshot(pr: dict) -> tuple:
    return (pr.get("state"), pr["head"]["sha"], pr["base"].get("sha"),
            pr["user"]["id"], pr["user"].get("type"), pr.get("body") or "",
            sorted(label.get("name", "") for label in pr.get("labels") or []
                   if label.get("name") != SIGNED_LABEL))


def confirm_current_pr(gh: GitHub, pr: dict) -> None:
    """Do not approve a PR whose eligibility inputs changed during API reads."""
    latest = gh.get(f"/repos/{gh.repo}/pulls/{pr['number']}")
    if _evaluation_snapshot(latest) != _evaluation_snapshot(pr):
        raise RuntimeError("PR metadata changed during CLA evaluation; recheck")


def head_blockers(gh: GitHub, pr: dict) -> list[str]:
    """Refuse even an override when a sibling PR could replace this SHA status."""
    peers = open_prs_at_head(gh, pr["head"]["sha"])
    numbers = sorted({peer["number"] for peer in peers})
    if pr["number"] not in numbers:
        raise RuntimeError("Pull request changed while checking its head; recheck")
    if len(numbers) > 1:
        refs = ", ".join(f"#{number}" for number in numbers)
        return [f"Open pull requests {refs} share the same head commit. Close duplicates, then recheck the remaining pull request."]
    return []


def run(gh: GitHub, event_name: str, event: dict, server_url: str = "https://github.com") -> Evaluation:
    """Check one pull request, record new signatures, and set its status, comment and label."""
    event_pr = event.get("pull_request") or {}
    number = (event_pr or event.get("issue") or {}).get("number")
    # On PR events the payload supplies the head even if the first API read
    # fails. Comment events need the PR lookup before its head is available.
    event_sha = (event_pr.get("head") or {}).get("sha")
    pending_sha = None
    if event_sha:
        set_status(gh, event_sha, "pending", "Checking contributor signatures")
        pending_sha = event_sha
    pr = gh.get(f"/repos/{gh.repo}/pulls/{number}")
    if pr.get("state") != "open":
        # Closing a duplicate must clear the shared SHA's rejection once the
        # survivor qualifies. Closed comment events use the same safe refresh.
        peers = open_prs_at_head(gh, pr["head"]["sha"])
        if not peers:
            return Evaluation()
        number = peers[0]["number"]
        pr = gh.get(f"/repos/{gh.repo}/pulls/{number}")
    if pr["head"]["sha"] != pending_sha:
        set_status(gh, pr["head"]["sha"], "pending", "Checking contributor signatures")
    opener = Person(pr["user"]["login"], int(pr["user"]["id"]))
    opener_is_bot = pr["user"].get("type") == "Bot" and opener.id in BOT_IDS
    doc_url = f"{server_url}/{gh.repo}/blob/{pr['base']['repo']['default_branch']}/{DOCUMENT_PATH}"
    guide_url = f"{server_url}/{gh.repo}/blob/{pr['base']['repo']['default_branch']}/{AGENT_GUIDE_PATH}"
    # Only a person is held; bots keep today's behaviour whatever the result.
    holdable = pr["user"].get("type") == "User"
    existing = find_comment(gh, number)
    previous = gate_state(existing)
    blockers = head_blockers(gh, pr)
    if blockers:
        evaluation = Evaluation(blockers=blockers)
        upsert_comment(gh, number, with_gate(render_comment(evaluation, doc_url), previous, guide_url),
                       create=True, existing=existing)
        set_status(gh, pr["head"]["sha"], "failure", "Close duplicate pull requests sharing this head", doc_url)
        sync_signed_label(gh, pr, False)
        return evaluation
    if OVERRIDE_LABEL in {label.get("name") for label in pr.get("labels") or []}:
        confirm_current_pr(gh, pr)
        apply_gate(gh, pr, False, previous, lambda state: upsert_comment(
            gh, number, with_gate(existing["body"], state, guide_url), create=False, existing=existing)
            if existing else True)
        set_status(gh, pr["head"]["sha"], "success", "CLA reviewed by a maintainer", doc_url)
        sync_signed_label(gh, pr, False)  # a maintainer's review is not a signature
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
    new, _ = scan_comments(gh, number, pr["base"]["repo"]["id"], {"pull_request": number},
                           signers={p.id for p in evaluation.unsigned})
    new_ids = {int(s["id"]) for s in new}

    confirm_current_pr(gh, pr)
    blockers = head_blockers(gh, pr)
    if evaluation.unsigned:
        # Another run (the signing issue's recheck queues separately) may have recorded
        # signatures meanwhile: re-read just before writing, so a stale run never
        # writes a failure or a hold over a signed result.
        store = {"signatures": [*load_store(gh)[0]["signatures"], *store["signatures"]]}
        signed_ids |= {int(s["id"]) for s in store["signatures"]}
    evaluation = evaluate(opener, opener_is_bot, actors, signed_ids | new_ids)
    for ref in overflow:
        evaluation.unknown.append(f"#{ref} has more than {MAX_COMMITS} commits; a maintainer can review them "
                                  f"and apply `{OVERRIDE_LABEL}`")
    evaluation.blockers.extend(blockers)
    description = ("All contributors have signed the CLA" if evaluation.passed
                   else f"{len(evaluation.unsigned) + len(evaluation.unknown)} contributor(s) still need to sign")
    if evaluation.blockers:
        description = "Close duplicate pull requests sharing this head"
    cleared = evaluation.passed and bool(evaluation.signed)
    held = holdable and any(p.id == opener.id for p in evaluation.unsigned)
    if cleared:
        records = {int(s["id"]): s for s in [*store["signatures"], *new]}
        signatures = {p.login: signature_permalink(gh, server_url, records.get(p.id)) for p in evaluation.signed}
        body = render_comment(evaluation, document_permalink(gh, doc_url, server_url), signatures)
    else:
        body = render_comment(evaluation, doc_url)
    # Exempt-only pull requests pass with no signature to report.
    apply_gate(gh, pr, held, previous, lambda state: upsert_comment(
        gh, number, with_gate(body, state, guide_url), create=cleared or not evaluation.passed, existing=existing),
        newly_signed=holdable and opener.id in new_ids)
    set_status(gh, pr["head"]["sha"], "success" if evaluation.passed else "failure", description, doc_url)
    sync_signed_label(gh, pr, cleared)
    return evaluation


def needs_hold_check(gh: GitHub, event: dict) -> bool:
    """On "Ready for review", only an unsigned human opener needs the full check (which
    drafts the pull request again). Exempt and bot openers cost no API call, a signed
    one costs the one signature read; neither rewrites the head's `CLA` status."""
    user = (event.get("pull_request") or {}).get("user") or {}
    if user.get("type") != "User" or not isinstance(user.get("id"), int) or exempt(Person(user.get("login", ""), user["id"])):
        return False
    try:
        signed = {int(s["id"]) for s in load_store(gh)[0]["signatures"]}
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as err:
        print(f"CLA: could not read signatures: {err}")
        return True  # let the full check decide (and fail visibly) rather than skip the hold
    return user["id"] not in signed


def main() -> int:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    if event_name == "issue_comment":
        if not (event.get("issue") or {}).get("pull_request"):
            signers: set[int] = set()
            print("CLA: recorded", sign_on_issue(gh, event, signers), "signature(s) from the issue")
            checked = recheck_open_prs_of(gh, signers, os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
            if checked:
                print("CLA: rechecked", ", ".join(f"#{n}" for n in checked))
            return 0
    if event.get("action") == "ready_for_review" and not needs_hold_check(gh, event):
        print("CLA: ready for review; nothing to hold")
        return 0
    evaluation = run(gh, event_name, event, os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    print("CLA:", "passed" if evaluation.passed else f"waiting on {evaluation.unsigned} {evaluation.unknown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
