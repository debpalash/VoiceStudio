#!/usr/bin/env python3
"""Keep each outside contributor to at most two open pull requests.

Run from the base branch on ``pull_request_target`` (opened / reopened). The
repository owner, organisation members, collaborators and bots are exempt. For
anyone else, their oldest ``LIMIT`` other open pull requests stay open; the
opened or reopened one, and any other beyond the limit, is closed with a comment explaining the limit, so review and CI capacity go to
pull requests that can be reviewed.

Security: no pull-request code is checked out or executed. The script only
reads the open pull-request list and closes or comments through the API.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

API = "https://api.github.com"
LIMIT = 2
EXEMPT_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MESSAGE = (
    "Thanks for contributing! This repository keeps each outside contributor to "
    f"at most {LIMIT} open pull requests at a time, so every one can get a real "
    "review and a CI run. You already have {open_count} open: {others}. This one "
    "is closed for now; reopen or resubmit it once one of those is merged or "
    "closed, and please batch related small fixes into one pull request."
)


def is_exempt(association: str, user_type: str) -> bool:
    return association.upper() in EXEMPT_ASSOCIATIONS or user_type == "Bot"


def plan(number: int, author: str, open_prs: list[dict], limit: int = LIMIT) -> tuple[list[int], list[int]]:
    """``(kept, to_close)`` for ``author``'s open pull requests.

    The author's other open pull requests are kept oldest first, and the
    triggering one ``number`` comes last, so a newly opened or reopened pull
    request is the first to close. Every run reconciles the whole set, so a pull
    request that a skipped or superseded run never checked is closed too.
    """
    others = sorted(
        pr["number"] for pr in open_prs
        if (pr.get("user") or {}).get("login") == author and pr["number"] != number
    )
    ordered = [*others, number]
    return ordered[:limit], ordered[limit:]


def _api(method: str, path: str, token: str, body: dict | None = None):
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    return json.loads(data) if data else None


def open_pulls(repo: str, token: str) -> list[dict]:
    pulls: list[dict] = []
    for page in range(1, 21):
        batch = _api("GET", f"/repos/{repo}/pulls?state=open&per_page=100&page={page}", token)
        pulls.extend(batch)
        if len(batch) < 100:
            break
    return pulls


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    number = int(os.environ["PR"])
    author = os.environ["AUTHOR"]
    if is_exempt(os.environ.get("ASSOCIATION", ""), os.environ.get("AUTHOR_TYPE", "")):
        print(f"#{number}: {author} is exempt")
        return 0
    kept, to_close = plan(number, author, open_pulls(repo, token))
    if not to_close:
        print(f"#{number}: within the limit")
        return 0
    others = ", ".join(f"#{n}" for n in kept)
    for closing in to_close:
        _api("POST", f"/repos/{repo}/issues/{closing}/comments", token,
             {"body": MESSAGE.format(open_count=len(kept), others=others)})
        _api("PATCH", f"/repos/{repo}/pulls/{closing}", token, {"state": "closed"})
        print(f"#{closing}: closed ({author} keeps {others} open)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
