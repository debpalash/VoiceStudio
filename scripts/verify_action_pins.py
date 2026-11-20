"""Check that every pinned action SHA is the commit its version comment names.

`tests/test_workflow_action_pins.py` proves the pins are full SHAs, that each action
has exactly one of them, and that each carries a version label. None of that notices a
*swapped* SHA: a well-formed 40-hex pin with a plausible `# v7.0.1` comment passes, so
the label is documentation rather than a check.

This script resolves each distinct `owner/repo@label` pair through the GitHub API and
compares the tag's commit with the SHA the workflow pins. It is deliberately outside the
pytest run: PR CI stays hermetic, and `.github/workflows/action-pin-verify.yml` runs it
weekly with a token.

Usage:
    python3 scripts/verify_action_pins.py            # resolve and compare every pin
    GITHUB_TOKEN=... python3 scripts/verify_action_pins.py

Exit codes: 0 when every pin resolves to its tag, 1 on any mismatch or unresolved pin
(including a rate-limited run, where the message says to set GITHUB_TOKEN).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
API = "https://api.github.com"
SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_TAG_DEPTH = 10


class LookupFailed(Exception):
    """A GitHub lookup failed (not a confirmed absence), so the pin is unverified."""


def _uses_nodes(node: yaml.Node, found: list[yaml.ScalarNode], path: frozenset[int]) -> None:
    """Collect every scalar value whose mapping key is `uses`, at any depth."""
    if id(node) in path:
        return
    path = path | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if (
                isinstance(key, yaml.ScalarNode)
                and key.value == "uses"
                and isinstance(value, yaml.ScalarNode)
            ):
                found.append(value)
            _uses_nodes(key, found, path)
            _uses_nodes(value, found, path)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _uses_nodes(item, found, path)


def pinned_references() -> list[dict[str, str]]:
    """Remote `owner/repo@sha # label` references across every workflow and action."""
    files = sorted(
        path
        for base in (WORKFLOWS, ACTIONS)
        if base.is_dir()
        for path in base.rglob("*")
        if path.suffix in {".yml", ".yaml"} and path.is_file()
    )
    found: list[dict[str, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
            if document is None:
                continue
            nodes: list[yaml.ScalarNode] = []
            _uses_nodes(document, nodes, frozenset())
            for node in nodes:
                value = node.value.strip()
                if value.startswith(("./", "docker://")):
                    continue
                action, _, ref = value.partition("@")
                tail = lines[node.end_mark.line][node.end_mark.column :]
                comment = re.match(r"\s*#\s*(\S+)", tail)
                if not SHA.match(ref) or not comment:
                    continue
                found.append(
                    {
                        "file": str(path.relative_to(ROOT / ".github")),
                        "action": action,
                        "ref": ref,
                        "label": comment.group(1),
                    }
                )
    return found


class Resolver:
    """Tag-to-commit resolution with one request per distinct pair, and a cache."""

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.cache: dict[tuple[str, str], tuple[str, str] | None] = {}
        self.rate_limited = False

    def _get(self, url: str) -> object | None:
        """Parsed JSON, None for a confirmed 404, or LookupFailed for anything else.

        A transient failure must never read as "absent": the caller would fall
        through to branch resolution and could pass a pin it never compared.
        """
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "voicestudio-action-pin-verify",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (403, 429) and not self.token:
                self.rate_limited = True
            raise LookupFailed(f"HTTP {error.code} for {url}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LookupFailed(f"{type(error).__name__} for {url}") from error

    def _ref_commit(self, repo: str, kind: str, label: str) -> str | None:
        """The commit a `refs/<kind>/<label>` points at, following annotated tags.

        None means the ref is confirmed absent; a failed lookup raises LookupFailed.
        """
        query = urllib.parse.quote(label, safe="")
        refs = self._get(f"{API}/repos/{repo}/git/matching-refs/{kind}/{query}")
        if refs is None:
            return None
        if not isinstance(refs, list):
            raise LookupFailed(f"unexpected matching-refs payload for {repo} {label}")
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("ref") != f"refs/{kind}/{label}":
                continue
            target = ref.get("object") or {}
            # An annotated tag may point at another tag; peel until a commit.
            for _ in range(_MAX_TAG_DEPTH):
                if not isinstance(target.get("sha"), str):
                    raise LookupFailed(f"ref {label} of {repo} has no object sha")
                if target.get("type") != "tag":
                    return target["sha"]
                tag = self._get(f"{API}/repos/{repo}/git/tags/{target['sha']}")
                if not isinstance(tag, dict):
                    raise LookupFailed(f"tag object {target['sha']} of {repo} unreadable")
                target = tag.get("object") or {}
            raise LookupFailed(f"tag {label} of {repo} nests deeper than {_MAX_TAG_DEPTH}")
        return None

    def resolve(self, action: str, label: str) -> tuple[str, str] | None:
        """``("tag"|"branch", commit)`` for a label, or None when it names neither.

        A subpath reference (`actions/cache/restore`) resolves against its repository
        (`actions/cache`). A channel label (`stable`) is a branch, which the pin
        deliberately freezes at pin time, so it is reported rather than compared.
        """
        repo = "/".join(action.split("/")[:2])
        key = (repo.lower(), label)
        if key in self.cache:
            return self.cache[key]
        found: tuple[str, str] | None = None
        for kind in ("tags", "heads"):
            commit = self._ref_commit(repo, kind, label)
            if commit is not None:
                found = ("tag" if kind == "tags" else "branch", commit)
                break
        self.cache[key] = found
        return found


def main() -> int:
    references = pinned_references()
    if not references:
        print("No pinned action references were found — the parser is broken.", file=sys.stderr)
        return 1
    resolver = Resolver(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    mismatched: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    channels: set[str] = set()
    for use in references:
        pair = f"{use['action']} ({use['label']})"
        where = f"{use['file']}: {use['action']}@{use['ref']} ({use['label']})"
        try:
            resolved = resolver.resolve(use["action"], use["label"])
        except LookupFailed as error:
            unresolved.setdefault(pair, f"{where} — lookup failed: {error}")
            continue
        if resolved is None:
            unresolved.setdefault(pair, where)
        elif resolved[0] == "branch":
            channels.add(pair)
        elif resolved[1].lower() != use["ref"].lower():
            mismatched.setdefault(pair, f"{where} — {use['label']} is {resolved[1]}")
    for line in mismatched.values():
        print(f"MISMATCH {line}")
    for line in unresolved.values():
        print(f"UNRESOLVED {line}")
    for pair in sorted(channels):
        print(f"CHANNEL {pair} — a branch label the pin freezes at pin time; not compared")
    verified = sum(
        1
        for use in references
        if f"{use['action']} ({use['label']})" not in mismatched
        and f"{use['action']} ({use['label']})" not in unresolved
        and f"{use['action']} ({use['label']})" not in channels
    )
    print(
        f"{len(references)} pinned references across {len(resolver.cache)} distinct "
        f"action/label pairs: {verified} verified, {len(mismatched)} mismatched, "
        f"{len(unresolved)} unresolved, {len(channels)} channel labels"
    )
    if mismatched or unresolved:
        if resolver.rate_limited and not resolver.token:
            print(
                "GitHub rate-limited the unauthenticated run — set GITHUB_TOKEN and retry.",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
