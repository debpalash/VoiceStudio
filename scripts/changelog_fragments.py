"""Changelog fragments: one small file per PR, folded into CHANGELOG.md at release.

`changelog.d/<id>.<type>.md` holds a single entry. `apply_fragments` inserts the
entries into the `## [Unreleased]` block under the matching `### Type` heading
and `scripts/release.py` removes the files once they are in the changelog.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_DIR = ROOT / "changelog.d"

# Subsection order used by CHANGELOG.md (CLAUDE.md, "Release notes / changelog").
SECTIONS = ("Changed", "Added", "Docs", "Fixed", "License", "CI")
_TYPES = {name.lower(): name for name in SECTIONS}
NAME = re.compile(r"^(?P<id>[\w-]+)\.(?P<type>" + "|".join(_TYPES) + r")\.md$")


def _sort_key(name: str):
    ident = NAME.match(name).group("id")
    digits = re.match(r"\d+", ident)
    return (int(digits.group()) if digits else 0, ident)


def fragment_files(directory: Path = FRAGMENT_DIR) -> list[Path]:
    """Fragment files in the directory, oldest PR number first."""
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and NAME.match(p.name)]
    return sorted(files, key=lambda p: _sort_key(p.name))


def entry_of(path: Path) -> str:
    """The one-line entry a fragment holds, without a leading list marker."""
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ValueError(f"{path.name}: a fragment is exactly one line, found {len(lines)}")
    return re.sub(r"^[-*]\s+", "", lines[0])


def read_fragments(directory: Path = FRAGMENT_DIR) -> list[tuple[str, str]]:
    """(section, entry) pairs, oldest PR first; each is inserted at the top, so the newest ends up first."""
    pairs = [
        (_TYPES[NAME.match(p.name).group("type")], entry_of(p))
        for p in fragment_files(directory)
    ]
    return pairs


def apply_fragments(text: str, fragments: list[tuple[str, str]]) -> str:
    """Insert each entry into `## [Unreleased]`; entries already there are skipped."""
    if not fragments:
        return text
    match = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not match:
        raise ValueError("CHANGELOG.md has no [Unreleased] section")
    body = match.group(1)
    for section, entry in fragments:
        heading = re.search(rf"^### {section}[ \t]*\n\n?", body, re.M)
        if heading:
            under = re.split(r"^#{2,3} ", body[heading.end():], maxsplit=1, flags=re.M)[0]
            if f"- {entry}\n" in under or under.rstrip().endswith(f"- {entry}"):
                continue
        if heading:
            rest = body[heading.end():]
            gap = "" if rest.startswith("- ") else "\n"
            body = body[: heading.end()] + f"- {entry}\n" + gap + rest
            continue
        later = [
            m for m in re.finditer(r"^### (\w+)[ \t]*$", body, re.M)
            if m.group(1) in SECTIONS and SECTIONS.index(m.group(1)) > SECTIONS.index(section)
        ]
        block = f"### {section}\n\n- {entry}\n\n"
        if later:
            at = later[0].start()
            body = body[:at] + block + body[at:]
        else:
            body = body.rstrip("\n") + "\n\n" + block
    return text[: match.start(1)] + body + text[match.end(1):]


def remove_fragments(directory: Path = FRAGMENT_DIR) -> list[str]:
    """Delete the fragment files (never the README); returns their names."""
    names = []
    for path in fragment_files(directory):
        path.unlink()
        names.append(path.name)
    return names


def main() -> None:
    """Fold the fragments into CHANGELOG.md and delete them (what a release does)."""
    path = ROOT / "CHANGELOG.md"
    path.write_text(apply_fragments(path.read_text(encoding="utf-8"), read_fragments()), encoding="utf-8")
    for name in remove_fragments():
        print("folded", name)


if __name__ == "__main__":
    main()
