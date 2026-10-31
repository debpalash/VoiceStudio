"""Skip runtime CI for documentation/test-only changes, conservatively."""
from __future__ import annotations

import json
import os
from pathlib import PurePosixPath, Path
import re
import subprocess


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


def main() -> None:
    needed = True
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        needed = detect(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_REPOSITORY"])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Change detection unavailable; running runtime CI.")
    value = str(needed).lower()
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"runtime={value}\n")
    print(f"Runtime CI needed: {value}")


if __name__ == "__main__":
    main()
