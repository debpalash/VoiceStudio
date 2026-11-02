#!/usr/bin/env python3
"""Validate Electron artifacts and immutable Tauri sunset feeds."""
import base64
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from check_electron_release_assets import verify_release

def validate_sunset(manifest, tag):
    if manifest.get("version") != tag.removeprefix("v"):
        raise ValueError("Tauri version must match sunset tag")
    if not manifest.get("platforms"):
        raise ValueError("Missing Tauri platforms")
    for entry in manifest["platforms"].values():
        url = urlparse(entry.get("url", ""))
        prefix = f"/debpalash/VoiceStudio/releases/download/{tag}/"
        if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith(prefix):
            raise ValueError("Tauri URLs must point to immutable sunset assets")
        if not entry.get("signature"):
            raise ValueError("Missing Tauri signature")

def channel_for(version):
    """`X.Y.Z` is Stable, `X.Y.Z-beta.N` is Beta; any other shape is not a release."""
    if re.fullmatch(r"\d+\.\d+\.\d+", version):
        return "stable"
    if re.fullmatch(r"\d+\.\d+\.\d+-beta\.\d+", version):
        return "beta"
    raise ValueError(f"Not a stable or beta release version: {version}")

def _gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


# The same agent list the commit-identity gate uses: agents are never credited.
_spec = importlib.util.spec_from_file_location(
    "agent_identities", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "agent_identities.py")
agents = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agents)
# GitHub's login for commits whose author email is not linked to any account.
UNLINKED_LOGIN = "invalid-email-address"


def is_credit_worthy(login):
    """People only: no bots, no AI agents, no unlinked-commit placeholder."""
    name = login.removeprefix("app/").lower()
    return not (login.startswith("app/") or name.endswith("[bot]")
                or name in agents.AGENT_LOGINS or name == UNLINKED_LOGIN)


def contributors_section(repo, tag, gh=_gh):
    """A `### Contributors` block for everyone who contributed since the last stable release.

    Two sources, because either alone misses people: commit authors in the tag
    comparison (ranked by commit count), then authors of PRs merged since that
    release whose commits are not linked to their GitHub account. The base is the
    latest published stable release, so a beta and a stable release both cover the
    whole span since the previous stable. Bots, AI agents and unlinked placeholders
    are left out. Returns "" when there is nobody to thank.
    """
    latest = gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name + \" \" + .published_at").split()
    base, since = latest[0], latest[1][:10]
    commit_authors = gh("api", f"repos/{repo}/compare/{base}...{tag}", "--paginate",
                        "--jq", ".commits[].author.login // empty").split()
    pr_authors = gh("pr", "list", "--repo", repo, "--state", "merged", "--search", f"merged:>={since}",
                    "--limit", "1000", "--json", "author", "--jq", ".[].author.login").split()
    counts = Counter(login for login in commit_authors if is_credit_worthy(login))
    ordered = sorted(counts, key=lambda login: (-counts[login], login.lower()))
    seen = {login.lower() for login in ordered}
    for login in sorted({p for p in pr_authors if is_credit_worthy(p)}, key=str.lower):
        if login.lower() not in seen:
            ordered.append(login)
            seen.add(login.lower())
    if not ordered:
        return ""
    mentions = ", ".join(f"@{login}" for login in ordered)
    return f"### Contributors\n\nThanks to everyone who contributed to this release: {mentions}\n"


def prepare(assets, tag, sunset_tag=None, repo=None, gh=_gh):
    version = json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]
    if tag != f"v{version}":
        raise ValueError("Tag must match package version")
    channel = channel_for(version)
    files = [p for p in assets.iterdir() if p.is_file()]
    release = {"tagName": tag, "isPrerelease": channel == "beta", "assets": [
        {"name": p.name, "size": p.stat().st_size,
         "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in files]}
    verify_release(release, assets, channel=channel, version=version)
    for manifest in assets.glob(f"electron-{channel}-*.yml"):
        text = manifest.read_text(encoding="utf-8")
        artifact = re.search(r"^\s+- url:\s*(.+)$", text, re.M)[1].strip().strip("'\"")
        expected = re.search(r"^\s{4}sha512:\s*(.+)$", text, re.M)[1].strip().strip("'\"")
        actual = base64.b64encode(hashlib.sha512((assets / artifact).read_bytes()).digest()).decode()
        if actual != expected:
            raise ValueError("Updater payload checksum mismatch")
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        if not (assets / f"VoiceStudio-{version}-{suffix}").is_file():
            raise ValueError(f"Missing installer: {suffix}")
    # Archived Tauri clients read `latest/download`, which never serves a
    # prerelease, so betas carry no sunset feeds.
    if sunset_tag and channel == "stable":
        for name in ("latest.json", "latest-user.json"):
            validate_sunset(json.loads((assets / name).read_text(encoding="utf-8")), sunset_tag)
    section = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
                        Path("CHANGELOG.md").read_text(encoding="utf-8"), re.M | re.S)
    if not section or not section[1].strip():
        raise ValueError("Write versioned CHANGELOG notes before release")
    notes = section[1].strip() + "\n"
    # A hand-written Contributors block (with roles, reporters) always wins.
    if repo and not re.search(r"^#{2,4} Contributors", notes, re.M):
        try:
            block = contributors_section(repo, tag, gh)
        except (subprocess.CalledProcessError, OSError) as error:
            # Credits must never block a release; say so loudly instead.
            print(f"::warning::Contributors section skipped: {error}", file=sys.stderr)
            block = ""
        if block:
            notes += "\n" + block
    (assets / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    (assets / "SHA256SUMS.txt").write_text("\n".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(files)
        if p.name not in {"SHA256SUMS.txt", "RELEASE_NOTES.md"}) + "\n", encoding="utf-8")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--sunset-tag")
    parser.add_argument("--repo", help="owner/name; adds a Contributors section from the tag comparison")
    parser.add_argument("--print-channel", action="store_true",
                        help="Print stable or beta for the package version and exit")
    args = parser.parse_args()
    if args.print_channel:
        print(channel_for(json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]))
    elif args.assets and args.tag:
        prepare(args.assets, args.tag, args.sunset_tag, repo=args.repo)
    else:
        parser.error("--assets and --tag are required")
