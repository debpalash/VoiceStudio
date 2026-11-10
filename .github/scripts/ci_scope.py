"""Skip runtime CI for documentation/test-only changes, conservatively.

Also holds runtime CI on a pull request whose opener has not signed the CLA
(`cla_held`): the required check reports the skip explicitly, and the CLA check
(.github/scripts/cla_check.py) re-runs the skipped run once they sign. This saves
runners; it is not a security boundary, and the CLA status still gates merging.
`--cla` prints only that decision, for workflows with their own path rules.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import PurePosixPath, Path
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cla_check import BOT_IDS, MAINTAINER_IDS, OVERRIDE_LABEL, SIGNATURE_BRANCH, SIGNATURE_PATH, SIGNED_LABEL


def non_runtime(path: str) -> bool:
    if not isinstance(path, str) or not path or PurePosixPath(path).is_absolute():
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(part in {".", ".."} for part in parts):
        return False
    # These documentation-tree assets are inputs to the installed phone apps.
    if path in {"docs/logo.svg", "docs/logo.png"}:
        return False
    if PurePosixPath(path).suffix.lower() in {".md", ".mdx", ".rst", ".adoc"}:
        return True
    if path.startswith(("docs/sponsors/", "docs/media/")) and PurePosixPath(path).suffix.lower() in {".svg", ".png", ".webp", ".jpg", ".jpeg", ".gif", ".mp4"}:
        return True
    if any(part.lower() in {"tests", "test", "__tests__", "__snapshots__", "androidtest"} for part in parts[:-1]):
        return True
    if any(part.endswith("Tests") for part in parts[:-1]) and parts[0] == "mobile":
        return True
    return bool(re.fullmatch(r"(?:test_.+\.py|.+_test\.py|conftest\.py|.+\.(?:test|spec)\.[cm]?[jt]sx?)", parts[-1]))


def runtime_needed(files: list[dict], limit: int) -> bool:
    # GitHub truncates pull-request files at 3000 and compare files at 300.
    if not isinstance(files, list) or not files or len(files) >= limit:
        return True
    for file in files:
        if not isinstance(file, dict) or file.get("status") not in {"added", "modified", "removed", "renamed"}:
            return True
        paths = [file.get("filename", "")]
        if file["status"] == "renamed":
            paths.append(file.get("previous_filename", ""))
        if not all(non_runtime(path) for path in paths):
            return True
    return False


def detect(event_name: str, event: dict, repo: str) -> bool:
    if event_name == "pull_request":
        endpoint = f"repos/{repo}/pulls/{int(event['number'])}/files"
        args, limit = ["--paginate", "--slurp"], 3000
    elif event_name in {"push", "merge_group"}:
        base, head = ((event.get("before"), event.get("after")) if event_name == "push"
                      else (event["merge_group"]["base_sha"], event["merge_group"]["head_sha"]))
        if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) and sha != "0" * 40 for sha in (base, head)):
            return True
        endpoint = f"repos/{repo}/compare/{base}...{head}"
        args, limit = ["--jq", ".files"], 300
    else:
        # Explicit manual runs, releases and scheduled audits always run fully.
        return True
    result = subprocess.run(["gh", "api", endpoint, *args], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    if event_name == "pull_request":
        if not isinstance(payload, list) or not all(isinstance(page, list) for page in payload):
            return True
        payload = [file for page in payload for file in page]
    return runtime_needed(payload, limit)


def _exempt(user: dict) -> bool:
    """Bots and exempt accounts are never held, exactly as before the hold existed."""
    user_id = user.get("id")
    return user.get("type") != "User" or not isinstance(user_id, int) or user_id in MAINTAINER_IDS | BOT_IDS


def cla_held(pr: dict, signed_ids: set[int] | None) -> bool:
    """True while a person who must sign opened `pr` and has not signed.

    A maintainer's `cla-override`, or the check's `cla-signed` label, releases it;
    unknown signatures (None) never hold anything.
    """
    user = pr.get("user") or {}
    if signed_ids is None or _exempt(user):
        return False
    if {SIGNED_LABEL, OVERRIDE_LABEL} & {label.get("name") for label in pr.get("labels") or []}:
        return False
    return int(user["id"]) not in signed_ids


def _gh_json(endpoint: str):
    result = subprocess.run(["gh", "api", endpoint], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def cla_hold(event_name: str, event: dict, repo: str) -> bool:
    if event_name != "pull_request" or _exempt((event.get("pull_request") or {}).get("user") or {}):
        return False  # no API call for the owner, bots, pushes or the merge queue
    # Read labels live: the CLA check's re-run replays this event with its old labels.
    pr = _gh_json(f"repos/{repo}/pulls/{int(event['number'])}")
    store = _gh_json(f"repos/{repo}/contents/{SIGNATURE_PATH}?ref={SIGNATURE_BRANCH}")
    signatures = json.loads(base64.b64decode(store["content"]))["signatures"]
    return cla_held(pr, {int(s["id"]) for s in signatures})


def held_or_clear(event_name: str, event: dict | None, repo: str) -> bool:
    """Any doubt (API failure, missing store) means "clear": CI runs."""
    if event is None:
        return False
    try:
        held = cla_hold(event_name, event, repo)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("CLA state unavailable; running runtime CI.")
        return False
    if held:
        print("The opener has not signed the CLA yet: heavy CI is skipped. "
              "Signing re-runs it automatically (see the CLA check's comment).")
    return held


def main() -> None:
    needed, event = True, None
    name, repo = os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("GITHUB_REPOSITORY", "")
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if "--cla" not in sys.argv:
            needed = detect(name, event, repo)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Change detection unavailable; running runtime CI.")
    held = needed and held_or_clear(name, event, repo)
    cla = "held" if held else "clear"
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        if "--cla" in sys.argv:
            output.write(f"cla={cla}\n")
            return
        value = str(needed and not held).lower()
        output.write(f"runtime={value}\ncla={cla}\n")
    print(f"Runtime CI needed: {value}")


if __name__ == "__main__":
    main()
