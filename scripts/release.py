#!/usr/bin/env python3
"""Interactive release helper: Stable or Beta, draft or publish, then confirm.

    uv run python scripts/release.py                 # ask everything
    uv run python scripts/release.py --dry-run       # show the plan, change nothing
    uv run python scripts/release.py --publish TAG   # publish an existing draft

Asks first (channel, version, draft/publish), shows the new tag next to the last
release, and only then switches to an up-to-date `main` if needed. Bumps the three version files in lockstep,
refreshes uv.lock, writes the CHANGELOG section, runs the version guards and
opens the release pull request. Run it again once that merges: main then has
the version, so it only tags and either leaves the build as a draft (tag push)
or dispatches `electron-release.yml` with `publish`. Nothing leaves the machine
until you type the tag name to confirm. See docs/RELEASING.md.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_electron_release import channel_for  # noqa: E402
from changelog_fragments import apply_fragments, read_fragments, remove_fragments  # noqa: E402

GUARD_TESTS = [
    "tests/test_app_version.py",
    "tests/test_voicestudio_branding.py",
    "tests/test_changelog_style.py",
]
RELEASE_FILES = [
    "package.json", "pyproject.toml", "backend/core/version.py", "uv.lock", "CHANGELOG.md",
    "changelog.d",
]
# Platforms whose installers are signed. Kept apart from the table below so that
# what the helper prints is plain platform names, never anything derived from the
# credential-name table (a static analyser treats that table as sensitive).
PLATFORMS = ("macOS", "Windows")
# Repo variable names electron-release.yml reads to sign each platform's
# installers. Only names are listed and checked here, never values.
SIGNING_ENV_NAMES = {
    "macOS": (
        "ELECTRON_MACOS_CSC_LINK", "ELECTRON_MACOS_CSC_KEY_PASSWORD",
        "APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER",
    ),
    "Windows": ("ELECTRON_WINDOWS_CSC_LINK", "ELECTRON_WINDOWS_CSC_KEY_PASSWORD"),
}
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?")


def latest_stable(tags: list[str]) -> str | None:
    finals = [t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    return max(finals, key=lambda t: tuple(map(int, t[1:].split(".")))) if finals else None


def next_version(current: str, channel: str, tags: list[str] = ()) -> str:
    """The version to release next.

    `current` is package.json on main, which usually already names the upcoming
    release; if that base is already tagged, move to the next patch. Beta numbers
    continue after any existing `vBASE-beta.N` tag: `0.5.7-beta.1`, `-beta.2`...
    """
    match = _VERSION.fullmatch(current)
    if not match:
        raise ValueError(f"Not a stable or beta version: {current}")
    if channel not in ("stable", "beta"):
        raise ValueError(f"Unknown channel: {channel}")
    major, minor, patch = (int(g) for g in match.groups()[:3])
    taken = set(tags)
    if f"v{major}.{minor}.{patch}" in taken:
        patch += 1
    base = f"{major}.{minor}.{patch}"
    if channel == "stable":
        return base
    numbers = [
        int(m.group(1))
        for t in taken
        if (m := re.fullmatch(rf"v{re.escape(base)}-beta\.(\d+)", t))
    ]
    return f"{base}-beta.{max(numbers, default=0) + 1}"


def bump_files(root: Path, version: str) -> None:
    """Set the canonical version and its two toolchain mirrors."""
    targets = [
        ("package.json", r'(?m)^(\s*"version":\s*")[^"]+(")'),
        ("pyproject.toml", r'(?m)^(version\s*=\s*")[^"]+(")'),
        ("backend/core/version.py", r'(?m)^(_FALLBACK_VERSION\s*=\s*")[^"]+(")'),
    ]
    for name, pattern in targets:
        path = root / name
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(pattern, rf"\g<1>{version}\g<2>", text, count=1)
        if count != 1:
            raise ValueError(f"Could not find the version in {name}")
        path.write_text(updated, encoding="utf-8")


def prepare_changelog(text: str, version: str, today: datetime.date) -> str:
    """Return the changelog with a `## [version]` section; a no-op if one exists.

    Stable renames `[Unreleased]` and starts a fresh one. Beta copies it, so the
    next beta or the final stable release still carries the full list.
    """
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
        return text
    match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise ValueError("CHANGELOG.md has no [Unreleased] entries to release")
    body = match.group(1).strip("\n")
    heading = f"## [{version}] — {today.isoformat()}"
    if channel_for(version) == "stable":
        section = f"## [Unreleased]\n\n{heading}\n\n{body}\n\n"
    else:
        section = f"## [Unreleased]\n\n{body}\n\n{heading}\n\n{body}\n\n"
    return text[: match.start()] + section + text[match.end():]


def sh(*cmd: str, check: bool = True, capture: bool = False) -> str:
    result = subprocess.run(
        cmd, cwd=ROOT, check=check, text=True, capture_output=capture, encoding="utf-8"
    )
    return (result.stdout or "").strip() if capture else ""


def ask(question: str, choices: list[str], default: str | None = None) -> str:
    hint = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        answer = input(f"{question} [{hint}] ").strip().lower() or (default or "")
        if answer in choices:
            return answer
        print(f"  Please answer one of: {', '.join(choices)}")


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def read_key() -> str:
    """One keypress as up/down/enter/esc or the character itself (stdlib only)."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        return {"\r": "enter", "\x03": "interrupt", "\x1b": "esc"}.get(ch, ch)
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keeps Ctrl-C working
        # Unbuffered: sys.stdin would swallow the rest of an arrow's escape sequence.
        ch = os.read(fd, 1).decode("latin-1")
        if ch == "\x1b":
            if not select.select([fd], [], [], 0.05)[0]:
                return "esc"
            seq = os.read(fd, 2).decode("latin-1")
            return {"[A": "up", "[B": "down", "OA": "up", "OB": "down"}.get(seq, "")
        return "enter" if ch in ("\r", "\n") else ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def select_option(question: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """Arrow-key menu (up/down or k/j, Enter). Typed prompt when not on a terminal."""
    values = [value for value, _ in options]
    if not interactive():
        return ask(question, values, values[default])
    if os.name == "nt":
        os.system("")  # enable ANSI escapes in legacy Windows consoles
    index = default
    out = sys.stdout

    def draw(redraw: bool) -> None:
        if redraw:
            out.write(f"\x1b[{len(options)}A")
        for i, (value, label) in enumerate(options):
            line = f"{'>' if i == index else ' '} {value:<9} {label}"
            out.write("\x1b[2K" + (f"\x1b[1;36m{line}\x1b[0m" if i == index else line) + "\n")
        out.flush()

    out.write(f"{question}  (up/down to move, Enter to select)\n\x1b[?25l")
    draw(False)
    try:
        while True:
            key = read_key()
            if key in ("up", "k"):
                index = (index - 1) % len(options)
            elif key in ("down", "j"):
                index = (index + 1) % len(options)
            elif key == "enter":
                break
            elif key in ("interrupt", "esc", "q"):
                raise KeyboardInterrupt
            draw(True)
    finally:
        out.write("\x1b[?25h")
    # Collapse the menu to the chosen answer.
    out.write(f"\x1b[{len(options) + 1}A\x1b[J{question} {values[index]}\n")
    out.flush()
    return values[index]


def check_ready(tag: str | None = None) -> None:
    """Read-only checks for anything the helper cannot fix on its own."""
    problems = []
    if sh("git", "status", "--porcelain", "--untracked-files=no", capture=True):
        problems.append("tracked files have uncommitted changes (commit or stash them)")
    if sh("git", "rev-list", "--count", "origin/main..main", capture=True, check=False) not in ("", "0"):
        problems.append("local main has commits that are not on origin/main")
    if subprocess.run(["gh", "auth", "status"], cwd=ROOT, capture_output=True).returncode:
        problems.append("gh is not authenticated (run: gh auth login)")
    if tag and sh("git", "ls-remote", "--tags", "origin", tag, capture=True):
        problems.append(f"tag {tag} already exists on origin")
    if problems:
        raise SystemExit("Cannot release:\n  - " + "\n  - ".join(problems))


def ensure_main() -> None:
    """Releases are cut from main: switch to it and fast-forward to origin/main."""
    if sh("git", "branch", "--show-current", capture=True) != "main":
        sh("git", "switch", "main")
    sh("git", "merge", "--ff-only", "origin/main")


def unsigned_platforms() -> tuple[list[str], bool]:
    """Platforms whose signing secrets are missing, and whether the list is a guess.

    A guess (gh could not list secrets) treats every platform as unsigned so the
    owner is asked rather than assuming installers will be signed.
    """
    result = subprocess.run(
        ["gh", "secret", "list", "--json", "name", "--jq", ".[].name"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode:
        return list(PLATFORMS), True
    present = set(result.stdout.split())
    missing = [
        platform for platform in PLATFORMS
        if not all(name in present for name in SIGNING_ENV_NAMES[platform])
    ]
    return missing, False


def ask_allow_unsigned() -> list[str]:
    """Report signing per platform; return the platforms that would ship unsigned.

    Raises SystemExit when the owner declines. An empty list means everything is
    signed and no question is asked.
    """
    gaps, guessed = unsigned_platforms()
    if not gaps:
        print("Signing: macOS and Windows secrets are configured.")
        return []
    for platform in PLATFORMS:
        state = (
            "?  could not read repo secrets" if guessed
            else "NO signing secrets (installers ship unsigned)" if platform in gaps
            else "signed"
        )
        print(f"  {platform:8} {state}")
    print("  (v0.5.6 also shipped unsigned installers; users see SmartScreen/Gatekeeper warnings.)")
    answer = select_option(
        f"Publish with unsigned {' and '.join(gaps)} installers?",
        [("no", "cancel; add the secrets (docs/RELEASING.md → Credentials) or pick draft"),
         ("yes", "publish anyway; the release notes disclose it")],
    )
    if answer != "yes":
        raise SystemExit("Cancelled: configure signing, or choose draft.")
    return gaps


def dispatch_publish(tag: str, allow_unsigned: bool | list) -> None:
    sh(
        "gh", "workflow", "run", "electron-release.yml", "--ref", "main",
        "-f", f"release_tag={tag}", "-f", "publish=true",
        "-f", f"allow_unsigned={str(bool(allow_unsigned)).lower()}",
    )


def publish_existing(tag: str) -> None:
    channel = channel_for(tag.removeprefix("v"))
    if subprocess.run(["gh", "auth", "status"], cwd=ROOT, capture_output=True).returncode:
        raise SystemExit("gh is not authenticated (run: gh auth login)")
    unsigned = ask_allow_unsigned()
    print(f"\nPublish {tag} ({channel}) from its existing build"
          f"{' (UNSIGNED: ' + ', '.join(unsigned) + ')' if unsigned else ''}.")
    if input(f"Type {tag} to confirm: ").strip() != tag:
        raise SystemExit("Cancelled.")
    dispatch_publish(tag, unsigned)
    print("Dispatched electron-release.yml with publish=true. Watch: gh run list --workflow electron-release.yml")


def release(args: argparse.Namespace) -> None:
    sh("git", "fetch", "-q", "--tags", "origin", "main")
    current = json.loads(sh("git", "show", "origin/main:package.json", capture=True))["version"]
    tags = sh("git", "tag", "-l", "v*", capture=True).split()
    last = latest_stable(tags) or "none"
    branch = sh("git", "branch", "--show-current", capture=True)
    behind = sh("git", "rev-list", "--count", "main..origin/main", capture=True, check=False) or "?"
    check_ready()

    print(f"Last release: {last}    package.json on main: {current}")
    channel = select_option(
        "Release channel?",
        [("stable", "GitHub latest, :stable Docker, all stores"),
         ("beta", "opt-in prerelease, never latest, :beta Docker")],
    )
    suggestion = next_version(current, channel, tags)
    shape = "X.Y.Z-beta.N, e.g. 0.5.7-beta.1" if channel == "beta" else "X.Y.Z"
    print(f"  New release: v{suggestion}   (last release {last}; format {shape})")
    version = input(f"Version [{suggestion}]: ").strip().removeprefix("v") or suggestion
    if channel_for(version) != channel:
        raise SystemExit(f"{version} is not a {channel} version ({shape})")
    tag = f"v{version}"
    check_ready(tag)
    mode = select_option(
        "Draft or publish?",
        [("draft", "build only, nothing public; publish later"),
         ("publish", "build and publish now")],
    )
    unsigned = ask_allow_unsigned() if mode == "publish" else []

    switch = (
        f"switch from {branch} to main, " if branch != "main" else ""
    ) + (f"fast-forward main by {behind} commits, " if behind not in ("0", "?") else "")
    # main only changes through pull requests, so a release takes two runs: the
    # first opens the release PR, the second (after it merges) tags main.
    on_main = current == version
    build = f"tag {tag}, push tag{', publish' if mode == 'publish' else ' (draft; publish later with --publish)'}"
    steps = (
        f"{switch}{build}  (main already has {version})" if on_main else
        f"""{switch}set package.json + pyproject.toml + backend/core/version.py to {version},
            uv lock, CHANGELOG section, run version guards,
            commit "chore(release): {tag}" on release/{tag}, push it and open its pull request;
            after it merges, run this again with {version} to {build}"""
    )
    print(f"""
Release plan
  channel   {channel}  ({'GitHub prerelease, never latest; :beta Docker' if channel == 'beta' else 'GitHub latest; :stable Docker'})
  new tag   {tag}   (last release {last})
  mode      {mode}{'  (UNSIGNED: ' + ', '.join(unsigned) + ')' if unsigned else ''}
  steps     {steps}""")
    if args.dry_run:
        print("\n--dry-run: nothing changed.")
        return
    action = "tags main" if on_main else "opens the release pull request"
    if input(f"\nThis {action}. Type {tag} to confirm: ").strip() != tag:
        raise SystemExit("Cancelled.")

    ensure_main()
    if not on_main:
        open_release_pr(version, tag)
        return
    print(f"main already has {version}; tagging it.")
    sh("git", "tag", tag)
    sh("git", "push", "origin", tag)
    if mode == "publish":
        # The tag push starts a build-only run; the dispatch builds and publishes.
        runs = sh("gh", "run", "list", "--workflow", "electron-release.yml", "--branch", tag,
                  "--event", "push", "--json", "databaseId", "--jq", ".[].databaseId", capture=True)
        for run_id in runs.split():
            sh("gh", "run", "cancel", run_id, check=False)
        dispatch_publish(tag, unsigned)
        print(f"\nPublishing {tag}. Then verify: docs/RELEASING.md → Beta channel / verification table.")
    else:
        print(f"\nDraft build started for {tag}. Publish later: make release-publish TAG={tag}")


def open_release_pr(version: str, tag: str) -> None:
    """Bump, fold the changelog and open the release pull request from release/<tag>."""
    branch = f"release/{tag}"
    local = sh("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
               capture=True, check=False)
    if local or sh("git", "ls-remote", "--heads", "origin", branch, capture=True):
        # An earlier run already committed the bump (its push or pull request
        # step failed): finish that run instead of bumping a second time.
        if local:
            sh("git", "switch", branch)
        else:
            sh("git", "switch", "-c", branch, "--track", f"origin/{branch}")
        publish_release_branch(branch, version, tag)
        return
    sh("git", "switch", "-c", branch)
    try:
        bump_files(ROOT, version)
        changelog = ROOT / "CHANGELOG.md"
        folded = apply_fragments(changelog.read_text(encoding="utf-8"), read_fragments())
        changelog.write_text(
            prepare_changelog(folded, version, datetime.date.today()),
            encoding="utf-8",
        )
        sh("uv", "lock")
        sh("uv", "run", "pytest", *GUARD_TESTS, "-q")
        # Only now, with every guard passed: a failed release keeps every fragment.
        remove_fragments()
    except (subprocess.CalledProcessError, ValueError) as error:
        # Nothing is committed or pushed yet: put the working tree back.
        sh("git", "checkout", "--", *RELEASE_FILES)
        sh("git", "switch", "main")
        sh("git", "branch", "-D", branch)
        raise SystemExit(
            f"\nRelease aborted before committing: {error}\n"
            "The version files, uv.lock and CHANGELOG.md were restored; nothing was pushed."
        )
    sh("git", "add", *RELEASE_FILES)
    sh("git", "commit", "-m", f"chore(release): {tag}")
    publish_release_branch(branch, version, tag)


def publish_release_branch(branch: str, version: str, tag: str) -> None:
    """Push the release branch and open its pull request unless one is open already."""
    sh("git", "push", "-u", "origin", branch)
    if not sh("gh", "pr", "list", "--head", branch, "--state", "open",
              "--json", "number", "--jq", ".[].number", capture=True):
        sh("gh", "pr", "create", "--base", "main", "--head", branch,
           "--title", f"chore(release): {tag}",
           "--body", f"Release commit from `scripts/release.py`: {version} in the three version "
                     "files, uv.lock, and the CHANGELOG section folded from changelog.d.")
    sh("git", "switch", "main")
    print(f"\nOpened the release pull request from {branch}. Once it merges, run "
          f"`make release` again with {version}: it tags main and starts the build.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show the plan and change nothing")
    parser.add_argument("--publish", metavar="TAG", help="publish an existing draft build of TAG")
    args = parser.parse_args()
    if args.publish:
        publish_existing(args.publish)
    else:
        release(args)


if __name__ == "__main__":
    main()
