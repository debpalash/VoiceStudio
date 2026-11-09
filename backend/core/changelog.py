"""Parse the shipped CHANGELOG.md into structured release notes.

Feeds ``GET /api/settings/changelog`` — the Settings → Updates "What's new"
viewer. Local-first by design: the changelog ships with the app (repo root in
dev; copied into the packaged project dir by the Tauri bootstrap alongside
README.md), so the viewer works fully offline.

The house format (see CHANGELOG.md / the release-notes hard rule):

    ## [X.Y.Z] — DATE
    one-paragraph headline (the "intro")
    ### Added / Fixed / Changed / ...
    - **Bold one-line lead.** 1-3 lines of plain-English why. (#NNN)

Bullets may be a single long line (recent sections) *or* hard-wrapped across
indented continuation lines (older sections) — the parser normalizes both to
one logical line per bullet. Bullets stay raw markdown-lite; the frontend's
safe renderer handles **bold** / `code` / (#NNN) refs.
"""
from __future__ import annotations

import os
import re

#: ``## [0.3.9] — 2026-07-02`` (em/en dash or hyphen; date optional).
_RELEASE_RE = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]\s*(?:[—–-]\s*(?P<date>.+?))?\s*$")
_SECTION_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.*\S)\s*$")
#: ``| a | b |`` download-table rows and ``![alt](src)`` images belong to the
#: GitHub release page, not the in-app notes.
_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_IMAGE_ONLY_RE = re.compile(r"^!\[[^\]]*\]\([^)\s]*\)$")
#: A bold-only label paragraph (``**Download**``) that introduces a table.
_TABLE_LABEL_RE = re.compile(r"^\*\*[^*]+\*\*:?$")


def changelog_path() -> str | None:
    """The shipped CHANGELOG.md, or None when this install doesn't have one.

    ``backend/core/changelog.py`` → two levels up is the project root: the
    repo root in dev, and ``<env>/project`` in packaged installs (where the
    bootstrap copies CHANGELOG.md next to README.md). ``OMNIVOICE_CHANGELOG``
    overrides for tests/containers.
    """
    override = os.environ.get("OMNIVOICE_CHANGELOG")
    if override:
        return override if os.path.isfile(override) else None
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(os.path.dirname(os.path.dirname(here)), "CHANGELOG.md")
    return candidate if os.path.isfile(candidate) else None


def _looks_like_release_version(version: str) -> bool:
    """Only released ``X.Y.Z...`` sections (skip ``[Unreleased]`` etc.)."""
    return bool(re.match(r"^v?\d", version.strip()))


def _shown_to(version: str, app_version: str | None) -> bool:
    """Whether ``version`` belongs in the notes of an app at ``app_version``.

    Sections newer than the running app (a drafted release, or a CHANGELOG
    heading for a version never tagged) are not this app's history, and a
    stable app skips beta sections. Unparseable versions stay visible.
    """
    if not app_version:
        return True
    try:
        from packaging.version import Version

        shown, running = Version(version), Version(app_version)
    except Exception:
        return True
    return shown <= running and (running.is_prerelease or not shown.is_prerelease)


def parse_changelog(
    text: str, limit_versions: int = 5, app_version: str | None = None
) -> list[dict]:
    """CHANGELOG.md text → newest-first list of releases::

        {"version": "0.3.9", "date": "2026-07-02", "intro": "…",
         "sections": [{"title": "Fixed", "bullets": ["…", …]}, …]}

    Tolerates both single-line bullets and older hard-wrapped bullets
    (continuation lines are joined with a space). Content between the version
    heading and the first ``###`` becomes ``intro`` (paragraphs joined by
    blank lines).
    """
    releases: list[dict] = []
    release: dict | None = None
    section: dict | None = None
    intro_parts: list[str] = []
    bullet_open = False   # last bullet may still absorb continuation lines
    intro_new_para = True

    def close_release():
        nonlocal release, section, intro_parts, bullet_open, intro_new_para
        if release is not None:
            release["intro"] = "\n\n".join(p for p in intro_parts if p)
            release["sections"] = [s for s in release["sections"] if s["bullets"]]
            releases.append(release)
        release = None
        section = None
        intro_parts = []
        bullet_open = False
        intro_new_para = True

    for raw in text.splitlines():
        m = _RELEASE_RE.match(raw)
        if m:
            close_release()
            if len(releases) >= limit_versions:
                break
            version = m.group("version").strip().lstrip("v")
            if not _looks_like_release_version(version) or not _shown_to(version, app_version):
                continue  # [Unreleased], or not part of this app's history
            release = {
                "version": version,
                "date": (m.group("date") or "").strip(),
                "intro": "",
                "sections": [],
            }
            continue
        if release is None:
            continue

        line = raw.strip()
        if not line:
            bullet_open = False
            intro_new_para = True
            continue

        if _TABLE_ROW_RE.match(line) or _IMAGE_ONLY_RE.match(line):
            if (
                _TABLE_ROW_RE.match(line)
                and section is None
                and intro_parts
                and _TABLE_LABEL_RE.match(intro_parts[-1])
            ):
                intro_parts.pop()
            bullet_open = False
            intro_new_para = True
            continue

        sm = _SECTION_RE.match(raw)
        if sm:
            section = {"title": sm.group("title"), "bullets": []}
            release["sections"].append(section)
            bullet_open = False
            continue

        bm = _BULLET_RE.match(raw)
        if bm:
            if section is None:
                # Rare: a bullet before any ### heading — group it untitled.
                section = {"title": "", "bullets": []}
                release["sections"].append(section)
            section["bullets"].append(bm.group("text"))
            bullet_open = True
            continue

        if section is not None:
            if bullet_open and section["bullets"]:
                # Hard-wrapped bullet continuation (older sections) → join.
                section["bullets"][-1] += " " + line
            continue

        # Headline paragraph(s) before the first ### section.
        if intro_new_para or not intro_parts:
            intro_parts.append(line)
        else:
            intro_parts[-1] += " " + line
        intro_new_para = False

    close_release()
    return releases[:limit_versions]
