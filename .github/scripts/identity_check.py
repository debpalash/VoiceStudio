#!/usr/bin/env python3
"""Run base-owned identity policy against fetched PR commits as inert data.

The trusted pull_request_target workflow publishes a separate commit status.
An unprivileged fork workflow can forge a native job name, but cannot replace
this status with its read-only token. The native job remains an API-health gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cla_check import GitHub, head_blockers, set_status

ROOT = Path(__file__).resolve().parents[2]
STATUS_CONTEXT = "Commit identity policy"


def _snapshot(pr: dict) -> tuple:
    return (pr.get("state"), pr["head"]["sha"], pr["base"]["sha"], pr.get("body") or "")


def run(gh: GitHub, event: dict, root: Path = ROOT) -> bool:
    event_pr = event["pull_request"]
    number = int(event_pr["number"])
    pending_sha = event_pr["head"]["sha"]
    set_status(gh, pending_sha, "pending", "Checking commit identities", context=STATUS_CONTEXT)
    pr = gh.get(f"/repos/{gh.repo}/pulls/{number}")
    if pr["state"] != "open":
        return True
    sha, base = pr["head"]["sha"], pr["base"]["sha"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (sha, base)):
        raise RuntimeError("Invalid commit identity comparison")
    if sha != pending_sha:
        raise RuntimeError("PR head changed since this event; run the current target workflow")

    if head_blockers(gh, pr):
        set_status(gh, sha, "failure", "Close duplicate PR heads and rerun the survivor's identity check",
                   context=STATUS_CONTEXT)
        return False

    # Fetch objects only: never checkout PR files, run its workflows, or fall
    # back to its checker. The base checkout holds every executable policy file.
    environment = {key: value for key, value in os.environ.items() if key != "GITHUB_TOKEN"}
    policy_base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, env=environment, text=True).strip()
    if policy_base != base:
        raise RuntimeError("PR base changed since this policy checkout; run a fresh target workflow")
    subprocess.run(["git", "fetch", "--no-tags", "origin", f"refs/pull/{number}/head"],
                   cwd=root, env=environment, check=True)
    fetched = subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=root, env=environment, text=True).strip()
    if fetched != sha:
        raise RuntimeError("PR head changed during fetch; recheck")
    with tempfile.TemporaryDirectory(prefix="identity-event-") as directory:
        metadata = Path(directory) / "event.json"
        metadata.write_text(json.dumps({"pull_request": {"body": pr.get("body")}}), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(root / "scripts/check_commit_identities.py"),
            "--hash-file", str(root / "scripts/blocked_identity_hashes.txt"),
            "--range", f"{base}..{sha}", "--event", str(metadata),
        ], cwd=root, env=environment)
    if result.returncode not in (0, 1):
        raise RuntimeError("Commit identity policy could not be evaluated")
    if _snapshot(gh.get(f"/repos/{gh.repo}/pulls/{number}")) != _snapshot(pr):
        raise RuntimeError("PR metadata changed during identity evaluation; recheck")
    duplicates = head_blockers(gh, pr)
    passed = result.returncode == 0 and not duplicates
    set_status(gh, sha, "success" if passed else "failure",
               ("Close duplicate PR heads and rerun the survivor's identity check" if duplicates else
                "Commit identities are clean" if passed else "Blocked commit identities or agent attribution"),
               context=STATUS_CONTEXT)
    return passed


def main() -> int:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request_target":
        raise RuntimeError("Identity enforcement requires trusted pull_request_target orchestration")
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    gh = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    print("Commit identity policy:", "passed" if run(gh, event) else "blocked")
    return 0  # eligibility is the status; API/execution failures raise


if __name__ == "__main__":
    sys.exit(main())
