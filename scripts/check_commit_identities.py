#!/usr/bin/env python3
"""Fail when commits in a revision range carry a blocked identity.

Checks every commit's author and committer name/email plus each
``Co-authored-by:`` / ``Signed-off-by:`` trailer email against:

* hashed personal emails (``scripts/blocked_identity_hashes.txt``): emails are
  trimmed, lowercased, SHA-256 hashed and compared, so the list never
  republishes the addresses it guards;
* literal placeholder/tool identities that are not personal data
  (``mergetest``, ``test@local``, ``you@example.com``) and hostname-style
  auto-detected emails (``*.local``, ``*.localdomain``, ``*.(none)``);
* AI agents: an agent email in any field, an unambiguous agent name as author
  or committer (a person may be called Claude, so only names such as "Claude
  Code" or "Cursor Agent"), any agent name in a trailer, or an agent
  attribution line ("Generated with ...", session or share links) in the
  commit message or, with ``--event``, the pull request description. Agent
  identities live in ``.github/scripts/agent_identities.py``, shared with the
  CLA check. Commits carry
  the git identity of the person submitting them and nobody else's; human
  co-authors are allowed.

Findings name the commit, the field and the *kind* of match only; the
matched value is never printed. Exit 0 = clean, 1 = violations, 2 = error.

Usage: check_commit_identities.py [--range A..B | --base BRANCH] [--hash-file PATH] [--event PATH]
Default range is ``origin/<base>..HEAD`` with base from ``--base``,
``$GITHUB_BASE_REF``, or ``main``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_HASH_FILE = Path(__file__).with_name("blocked_identity_hashes.txt")
_AGENTS_FILE = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "agent_identities.py"
_spec = importlib.util.spec_from_file_location("agent_identities", _AGENTS_FILE)
agents = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agents)

HASHED_KIND = "blocked personal email (hashed)"
BLOCKED_NAMES = {"mergetest": "placeholder identity name 'mergetest'"}
BLOCKED_EMAILS = {
    "test@local": "placeholder email 'test@local'",
    "you@example.com": "placeholder email 'you@example.com'",
}
BLOCKED_LOCALPARTS = {"mergetest": "placeholder identity 'mergetest'"}
# Git's auto-detected "user@host" fallbacks leak machine hostnames.
HOSTNAME_SUFFIXES = (".local", ".localdomain", ".(none)")
HOSTNAME_KIND = "hostname-style auto-detected email"

AI_KIND = "AI agent identity"
AI_ATTRIBUTION_KIND = "AI agent attribution"
# Agents that commit or get credited as co-authors (agent_identities.py). GitHub
# App bots such as dependabot[bot] are not agents and stay allowed, and so do
# people with a work address at an AI company.
_AGENTS = (
    r"(claude|cursor|copilot|codex|chatgpt|openai|gemini|devin|jules|aider|cline|windsurf|coderabbit|greptile"
    r"|sourcery|tabnine|codeium|amazon q|kiro|qodo|openhands|sweep)\b"
    # A generic "AI"/"LLM" credits a tool only when it ends the phrase or a tool
    # word follows, so a person ("an AI consultant", "the LLM team") never trips it.
    r"|(an? )?(ai|llm)\b(?=[ \t-]*(assist\w*|models?|tools?|agents?|chat\w*|help|coding|code"
    r"|pair[ \t-]?programm\w*)\b|[ \t]*([.,;:!?)]|$))"
)
AI_ATTRIBUTION = re.compile(
    rf"^[ \t>*_-]*(generated|written|created|authored)[ \t]+(with|by|using)[ \t]+\[?({_AGENTS})"
    r"|🤖[ \t]*(generated|written|created|authored)\b|^[ \t]*claude-session[ \t]*:"
    # Agent session and share links.
    r"|\bclaude\.ai/(c|chat|share|code|new)\b|\bclaude\.com/(claude-)?code\b"
    r"|\b(chatgpt\.com|chat\.openai\.com)/(c|share)/",
    re.IGNORECASE | re.MULTILINE,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRAILER = re.compile(r"^[ \t]*(co-authored-by|signed-off-by)[ \t]*:(.*)$", re.IGNORECASE | re.MULTILINE)
_FIELD_SEP = "\x1f"


def normalise(email: str) -> str:
    return email.strip().strip("<>").strip().lower()


def email_digest(email: str) -> str:
    return hashlib.sha256(normalise(email).encode("utf-8")).hexdigest()


def load_hashes(path: Path) -> set[str]:
    digests: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _HEX64.match(line):
            raise ValueError(f"{path}:{lineno}: expected a 64-char lowercase hex SHA-256 digest")
        digests.add(line)
    return digests


def classify_email(email: str, hashes: set[str]) -> list[str]:
    value = normalise(email)
    if not value:
        return []
    kinds: list[str] = []
    if email_digest(value) in hashes:
        kinds.append(HASHED_KIND)
    if value in BLOCKED_EMAILS:
        kinds.append(BLOCKED_EMAILS[value])
    local = value.split("@", 1)[0]
    if local in BLOCKED_LOCALPARTS:
        kinds.append(BLOCKED_LOCALPARTS[local])
    if "@" in value and value.endswith(HOSTNAME_SUFFIXES):
        kinds.append(HOSTNAME_KIND)
    if agents.is_agent_email(value):
        kinds.append(AI_KIND)
    return kinds


def classify_name(name: str) -> list[str]:
    """Author or committer name: placeholders and unambiguous agent names."""
    kind = BLOCKED_NAMES.get(name.strip().lower())
    return ([kind] if kind else []) + ([AI_KIND] if agents.is_agent_author_name(name) else [])


def classify_trailer_name(name: str) -> list[str]:
    return [AI_KIND] if agents.is_agent_trailer_name(name) else []


def classify_text(text: str) -> list[str]:
    return [AI_ATTRIBUTION_KIND] if AI_ATTRIBUTION.search(text or "") else []


def trailers(body: str) -> list[tuple[str, str, str]]:
    """(key, name, email) for each Co-authored-by / Signed-off-by trailer."""
    found = []
    for key, value in _TRAILER.findall(body):
        match = re.search(r"<([^>]*)>", value)
        email = match.group(1) if match else value
        name = value[: match.start()] if match else ""
        found.append((key.lower(), name.strip(), email if "@" in email else ""))
    return found


def pr_body(event_path: Path) -> str:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    return ((event.get("pull_request") or {}).get("body")) or ""


def read_commits(rev_range: str) -> list[dict[str, str]]:
    fmt = _FIELD_SEP.join(["%H", "%an", "%ae", "%cn", "%ce", "%B"])
    proc = subprocess.run(
        ["git", "-c", "log.showSignature=false", "log", "-z", "--no-color", f"--format={fmt}", rev_range, "--"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git log failed for {rev_range!r}")
    commits = []
    for record in proc.stdout.split("\0"):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, an, ae, cn, ce, body = (record.split(_FIELD_SEP, 5) + [""] * 6)[:6]
        commits.append({"sha": sha, "an": an, "ae": ae, "cn": cn, "ce": ce, "body": body})
    return commits


def find_violations(commits: list[dict[str, str]], hashes: set[str]) -> list[tuple[str, str, str]]:
    violations = []
    for c in commits:
        short = c["sha"][:10]
        checks = [
            ("author name", classify_name(c["an"])),
            ("author email", classify_email(c["ae"], hashes)),
            ("committer name", classify_name(c["cn"])),
            ("committer email", classify_email(c["ce"], hashes)),
        ]
        for key, name, email in trailers(c["body"]):
            label = "Co-authored-by" if key == "co-authored-by" else "Signed-off-by"
            checks.append((f"{label} trailer name", classify_trailer_name(name)))
            checks.append((f"{label} trailer email", classify_email(email, hashes)))
        checks.append(("message", classify_text(c["body"])))
        for field, kinds in checks:
            violations.extend((short, field, kind) for kind in kinds)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--range", dest="rev_range", help="git revision range, e.g. origin/main..HEAD")
    parser.add_argument("--base", default=os.environ.get("GITHUB_BASE_REF") or "main", help="base branch for the default range")
    parser.add_argument("--hash-file", type=Path, default=DEFAULT_HASH_FILE, help="blocked email digest list")
    parser.add_argument("--event", type=Path, help="GitHub event JSON; also checks the pull request description")
    args = parser.parse_args(argv)
    rev_range = args.rev_range or f"origin/{args.base}..HEAD"

    try:
        hashes = load_hashes(args.hash_file)
        commits = read_commits(rev_range)
        description = pr_body(args.event) if args.event else ""
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"commit-identity: error: {exc}", file=sys.stderr)
        return 2

    violations = find_violations(commits, hashes)
    violations += [("PR", "description", kind) for kind in classify_text(description)]
    if not violations:
        print(f"commit-identity: {len(commits)} commit(s) in {rev_range} clean.")
        return 0
    print(f"commit-identity: blocked identities in {rev_range}:")
    for short, field, kind in violations:
        print(f"  {short}  {field}: {kind}")
    print(
        "Set an allowed identity (e.g. your GitHub noreply address), then rewrite only the\n"
        "commits listed above and force-push: `git rebase -i origin/<base>`, mark each listed\n"
        "commit `edit`, and at each stop run `git commit --amend --no-edit --reset-author`\n"
        "and `git rebase --continue`. Other people's commits keep their authors.\n"
        "Remove blocked Co-authored-by/Signed-off-by trailers and AI agent attribution lines\n"
        "with `git rebase -i` (reword) and from the pull request description. Commits carry\n"
        "only your own git identity: no AI agent co-authors or 'Generated with ...' lines."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
