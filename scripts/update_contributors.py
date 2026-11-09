# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=13.0.0"]
# ///
"""Refresh the bundled Home contributor credits from the GitHub contributors API.

Keeps human contributors (GitHub ``type: User``, no bots or automation accounts)
with strictly more than ``MIN_COMMITS`` commits, owner first, then by commits.
Each avatar is downloaded at a fixed size, re-encoded as a metadata-free PNG and
bundled, so the apps show current pictures with no runtime network call:

* Electron: ``features/home/home-contributors-data.ts`` + ``features/home/contributors/``
  (Android copies that folder into its APK assets at build time)
* iOS: ``Contributors.xcassets/contributor-<login>.imageset`` + ``HomeContributors.swift``
* Android: ``HomeContributors.kt``
* README Team contributor rows (English and every translation in ``docs/readme/``)

``homeCoreMaintainers`` is owner-assigned and preserved. Output is deterministic:
files (and the snapshot date) change only when the contributor data or avatars do.
Run from the repository root; ``GITHUB_TOKEN`` is used when set:

    uv run scripts/update_contributors.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "debpalash/VoiceStudio"
OWNER = REPO.split("/")[0]
MIN_COMMITS = 10
AVATAR_SIZE = 128  # 2x the largest Electron render (56px), 3x the iOS 34pt cluster
HOME = ROOT / "electron/src/renderer/src/features/home"
DATA_TS = HOME / "home-contributors-data.ts"
AVATARS = HOME / "contributors"
XCASSETS = ROOT / "mobile/ios/VoiceStudio/Resources/Contributors.xcassets"
SWIFT = ROOT / "mobile/ios/VoiceStudio/Features/HomeContributors.swift"
KOTLIN = ROOT / "mobile/android/features/src/main/kotlin/com/voicestudio/mobile/features/HomeContributors.kt"
READMES = (ROOT / "README.md", *sorted((ROOT / "docs/readme").glob("README_*.md")))

# Automation accounts that GitHub reports as ``type: User``.
AUTOMATION_LOGINS = frozenset({
    "actions-user", "github-actions", "dependabot", "dependabot-preview", "renovate", "renovate-bot",
    "web-flow", "imgbotapp", "allcontributors", "semantic-release-bot", "snyk-bot", "pre-commit-ci",
    "copilot", "claude", "codex", "cursor", "cursoragent", "devin-ai-integration", "google-labs-jules",
    "copilot-swe-agent", "coderabbitai", "greptile-apps", "openhands-agent", "sweep-ai",
})

Fetch = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    headers = {"User-Agent": "voicestudio-contributors-refresh", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:  # noqa: S310 — fixed https URLs
        return response.read()


def fetch_contributors(fetch: Fetch) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        batch = json.loads(fetch(f"https://api.github.com/repos/{REPO}/contributors?per_page=100&page={page}"))
        if not batch:
            return rows
        rows += batch
        page += 1


def is_human(row: dict) -> bool:
    login = str(row.get("login") or "")
    return (
        row.get("type") == "User"
        and bool(login)
        and not login.lower().endswith("[bot]")
        and login.lower() not in AUTOMATION_LOGINS
    )


def select_contributors(rows: Iterable[dict], owner: str = OWNER, min_commits: int = MIN_COMMITS) -> list[dict]:
    """Humans with more than ``min_commits`` commits: owner first, then commits desc, then login."""
    people = [r for r in rows if is_human(r) and int(r.get("contributions") or 0) > min_commits]
    people.sort(key=lambda r: (r["login"] != owner, -int(r["contributions"]), r["login"].casefold()))
    return [{"login": r["login"], "commits": int(r["contributions"]), "avatar_url": r["avatar_url"]} for r in people]


def avatar_url(url: str, size: int = AVATAR_SIZE) -> str:
    return f"{url}{'&' if '?' in url else '?'}s={size}"


def normalise_png(data: bytes, size: int = AVATAR_SIZE) -> bytes:
    """Decode any avatar format and re-encode a square, metadata-free PNG (iOS imagesets need PNG)."""
    from PIL import Image  # noqa: PLC0415 — only the refresh needs Pillow

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA" if has_alpha else "RGB")
        if image.size != (size, size):
            image = image.resize((size, size), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()


def parse_core(ts: str) -> list[str]:
    match = re.search(r"homeCoreMaintainers = \[(.*?)\]", ts, re.S)
    if not match:
        raise SystemExit(f"{DATA_TS}: homeCoreMaintainers not found")
    return re.findall(r"'([^']+)'", match.group(1))


def parse_date(ts: str) -> str | None:
    match = re.search(r"contributors snapshot, (\d{4}-\d{2}-\d{2})\.", ts)
    return match.group(1) if match else None


def render_ts(people: list[dict], core: list[str], date: str) -> str:
    imports = "".join(f"import avatar{i} from './contributors/{p['login']}.png';\n" for i, p in enumerate(people))
    entries = "".join(
        f"  {{ login: '{p['login']}', commits: {p['commits']}, avatar: avatar{i} }},\n" for i, p in enumerate(people)
    )
    return (
        f"{imports}\n"
        f"// GitHub /repos/{REPO}/contributors snapshot, {date}.\n"
        f"// Human contributors with strictly more than {MIN_COMMITS} repository commits; bundled for offline use.\n"
        "// Regenerate with `uv run scripts/update_contributors.py` (weekly: .github/workflows/contributors-refresh.yml).\n"
        "// Core maintainers are assigned by the project owner, independently of commit counts.\n"
        f"export const homeCoreMaintainers = [{', '.join(repr(c) for c in core)}] as const;\n"
        "\n"
        f"export const homeContributors = [\n{entries}] as const;\n"
    )


def _quoted(logins: list[str]) -> str:
    return ", ".join(f'"{login}"' for login in logins)


def replace_once(text: str, pattern: str, replacement: str, where: Path | str) -> str:
    new, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{where}: pattern {pattern!r} not found")
    return new


def render_swift(text: str, logins: list[str]) -> str:
    return replace_once(text, r"static let logins = \[.*?\]", f"static let logins = [{_quoted(logins)}]", SWIFT)


def render_kotlin(text: str, logins: list[str]) -> str:
    return replace_once(text, r"val logins = listOf\(.*?\)", f"val logins = listOf({_quoted(logins)})", KOTLIN)


def readme_row(logins: list[str]) -> str:
    return " ".join(
        f'<a href="https://github.com/{u}"><img src="https://github.com/{u}.png?size=96" width="44" height="44" '
        f'alt="@{u}" title="@{u}"></a>'
        for u in logins
    )


def render_readme(text: str, logins: list[str], where: Path | str = "README") -> str:
    """Replace the single Team contributor avatar row (the core team has its own table)."""
    row = re.compile(r'^<a href="https://github\.com/[^"]+"><img src="https://github\.com/[^"]+\.png\?size=96".*$', re.M)
    if len(row.findall(text)) != 1:
        raise SystemExit(f"{where}: expected exactly one contributor avatar row")
    return row.sub(lambda _m: readme_row(logins), text)


def imageset_contents(login: str) -> str:
    return json.dumps(
        {"images": [{"filename": f"{login}.png", "idiom": "universal", "scale": "3x"}],
         "info": {"author": "xcode", "version": 1}},
        indent=2,
    ) + "\n"  # byte-identical to scripts/export-mobile-contributors.ts


def same_pixels(path: Path, png: bytes) -> bool:
    """True when ``path`` already holds this image, so an encoder upgrade alone never rewrites it."""
    from PIL import Image  # noqa: PLC0415

    if not path.is_file():
        return False
    with Image.open(path) as old, Image.open(io.BytesIO(png)) as new:
        return old.format == "PNG" and old.mode == new.mode and old.size == new.size and old.tobytes() == new.tobytes()


def write_if_changed(path: Path, data: bytes | str) -> bool:
    raw = data.encode() if isinstance(data, str) else data
    if path.is_file() and path.read_bytes() == raw:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True


def apply(people: list[dict], avatars: dict[str, bytes], today: str) -> list[str]:
    """Write every generated file; return the paths that changed."""
    changed: list[Path] = []
    logins = [p["login"] for p in people]
    keep = set(logins)

    for login, png in avatars.items():
        for path in (AVATARS / f"{login}.png", XCASSETS / f"contributor-{login}.imageset/{login}.png"):
            if not same_pixels(path, png) and write_if_changed(path, png):
                changed.append(path)
        if write_if_changed(XCASSETS / f"contributor-{login}.imageset/Contents.json", imageset_contents(login)):
            changed.append(XCASSETS / f"contributor-{login}.imageset/Contents.json")
    for path in sorted(AVATARS.iterdir()):
        if path.stem not in keep or path.suffix != ".png":
            path.unlink()
            changed.append(path)
    for path in sorted(XCASSETS.glob("contributor-*.imageset")):
        if path.name.removeprefix("contributor-").removesuffix(".imageset") not in keep:
            shutil.rmtree(path)
            changed.append(path)

    current = DATA_TS.read_text()
    core = parse_core(current)
    unchanged_ts = render_ts(people, core, parse_date(current) or today)
    ts = unchanged_ts if unchanged_ts == current and not changed else render_ts(people, core, today)
    if write_if_changed(DATA_TS, ts):
        changed.append(DATA_TS)

    for path, render in ((SWIFT, render_swift), (KOTLIN, render_kotlin)):
        if write_if_changed(path, render(path.read_text(), logins)):
            changed.append(path)
    readme_logins = [login for login in logins if login not in core]
    for path in READMES:
        if write_if_changed(path, render_readme(path.read_text(), readme_logins, path)):
            changed.append(path)
    return [str(p.relative_to(ROOT)) for p in changed]


def main(fetch: Fetch = http_get) -> int:
    people = select_contributors(fetch_contributors(fetch))
    if not people or people[0]["login"] != OWNER:
        print(f"refusing to write: {OWNER} missing from the contributors list", file=sys.stderr)
        return 1
    avatars = {p["login"]: normalise_png(fetch(avatar_url(p["avatar_url"]))) for p in people}
    changed = apply(people, avatars, dt.datetime.now(dt.UTC).date().isoformat())
    print("\n".join(changed) if changed else "contributors snapshot is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
